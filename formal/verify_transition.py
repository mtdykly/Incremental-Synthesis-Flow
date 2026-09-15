"""Prove all outputs and next-state/control functions for arbitrary shared Q.

This is a one-step inductive proof with an explicit unchanged state mapping,
not bounded simulation and not a guess based on internal wire names.
"""
import copy
import json
from pathlib import Path
from canonical import intrinsic_signature
from region import is_hard_boundary
from state_matcher import REGISTER_TYPES, initialization
from yosys_runner import quote


def build_transition_problem(candidate, reference, top, pairs):
    modules = [copy.deepcopy(candidate['modules'][top]), copy.deepcopy(reference['modules'][top])]
    state_pairs = [(b, n) for b, n in sorted(pairs.items())
                   if is_hard_boundary(modules[0]['cells'][b]['type'])]
    for side, mod in enumerate(modules):
        expected = {pair[side] for pair in state_pairs}
        actual = {name for name, c in mod['cells'].items() if is_hard_boundary(c['type'])}
        if expected != actual:
            raise ValueError('unmapped state in final verification')
    for i, (b, n) in enumerate(state_pairs):
        bc, nc = modules[0]['cells'][b], modules[1]['cells'][n]
        if bc['type'] not in REGISTER_TYPES or intrinsic_signature(bc) != intrinsic_signature(nc):
            raise ValueError('unsupported or changed state semantics')
        # Initial values are checked explicitly before state is abstracted.
        class Graph:
            pass
        gs = []
        for mod in modules:
            g = Graph()
            g.module_data, g.cells = mod, mod['cells']
            gs.append(g)
        if initialization(gs[0], b) != initialization(gs[1], n):
            raise ValueError('state initialization changed')
        for side, name in enumerate((b, n)):
            mod = modules[side]
            cell = mod['cells'].pop(name)
            for pin, bits in cell['connections'].items():
                port = f'__state_{i}_{pin}'
                if port in mod['ports']:
                    raise ValueError('reserved verification port collision')
                mod['ports'][port] = {'direction': 'input' if pin == 'Q' else 'output', 'bits': bits[:]}
    for mod in modules:
        mod['attributes'] = {}
        mod['netnames'] = {}
        mod['cells'] = {n: c for n, c in mod['cells'].items() if c['type'] != '$scopeinfo'}
    # Explicit implementation correspondences are proof obligations, not
    # assumptions: equiv_status -assert must discharge these internal points
    # as well as every top/transition output. They keep arithmetic cones local.
    for i, (b, n) in enumerate(sorted(pairs.items())):
        if b not in modules[0]['cells'] or n not in modules[1]['cells']:
            continue
        bc, nc = modules[0]['cells'][b], modules[1]['cells'][n]
        for pin, bits in bc['connections'].items():
            if bc['port_directions'][pin] != 'output':
                continue
            if nc['port_directions'].get(pin) != 'output' or len(nc['connections'][pin]) != len(bits):
                raise ValueError('retained output layout mismatch')
            for mod, cell in zip(modules, (bc, nc)):
                mod['netnames'][f'__proof_{i}_{pin}'] = {
                    'hide_name': 0, 'bits': cell['connections'][pin][:], 'attributes': {}}
    return {'modules': dict(zip(('gold', 'gate'), modules))}


def write_transition_verification(path, candidate, reference, top, pairs):
    path = Path(path)
    problem = build_transition_problem(candidate, reference, top, pairs)
    data = path.with_suffix('.json')
    data.write_text(json.dumps(problem))
    path.write_text(f'read_json {quote(data)}\n'
                    'equiv_make gold gate equiv\n'
                    'hierarchy -top equiv\nopt -full\ncheck -assert\n'
                    'equiv_simple\nequiv_status -assert\n')
