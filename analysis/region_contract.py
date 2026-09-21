"""Versioned Base-only contracts and the shared local RTLIL extraction/proof stages."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'formal'))
from region import is_hard_boundary
from netlist_graph import NetlistGraph
from canonical_matcher import canonical_match
from verify_transition import write_transition_verification
from yosys_runner import quote, run_yosys


class Unsupported(ValueError):
    """A boundary cannot be certified/extracted; try a containing envelope."""


def write_json(path, data):
    Path(path).write_text(json.dumps(data, indent=2))


def contract_id(path, outputs):
    identity = json.dumps([path, sorted((x['signal'], x['offset']) for x in outputs)])
    return hashlib.sha256(identity.encode()).hexdigest()[:20]


def layouts(instance):
    return {w['signal']: w for w in instance['wires']}


def request_bit(wire, offset, port):
    return {k: wire[k] for k in ('signal', 'width', 'signed', 'start_offset', 'upto')} | {
        'offset': offset, 'port': port}


def combinational(data, top='incremental_region'):
    for name, cell in data['modules'][top]['cells'].items():
        if is_hard_boundary(cell['type']) or cell['type'].startswith(('$any', '$all', '$assert', '$assume', '$check', '$print')):
            raise Unsupported(f'state/memory/side-effect cell in combinational region: {name} ({cell["type"]})')


def prove(candidate_path, reference_path, directory, yosys, timeout, internal_points=True):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    candidate, reference = [json.loads(Path(p).read_text()) for p in (candidate_path, reference_path)]
    combinational(candidate); combinational(reference)
    pairs = {}
    if internal_points:
        # Both operands are LOCAL regions. For Base certification both derive only from Base.
        graphs = [NetlistGraph(p, 'incremental_region') for p in (candidate_path, reference_path)]
        pairs = {m['base']: m['new'] for m in canonical_match(*graphs)['matches']}
    script = directory / 'verify.ys'
    write_transition_verification(script, candidate, reference, 'incremental_region', pairs)
    result = run_yosys(script, directory / 'verify.log', yosys, timeout)
    write_json(directory / 'verification.json', result)
    return result


def extract_and_lower(preproc, request, directory, plugin, yosys, timeout, prefix='new_region'):
    """Only the extracted hierarchy is present when proc/flatten execute."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / 'region_request.json', request)
    script = directory / 'extract.ys'
    script.write_text('\n'.join([
        'plugin -i ' + quote(plugin), 'read_rtlil ' + quote(preproc),
        'eco_extract -spec ' + quote(directory / 'region_request.json') +
        ' -report ' + quote(directory / 'extraction.json'),
        'hierarchy -check -top incremental_region',
        'write_rtlil ' + quote(directory / (prefix + '_preproc.rtlil')),
        'proc -noopt', 'opt_expr -keepdc', 'flatten -scopename', 'memory_collect', 'memory_map',
        'opt_clean', 'check -assert',
        'write_json ' + quote(directory / (prefix + '_spec.json')),
        'opt -keepdc', 'opt_clean', 'check -assert',
        'write_json ' + quote(directory / (prefix + '_synth.json'))]) + '\n')
    result = run_yosys(script, directory / 'extract.log', yosys, timeout)
    if result['status'] != 'passed':
        log = (directory / 'extract.log').read_text()
        error = next((s for s in reversed(log.splitlines()) if 'ERROR:' in s), result['status'])
        raise Unsupported(f'region extraction/lowering failed: {error} ({directory / "extract.log"})')
    for suffix in ('spec', 'synth'):
        combinational(json.loads((directory / f'{prefix}_{suffix}.json').read_text()))
    return result
