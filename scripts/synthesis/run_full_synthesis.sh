#!/bin/bash

set -euo pipefail


if [ $# -ne 2 ]; then
    echo "Usage:"
    echo "$0 <case> <base|new>"
    exit 1
fi


CASE=$1
VERSION=$2


ROOT=$(git rev-parse --show-toplevel)


CASE_DIR=$ROOT/benchmarks/cases/$CASE


WORK_DIR=$ROOT/results/$CASE/work/$VERSION

OUT_DIR=$ROOT/results/$CASE/$VERSION


CONFIG=$CASE_DIR/design.yaml


if [ ! -f "$CONFIG" ]; then
    echo "Missing config:"
    echo "$CONFIG"
    exit 1
fi


mkdir -p "$OUT_DIR"


YOSYS_SCRIPT=$OUT_DIR/run.ys



python3 - "$CONFIG" "$WORK_DIR" "$YOSYS_SCRIPT" <<'PY'

import yaml
import sys
import os
import fnmatch


config_file=sys.argv[1]
work_dir=sys.argv[2]
ys_file=sys.argv[3]


with open(config_file) as f:
    cfg=yaml.safe_load(f)



top=cfg["top"]

source_dirs=cfg["source_dirs"]

include_dirs=cfg.get("include_dirs", [])

exclude=cfg.get("exclude_patterns", [])



rtl_files=[]


for d in source_dirs:

    path=os.path.join(work_dir,d)

    for root,dirs,files in os.walk(path):

        for file in files:

            if not file.endswith(".sv"):
                continue

            skip=False

            for pattern in exclude:

                if fnmatch.fnmatch(file,pattern):

                    skip=True

            if not skip:

                rtl_files.append(
                    os.path.join(root,file)
                )


rtl_files.sort()



with open(ys_file,"w") as f:

    f.write("# Auto generated\n\n")

    f.write("read_verilog -sv \\\n")


    for inc in include_dirs:

        f.write(
            "    -I{} \\\n".format(
                os.path.join(work_dir,inc)
            )
        )


    for rtl in rtl_files:

        f.write(
            "    {} \\\n".format(rtl)
        )


    f.write("\n")

    f.write(
        "hierarchy -top {}\n\n".format(top)
    )


    f.write("""
proc

opt

memory

opt_clean

stat

write_rtlil {}/design.rtlil

write_json {}/design.json

write_verilog {}/design.v
""".format(
        os.path.dirname(ys_file),
        os.path.dirname(ys_file),
        os.path.dirname(ys_file)
    ))

PY



echo "Running:"
echo "$YOSYS_SCRIPT"


yosys -s "$YOSYS_SCRIPT"