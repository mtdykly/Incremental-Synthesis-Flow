"""Offline, Base-only boundary discovery and SAT-certified region contracts."""
import copy
import json
from pathlib import Path

from netlist_graph import NetlistGraph
from region import is_hard_boundary
from region_netlist import extract_region
from region_contract import (Unsupported, contract_id, layouts, request_bit, write_json,
                             extract_and_lower, prove)
from rtl_sources import digest


def candidates(index):
    """Envelopes plus whole-process/public-expression output groups; no New input."""
    result = {}
    for instance in index['instances']:
        wires = layouts(instance)
        envelope = [{'signal': w['signal'], 'offset': i} for w in instance['wires']
                    if w['output'] for i in range(w['width'])]
        groups = [('module', envelope)]
        groups += [(obj['kind'], obj.get('outputs', [])) for obj in instance['objects']
                   if obj['kind'] in ('process', 'cell', 'connection') and obj.get('combinational', True)]
        for kind, outputs in groups:
            outputs = sorted({(o['signal'], o['offset']) for o in outputs if not wires[o['signal']]['input']})
            if not outputs:
                continue
            outputs = [{'signal': n, 'offset': i} for n, i in outputs]
            key = contract_id(instance['path'], outputs)
            result.setdefault(key, {'id': key, 'kind': kind, 'instance': instance, 'targets': outputs})
    return result


def flat_bit(graph, path, signal, offset):
    name = '.'.join(path + [signal])
    net = graph.module_data.get('netnames', {}).get(name)
    if net is None or offset >= len(net['bits']):
        raise Unsupported(f'Base symbol optimized away or ambiguous: {name}[{offset}]')
    return net['bits'][offset]


