"""Fail closed on unsupported directions, floating sinks, drivers and cycles."""
from collections import defaultdict, deque
from region import is_hard_boundary


def check_module(module):
    drivers, users = {}, []
    def drive(bit, owner):
        if isinstance(bit, str):
            raise ValueError('constant used as driven output')
        if bit in drivers:
            raise ValueError(f'multiple drivers on bit {bit}')
        drivers[bit] = owner
    for name, port in module.get('ports', {}).items():
        if port['direction'] == 'input':
            for bit in port['bits']:
                drive(bit, None)
        elif port['direction'] == 'output':
            users.extend((bit, None) for bit in port['bits'])
        else:
            raise ValueError('inout is unsupported')
    cells = {n: c for n, c in module.get('cells', {}).items() if c['type'] != '$scopeinfo'}
    comb = {n for n, c in cells.items() if not is_hard_boundary(c['type'])}
    for name, cell in cells.items():
        for port, bits in cell.get('connections', {}).items():
            direction = cell.get('port_directions', {}).get(port)
            if direction == 'output':
                for bit in bits:
                    drive(bit, name)
            elif direction == 'input':
                users.extend((bit, name) for bit in bits)
            else:
                raise ValueError(f'unsupported direction: {name}.{port}')
    edges, degree = defaultdict(set), dict.fromkeys(comb, 0)
    for bit, user in users:
        if isinstance(bit, str):
            if bit not in {'0', '1', 'x', 'z'}:
                raise ValueError(f'invalid constant {bit}')
            continue
        if bit not in drivers:
            raise ValueError(f'undriven bit {bit} used by {user}')
        driver = drivers[bit]
        if driver in comb and user in comb and user not in edges[driver]:
            edges[driver].add(user)
            degree[user] += 1
    queue = deque(n for n in comb if not degree[n])
    count = 0
    while queue:
        count += 1
        for user in edges[queue.popleft()]:
            degree[user] -= 1
            if not degree[user]:
                queue.append(user)
    if count != len(comb):
        raise ValueError('combinational cycle')
