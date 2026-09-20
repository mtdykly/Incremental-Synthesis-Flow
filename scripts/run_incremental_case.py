#!/usr/bin/env python3
"""Run extraction, local synthesis, checked stitching and mandatory proof."""
import argparse
import copy
import shutil
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis'))
from run_incremental import plan_command, stitch_command
from yosys_runner import run_yosys


def run_case(args):
    args = copy.copy(args)
    args._extra_region = None
    start = time.perf_counter()
    out = Path(args.root).resolve() / 'results' / args.case / 'incremental'
    out.mkdir(parents=True, exist_ok=True)
    from rtl_region import seed_mode, expansion_from_failure
    from netlist_graph import NetlistGraph
    mode = seed_mode(args)
    limit = getattr(args, 'max_expansions', 5 if mode != 'netlist' else 0)
    report = {'status': 'running', 'verified': False, 'level': 'generic', 'stages': {},
              'seed_mode': mode, 'attempts': [], 'expansion_rounds': 0,
              'requires_full_new_netlist': True,
              'timing_scope': 'frontend included' if getattr(args, 'frontend', False) else 'cached frontend'}
    report_path = out / 'run_report.json'
    report_path.write_text(json.dumps(report, indent=2))
    code = 1
    run_id = str(time.time_ns())
    try:
        if getattr(args, 'frontend', False):
            sys.path.insert(0, str(ROOT / 'scripts' / 'synthesis'))
            from run_frontend import generate
            for version in ('base', 'new'):
                stage = generate(Path(args.root).resolve(), args.case, version, args.yosys, args.timeout)
                report['stages']['frontend_' + version] = stage
                if stage['status'] != 'passed':
                    raise ValueError('frontend failed')
        if limit < 0:
            raise ValueError('max-expansions must be nonnegative')
        for iteration in range(limit + 1):
            attempt = {'iteration': iteration}
            report['attempts'].append(attempt)
            t = time.perf_counter()
            code = plan_command(args)
            stage = {'seconds': time.perf_counter() - t, 'returncode': code}
            report['stages']['plan'] = attempt['plan'] = stage
            plan = json.loads((out.parent / 'analysis' / 'region_plan.json').read_text())
            attempt['region_cells'] = {s: len(plan[s + '_cells']) for s in ('base', 'new')}
            if code:
                report['failure_kind'] = 'unsupported_or_unclosed_region'
                raise ValueError('region is not stitchable; inspect region_plan.json')
            stage = run_yosys(out / 'synthesize_region.ys', out / 'synthesize_region.log', args.yosys, args.timeout)
            report['stages']['synthesis'] = attempt['synthesis'] = stage
            if stage['status'] != 'passed':
                code = 3
                raise ValueError('local synthesis did not pass')
            t = time.perf_counter()
            code = stitch_command(args)
            stage = {'seconds': time.perf_counter() - t, 'returncode': code}
            report['stages']['stitch_and_verify'] = attempt['stitch_and_verify'] = stage
            outcome = json.loads((out / 'verification.json').read_text())
            attempt['verification'] = outcome
            archive = out / 'attempts' / run_id / f'{iteration:03d}'
            archive.mkdir(parents=True, exist_ok=True)
            for path in (out / 'verification.json', out / 'verify_stitched.log',
                         out / 'verify_stitched.points.json', out.parent / 'analysis' / 'region_plan.json'):
                if path.is_file():
                    shutil.copy2(path, archive / path.name)
            attempt['artifacts'] = str(archive)
            attempt['verification'] = dict(outcome, log=str(archive / 'verify_stitched.log'))
            report['expansion_rounds'] = iteration
            if code == 0:
                report.update(status='success', verified=True, retained_base_cells=len(plan['retained_pairs']),
                              replaced_base_cells=len(plan['base_cells']), new_region_cells=len(plan['new_cells']),
                              boundary_ports={k: len(v) for k, v in plan['new_boundary'].items()},
                              rtl_guidance=plan.get('rtl_guidance'))
                break
            if iteration == limit or getattr(args, 'replacement', None):
                raise ValueError('equivalence did not pass; expansion limit reached or explicit replacement supplied')
            base = NetlistGraph(out.parent / 'base' / 'design_flat.json', plan['top'])
            new = NetlistGraph(out.parent / 'new' / 'design_flat.json', plan['top'])
            expanded = expansion_from_failure(base, new, plan, outcome)
            if expanded is None:
                raise ValueError('equivalence did not pass; no actionable unproven cone or no further safe expansion')
            args._extra_region = expanded
            attempt['expansion'] = {s: len(expanded[s]) for s in expanded}
            print(f"Expanding unproven combinational cones: round {iteration + 1}/{limit}")
    except (ValueError, OSError, KeyError) as exc:
        report.update(status='failed', error=str(exc))
        code = code or 1
    finally:
        report['total_seconds'] = time.perf_counter() - start
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        if mode != 'netlist':
            from rtl_region import write_experiment_summary
            write_experiment_summary(out, report)
    print(f"Run report: {report_path} ({report['status']})")
    return code


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case')
    p.add_argument('--root', default=str(ROOT))
    p.add_argument('--yosys', default='yosys')
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--topology-rounds', type=int, default=2)
    p.add_argument('--rtl-guided', action='store_true', help='use RTL hints with hybrid safety completion')
    p.add_argument('--seed-mode', choices=['netlist', 'rtl', 'hybrid'])
    p.add_argument('--max-expansions', type=int, default=5)
    p.add_argument('--formal', action='store_true')
    p.add_argument('--frontend', action='store_true', help='regenerate both IRs from existing case checkouts')
    p.set_defaults(replacement=None)
    return run_case(p.parse_args())


if __name__ == '__main__':
    sys.exit(main())