def make_contract(graph, candidate):
    instance, path = candidate['instance'], candidate['instance']['path']
    wires = layouts(instance)
    if any(w['input'] and w['output'] for w in wires.values()):
        raise Unsupported('inout boundary unsupported')
    inputs = [request_bit(w, i, '') for w in sorted(wires.values(), key=lambda w: w['signal'])
              if w['input'] for i in range(w['width'])]
    for i, item in enumerate(inputs):
        item.update(port=f'region_in_{i:04d}', bit=flat_bit(graph, path, item['signal'], item['offset']))
    cuts = {x['bit'] for x in inputs if isinstance(x['bit'], int)}
    output_symbols = {(o['signal'], o['offset']) for o in candidate['targets']}
    selected = set()
    def visit(bit):
        if isinstance(bit, str) or bit in cuts:
            return
        driver = graph.bit_driver.get(str(bit))
        if not driver:
            raise Unsupported(f'no certified module-input source for Base bit {bit}')
        cell = driver[0]
        if cell in selected:
            return
        if is_hard_boundary(graph.cells[cell]['type']):
            raise Unsupported(f'region crosses state/memory: {cell}')
        selected.add(cell)
        for _, _, source in graph.cell_input_bits(cell):
            visit(source)
    for n, i in output_symbols:
        visit(flat_bit(graph, path, n, i))
    # Every external use of every deleted cell output must be exported, including
    # shared subexpressions that were not an original RTL output seed.
    aliases = {}
    for w in sorted(wires.values(), key=lambda w: (not w['output'], w['signal'])):
        if w['input']:
            continue
        for i in range(w['width']):
            try:
                bit = flat_bit(graph, path, w['signal'], i)
                if isinstance(bit, int): aliases.setdefault(bit, (w['signal'], i))
            except Unsupported:
                pass
    proof_cells = set(selected)
    retained_shared = set()
    def retain_fanin(cell):
        if cell in retained_shared or cell not in selected:
            return
        retained_shared.add(cell)
        for _, _, b in graph.cell_input_bits(cell):
            driver = graph.bit_driver.get(str(b))
            if driver: retain_fanin(driver[0])
    for cell in sorted(selected):
        for _, _, bit in graph.cell_output_bits(cell):
            if bit in cuts:
                raise Unsupported('deleted cell also drives a retained input cut')
            external = any(u[0] not in selected for u in graph.bit_users.get(str(bit), []))
            external |= str(bit) in graph.primary_outputs
            if external:
                if bit not in aliases:
                    # Global opt_merge may share an unnamed expression across
                    # module boundaries. Keep that old fanin for outside users;
                    # the local New slice independently recomputes its copy.
                    retain_fanin(cell)
                else:
                    output_symbols.add(aliases[bit])
    selected -= retained_shared
    outputs, reconnect, fixed = [], [], []
    seen_sinks = {}
    for n, i in sorted(output_symbols):
        item = request_bit(wires[n], i, f'region_out_{len(outputs):04d}')
        bit = flat_bit(graph, path, n, i)
        item['bit'] = bit
        outputs.append(item)
        if not path and wires[n]['output']:
            sinks = [['port', n, i]]
        elif isinstance(bit, str):
            # A constant has no uniquely attributable consumers in a flat design.
            # It may remain fixed; any changed New value must promote to parent.
            fixed.append({'port': item['port'], 'value': bit})
            sinks = []
        else:
            driver = graph.bit_driver.get(str(bit))
            if not driver or driver[0] not in selected:
                raise Unsupported(f'output {n}[{i}] aliases retained input/logic; promote envelope')
            sinks = [['cell', c, p, k] for c, p, k in graph.bit_users.get(str(bit), []) if c not in selected]
            sinks += [['port', p, k] for p, data in graph.module_data['ports'].items()
                      if data['direction'] == 'output' for k, b in enumerate(data['bits']) if b == bit]
        # Root outputs can also be read internally by retained logic.
        if not path and isinstance(bit, int) and graph.bit_driver.get(str(bit), (None,))[0] in selected:
            sinks += [['cell', c, p, k] for c, p, k in graph.bit_users.get(str(bit), []) if c not in selected]
        for sink in sinks:
            key = tuple(sink)
            if key not in seen_sinks:
                seen_sinks[key] = item['port']
                reconnect.append({'sink': sink, 'region_port': item['port']})
    # Exported aliases must stay equal in New; one consumer cannot be connected
    # to two different replacement outputs after optimized Base aliases split.
    aliases_out = {}
    for o in outputs:
        if isinstance(o['bit'], int): aliases_out.setdefault(o['bit'], []).append(o['port'])
    boundary = {d: [{'bit': x['bit'], 'key': [x['signal'], x['offset']]} for x in items]
                for d, items in [('inputs', inputs), ('outputs', outputs)]}
    return {'schema_version': 1, 'id': candidate['id'], 'kind': candidate['kind'],
            'instance_path': path, 'top': graph.top, 'base_cells': sorted(selected),
            'inputs': inputs, 'outputs': outputs, 'fixed_outputs': fixed,
            'proof_cells': sorted(proof_cells), 'retained_shared_fanin': sorted(retained_shared),
            'aliased_outputs': [v for v in aliases_out.values() if len(v) > 1] if path else [],
            'request': {'top': graph.top, 'instance_path': path, 'inputs': inputs, 'outputs': outputs},
            'plan': {'schema_version': 2, 'top': graph.top, 'stitchable': True,
                     'mode': 'rtl_direct', 'base_cells': sorted(selected), 'base_boundary': boundary,
                     'new_boundary': copy.deepcopy(boundary), 'reconnect': reconnect}}


def constrained_reference(data, contract):
    """Certify under exact constant/equality constraints of Base external wiring.

    Input ports remain distinct, including unused/aliased module inputs. Only
    their internal uses are constrained. The same actual Base bits are wired at
    stitch time, so this introduces no unrecorded boundary assumptions.
    """
    data = copy.deepcopy(data)
    mod = data['modules']['incremental_region']
    first, mapping = {}, {}
    for item in contract['inputs']:
        port_bit = mod['ports'][item['port']]['bits'][0]
        b = item['bit']
        target = b if isinstance(b, str) else first.setdefault(b, port_bit)
        mapping[port_bit] = target
    for cell in mod['cells'].values():
        cell['connections'] = {p: [mapping.get(b, b) for b in bs] for p, bs in cell['connections'].items()}
    for net in mod.get('netnames', {}).values(): net['bits'] = [mapping.get(b, b) for b in net['bits']]
    for port in mod['ports'].values():
        if port['direction'] == 'output': port['bits'] = [mapping.get(b, b) for b in port['bits']]
    return data


