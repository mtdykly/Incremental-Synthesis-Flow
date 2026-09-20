"""Plan a closed combinational region with explicit retained-source/sink maps."""

from cone import is_sequential


MEMORY_MARKERS = ("mem", "ram", "rom")


def is_hard_boundary(cell_type):
    value = (cell_type or "").lower()
    return is_sequential(value) or any(x in value for x in MEMORY_MARKERS)


def plan_regions(base, new, match_result, base_region=None, new_region=None):
    """Close paired regions using explicit external sink pins.

    Inputs need only exist in the retained Base implementation. They need not
    have been used by the deleted Base region. Every retained sink is connected
    from New, so cell-free wiring ECOs and split/merged aliases are represented.
    """
    from connection_diff import source
    requested_base, requested_new = set(base_region or []), set(new_region or [])
    if requested_base - set(base.cells) or requested_new - set(new.cells):
        raise ValueError('requested region contains unknown cells')
    # Selecting either endpoint invalidates reuse of the entire pair. Anything
    # without a checked match is still absorbed, even if RTL provenance missed it.
    mapping = {x['base']: x['new'] for x in match_result['matches']
               if x['base'] not in requested_base and x['new'] not in requested_new}
    reverse = {n: b for b, n in mapping.items()}
    br = set(base.cells) - set(mapping)
    nr = set(new.cells) - set(reverse)
    unsupported = {'base': sorted(b for b in br if is_hard_boundary(base.graph.nodes[b]['type'])),
                   'new': sorted(n for n in nr if is_hard_boundary(new.graph.nodes[n]['type']))}
    br -= set(unsupported['base'])
    nr -= set(unsupported['new'])
    errors = []
    bp, np = base.module_data['ports'], new.module_data['ports']
    signature = lambda ports: {p: (d['direction'], len(d['bits']), d.get('offset', 0),
                                   d.get('upto', 0), d.get('signed', 0)) for p, d in ports.items()}
    if signature(bp) != signature(np) or any(d['direction'] == 'inout' for d in np.values()):
        errors.append('top-level interface changed or contains inout')

    # Rejected/ambiguous matches are absorbed into the region. This fixed
    # complement is closed under all unresolved combinational dependencies.
    inputs = {}
    def retained(bit):
        key = source(new, bit, reverse)
        if key is None:
            raise ValueError(f'no retained source for New bit {bit}')
        if key[0] == 'constant':
            return bit
        if key[0] == 'input':
            return base.input_bits[key[1][0]]
        return base.cells[key[1]]['connections'][key[2]][key[3]]

    def need_input(bit):
        if isinstance(bit, str):
            return
        driver = new.bit_driver.get(str(bit))
        if driver and driver[0] in nr:
            return
        try:
            inputs[bit] = retained(bit)
        except (ValueError, KeyError, IndexError) as exc:
            errors.append(str(exc))
    for n in sorted(nr):
        for _, _, bit in new.cell_input_bits(n):
            need_input(bit)

    sinks = []
    for port, data in sorted(np.items()):
        if data['direction'] == 'output':
            for i, bit in enumerate(data['bits']):
                sinks.append((['port', port, i], bit))
    for b, n in sorted(mapping.items()):
        for port, i, bit in new.cell_input_bits(n):
            sinks.append((['cell', b, port, i], bit))

    outputs, reconnect = [], []
    for sink, bit in sinks:
        driver = new.bit_driver.get(str(bit)) if isinstance(bit, int) else None
        if driver and driver[0] in nr:
            index = len(outputs)
            outputs.append({'key': sink, 'bit': bit})
            reconnect.append({'sink': sink, 'region_port': f'region_out_{index:04d}'})
        else:
            try:
                reconnect.append({'sink': sink, 'base_bit': retained(bit)})
            except (ValueError, KeyError, IndexError) as exc:
                errors.append(str(exc))
    ni = [{'key': ['source', bit], 'bit': bit} for bit in sorted(inputs)]
    bi = [{'key': item['key'], 'bit': inputs[item['bit']]} for item in ni]
    bo = []
    for item in outputs:
        s = item['key']
        try:
            bit = bp[s[1]]['bits'][s[2]] if s[0] == 'port' else base.cells[s[1]]['connections'][s[2]][s[3]]
        except (KeyError, IndexError):
            bit = 'x'
        bo.append({'key': s, 'bit': bit})
    return {'schema_version': 2, 'top': base.top, 'mode': 'generic_register_bounded',
            'stitchable': not errors and not any(unsupported.values()),
            'base_cells': sorted(br), 'new_cells': sorted(nr),
            'base_boundary': {'inputs': bi, 'outputs': bo},
            'new_boundary': {'inputs': ni, 'outputs': outputs},
            'reconnect': reconnect, 'retained_pairs': mapping,
            'requested_region': {'base': sorted(requested_base), 'new': sorted(requested_new)},
            'connection_changes': match_result.get('connection_changes', []),
            'unsupported_changed_state_cells': unsupported,
            'unresolved_boundary_pins': errors,
            'boundary_mismatch': {},
            'closure': 'unresolved combinational cells absorbed; New inputs mapped to retained sources'}
