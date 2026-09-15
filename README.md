# Incremental Synthesis Flow

This repository prototypes **register-bounded combinational ECO synthesis** on
flattened, pre-ABC Yosys netlists.  It deliberately separates three questions:

1. canonical matching finds structurally stable cells between Base and New;
2. the region planner turns unmatched cells into a closed replacement region;
3. extraction, local synthesis, stitching, and whole-design equivalence check
   implement and validate the replacement.

The prototype does not claim that every unmatched cell is functionally changed
or that the selected region is globally minimal.  Ambiguous matches must be
formally resolved before planning; a changed register or memory, or a boundary
that cannot be paired between versions, is rejected rather than stitched
unsafely.

## Workflow

Generate flattened generic JSON for both revisions first.  The expected files
are `results/<case>/base/design_flat.json` and
`results/<case>/new/design_flat.json`.  Then run:

```sh
python3 analysis/run_incremental.py plan eco-002
yosys -s results/eco-002/incremental/synthesize_region.ys
python3 analysis/run_incremental.py stitch eco-002
yosys -s results/eco-002/incremental/verify_stitched.ys
```

`plan` writes `analysis/region_plan.json`, including cut-input and cut-output
identities, unsupported state changes, unresolved pins, and any Base/New
boundary mismatch.  It exits with status 2 instead of extracting a region when
the cut is unsafe.

The extracted New region has stable scalar cut ports.  Its generated Yosys
script performs local optimization only.  `stitch` deletes the old Base cells,
maps replacement ports back onto the Base cut nets, gives internal replacement
nets fresh IDs, and emits both `stitched.json` and a mandatory top-level
equivalence script against the full New generic netlist.

## Current scope

- flattened Yosys generic netlists;
- combinational changes bounded by matched cells, top-level ports, registers,
  or memories;
- unchanged state structure (no retiming or state re-encoding);
- conservative failure when a boundary cannot be paired exactly.

Future work can import proven formal match resolutions, discover functional
invariant boundaries between pre- and post-optimization Base representations,
and iteratively expand a region after a failed local feasibility check.
