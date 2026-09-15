"""Register identity is anchored at named Q bits; D may change in an ECO."""

from canonical import intrinsic_signature
from connection_diff import same_inputs

REGISTER_TYPES = {'$dff', '$dffe', '$adff', '$adffe', '$sdff', '$sdffe', '$sdffce'}


def state_key(graph, name):
    cell = graph.cells[name]
    if cell['type'] not in REGISTER_TYPES:
        return None
    q = cell['connections']['Q']
    aliases = []
    for bit in q:
        names = []
        for net, data in graph.module_data.get('netnames', {}).items():
            if not data.get('hide_name', 0):
                names.extend((net, i) for i, value in enumerate(data.get('bits', [])) if value == bit)
        if not names:
            return None
        aliases.append(tuple(sorted(names)))
    return tuple(aliases)


def initialization(graph, name):
    values = {}
    for data in graph.module_data.get('netnames', {}).values():
        init = data.get('attributes', {}).get('init')
        if init is not None:
            for bit, value in zip(data['bits'], reversed(str(init))):
                if bit in values and values[bit] != value:
                    raise ValueError('conflicting initialization aliases')
                values[bit] = value
    return tuple(values.get(bit, 'x') for bit in graph.cells[name]['connections']['Q'])


def state_candidates(base, new):
    from collections import defaultdict
    result = []
    candidates = defaultdict(list)
    for n in sorted(new.cells):
        key = state_key(new, n)
        if key is not None:
            candidates[key, intrinsic_signature(new.cells[n])].append(n)
    for b in sorted(base.cells):
        key = state_key(base, b)
        if key is None:
            continue
        choices = candidates[key, intrinsic_signature(base.cells[b])]
        if len(choices) == 1:
            result.append((b, choices[0]))
    return result


def state_compatible(base, new, b, n, mapping):
    return (base.cells[b]['type'] in REGISTER_TYPES
            and initialization(base, b) == initialization(new, n)
            and same_inputs(base, new, b, n, mapping, ignore=('D',)))
