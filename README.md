# Incremental Synthesis Flow

Register-bounded combinational ECO synthesis on flattened Yosys **generic**
netlists. The flow retains Base cells, synthesizes an unresolved New region,
reconnects external sinks, and proves the result against **New**. Base and New
may intentionally differ in function.

## Install and reproduce

Python 3.10+, Yosys with SAT and ABC, and the Python dependencies are required:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
# For small fixtures without native Yosys: pip install yowasp-yosys
# Then pass --yosys yowasp-yosys to the commands below.
```

Use native Yosys for `eco-002`: YoWASP 0.69 hit Wasmtime's call-stack limit in
its final proof. Native Yosys 0.33 completed the proof. See the
[validation record](docs/validation.md) for the tested local environment.

The historical source gitlink had no repository URL. Supply the actual RTL
repository URL; the fetch script verifies all case commits before use:

```sh
bash scripts/benchmark/fetch_source.sh <rtl-repository-url>
bash scripts/benchmark/checkout_case.sh eco-002
python scripts/run_incremental_case.py eco-002 --frontend
```

`--frontend` regenerates both inputs using the same deterministic source list
and Yosys passes. Without it, the command uses existing
`results/<case>/{base,new}/design_flat.json` files. The committed JSONs can be
used to reproduce the generic flow without the missing source checkout:

```sh
python scripts/run_incremental_case.py eco-002
```

Only successful extraction, local synthesis, structural checks and final SAT
proof produce `status: success` and `verified: true` in
`results/<case>/incremental/run_report.json`. Tool errors, timeouts and failed
proofs return nonzero. Every invocation replaces the prior run status.

## Correctness model

- Source locations, scopes, names and topology fingerprints propose cell
  identities. They do **not** prove reuse. Input pin checks compare constants,
  corresponding primary inputs, and driver output port/bit identities.
- Only explicitly supported symmetric operations may swap complete operands;
  subtraction, shifts and mux inputs retain their order.
- Register Q aliases provide state candidates, including slices of a named
  vector. Parameters, widths, clock/reset/enable input correspondence and
  initialization must agree. D may change. Unresolved state and memory block
  the plan.
- Unresolved combinational cells form a conservative closed region. New cut
  inputs may be additional retained Base sources; old and new input sets need
  not be identical. This is not a search for a globally minimal region.
- Every retained cell input and top output gets an explicit New connection.
  Cell-free rewires, constants, input/output aliases, output merging and output
  splitting are represented even when no cells are inserted.
- Replacement ports must retain their names, directions and widths. Fresh
  IDs account for netnames. Unambiguous aliases are updated; obsolete or split
  internal aliases are dropped. Duplicate drivers, floating sinks and
  combinational cycles fail structural checks.
- The final proof shares arbitrary current-state Q bits between corresponding
  registers and compares all top outputs and every D/clock/reset/enable input.
  Matching register semantics and initialization establish the induction base;
  equality of transition functions establishes the step. SAT checks each
  output obligation and `equiv_status -assert` requires every proof to pass;
  an unproved result cannot count as success. Explicit retained-cell output
  correspondences add internal proof obligations to keep arithmetic cones
  local; those obligations must also pass. There is no bounded simulation.

Supported state primitives are `$dff`, `$dffe`, `$adff`, `$adffe`, `$sdff`,
`$sdffe` and `$sdffce`. Retiming, state re-encoding, memories, inout ports and
top-level interface changes are rejected. Proofs use Yosys logic semantics;
this is not a transistor timing or metastability model.

## Matching proofs and detailed commands

```sh
python analysis/run_incremental.py plan eco-002 --formal
# Or import proofs bound to the exact current input-netlist digest:
python analysis/run_incremental.py plan eco-002 --formal-results <formal_results.json>
yosys -s results/eco-002/incremental/synthesize_region.ys
python analysis/run_incremental.py stitch eco-002
```

`stitch` performs verification itself. A stale plan is rejected. Formal jobs
record `proven`, `disproven`, `unknown`, or `tool_error`, common symbolic inputs,
commands and logs. Only one-to-one **local cell** proofs with compatible pin
layouts and source correspondence can grant reuse. Full-cone equivalence
alone cannot justify preserving a cell under changed upstream inputs.
Unresolved candidates are rebuilt or block planning at state boundaries.

## Frontend, mapping and Base correspondence

```sh
python scripts/synthesis/run_frontend.py eco-002 base
python scripts/synthesis/run_frontend.py eco-002 base --mapped
# Technology-specific full baseline:
python scripts/synthesis/run_frontend.py eco-002 base --mapped --liberty <cells.lib>
python formal/build_base_correspondence.py <frontend_flat.json> <mapped.json> \
  --top riscv_core --output results/base-correspondence
```

The frontend writes `frontend_flat.json`, `frontend_flat.rtlil` and compatible
`design_flat.json`/`design.*` files. The optional second stage writes `mapped.json`
and `mapped.v`; without Liberty these are generic gates, not technology cells.

Base correspondence discovery proves common named **combinational** cones
against shared primary inputs and records unknown state boundaries. It
supports Yosys internal cells; external Liberty cells require functional models.
Discovery is diagnostic and does not enable mapped gate stitching. The
validated incremental implementation remains generic.

## Tests and measurements

```sh
python -m pytest -q
python scripts/benchmark/run_benchmark.py eco-001 eco-002 --frontend
```

Tests exercise the actual matcher, wiring/alias counterexamples, register
renaming and reset changes, real local synthesis, positive and negative SAT
results, proof merging, the unified runner, frontend generation and
combinational Base correspondence. Yosys tests explicitly skip if neither
`yosys` nor `yowasp-yosys` is on PATH.

Reports include measured stage and total times, actually retained Base cells,
region sizes and verification status. Cached-frontend timings are labeled;
they are not end-to-end RTL timings. Liberty area and speedup remain null when
no comparable measured baseline exists. Generic cell count is not area.
See [case interpretation](benchmarks/cases/README.md) before using historical
logs or interpreting `eco-001` as a functional ECO experiment.
