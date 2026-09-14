import hashlib
import json
from collections import defaultdict


def _hash(obj):

    text = json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def intrinsic_fingerprint(graph, node):

    data = graph.graph.nodes[node]

    directions = data.get(
        "port_directions",
        {}
    )

    connections = data.get(
        "connections",
        {}
    )

    ports = []

    for port_name in sorted(connections.keys()):

        bits = connections[port_name]

        ports.append(
            {
                "name": port_name,
                "direction": directions.get(
                    port_name,
                    "unknown"
                ),
                "width": len(bits),
            }
        )

    signature = {
        "type": data.get("type"),
        "parameters": data.get(
            "parameters",
            {}
        ),
        "ports": ports,
    }

    return _hash(signature)


def _input_token(
        netlist,
        bit,
        previous_fp
):

    # constants
    if isinstance(bit, str):
        return f"CONST:{bit}"

    bit_key = str(bit)

    # top-level primary input
    if bit_key in netlist.primary_inputs:

        return (
            "PI:"
            +
            netlist.primary_inputs[bit_key]
        )

    # driven by another cell
    driver = netlist.bit_driver.get(
        bit_key
    )

    if driver is None:
        return "UNDRIVEN"

    driver_cell, driver_port, driver_index = driver

    return (
        "CELL:"
        + previous_fp[driver_cell]
        + ":"
        + driver_port
        + ":"
        + str(driver_index)
    )


def compute_fingerprints(
        netlist,
        rounds=4
):

    # round 0: only intrinsic properties
    fp = {
        node: intrinsic_fingerprint(
            netlist,
            node
        )
        for node in netlist.graph.nodes
    }

    for _ in range(rounds):

        next_fp = {}

        for node in netlist.graph.nodes:

            data = netlist.graph.nodes[node]

            directions = data.get(
                "port_directions",
                {}
            )

            connections = data.get(
                "connections",
                {}
            )

            input_ports = []

            for port_name in sorted(
                connections.keys()
            ):

                direction = directions.get(
                    port_name
                )

                if direction not in (
                    "input",
                    "inout"
                ):
                    continue

                bits = connections[
                    port_name
                ]

                # 注意：这里绝对不能排序 bits
                bit_tokens = []

                for bit in bits:

                    bit_tokens.append(
                        _input_token(
                            netlist,
                            bit,
                            fp
                        )
                    )

                input_ports.append(
                    {
                        "port": port_name,
                        "bits": bit_tokens,
                    }
                )

            signature = {
                "self": fp[node],
                "inputs": input_ports,
            }

            next_fp[node] = _hash(
                signature
            )

        fp = next_fp

    return fp