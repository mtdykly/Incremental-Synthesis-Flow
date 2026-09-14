from collections import deque


def is_sequential(cell_type):

    if not cell_type:
        return False

    t = cell_type.lower()

    keywords = [
        "dff",
        "adff",
        "sdff",
        "dffe",
        "dlatch",
        "ff",
    ]

    return any(
        k in t
        for k in keywords
    )


def fanout_cone(
        graph,
        roots,
        stop_at_sequential=False
):

    visited = set()

    queue = deque()

    for root in roots:

        if root in graph:
            queue.append(root)

    while queue:

        node = queue.popleft()

        if node in visited:
            continue

        visited.add(node)

        node_type = graph.nodes[
            node
        ].get("type")

        if (
            stop_at_sequential
            and
            node not in roots
            and
            is_sequential(
                node_type
            )
        ):
            continue

        for successor in graph.successors(
            node
        ):

            if successor not in visited:
                queue.append(
                    successor
                )

    return visited