def base_region(graph, contract):
    data = extract_region(graph, contract['proof_cells'], contract['plan']['base_boundary'])
    mod = data['modules']['incremental_region']
    maximum = max([b for c in mod['cells'].values() for bs in c['connections'].values() for b in bs
                   if isinstance(b, int)] + [b for n in mod['netnames'].values() for b in n['bits']
                                             if isinstance(b, int)] + [1])
    seen = set()
    for item in contract['inputs']:
        b = item['bit']
        if isinstance(b, str) or b in seen:
            maximum += 1
            mod['ports'][item['port']]['bits'] = [maximum]
        seen.add(b)
    return data


def prepare(base_dir, plugin, yosys, timeout, rebuild=False):
    base_dir = Path(base_dir)
    manifest = json.loads((base_dir / 'manifest.json').read_text())
    identity = {'manifest_sha256': digest(base_dir / 'manifest.json'),
                'implementation': {str(p.relative_to(Path(__file__).resolve().parents[1])): digest(p)
                                   for folder in ('analysis', 'formal')
                                   for p in sorted((Path(__file__).resolve().parents[1] / folder).rglob('*.py'))}}
    cache = base_dir / 'contracts'
    cache.mkdir(exist_ok=True)
    catalog = cache / 'catalog.json'
    if catalog.is_file() and not rebuild:
        result = json.loads(catalog.read_text())
        if result['identity'] != identity or any(not (cache / p).is_file() or digest(cache / p) != sha
                                                 for p, sha in result['artifacts'].items()):
            raise ValueError('stale Base contracts; rerun --rtl-direct --frontend')
        for key, contract in result['contracts'].items():
            if contract['status'] == 'certified' and contract != json.loads((cache / key / 'contract.json').read_text()):
                raise ValueError('stale or inconsistent Base contract catalog; rerun --rtl-direct --frontend')
        return result
    graph = NetlistGraph(base_dir / 'design_flat.json', manifest['top'])
    index = json.loads((base_dir / 'source_index.json').read_text())
    result = {'schema_version': 1, 'identity': identity, 'new_inputs_read': False, 'contracts': {}}
    all_candidates = candidates(index)
    for count, (key, candidate) in enumerate(sorted(all_candidates.items()), 1):
        directory = cache / key
        directory.mkdir(exist_ok=True)
        try:
            contract = make_contract(graph, candidate)
            extract_and_lower(base_dir / 'base_preproc.rtlil', contract['request'], directory,
                              plugin, yosys, timeout, prefix='base_region')
            write_json(directory / 'base_cone.json', base_region(graph, contract))
            reference = constrained_reference(json.loads((directory / 'base_region_spec.json').read_text()), contract)
            write_json(directory / 'base_spec_constrained.json', reference)
            proof = prove(directory / 'base_cone.json', directory / 'base_spec_constrained.json',
                          directory / 'proof', yosys, timeout)
            if proof['status'] != 'passed':
                raise Unsupported(f'Base cutpoint certification {proof["status"]}: {proof["log"]}')
            contract.update(status='certified', proof=proof)
            write_json(directory / 'contract.json', contract)
            result['contracts'][key] = contract
        except (Unsupported, KeyError, ValueError) as exc:
            result['contracts'][key] = {'id': key, 'status': 'unsupported', 'kind': candidate['kind'],
                                        'instance_path': candidate['instance']['path'], 'reason': str(exc)}
        if count % 10 == 0:
            print(f'Base-only contracts: {count}/{len(all_candidates)}', flush=True)
    result['artifacts'] = {str(p.relative_to(cache)): digest(p) for p in cache.rglob('*') if p.is_file() and p != catalog}
    write_json(catalog, result)
    return result
