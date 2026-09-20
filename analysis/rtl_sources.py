"""Share the frontend's source selection and bind provenance to exact inputs."""
import fnmatch
import hashlib
import json
from pathlib import Path
import re


RTL_SUFFIXES = {'.v', '.sv', '.vh', '.svh'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files(work, cfg):
    return sorted({p.resolve() for directory in cfg['source_dirs'] for p in (work / directory).rglob('*')
                   if p.is_file() and p.suffix in {'.v', '.sv'}
                   and not any(fnmatch.fnmatch(p.name, pat) for pat in cfg.get('exclude_patterns', []))})


def input_files(work, cfg):
    """Track literal includes recursively; dynamic includes require fallback."""
    from rtl_diff import strip_comments
    work = work.resolve()
    files = set(source_files(work, cfg))
    pending = list(files)
    diagnostics = []
    while pending:
        path = pending.pop()
        code = strip_comments(path.read_text())
        for match in re.finditer(r'(?m)^\s*`include\s+([^\n]+)', code):
            literal = re.fullmatch(r'"([^"]+)"\s*', match[1])
            if not literal:
                diagnostics.append(f'dynamic include in {path.relative_to(work)}')
                continue
            choices = [path.parent / literal[1], work / literal[1]] + [
                work / d / literal[1] for d in cfg.get('include_dirs', [])]
            found = next((p.resolve() for p in choices if p.is_file()), None)
            if found is None:
                diagnostics.append(f'unresolved include {literal[1]} in {path.relative_to(work)}')
            elif not found.is_relative_to(work):
                diagnostics.append(f'include outside source root: {found}')
            elif found not in files:
                files.add(found)
                pending.append(found)
    return sorted(files), sorted(set(diagnostics))


def snapshot_inputs(work, cfg):
    files, diagnostics = input_files(work, cfg)
    return {str(p.relative_to(work.resolve())): digest(p) for p in files}, diagnostics


def load_bound_inputs(root, case, version):
    import yaml
    root = Path(root).resolve()
    config = root / 'benchmarks' / 'cases' / case / 'design.yaml'
    cfg = yaml.safe_load(config.read_text())
    work = root / 'results' / case / 'work' / version
    out = root / 'results' / case / version
    if not all((out / name).is_file() for name in (
            'frontend_report.json', 'source_manifest.json', 'elaborated_hier.json')):
        raise ValueError(f'{version}: missing RTL provenance; rerun --frontend with case checkouts')
    report = json.loads((out / 'frontend_report.json').read_text())
    manifest = json.loads((out / 'source_manifest.json').read_text())
    hashes, diagnostics = snapshot_inputs(work, cfg)
    if report.get('status') != 'passed' or manifest['inputs'] != hashes or manifest['config_sha256'] != digest(config):
        raise ValueError(f'{version}: stale RTL provenance; rerun --frontend')
    for filename, expected in manifest['artifacts'].items():
        if digest(out / filename) != expected:
            raise ValueError(f'{version}: stale {filename}; rerun --frontend')
    if diagnostics:
        raise ValueError('; '.join(diagnostics))
    return {'work': work, 'out': out, 'manifest': manifest,
            'texts': {name: (work / name).read_text() for name in hashes},
            'hierarchy': json.loads((out / 'elaborated_hier.json').read_text())}
