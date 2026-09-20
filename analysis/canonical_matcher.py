from collections import defaultdict

from canonical import canonical_record
from fingerprint import compute_fingerprints


def _group_unique(
        base_nodes,
        new_nodes,
        base_records,
        new_records,
        key_func
):

    base_groups = defaultdict(list)
    new_groups = defaultdict(list)

    for node in base_nodes:

        key = key_func(
            base_records[node]
        )

        if key is not None:
            base_groups[key].append(node)

    for node in new_nodes:

        key = key_func(
            new_records[node]
        )

        if key is not None:
            new_groups[key].append(node)

    pairs = []

    for key in (
        set(base_groups.keys())
        &
        set(new_groups.keys())
    ):

        b = base_groups[key]
        n = new_groups[key]

        #
        # 只接受唯一的一对一关系。
        #
        # 如果一边出现多个，
        # 就保留给后面的 formal。
        #
        if len(b) == 1 and len(n) == 1:

            pairs.append(
                (
                    b[0],
                    n[0]
                )
            )

    return pairs


def _source_anchor_key(record):

    if not record["sources"]:
        return None

    if record["scope"] == "<global>":
        return None

    return (
        record["scope"],
        record["type"],
        tuple(record["sources"]),
        record["intrinsic"],
    )


def _scope_struct_key(record):

    if record["scope"] == "<global>":
        return None

    return (
        record["scope"],
        record["type"],
        record["intrinsic"],
    )


def _add_matches(
        pairs,
        method,
        matches,
        base_unmatched,
        new_unmatched
):

    for base_node, new_node in pairs:

        if base_node not in base_unmatched:
            continue

        if new_node not in new_unmatched:
            continue

        matches.append(
            {
                "base":
                    base_node,

                "new":
                    new_node,

                "method":
                    method,
            }
        )

        base_unmatched.remove(
            base_node
        )

        new_unmatched.remove(
            new_node
        )


def _neighbor_agreement_score(
        base,
        new,
        base_node,
        new_node,
        base_to_new
):

    score = 0

    new_preds = set(
        new.graph.predecessors(
            new_node
        )
    )

    new_succs = set(
        new.graph.successors(
            new_node
        )
    )

    #
    # 已经确认匹配的 fanin
    #
    mapped_preds = []

    for pred in base.graph.predecessors(
        base_node
    ):

        mapped = base_to_new.get(
            pred
        )

        if mapped is not None:
            mapped_preds.append(
                mapped
            )

    if mapped_preds:

        hit = sum(
            1
            for x in mapped_preds
            if x in new_preds
        )

        score += (
            20.0
            *
            hit
            /
            len(mapped_preds)
        )

    #
    # 已确认匹配的 fanout
    #
    mapped_succs = []

    for succ in base.graph.successors(
        base_node
    ):

        mapped = base_to_new.get(
            succ
        )

        if mapped is not None:
            mapped_succs.append(
                mapped
            )

    if mapped_succs:

        hit = sum(
            1
            for x in mapped_succs
            if x in new_succs
        )

        score += (
            10.0
            *
            hit
            /
            len(mapped_succs)
        )

    return score


def _candidate_score(
        base,
        new,
        base_node,
        new_node,
        base_records,
        new_records,
        base_fp,
        new_fp,
        base_to_new
):

    br = base_records[
        base_node
    ]

    nr = new_records[
        new_node
    ]

    score = 0.0
    reasons = []

    #
    # scope
    #
    if br["scope"] == nr["scope"]:

        score += 30

        reasons.append(
            "same_scope"
        )

    #
    # cell type
    #
    if br["type"] == nr["type"]:

        score += 25

        reasons.append(
            "same_type"
        )

    #
    # intrinsic cell shape
    #
    if br["intrinsic"] == nr["intrinsic"]:

        score += 25

        reasons.append(
            "same_intrinsic"
        )

    #
    # RTL src
    #
    bsrc = set(
        br["sources"]
    )

    nsrc = set(
        nr["sources"]
    )

    exact_src = (
        bsrc
        &
        nsrc
    )

    if exact_src:

        score += 35

        reasons.append(
            "same_source_location"
        )

    else:

        bfiles = {
            x[0]
            for x in bsrc
        }

        nfiles = {
            x[0]
            for x in nsrc
        }

        if bfiles & nfiles:

            score += 15

            reasons.append(
                "same_source_file"
            )

    #
    # topology fingerprint
    #
    if (
        base_fp.get(base_node)
        ==
        new_fp.get(new_node)
    ):

        score += 30

        reasons.append(
            "same_topology_fingerprint"
        )

    #
    # 已确定邻居对应关系
    #
    neighbor_score = (
        _neighbor_agreement_score(
            base,
            new,
            base_node,
            new_node,
            base_to_new
        )
    )

    if neighbor_score > 0:

        score += neighbor_score

        reasons.append(
            "matched_neighbors"
        )

    return score, reasons


