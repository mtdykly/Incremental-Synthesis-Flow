#!/usr/bin/env python3

import sys
from collections import defaultdict

sys.path.insert(0, "analysis")

from netlist_graph import NetlistGraph
from state_matcher import state_key, REGISTER_TYPES
from canonical import intrinsic_signature


case = "eco-002"
top = "riscv_core"

base = NetlistGraph(
    f"results/{case}/base/design_flat.json",
    top=top
)

new = NetlistGraph(
    f"results/{case}/new/design_flat.json",
    top=top
)


new_candidates = defaultdict(list)

for n in sorted(new.cells):

    if new.cells[n]["type"] not in REGISTER_TYPES:
        continue

    key = state_key(new, n)
    sig = intrinsic_signature(new.cells[n])

    new_candidates[(key, sig)].append(n)


print("\n===== BASE STATE CELLS =====\n")

for b in sorted(base.cells):

    if base.cells[b]["type"] not in REGISTER_TYPES:
        continue

    key = state_key(base, b)
    sig = intrinsic_signature(base.cells[b])

    choices = new_candidates.get(
        (key, sig),
        []
    )

    print("BASE:", b)
    print("  type       :", base.cells[b]["type"])
    print("  state_key  :", key)
    print("  intrinsic  :", sig)
    print("  choices    :", choices)
    print()