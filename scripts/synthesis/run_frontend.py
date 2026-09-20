#!/usr/bin/env python3
"""Reproducible front end, with an optional separate full mapping stage."""
import argparse
import json
import sys
import subprocess
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'analysis'))
from yosys_runner import quote, identifier, run_yosys
from rtl_sources import source_files, snapshot_inputs, digest


def generate(root, case, version, yosys='yosys', timeout=120, mapped=False, liberty=None):
    cfg = yaml.safe_load((root / 'benchmarks' / 'cases' / case / 'design.yaml').read_text())
    work = root / 'results' / case / 'work' / version
    out = root / 'results' / case / version
    out.mkdir(parents=True, exist_ok=True)
    files = source_files(work, cfg)
    if not files:
        raise ValueError(f'no RTL sources in {work}; checkout the case first')
    includes = [
        '-I' + str((work / d).resolve())
        for d in cfg.get('include_dirs', [])
    ]
    lines = [
        'read_verilog -sv ' + ' '.join(includes + [quote(p) for p in files]),
        'hierarchy -check -top ' + identifier(cfg['top']),
        'proc -noopt',
        'write_json ' + quote(out / 'elaborated_hier.json'),
        'opt_expr',
        'flatten -scopename',
        'opt',
        'memory',
        'opt_clean',
        'check -assert',
        'write_json ' + quote(out / 'design_flat.json'),
        'write_rtlil ' + quote(out / 'design_flat.rtlil'),
        'write_verilog ' + quote(out / 'design_flat.v'),
    ]
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
    if result['status'] == 'passed':
        hashes, diagnostics = snapshot_inputs(work, cfg)
        def git_head(directory):
            proc = subprocess.run(['git', '-C', str(directory), 'rev-parse', 'HEAD'],
                                  capture_output=True, text=True)
            return proc.stdout.strip() if proc.returncode == 0 else None
        log = (out / 'frontend.log').read_text()
        manifest = {'schema_version': 1, 'top': cfg['top'], 'inputs': hashes,
                    'compile_sources': result['sources'], 'diagnostics': diagnostics,
                    'config_sha256': digest(root / 'benchmarks' / 'cases' / case / 'design.yaml'),
                    'script_sha256': digest(script), 'frontend_python_sha256': digest(__file__),
                    'yosys_version': next((line.strip() for line in log.splitlines()
                                           if line.strip().startswith('Yosys ')), 'see frontend.log'),
                    'framework_commit': git_head(ROOT), 'worktree_commit': git_head(work),
                    'artifacts': {name: digest(out / name)
                                  for name in ('design_flat.json', 'elaborated_hier.json')}}
        (out / 'source_manifest.json').write_text(json.dumps(manifest, indent=2))
        result['provenance_manifest'] = str(out / 'source_manifest.json')
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
    result = generate(
        a.root.resolve(),
        a.case,
        a.version,
        a.yosys,
        a.timeout,
        a.mapped,
        a.liberty
    )

    out = a.root.resolve() / 'results' / a.case / a.version

    print(f"Frontend status: {result['status']}")
    print(f"Output directory: {out}")
    print(f"Yosys log: {out / 'frontend.log'}")

    if result['status'] == 'passed':
        print(f"Generated: {out / 'design_flat.json'}")
        return 0

    print("Frontend generation failed; inspect frontend.log")
    return 1

if __name__ == '__main__':
    sys.exit(main())
