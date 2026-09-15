#!/usr/bin/env python3
"""Measure complete generic runs, without treating cell count as area."""
import argparse
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from run_incremental_case import run_case


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('cases', nargs='+')
    p.add_argument('--root', default=str(ROOT))
    p.add_argument('--yosys', default='yosys')
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--formal', action='store_true')
    p.add_argument('--frontend', action='store_true')
    p.add_argument('--output', type=Path, default=ROOT / 'results' / 'benchmark.json')
    p.set_defaults(topology_rounds=2, replacement=None)
    args = p.parse_args()
    results = []
    failed = False
    for case in args.cases:
        args.case = case
        failed |= bool(run_case(args))
        report = json.loads((Path(args.root) / 'results' / case / 'incremental' / 'run_report.json').read_text())
        report.update(case=case, liberty_area=None, speedup=None,
                      timing_scope='frontend included' if args.frontend else 'cached frontend; analysis through proof')
        results.append(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2))
    return int(failed)


if __name__ == '__main__':
    sys.exit(main())
