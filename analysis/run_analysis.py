import json
import os
import sys

from netlist_graph import NetlistGraph
from canonical_matcher import canonical_match
from cone import fanout_cone


if len(sys.argv) != 2:

    print(
        "Usage: python3 analysis/run_analysis.py <case>"
    )

    sys.exit(1)


case = sys.argv[1]


base_json = (
    f"results/{case}/base/"
    f"design_flat.json"
)

new_json = (
    f"results/{case}/new/"
    f"design_flat.json"
)


out_dir = (
    f"results/{case}/analysis"
)

os.makedirs(
    out_dir,
    exist_ok=True
)


print(
    "Loading base netlist..."
)

base = NetlistGraph(
    base_json,
    top="riscv_core"
)


print(
    "Loading new netlist..."
)

new = NetlistGraph(
    new_json,
    top="riscv_core"
)


print()
print(
    "Running canonical structural matcher..."
)


result = canonical_match(
    base,
    new,

    topology_rounds=2,

    top_candidates=3
)


matches = result[
    "matches"
]

base_unmatched = result[
    "base_unmatched"
]

new_unmatched = result[
    "new_unmatched"
]


#
# Formal 前的 cone
#
# 注意这个还不是最终 affected cone。
#
base_preformal_cone = fanout_cone(
    base.graph,
    base_unmatched,
    stop_at_sequential=True
)

new_preformal_cone = fanout_cone(
    new.graph,
    new_unmatched,
    stop_at_sequential=True
)


method_counts = {}

for item in matches:

    method = item[
        "method"
    ]

    method_counts[
        method
    ] = (
        method_counts.get(
            method,
            0
        )
        +
        1
    )


summary = {

    "base_logic_cells":
        base.cell_count(),

    "new_logic_cells":
        new.cell_count(),

    "canonical_matched":
        len(matches),

    "base_unmatched":
        len(base_unmatched),

    "new_unmatched":
        len(new_unmatched),

    "formal_candidate_groups":
        len(
            result[
                "formal_candidates"
            ]
        ),

    "base_preformal_comb_cone":
        len(
            base_preformal_cone
        ),

    "new_preformal_comb_cone":
        len(
            new_preformal_cone
        ),

    "match_methods":
        method_counts,
}


canonical_output = {

    "summary":
        summary,

    "matches":
        matches,

    "base_unmatched":
        base_unmatched,

    "new_unmatched":
        new_unmatched,

    "base_records":
        result[
            "base_records"
        ],

    "new_records":
        result[
            "new_records"
        ],
}


with open(
    f"{out_dir}/canonical_match.json",
    "w"
) as f:

    json.dump(
        canonical_output,
        f,
        indent=2
    )


with open(
    f"{out_dir}/formal_candidates.json",
    "w"
) as f:

    json.dump(
        {
            "candidates":
                result[
                    "formal_candidates"
                ]
        },
        f,
        indent=2
    )


with open(
    f"{out_dir}/preformal_cone.json",
    "w"
) as f:

    json.dump(
        {
            "base":
                sorted(
                    base_preformal_cone
                ),

            "new":
                sorted(
                    new_preformal_cone
                ),
        },
        f,
        indent=2
    )


print()
print(
    "===== Canonical Matching ====="
)

for key, value in summary.items():

    print(
        f"{key}: {value}"
    )


print()

print(
    "Written:"
)

print(
    f"  {out_dir}/canonical_match.json"
)

print(
    f"  {out_dir}/formal_candidates.json"
)

print(
    f"  {out_dir}/preformal_cone.json"
)