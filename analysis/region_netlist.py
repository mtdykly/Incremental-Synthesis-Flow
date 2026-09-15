"""Extract a planned region and stitch its locally synthesized replacement."""

import copy
import json


def _boundary_maps(boundary):
    by_bit = {}
    by_port = {}
    for direction in ("inputs", "outputs"):
        prefix = "region_in" if direction == "inputs" else "region_out"
        for index, item in enumerate(boundary[direction]):
            port = f"{prefix}_{index:04d}"
            by_bit[str(item["bit"])] = port
            by_port[port] = item
    return by_bit, by_port


def extract_region(netlist, cells, boundary, module_name="incremental_region"):
    """Build a valid Yosys JSON design whose scalar ports are the cut bits."""

    selected = set(cells)
    boundary_by_bit, boundary_by_port = _boundary_maps(boundary)
    used_bits = set(boundary_by_bit)
    extracted_cells = {}

    for name in sorted(selected):
        cell = copy.deepcopy(netlist.cells[name])
        extracted_cells[name] = cell
        for bits in cell.get("connections", {}).values():
            used_bits.update(str(bit) for bit in bits if not isinstance(bit, str))

    ports = {}
    for port, item in boundary_by_port.items():
        direction = "input" if port.startswith("region_in") else "output"
        ports[port] = {"direction": direction, "bits": [item["bit"]]}

    netnames = {}
    for name, data in netlist.module_data.get("netnames", {}).items():
        bits = data.get("bits", [])
        if bits and all(isinstance(bit, str) or str(bit) in used_bits for bit in bits):
            netnames[name] = copy.deepcopy(data)

    return {
        "creator": "Incremental-Synthesis-Flow region extractor",
        "modules": {
            module_name: {
                "attributes": {"top": "1"},
                "ports": ports,
                "cells": extracted_cells,
                "netnames": netnames,
            }
        },
    }


def _module_max_bit(module):
    values = []
    for port in module.get("ports", {}).values():
        values.extend(bit for bit in port.get("bits", []) if isinstance(bit, int))
    for net in module.get('netnames', {}).values():
        values.extend(bit for bit in net.get('bits', []) if isinstance(bit, int))
    for cell in module.get("cells", {}).values():
        for bits in cell.get("connections", {}).values():
            values.extend(bit for bit in bits if isinstance(bit, int))
    return max(values, default=1)


def stitch_region(base_data, plan, replacement_data, module_name='incremental_region'):
    from structure_check import check_module
    if plan.get('schema_version') != 2:
        raise ValueError('obsolete region plan; run plan again')
    if not plan.get('stitchable'):
        raise ValueError('region plan is not stitchable')
    result = copy.deepcopy(base_data)
    top = result['modules'][plan['top']]
    top['cells'] = {n: c for n, c in top['cells'].items() if c['type'] != '$scopeinfo'}
    replacement = replacement_data['modules'][module_name]
    _, expected = _boundary_maps(plan['new_boundary'])
    ports = replacement.get('ports', {})
    if set(ports) != set(expected):
        raise ValueError('replacement port names/completeness mismatch')
    for name, data in ports.items():
        direction = 'input' if name.startswith('region_in') else 'output'
        if data.get('direction') != direction or len(data.get('bits', [])) != 1:
            raise ValueError(f'invalid replacement port {name}')
    next_bit = _module_max_bit(top) + 1
    remapped = {}
    for i, item in enumerate(plan['base_boundary']['inputs']):
        bit = ports[f'region_in_{i:04d}']['bits'][0]
        if isinstance(bit, str) or (bit in remapped and remapped[bit] != item['bit']):
            raise ValueError('replacement aliases distinct inputs')
        remapped[bit] = item['bit']
    # Use a deleted, undriven Base bit when possible; otherwise allocate fresh.
    retained_drivers = set()
    for name, cell in top['cells'].items():
        if name not in plan['base_cells']:
            for p, bits in cell.get('connections', {}).items():
                if cell.get('port_directions', {}).get(p) == 'output':
                    retained_drivers.update(bits)
    retained_drivers.update(b for p in top['ports'].values() if p['direction'] == 'input' for b in p['bits'])
    claimed = set(remapped.values()) | retained_drivers
    for i, item in enumerate(plan['base_boundary']['outputs']):
        bit = ports[f'region_out_{i:04d}']['bits'][0]
        old = item['bit']
        if isinstance(bit, int) and bit not in remapped and isinstance(old, int) and old not in claimed:
            remapped[bit] = old
            claimed.add(old)
    def remap(bit):
        nonlocal next_bit
        if isinstance(bit, str):
            return bit
        if bit not in remapped:
            remapped[bit] = next_bit
            next_bit += 1
        return remapped[bit]
    for name in plan['base_cells']:
        del top['cells'][name]
    alias_targets = {}
    for item in plan['reconnect']:
        sink = item['sink']
        bits = (top['ports'][sink[1]]['bits'] if sink[0] == 'port'
                else top['cells'][sink[1]]['connections'][sink[2]])
        i = sink[-1]
        old = bits[i]
        value = remap(ports[item['region_port']]['bits'][0]) if 'region_port' in item else item['base_bit']
        bits[i] = value
        alias_targets.setdefault(old, set()).add(value)
    for index, (name, data) in enumerate(sorted(replacement.get('cells', {}).items())):
        cell = copy.deepcopy(data)
        cell['connections'] = {p: [remap(b) for b in bits] for p, bits in cell['connections'].items()}
        name = f'$incremental${index}${name}'
        while name in top['cells']:
            name = '$' + name
        top['cells'][name] = cell
    # Old aliases can split. Drop ambiguous/deleted internal names instead of
    # assigning a false meaning; keep unambiguous aliases and exact port names.
    active = {b for c in top['cells'].values() for bits in c['connections'].values() for b in bits}
    active.update(b for p in top['ports'].values() for b in p['bits'])
    nets = {}
    for name, data in top.get('netnames', {}).items():
        data = copy.deepcopy(data)
        updated = []
        for bit in data['bits']:
            targets = alias_targets.get(bit, set())
            if bit not in retained_drivers and len(targets) == 1:
                bit = next(iter(targets))
            if (len(targets) > 1 and bit not in retained_drivers) or (isinstance(bit, int) and bit not in active):
                break
            updated.append(bit)
        else:
            data['bits'] = updated
            nets[name] = data
    for name, port in top['ports'].items():
        data = copy.deepcopy(top.get('netnames', {}).get(name, {}))
        data.update(hide_name=0, bits=port['bits'][:])
        data.setdefault('attributes', {})
        nets[name] = data
    top['netnames'] = nets
    check_module(top)
    return result


def load_json(path):
    with open(path) as stream:
        return json.load(stream)


def dump_json(data, path):
    with open(path, "w") as stream:
        json.dump(data, stream, indent=2)
