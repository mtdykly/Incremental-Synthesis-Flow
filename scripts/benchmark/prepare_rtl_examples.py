#!/usr/bin/env python3
"""Materialize the three self-contained RTL-direct examples without a git checkout."""
import argparse
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
CASES = ('rtl-assign', 'rtl-process', 'rtl-macro')


def prepare(root, cases):
    for case in cases:
        for version in ('base', 'new'):
            source = root / 'benchmarks/cases' / case / 'snapshots' / version
            destination = root / 'results' / case / 'work' / version
            files = [p for p in source.rglob('*') if p.is_file()]
            if not files:
                raise ValueError(f'missing example snapshot: {source}')
            expected = {p.relative_to(source) for p in files}
            if destination.exists():
                actual = {p.relative_to(destination) for p in destination.rglob('*') if p.is_file()}
                if actual != expected or any((destination / p.relative_to(source)).read_bytes() != p.read_bytes() for p in files):
                    raise ValueError(f'existing example worktree has changes; preserving {destination}')
            else:
                shutil.copytree(source, destination)
        print(f'Prepared {case}; run scripts/run_incremental_case.py {case} --rtl-direct --frontend')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cases', nargs='*', metavar='CASE')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    if set(args.cases) - set(CASES):
        parser.error('supported examples: ' + ', '.join(CASES))
    prepare(args.root.resolve(), args.cases or CASES)
