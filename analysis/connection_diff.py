"""Pin identities are relative to a correspondence, never raw JSON bit IDs."""

from canonical import intrinsic_signature


def source(netlist, bit, mapping=None):
    if isinstance(bit, str):
        return ("constant", bit)
    labels = tuple(sorted(k for k, v in netlist.input_bits.items() if v == bit))
    if labels:
        return ("input", labels)
    driver = netlist.bit_driver.get(str(bit))
    if driver:
        cell, port, index = driver
        if mapping is not None:
            cell = mapping.get(cell)
            if cell is None:
                return None
        return ("cell", cell, port, index)
    return None


def same_inputs(base, new, b, n, mapping, ignore=()):
    bc, nc = base.cells[b], new.cells[n]
    if intrinsic_signature(bc) != intrinsic_signature(nc):
        return False
    def pins(graph, cell, translate):
        return {p: tuple(source(graph, bit, translate) for bit in bits)
                for p, bits in cell['connections'].items()
                if cell['port_directions'][p] == 'input' and p not in ignore}
    bp, np = pins(base, bc, mapping), pins(new, nc, None)
    if any(x is None for bits in list(bp.values()) + list(np.values()) for x in bits):
        return False
    if bp == np:
        return True
    # Swap entire operands only when widths and signedness are symmetric.
    params = bc.get('parameters', {})
    if (bc['type'] in {'$and', '$or', '$xor', '$xnor', '$add', '$mul', '$eq', '$ne'}
            and set(bp) == {'A', 'B'} and len(bp['A']) == len(bp['B'])
            and params.get('A_SIGNED') == params.get('B_SIGNED')):
        return bp['A'] == np['B'] and bp['B'] == np['A']
    return False


def connection_diff(base, new, mapping):
    changes = []
    for b, n in sorted(mapping.items()):
        for p, i, bit in new.cell_input_bits(n):
            old = base.cells[b]['connections'].get(p, [])
            if i >= len(old) or source(base, old[i], mapping) != source(new, bit):
                changes.append({'sink': ['cell', b, p, i], 'new_bit': bit})
    for label, bit in sorted(new.output_bits.items()):
        old = base.output_bits.get(label)
        if old is None or source(base, old, mapping) != source(new, bit):
            changes.append({'sink': ['output', label], 'new_bit': bit})
    return changes
