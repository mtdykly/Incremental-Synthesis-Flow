"""Isolated whole-design verification. New generic data never leaves this stage.

The candidate is already fixed on entry. Matching here only creates asserted
proof obligations; it cannot authorize synthesis reuse or choose a boundary.
"""
import json
from pathlib import Path
from canonical_matcher import canonical_match
from netlist_graph import NetlistGraph
from state_matcher import state_candidates
from verify_transition import write_transition_verification, build_transition_problem
from yosys_runner import identifier, quote, run_yosys
from rtl_sources import digest


def verify(candidate, new_preproc, top, directory, yosys='yosys', timeout=120):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    candidate = Path(candidate)
    bound = digest(candidate)
    reference = directory / 'new_reference.json'
    script = directory / 'prepare_reference.ys'
    script.write_text('\n'.join([
        'read_rtlil ' + quote(new_preproc), 'hierarchy -check -top ' + identifier(top),
        'proc -noopt', 'opt_expr', 'flatten -scopename', 'opt', 'memory', 'opt_clean',
        'check -assert', 'write_json ' + quote(reference)]) + '\n')
    result = run_yosys(script, directory / 'prepare_reference.log', yosys, timeout)
    outcome = {'status': result['status'], 'reference_preparation': result,
               'candidate_sha256': bound, 'reference_source_sha256': digest(new_preproc),
               'scope': 'all top outputs and all state data/control inputs; arbitrary shared Q',
               'reference_use': 'verification only; not an incremental synthesis input'}
    if result['status'] == 'passed':
        try:
            graphs = [NetlistGraph(p, top) for p in (candidate, reference)]
            signature = lambda g: {n: (p['direction'], len(p['bits']), p.get('signed', 0),
                                       p.get('offset', 0), p.get('upto', 0))
                                    for n, p in g.module_data['ports'].items()}
            if signature(graphs[0]) != signature(graphs[1]):
                raise ValueError('top interface changed')
            matches = canonical_match(*graphs)
            pairs = {m['base']: m['new'] for m in matches['matches']}
            pairs.update(state_candidates(*graphs))
            script = directory / 'verify.ys'
            write_transition_verification(script, graphs[0].raw_data, graphs[1].raw_data, top, pairs)
            outcome.update(run_yosys(script, directory / 'verify.log', yosys, timeout))
        except ValueError as exc:
            outcome.update(status='unsupported', error=str(exc))
    if digest(candidate) != bound:
        outcome.update(status='tool_error', error='candidate changed during verification')
    (directory / 'verification.json').write_text(json.dumps(outcome, indent=2))
    return outcome


def verify_unchanged_controls(base, candidate, top, directory, yosys, timeout):
    """First version permits data ECOs, but cannot alter clock/reset/enable semantics."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    graphs = [NetlistGraph(p, top) for p in (candidate, base)]
    pairs = dict(state_candidates(*graphs))
    problem = build_transition_problem(graphs[0].raw_data, graphs[1].raw_data, top, pairs)
    for mod in problem['modules'].values():
        mod['ports'] = {n: p for n, p in mod['ports'].items() if p['direction'] == 'input' or (
            n.startswith('__state_') and not n.endswith('_D'))}
    if not any(p['direction'] == 'output' for p in problem['modules']['gold']['ports'].values()):
        return {'status': 'passed', 'scope': 'no state controls'}
    data = directory / 'controls.json'
    data.write_text(json.dumps(problem))
    script = directory / 'controls.ys'
    script.write_text('read_json ' + quote(data) + '\nequiv_make gold gate equiv\nhierarchy -top equiv\n'
                      'opt_expr -keepdc\nopt_merge\nopt_clean\nequiv_simple -undef\nequiv_status -assert\n')
    result = run_yosys(script, directory / 'controls.log', yosys, timeout)
    result['scope'] = 'unchanged state initialization, type, clock, reset and enable'
    return result