def _candidate_match(
        base,
        new,
        topology_rounds=2,
        top_candidates=3
):

    base_nodes = set(
        base.graph.nodes
    )

    new_nodes = set(
        new.graph.nodes
    )

    base_records = {
        node:
            canonical_record(
                node,
                base.graph.nodes[node]
            )

        for node
        in base_nodes
    }

    new_records = {
        node:
            canonical_record(
                node,
                new.graph.nodes[node]
            )

        for node
        in new_nodes
    }

    base_unmatched = set(
        base_nodes
    )

    new_unmatched = set(
        new_nodes
    )

    matches = []

    #
    # =====================================================
    # Stage 1
    #
    # scope + RTL source + type + intrinsic
    #
    # 最强 anchor
    # =====================================================
    #

    pairs = _group_unique(

        base_unmatched,
        new_unmatched,

        base_records,
        new_records,

        _source_anchor_key
    )

    _add_matches(
        pairs,
        "source_anchor",
        matches,
        base_unmatched,
        new_unmatched
    )

    #
    # =====================================================
    # Stage 2
    #
    # same scope + same type + same intrinsic
    #
    # 仅 unique bucket 才接受
    # =====================================================
    #

    pairs = _group_unique(

        base_unmatched,
        new_unmatched,

        base_records,
        new_records,

        _scope_struct_key
    )

    _add_matches(
        pairs,
        "scope_struct_unique",
        matches,
        base_unmatched,
        new_unmatched
    )

    #
    # =====================================================
    # Stage 3
    #
    # topology fingerprint
    #
    # 仍然限制在 same scope/type
    # =====================================================
    #

    base_fp = compute_fingerprints(
        base,
        rounds=topology_rounds
    )

    new_fp = compute_fingerprints(
        new,
        rounds=topology_rounds
    )

    def topology_key_base(record, node):

        return (
            record["scope"],
            record["type"],
            record["intrinsic"],
            base_fp[node],
        )

    def topology_key_new(record, node):

        return (
            record["scope"],
            record["type"],
            record["intrinsic"],
            new_fp[node],
        )

    base_groups = defaultdict(list)
    new_groups = defaultdict(list)

    for node in base_unmatched:

        r = base_records[node]

        if r["scope"] == "<global>":
            continue

        base_groups[
            topology_key_base(
                r,
                node
            )
        ].append(node)

    for node in new_unmatched:

        r = new_records[node]

        if r["scope"] == "<global>":
            continue

        new_groups[
            topology_key_new(
                r,
                node
            )
        ].append(node)

    topo_pairs = []

    for key in (
        set(base_groups)
        &
        set(new_groups)
    ):

        if (
            len(base_groups[key]) == 1
            and
            len(new_groups[key]) == 1
        ):

            topo_pairs.append(
                (
                    base_groups[key][0],
                    new_groups[key][0]
                )
            )

    _add_matches(
        topo_pairs,
        "topology_unique",
        matches,
        base_unmatched,
        new_unmatched
    )

    #
    # =====================================================
    # Stage 4
    #
    # 剩余 ambiguous 节点：
    #
    # 不直接配对，
    # 只生成 formal candidates。
    # =====================================================
    #

    base_to_new = {
        item["base"]:
            item["new"]

        for item
        in matches
    }

    formal_candidates = []

    for base_node in sorted(
        base_unmatched
    ):

        br = base_records[
            base_node
        ]

        candidates = []

        #
        # 第一优先：
        # 同 scope + 同 type
        #
        possible = [

            n
            for n in new_unmatched

            if (
                new_records[n]["scope"]
                ==
                br["scope"]
            )

            and

            (
                new_records[n]["type"]
                ==
                br["type"]
            )
        ]

        #
        # fallback：
        # 如果没有同 scope 节点，
        # 允许同 type，但降低可信度。
        #
        if not possible:

            possible = [

                n
                for n in new_unmatched

                if (
                    new_records[n]["type"]
                    ==
                    br["type"]
                )
            ]

        for new_node in possible:

            score, reasons = (
                _candidate_score(
                    base,
                    new,

                    base_node,
                    new_node,

                    base_records,
                    new_records,

                    base_fp,
                    new_fp,

                    base_to_new
                )
            )

            candidates.append(
                {
                    "new":
                        new_node,

                    "score":
                        round(
                            score,
                            3
                        ),

                    "reasons":
                        reasons,
                }
            )

        candidates.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        if candidates:

            formal_candidates.append(
                {
                    "base":
                        base_node,

                    "base_scope":
                        br["scope"],

                    "base_type":
                        br["type"],

                    "candidates":
                        candidates[
                            :top_candidates
                        ],
                }
            )

    return {

        "matches":
            matches,

        "base_unmatched":
            sorted(
                base_unmatched
            ),

        "new_unmatched":
            sorted(
                new_unmatched
            ),

        "formal_candidates":
            formal_candidates,

        "base_records":
            base_records,

        "new_records":
            new_records,
    }


