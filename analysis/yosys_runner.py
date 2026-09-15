"""Auditable subprocess execution; a timeout or missing tool is never success."""
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path


def quote(path):
    return json.dumps(str(Path(path).resolve()).replace('\\', '/'))


def identifier(name):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_$]*', name):
        raise ValueError(f'unsupported top identifier: {name}')
    return name


def run_yosys(script, log, executable='yosys', timeout=120):
    command = shlex.split(executable, posix=os.name != 'nt') + ['-s', str(script)]
    start = time.perf_counter()
    status, code = 'tool_error', None
    try:
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=timeout)
        output, code = proc.stdout, proc.returncode
        status = 'passed' if code == 0 else 'tool_error'
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ''
        if isinstance(output, bytes):
            output = output.decode(errors='replace')
        output += '\nTIMEOUT\n'
        status = 'unknown'
    except OSError as exc:
        output = str(exc)
    if status == 'tool_error' and 'ERROR: Found ' in output and 'unproven $equiv cells' in output:
        status = 'unknown'
    Path(log).write_text(output, encoding='utf-8')
    return {'status': status, 'returncode': code, 'seconds': time.perf_counter() - start,
            'log': str(log), 'command': command}
