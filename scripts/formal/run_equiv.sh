#!/bin/bash

set -euo pipefail


CASE=${1:?Usage: run_equiv.sh <case>}

ROOT=$(git rev-parse --show-toplevel)
TOP=$(python3 "$ROOT/scripts/synthesis/parse_design_config.py" "$ROOT/benchmarks/cases/$CASE/design.yaml" | head -n 1)


BASE_NETLIST="$ROOT/results/$CASE/base/design.v"
NEW_NETLIST="$ROOT/results/$CASE/new/design.v"


EQ_SCRIPT="$ROOT/results/$CASE/equiv.ys"


cat > "$EQ_SCRIPT" <<EOF


# =====================
# Base
# =====================

read_verilog "$BASE_NETLIST"

prep -top $TOP

flatten

rename $TOP base

design -stash gold



# =====================
# Reset
# =====================

design -reset



# =====================
# New
# =====================

read_verilog "$NEW_NETLIST"

prep -top $TOP

flatten

rename $TOP new

design -stash gate



# =====================
# Restore
# =====================

design -copy-from gold -as base base

design -copy-from gate -as new new


# =====================
# Equiv
# =====================

equiv_make base new equiv

prep -top equiv

async2sync

equiv_simple

equiv_status -assert

EOF


yosys -s "$EQ_SCRIPT"
