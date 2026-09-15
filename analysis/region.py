"""Plan a closed, combinational replacement region in two netlists.

The important distinction in this module is between a changed cell and a cut
boundary.  A matched cell is only a usable boundary when the corresponding
pin exists on both sides.  The planner therefore assigns version-independent
keys to cut bits and refuses to produce a stitchable plan when the two sets of
keys differ.
"""

from cone import is_sequential


MEMORY_MARKERS = ("mem", "ram", "rom")


def is_hard_boundary(cell_type):
    value = (cell_type or "").lower()
    return is_sequential(value) or any(x in value for x in MEMORY_MARKERS)


def _bit_key(bit):
    return str(bit)


def _normalized_cell(side, cell, new_to_base):
    if side == "base":
        return cell
    return new_to_base.get(cell)


def _source_key(netlist, side, bit, new_to_base):
    key = _bit_key(bit)
    if key in netlist.primary_inputs:
        return ("top_input", netlist.primary_inputs[key])

    driver = netlist.bit_driver.get(key)
    if driver is None:
        return None

    cell, port, index = driver
    normalized = _normalized_cell(side, cell, new_to_base)
    if normalized is None:
        return None
    return ("cell_output", normalized, port, index)


def _sink_key(netlist, side, bit, region, new_to_base):
    key = _bit_key(bit)
    sinks = []

    if key in netlist.primary_outputs:
        sinks.append(("top_output", netlist.primary_outputs[key]))

    for cell, port, index in netlist.bit_users.get(key, []):
        if cell in region:
            continue
        normalized = _normalized_cell(side, cell, new_to_base)
        if normalized is None:
            return None
        sinks.append(("cell_input", normalized, port, index))

    if not sinks:
        return None
    return ("sinks", tuple(sorted(sinks)))


def describe_boundary(netlist, region, side, new_to_base):
    inputs = {}
    outputs = {}
    invalid = []

    for cell in sorted(region):
        for _, _, bit in netlist.cell_input_bits(cell):
            if isinstance(bit, str):
                continue
            driver = netlist.bit_driver.get(_bit_key(bit))
            if driver is not None and driver[0] in region:
                continue
            identity = _source_key(netlist, side, bit, new_to_base)
            if identity is None:
                invalid.append({"kind": "input", "bit": bit, "cell": cell})
            else:
                inputs.setdefault(identity, bit)

        for _, _, bit in netlist.cell_output_bits(cell):
            if isinstance(bit, str):
                continue
            has_external_user = any(
                user[0] not in region
                for user in netlist.bit_users.get(_bit_key(bit), [])
            )
            is_output = _bit_key(bit) in netlist.primary_outputs
            if not (has_external_user or is_output):
                continue
            identity = _sink_key(netlist, side, bit, region, new_to_base)
            if identity is None:
                invalid.append({"kind": "output", "bit": bit, "cell": cell})
            else:
                outputs.setdefault(identity, bit)

    return inputs, outputs, invalid


def _serializable_boundary(mapping):
    return [
        {"key": list(key), "bit": bit}
        for key, bit in sorted(mapping.items(), key=lambda item: repr(item[0]))
    ]


def plan_regions(base, new, match_result):
    """Return paired replacement regions and a machine-checkable cut map.

    Sequential and memory changes are intentionally rejected in this first
    prototype.  Matched sequential cells remain valid natural boundaries.
    """

    matches = match_result["matches"]
    base_to_new = {item["base"]: item["new"] for item in matches}
    new_to_base = {value: key for key, value in base_to_new.items()}

    base_region = {
        node for node in match_result["base_unmatched"]
        if not is_hard_boundary(base.graph.nodes[node].get("type"))
    }
    new_region = {
        node for node in match_result["new_unmatched"]
        if not is_hard_boundary(new.graph.nodes[node].get("type"))
    }

    unsupported = {
        "base": sorted(set(match_result["base_unmatched"]) - base_region),
        "new": sorted(set(match_result["new_unmatched"]) - new_region),
    }

    base_inputs, base_outputs, base_invalid = describe_boundary(
        base, base_region, "base", new_to_base
    )
    new_inputs, new_outputs, new_invalid = describe_boundary(
        new, new_region, "new", new_to_base
    )

    missing = {
        "inputs_only_in_base": [list(x) for x in sorted(set(base_inputs) - set(new_inputs), key=repr)],
        "inputs_only_in_new": [list(x) for x in sorted(set(new_inputs) - set(base_inputs), key=repr)],
        "outputs_only_in_base": [list(x) for x in sorted(set(base_outputs) - set(new_outputs), key=repr)],
        "outputs_only_in_new": [list(x) for x in sorted(set(new_outputs) - set(base_outputs), key=repr)],
    }
    stitchable = not any(unsupported.values()) and not base_invalid and not new_invalid
    stitchable = stitchable and not any(missing.values())

    return {
        "schema_version": 1,
        "mode": "register_bounded_combinational",
        "stitchable": stitchable,
        "base_cells": sorted(base_region),
        "new_cells": sorted(new_region),
        "base_boundary": {
            "inputs": _serializable_boundary(base_inputs),
            "outputs": _serializable_boundary(base_outputs),
        },
        "new_boundary": {
            "inputs": _serializable_boundary(new_inputs),
            "outputs": _serializable_boundary(new_outputs),
        },
        "unsupported_changed_state_cells": unsupported,
        "unresolved_boundary_pins": {"base": base_invalid, "new": new_invalid},
        "boundary_mismatch": missing,
    }
