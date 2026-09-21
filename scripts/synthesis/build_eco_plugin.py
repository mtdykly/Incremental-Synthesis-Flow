#!/usr/bin/env python3
"""Build the pass with the SDK belonging to the selected native Yosys binary."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'analysis'))
from rtl_sources import digest


def build(yosys='yosys', config=None, cxx=None):
    command = shlex.split(yosys)
    binary = shutil.which(command[0]) if len(command) == 1 else None
    if not binary:
        raise ValueError('rtl-direct requires a native Yosys executable and its matching development SDK')
    config = config or str(Path(binary).with_name('yosys-config'))
    def flags(option):
        return subprocess.check_output([config, option], text=True).strip()
    try:
        version = subprocess.check_output([binary, '-V'], text=True).strip()
        compiler = cxx or os.environ.get('CXX') or flags('--cxx')
        if not shutil.which(shlex.split(compiler)[0]):
            compiler = shutil.which('g++') or compiler
        command = shlex.split(compiler) + shlex.split(flags('--cxxflags'))
        link = shlex.split(flags('--ldflags')) + shlex.split(flags('--ldlibs'))
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f'Yosys plugin SDK unavailable: {exc}') from exc
    source = ROOT / 'yosys-plugin/eco_extract.cc'
    out = ROOT / 'yosys-plugin/build'
    out.mkdir(exist_ok=True)
    target = out / 'eco_extract.so'
    stamp = out / 'build.json'
    key = {'yosys': str(Path(binary).resolve()), 'version': version,
           'source_sha256': digest(source), 'yosys_binary_sha256': digest(binary),
           'builder_sha256': digest(__file__), 'compiler': command, 'link': link}
    if target.is_file() and stamp.is_file() and json.loads(stamp.read_text()) == key:
        return target
    temporary = out / 'eco_extract.tmp.so'
    proc = subprocess.run(command + ['-shared', '-o', str(temporary), str(source)] + link,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    (out / 'build.log').write_text(proc.stdout)
    if proc.returncode:
        raise ValueError(f'Yosys plugin compilation failed; see {out / "build.log"}')
    probe = subprocess.run([binary, '-m', str(temporary), '-p', 'help eco_extract'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if probe.returncode:
        (out / 'build.log').write_text(proc.stdout + probe.stdout)
        raise ValueError(f'Yosys plugin load/ABI check failed; see {out / "build.log"}')
    temporary.replace(target)
    stamp.write_text(json.dumps(key, indent=2))
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yosys', default='yosys')
    parser.add_argument('--yosys-config')
    parser.add_argument('--cxx')
    args = parser.parse_args()
    print(build(args.yosys, args.yosys_config, args.cxx))
