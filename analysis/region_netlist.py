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
    for cell in module.get("cells", {}).values():
        for bits in cell.get("connections", {}).values():
            values.extend(bit for bit in bits if isinstance(bit, int))
    return max(values, default=1)


def stitch_region(base_data, plan, replacement_data, module_name="incremental_region"):
    """Replace base-region cells with a (possibly optimized) extracted module.

    Boundary port names are deliberately stable across local synthesis.  They
    are mapped to the old base cut nets; all replacement-internal bit IDs are
    freshly allocated to avoid collisions.
    """

    if not plan.get("stitchable"):
        raise ValueError("region plan is not stitchable")

    result = copy.deepcopy(base_data)
    top_name = plan.get("top")
    if top_name not in result["modules"]:
        top_name = next(iter(result["modules"]))
    top = result["modules"][top_name]
    replacement = replacement_data["modules"][module_name]

    for cell in plan["base_cells"]:
        top.get("cells", {}).pop(cell, None)

    _, base_ports = _boundary_maps(plan["base_boundary"])
    port_to_base_bit = {port: item["bit"] for port, item in base_ports.items()}
    replacement_port_bits = {
        str(data["bits"][0]): port_to_base_bit[name]
        for name, data in replacement.get("ports", {}).items()
    }

    next_bit = _module_max_bit(top) + 1
    internal_map = {}

    def remap(bit):
        nonlocal next_bit
        if isinstance(bit, str):
            return bit
        key = str(bit)
        if key in replacement_port_bits:
            return replacement_port_bits[key]
        if key not in internal_map:
            internal_map[key] = next_bit
            next_bit += 1
        return internal_map[key]

    for index, (name, source_cell) in enumerate(sorted(replacement.get("cells", {}).items())):
        cell = copy.deepcopy(source_cell)
        cell["connections"] = {
            port: [remap(bit) for bit in bits]
            for port, bits in cell.get("connections", {}).items()
        }
        new_name = f"$incremental${index}${name}"
        while new_name in top["cells"]:
            new_name = "$" + new_name
        top["cells"][new_name] = cell

    return result


def load_json(path):
    with open(path) as stream:
        return json.load(stream)


def dump_json(data, path):
    with open(path, "w") as stream:
        json.dump(data, stream, indent=2)
