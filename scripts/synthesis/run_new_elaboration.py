#!/usr/bin/env python3
"""Elaborate RTL without lowering processes or flattening the New design."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'analysis'))
from rtl_sources import source_files, snapshot_inputs, digest
from yosys_runner import identifier, quote, run_yosys
from build_eco_plugin import build


def input_identity(root, case, version, yosys, plugin):
    config = root / 'benchmarks/cases' / case / 'design.yaml'
    cfg = yaml.safe_load(config.read_text())
    work = root / 'results' / case / 'work' / version
    hashes, diagnostics = snapshot_inputs(work, cfg)
    if diagnostics or not hashes:
        raise ValueError('; '.join(diagnostics) or f'no sources in {work}')
    return cfg, work, {'inputs': hashes, 'config_sha256': digest(config),
                       'yosys_version': subprocess.check_output(shlex.split(yosys) + ['-V'], text=True).strip(),
                       'plugin_sha256': digest(plugin), 'frontend_sha256': digest(__file__)}


def elaborate(root, case, version='new', yosys='yosys', timeout=120, plugin=None, reuse=False):
    root = Path(root).resolve()
    plugin = plugin or build(yosys)
    cfg, work, identity = input_identity(root, case, version, yosys, plugin)
    out = root / 'results' / case / version / 'rtl_direct'
    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / 'manifest.json'
    if reuse:
        if not manifest_path.is_file():
            raise ValueError('missing Base rtl-direct setup; run --rtl-direct --frontend')
        manifest = json.loads(manifest_path.read_text())
        if manifest['identity'] != identity or any(not (out / p).is_file() or digest(out / p) != sha
                                                  for p, sha in manifest['artifacts'].items()):
            raise ValueError('stale Base rtl-direct setup; rerun --rtl-direct --frontend')
        return out, manifest
    manifest_path.unlink(missing_ok=True)
    include = ['-I' + str((work / p).resolve()) for p in cfg.get('include_dirs', [])]
    if any(any(c.isspace() or c in ';\"' for c in p) for p in include):
        raise ValueError('Yosys Verilog include directories must not contain whitespace or quotes')
    defines = cfg.get('defines', [])
    if isinstance(defines, dict):
        defines = [str(k) if v is None else f'{k}={v}' for k, v in defines.items()]
    if any(any(c.isspace() or c in '\n;"' for c in str(d)) for d in defines):
        raise ValueError('unsupported whitespace/quoting in configured Verilog define')
    params = []
    for key, value in cfg.get('parameters', {}).items():
        if any(c.isspace() or c in ';"' for c in str(value)):
            raise ValueError('unsupported parameter value')
        params += ['-chparam', identifier(key), str(value)]
    lines = ['plugin -i ' + quote(plugin),
             'read_verilog -sv ' + ' '.join(include + ['-D' + str(d) for d in defines] +
                                           [quote(p) for p in source_files(work, cfg)]),
             'hierarchy -check -top ' + identifier(cfg['top']) + ' ' + ' '.join(params),
             'uniquify',
             'eco_index -top ' + identifier(cfg['top']) + ' -json ' + quote(out / 'source_index.json'),
             'write_rtlil ' + quote(out / f'{version}_preproc.rtlil')]
    artifacts = ['source_index.json', f'{version}_preproc.rtlil']
    if version == 'base':
        lines += ['proc -noopt', 'opt_expr', 'flatten -scopename', 'opt', 'memory', 'opt_clean',
                  'check -assert', 'write_json ' + quote(out / 'design_flat.json')]
        artifacts.append('design_flat.json')
    script = out / 'elaborate.ys'
    script.write_text('\n'.join(lines) + '\n')
    result = run_yosys(script, out / 'elaborate.log', yosys, timeout)
    if result['status'] != 'passed':
        raise ValueError(f'{version} elaboration failed: {result["log"]}')
    manifest = {'schema_version': 1, 'identity': identity, 'top': cfg['top'], 'stage': result,
                'artifacts': {p: digest(out / p) for p in artifacts}}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return out, manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case')
    p.add_argument('--root', type=Path, default=ROOT)
    p.add_argument('--yosys', default='yosys')
    p.add_argument('--timeout', type=int, default=120)
    a = p.parse_args()
    print(elaborate(a.root, a.case, yosys=a.yosys, timeout=a.timeout)[0])
