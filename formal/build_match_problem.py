"""Build a local cell miter with explicit, shared symbolic input sources."""
import copy
import hashlib
import json
from connection_diff import source
from region import is_hard_boundary


def design_digest(base, new):
    return hashlib.sha256(json.dumps([base.module_data, new.module_data], sort_keys=True).encode()).hexdigest()


def build_match_problem(base, new, b, n, correspondences):
    bc, nc = base.cells[b], new.cells[n]
    if is_hard_boundary(bc['type']) or is_hard_boundary(nc['type']):
        raise ValueError('state cells require state correspondence')
    def layout(c, direction):
        return {p: len(bits) for p, bits in c['connections'].items()
                if c['port_directions'][p] == direction}
    if layout(bc, 'input') != layout(nc, 'input') or layout(bc, 'output') != layout(nc, 'output'):
        raise ValueError('local proof requires the same input/output pin layout')
    inputs = {}
    for p in sorted(layout(bc, 'input')):
        for i, (bb, nb) in enumerate(zip(bc['connections'][p], nc['connections'][p])):
            bs, ns = source(base, bb, correspondences), source(new, nb)
            if bs is None or ns is None or bs != ns:
                raise ValueError('candidate input source correspondence is unresolved')
            inputs[p, i] = bs
    identities = sorted({key for key in inputs.values() if key[0] != 'constant'}, key=repr)
    symbols = {key: i + 2 for i, key in enumerate(identities)}
    def module(cell):
        cell = copy.deepcopy(cell)
        ports = {f'i{i}': {'direction': 'input', 'bits': [symbols[key]]} for i, key in enumerate(identities)}
        next_bit = len(symbols) + 2
        for p in sorted(layout(cell, 'input')):
            cell['connections'][p] = [key[1] if key[0] == 'constant' else symbols[key]
                                       for key in (inputs[p, i] for i in range(len(cell['connections'][p])))]
        for p, width in sorted(layout(cell, 'output').items()):
            bits = list(range(next_bit, next_bit + width))
            next_bit += width
            cell['connections'][p] = bits
            ports['o_' + p] = {'direction': 'output', 'bits': bits}
        return {'attributes': {}, 'ports': ports, 'cells': {'dut': cell}, 'netnames': {}}
    return {'modules': {'gold': module(bc), 'gate': module(nc)}}, identities
