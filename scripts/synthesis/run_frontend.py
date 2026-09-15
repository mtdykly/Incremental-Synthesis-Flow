#!/usr/bin/env python3
"""Reproducible front end, with an optional separate full mapping stage."""
import argparse
import fnmatch
import json
import sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'analysis'))
from yosys_runner import quote, identifier, run_yosys


def generate(root, case, version, yosys='yosys', timeout=120, mapped=False, liberty=None):
    cfg = yaml.safe_load((root / 'benchmarks' / 'cases' / case / 'design.yaml').read_text())
    work = root / 'results' / case / 'work' / version
    out = root / 'results' / case / version
    out.mkdir(parents=True, exist_ok=True)
    files = sorted({p.resolve() for d in cfg['source_dirs'] for p in (work / d).rglob('*')
                    if p.suffix in {'.v', '.sv'} and not any(fnmatch.fnmatch(p.name, pat)
                    for pat in cfg.get('exclude_patterns', []))})
    if not files:
        raise ValueError(f'no RTL sources in {work}; checkout the case first')
    includes = ['-I' + quote(work / d) for d in cfg.get('include_dirs', [])]
    lines = ['read_verilog -sv ' + ' '.join(includes + [quote(p) for p in files]),
             'hierarchy -check -top ' + identifier(cfg['top']), 'proc', 'flatten',
             'opt', 'memory', 'opt_clean', 'check -assert',
             'write_json ' + quote(out / 'frontend_flat.json'),
             'write_rtlil ' + quote(out / 'frontend_flat.rtlil'),
             'write_json ' + quote(out / 'design_flat.json'),
             'write_json ' + quote(out / 'design.json'),
             'write_rtlil ' + quote(out / 'design.rtlil'),
             'write_verilog ' + quote(out / 'design.v')]
    if mapped:
        lines += ['techmap', 'opt']
        if liberty:
            lines += ['dfflibmap -liberty ' + quote(liberty), 'abc -liberty ' + quote(liberty)]
        else:
            lines += ['abc']
        lines += ['clean', 'check -assert', 'stat' + (' -liberty ' + quote(liberty) if liberty else ''),
                  'write_json ' + quote(out / 'mapped.json'), 'write_verilog ' + quote(out / 'mapped.v')]
    script = out / 'frontend.ys'
    script.write_text('\n'.join(lines) + '\n')
    result = run_yosys(script, out / 'frontend.log', yosys, timeout)
    result.update(mapping='liberty' if liberty else ('generic_gates' if mapped else 'none'),
                  sources=[str(p.relative_to(work.resolve())) for p in files])
    (out / 'frontend_report.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case')
    p.add_argument('version', choices=['base', 'new'])
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--yosys', default='yosys')
    p.add_argument('--timeout', type=int, default=120)
    p.add_argument('--mapped', action='store_true')
    p.add_argument('--liberty', type=Path)
    a = p.parse_args()
    return 0 if generate(a.root.resolve(), a.case, a.version, a.yosys, a.timeout,
                         a.mapped, a.liberty)['status'] == 'passed' else 1


if __name__ == '__main__':
    sys.exit(main())
