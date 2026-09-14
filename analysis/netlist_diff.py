from collections import defaultdict

from fingerprint import compute_fingerprints


def diff_netlist(
        base,
        new,
        rounds=4
):

    base_fps = compute_fingerprints(
        base,
        rounds=rounds
    )

    new_fps = compute_fingerprints(
        new,
        rounds=rounds
    )

    base_groups = defaultdict(list)
    new_groups = defaultdict(list)

    for node, fp in base_fps.items():
        base_groups[fp].append(node)

    for node, fp in new_fps.items():
        new_groups[fp].append(node)

    all_fps = (
        set(base_groups.keys())
        |
        set(new_groups.keys())
    )

    matched_pairs = []

    base_only = []
    new_only = []

    for fp in all_fps:

        base_nodes = sorted(
            base_groups.get(
                fp,
                []
            )
        )

        new_nodes = sorted(
            new_groups.get(
                fp,
                []
            )
        )

        matched_count = min(
            len(base_nodes),
            len(new_nodes)
        )

        for i in range(
            matched_count
        ):

            matched_pairs.append(
                {
                    "base": base_nodes[i],
                    "new": new_nodes[i],
                    "fingerprint": fp,
                }
            )

        base_only.extend(
            base_nodes[
                matched_count:
            ]
        )

        new_only.extend(
            new_nodes[
                matched_count:
            ]
        )

    return {
        "base_cell_count":
            len(base_fps),

        "new_cell_count":
            len(new_fps),

        "matched_count":
            len(matched_pairs),

        "base_only":
            sorted(base_only),

        "new_only":
            sorted(new_only),

        "matched_pairs":
            matched_pairs,
    }