import hashlib


def normalize_bits(bits):
    """
    Normalize Yosys bit representation.
    """
    return sorted(
        [str(x) for x in bits]
    )


def cell_fingerprint(cell_name, cell_data, module):

    cell_type = cell_data["type"]


    connections = cell_data.get(
        "connections",
        {}
    )


    conn_items=[]


    for port, bits in connections.items():

        norm_bits = normalize_bits(bits)

        conn_items.append(
            (
                port,
                tuple(norm_bits)
            )
        )


    conn_items.sort()


    signature = (
        cell_type,
        tuple(conn_items)
    )


    text = repr(signature)


    return hashlib.sha256(
        text.encode()
    ).hexdigest()