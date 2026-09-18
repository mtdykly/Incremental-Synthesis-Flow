#!/usr/bin/env python3
"""Create, locally synthesize, and stitch a combinational ECO region."""

import argparse
import json
import os
import sys
from pathlib import Path
from yosys_runner import quote, identifier, run_yosys

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
            "check -assert\nwrite_json {}\n".format(quote(input_json), quote(output_json))
        )


def _write_equivalence(path, candidate, reference, top):
    top = identifier(top)
    with open(path, "w") as stream:
        stream.write(
            "read_json {}\n"
            "prep -top {}\nrename {} candidate\nrename -hide\ndesign -stash candidate\n"
            "design -reset\n"
            "read_json {}\n"
            "prep -top {}\nrename {} reference\nrename -hide\ndesign -stash reference\n"
            "design -copy-from candidate -as candidate candidate\n"
            "design -copy-from reference -as reference reference\n"
            "equiv_make candidate reference equiv\n"
            "prep -top equiv\nasync2sync\nequiv_simple\nequiv_induct -seq 4\nequiv_status -assert\n".format(
                quote(candidate), top, top, quote(reference), top, top
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
    from structure_check import check_module
    check_module(base.module_data)
    check_module(new.module_data)
    match = canonical_match(base, new, topology_rounds=args.topology_rounds)
    if getattr(args, 'formal_results', None):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from formal.merge_matches import merge_matches
        match = merge_matches(base, new, match, load_json(args.formal_results))
    if getattr(args, 'formal', False):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from formal.run_matching import run_matching
        from formal.merge_matches import merge_matches
        proofs = run_matching(base, new, match, Path(incremental_dir) / 'matching',
                              args.yosys, args.timeout)
        match = merge_matches(base, new, match, proofs)
    dump_json(match, os.path.join(analysis_dir, 'incremental_matches.json'))
    plan = plan_regions(base, new, match)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from formal.build_match_problem import design_digest
    plan['design_digest'] = design_digest(base, new)
    plan.update({"case": args.case, "top": top})

    plan_path = os.path.join(analysis_dir, "region_plan.json")
    dump_json(plan, plan_path)
    print(f"Region plan: {plan_path}")
    print(f"Stitchable: {plan['stitchable']}")
    if not plan["stitchable"]:
        print("The boundary is not closed; inspect diagnostics in the plan.")
        return 2

    new_region = extract_region(
        new, plan["new_cells"], plan["new_boundary"]
    )
    new_region_path = os.path.join(incremental_dir, "new_region.json")
    synthesized_path = os.path.join(incremental_dir, "new_region_synth.json")
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from formal.build_match_problem import design_digest
    if plan.get('design_digest') != design_digest(NetlistGraph(base_path, plan['top']),
                                                 NetlistGraph(reference_path, plan['top'])):
        raise ValueError('stale plan: input netlists changed; run plan again')
    replacement_path = args.replacement or os.path.join(
        result_dir, "incremental", "new_region_synth.json"
    )
    output_dir = os.path.join(result_dir, "incremental")
    output_path = os.path.join(output_dir, "stitched.json")
    stitched = stitch_region(load_json(base_path), plan, load_json(replacement_path))
    dump_json(stitched, output_path)
    equiv_path = os.path.join(output_dir, "verify_stitched.ys")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from formal.verify_transition import write_transition_verification
    write_transition_verification(equiv_path, stitched, load_json(reference_path),
                                  plan['top'], plan['retained_pairs'])
    print(f"Stitched netlist: {output_path}")
    outcome = run_yosys(equiv_path, os.path.join(output_dir, 'verify_stitched.log'),
                        getattr(args, 'yosys', 'yosys'), getattr(args, 'timeout', 120))
    outcome.update(verified=outcome['status'] == 'passed',
                   proof_kind='transition_refinement',
                   undef_policy='reference_x_is_dont_care')
    dump_json(outcome, os.path.join(output_dir, 'verification.json'))
    print(f"Verification: {outcome['status']}")
    return 0 if outcome['verified'] else 3


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    plan_parser = subparsers.add_parser("plan", help="match and extract a region")
    plan_parser.add_argument("case")
    plan_parser.add_argument("--topology-rounds", type=int, default=2)
    plan_parser.add_argument('--formal', action='store_true')
    plan_parser.add_argument('--formal-results', help='import results for these exact input netlists')
    plan_parser.add_argument('--yosys', default='yosys')
    plan_parser.add_argument('--timeout', type=int, default=120)
    plan_parser.set_defaults(func=plan_command)

    stitch_parser = subparsers.add_parser("stitch", help="stitch synthesized region")
    stitch_parser.add_argument("case")
    stitch_parser.add_argument("--replacement")
    stitch_parser.add_argument('--yosys', default='yosys')
    stitch_parser.add_argument('--timeout', type=int, default=120)
    stitch_parser.set_defaults(func=stitch_command)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
