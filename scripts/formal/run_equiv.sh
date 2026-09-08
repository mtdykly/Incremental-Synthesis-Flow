#!/bin/bash

set -euo pipefail


CASE=$1

ROOT=$(git rev-parse --show-toplevel)


BASE_NETLIST="$ROOT/results/$CASE/base/design.v"
NEW_NETLIST="$ROOT/results/$CASE/new/design.v"


EQ_SCRIPT="$ROOT/results/$CASE/equiv.ys"


cat > "$EQ_SCRIPT" <<EOF


# =====================
# Base
# =====================

read_verilog $BASE_NETLIST

prep -top riscv_core

flatten

rename riscv_core base

design -stash gold



# =====================
# Reset
# =====================

design -reset



# =====================
# New
# =====================

read_verilog $NEW_NETLIST

prep -top riscv_core

flatten

rename riscv_core new

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

equiv_status

EOF


yosys -s "$EQ_SCRIPT"