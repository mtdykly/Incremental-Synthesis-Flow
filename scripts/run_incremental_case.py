#!/usr/bin/env python3
"""Run extraction, local synthesis, checked stitching and mandatory proof."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis'))
from run_incremental import plan_command, stitch_command
from yosys_runner import run_yosys


def run_case(args):
    start = time.perf_counter()
    out = Path(args.root).resolve() / 'results' / args.case / 'incremental'
    out.mkdir(parents=True, exist_ok=True)
    report = {'status': 'running', 'verified': False, 'level': 'generic', 'stages': {}}
    report_path = out / 'run_report.json'
    report_path.write_text(json.dumps(report, indent=2))
    code = 1
    try:
        if getattr(args, 'frontend', False):
            sys.path.insert(0, str(ROOT / 'scripts' / 'synthesis'))
            from run_frontend import generate
            for version in ('base', 'new'):
                stage = generate(Path(args.root).resolve(), args.case, version, args.yosys, args.timeout)
                report['stages']['frontend_' + version] = stage
                if stage['status'] != 'passed':
                    raise ValueError('frontend failed')
        t = time.perf_counter()
        code = plan_command(args)
        report['stages']['plan'] = {'seconds': time.perf_counter() - t, 'returncode': code}
        if code:
            raise ValueError('region is not stitchable')
        stage = run_yosys(out / 'synthesize_region.ys', out / 'synthesize_region.log', args.yosys, args.timeout)
        report['stages']['synthesis'] = stage
        if stage['status'] != 'passed':
            code = 3
            raise ValueError('local synthesis did not pass')
        t = time.perf_counter()
        code = stitch_command(args)
        report['stages']['stitch_and_verify'] = {'seconds': time.perf_counter() - t, 'returncode': code}
        if code:
            raise ValueError('equivalence did not pass')
        plan = json.loads((out.parent / 'analysis' / 'region_plan.json').read_text())
        report.update(status='success', verified=True, retained_base_cells=len(plan['retained_pairs']),
                      replaced_base_cells=len(plan['base_cells']), new_region_cells=len(plan['new_cells']))
    except (ValueError, OSError, KeyError) as exc:
        report.update(status='failed', error=str(exc))
        code = code or 1
    finally:
        report['total_seconds'] = time.perf_counter() - start
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f"Run report: {report_path} ({report['status']})")
    return code


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case')
    p.add_argument('--root', default=str(ROOT))
    p.add_argument('--yosys', default='yosys')
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--topology-rounds', type=int, default=2)
    p.add_argument('--formal', action='store_true')
    p.add_argument('--frontend', action='store_true', help='regenerate both IRs from existing case checkouts')
    p.set_defaults(replacement=None)
    return run_case(p.parse_args())


if __name__ == '__main__':
    sys.exit(main())