def stable_named_cell(graph, name):
    """Generated names may collide after Yosys renumbers unrelated cells."""
    return not graph.cells[name].get('hide_name', 0) and not name.startswith('$auto$')


def canonical_match(base, new, topology_rounds=2, top_candidates=3):
    """Keep candidate identity separate from permission to retain cell wiring."""
    from connection_diff import same_inputs, connection_diff
    from state_matcher import state_candidates, state_compatible
    from region import is_hard_boundary

    result = _candidate_match(base, new, topology_rounds, top_candidates)
    pairs = state_candidates(base, new)
    structural_pairs = [(x['base'], x['new']) for x in result['matches']]
    # A persistent name is useful even when the operation changed. It only
    # establishes a reconnectable output identity, never reuse permission.
    pairs += [(b, b) for b in sorted(set(base.cells) & set(new.cells))
              if stable_named_cell(base, b) and stable_named_cell(new, b)]
    mapping = {}
    used = set()
    for b, n in pairs:
        if b not in mapping and n not in used:
            bo = [(p, i) for p, i, _ in base.cell_output_bits(b)]
            no = [(p, i) for p, i, _ in new.cell_output_bits(n)]
            if bo == no:
                mapping[b] = n
                used.add(n)
    # Trace candidate identities back from corresponding sink pins. This also
    # covers anonymous logic feeding renamed register enable/reset pins. Only
    # unique proposals are kept; all still undergo the input check below.
    structural_fallback_used = False
    while True:
        proposals = set()
        bit_pairs = [(base.output_bits[label], bit) for label, bit in new.output_bits.items()
                     if label in base.output_bits]
        for b, n in sorted(mapping.items()):
            for p, i, bit in new.cell_input_bits(n):
                old = base.cells[b]['connections'].get(p, [])
                if i < len(old):
                    bit_pairs.append((old[i], bit))
        for bb, nb in bit_pairs:
            if not isinstance(bb, int) or not isinstance(nb, int):
                continue
            bd, nd = base.bit_driver.get(str(bb)), new.bit_driver.get(str(nb))
            if bd and nd and bd[1:] == nd[1:] and bd[0] not in mapping and nd[0] not in used:
                bo = [(p, i) for p, i, _ in base.cell_output_bits(bd[0])]
                no = [(p, i) for p, i, _ in new.cell_output_bits(nd[0])]
                if bo == no:
                    proposals.add((bd[0], nd[0]))
        unique = [(b, n) for b, n in sorted(proposals)
                  if sum(x == b for x, _ in proposals) == 1
                  and sum(y == n for _, y in proposals) == 1]
        if not unique:
            if structural_fallback_used:
                break
            # State/top pin identities outrank ambiguous source/topology hints.
            # flatten -scopename can put generated decode cells in the same
            # bucket; consuming them first can steal another register's driver.
            structural_fallback_used = True
            for b, n in structural_pairs:
                if b not in mapping and n not in used:
                    bo = [(p, i) for p, i, _ in base.cell_output_bits(b)]
                    no = [(p, i) for p, i, _ in new.cell_output_bits(n)]
                    if bo == no:
                        mapping[b] = n
                        used.add(n)
            continue
        for b, n in unique:
            mapping[b] = n
            used.add(n)
    accepted = []
    for b, n in sorted(mapping.items()):
        hard = is_hard_boundary(base.cells[b]['type'])
        valid = (state_compatible(base, new, b, n, mapping) if hard
                 else same_inputs(base, new, b, n, mapping))
        if valid:
            accepted.append({'base': b, 'new': n,
                             'method': 'state_identity' if hard else 'pin_checked',
                             'reuse': True})
    result['correspondences'] = [{'base': b, 'new': n} for b, n in sorted(mapping.items())]
    result['matches'] = accepted
    result['base_unmatched'] = sorted(set(base.cells) - {x['base'] for x in accepted})
    result['new_unmatched'] = sorted(set(new.cells) - {x['new'] for x in accepted})
    result['connection_changes'] = connection_diff(base, new, mapping)
    # Include rejected anchors in formal work; the old implementation hid them.
    result['formal_candidates'] = [
        {'base': b, 'candidates': [{'new': n, 'score': 0, 'reasons': ['unproven']}
          for n in sorted(result['new_unmatched'], key=lambda n: (mapping.get(b) != n, n))
          if mapping.get(b) == n or base.cells[b]['type'] == new.cells[n]['type']][:top_candidates]}
        for b in result['base_unmatched'] if not is_hard_boundary(base.cells[b]['type'])]
    return result
