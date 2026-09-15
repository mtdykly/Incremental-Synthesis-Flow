#!/usr/bin/env python3
"""Create, locally synthesize, and stitch a combinational ECO region."""

import argparse
import json
import os
import sys

from canonical_matcher import canonical_match
from netlist_graph import NetlistGraph
from region import plan_regions
from region_netlist import dump_json, extract_region, load_json, stitch_region


def _write_local_synthesis(path, input_json, output_json):
    with open(path, "w") as stream:
        stream.write(
            "read_json {}\n"
            "hierarchy -top incremental_region\n"
            "proc\nopt\ntechmap\nopt_clean\n"
            "write_json {}\n".format(input_json, output_json)
        )


def _write_equivalence(path, candidate, reference, top):
    with open(path, "w") as stream:
        stream.write(
            "read_json {}\n"
            "prep -top {}\nrename {} candidate\ndesign -stash candidate\n"
            "design -reset\n"
            "read_json {}\n"
            "prep -top {}\nrename {} reference\ndesign -stash reference\n"
            "design -copy-from candidate -as candidate candidate\n"
            "design -copy-from reference -as reference reference\n"
            "equiv_make candidate reference equiv\n"
            "prep -top equiv\nasync2sync\nequiv_simple\nequiv_status -assert\n".format(
                candidate, top, top, reference, top, top
            )
        )


def _read_top(config_path):
    # Avoid making the analysis path depend on PyYAML just to read one scalar.
    with open(config_path) as stream:
        for raw_line in stream:
            line = raw_line.split("#", 1)[0]
            if line.startswith("top:"):
                value = line.split(":", 1)[1].strip().strip("'\"")
                if value:
                    return value
    raise ValueError(f"missing top in {config_path}")


def plan_command(args):
    root = os.path.abspath(args.root)
    case_dir = os.path.join(root, "benchmarks", "cases", args.case)
    top = _read_top(os.path.join(case_dir, "design.yaml"))
    result_dir = os.path.join(root, "results", args.case)
    analysis_dir = os.path.join(result_dir, "analysis")
    incremental_dir = os.path.join(result_dir, "incremental")
    os.makedirs(analysis_dir, exist_ok=True)
    os.makedirs(incremental_dir, exist_ok=True)

    base_path = os.path.join(result_dir, "base", "design_flat.json")
    new_path = os.path.join(result_dir, "new", "design_flat.json")
    base = NetlistGraph(base_path, top=top)
    new = NetlistGraph(new_path, top=top)
    match = canonical_match(base, new, topology_rounds=args.topology_rounds)
    plan = plan_regions(base, new, match)
    plan.update({"case": args.case, "top": top})

    plan_path = os.path.join(analysis_dir, "region_plan.json")
    dump_json(plan, plan_path)
    print(f"Region plan: {plan_path}")
    print(f"Stitchable: {plan['stitchable']}")
    if not plan["stitchable"]:
        print("The boundary is not closed; inspect diagnostics in the plan.")
        return 2

    base_region = extract_region(
        base, plan["base_cells"], plan["base_boundary"]
    )
    new_region = extract_region(
        new, plan["new_cells"], plan["new_boundary"]
    )
    base_region_path = os.path.join(incremental_dir, "base_region.json")
    new_region_path = os.path.join(incremental_dir, "new_region.json")
    synthesized_path = os.path.join(incremental_dir, "new_region_synth.json")
    dump_json(base_region, base_region_path)
    dump_json(new_region, new_region_path)
    _write_local_synthesis(
        os.path.join(incremental_dir, "synthesize_region.ys"),
        new_region_path,
        synthesized_path,
    )
    print(f"Extracted new region: {new_region_path}")
    print("Next: yosys -s " + os.path.join(incremental_dir, "synthesize_region.ys"))
    return 0


def stitch_command(args):
    root = os.path.abspath(args.root)
    result_dir = os.path.join(root, "results", args.case)
    plan = load_json(os.path.join(result_dir, "analysis", "region_plan.json"))
    base_path = os.path.join(result_dir, "base", "design_flat.json")
    reference_path = os.path.join(result_dir, "new", "design_flat.json")
    replacement_path = args.replacement or os.path.join(
        result_dir, "incremental", "new_region_synth.json"
    )
    output_dir = os.path.join(result_dir, "incremental")
    output_path = os.path.join(output_dir, "stitched.json")
    stitched = stitch_region(load_json(base_path), plan, load_json(replacement_path))
    dump_json(stitched, output_path)
    equiv_path = os.path.join(output_dir, "verify_stitched.ys")
    _write_equivalence(equiv_path, output_path, reference_path, plan["top"])
    print(f"Stitched netlist: {output_path}")
    print(f"Mandatory verification: yosys -s {equiv_path}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan", help="match and extract a region")
    plan_parser.add_argument("case")
    plan_parser.add_argument("--topology-rounds", type=int, default=2)
    plan_parser.set_defaults(func=plan_command)
    stitch_parser = subparsers.add_parser("stitch", help="stitch synthesized region")
    stitch_parser.add_argument("case")
    stitch_parser.add_argument("--replacement")
    stitch_parser.set_defaults(func=stitch_command)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
