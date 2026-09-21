#!/usr/bin/env python3
"""Base-certified cuts + New pre-proc extraction + isolated mandatory verification."""
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
for path in ('analysis', 'formal', 'scripts/synthesis'):
    sys.path.insert(0, str(ROOT / path))
from base_cutpoints import prepare
from build_eco_plugin import build
from run_new_elaboration import elaborate
from region_contract import Unsupported, write_json, extract_and_lower, prove
from region_netlist import stitch_region
from rtl_diff import diff_sources
from rtl_direct import locate, envelopes, same_interface
from rtl_sources import digest
from verify_candidate_vs_rtl import verify, verify_unchanged_controls


def run_case(args):
    root = Path(args.root).resolve()
    out = root / 'results' / args.case / 'incremental'
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    report = {'schema_version': 1, 'flow': 'rtl_direct', 'status': 'running', 'verified': False,
              'requires_full_new_netlist': False, 'stages': {}, 'attempts': [],
              'algorithm_inputs': ['Base RTL', 'Base generic', 'Base certified contracts',
                                   'New hierarchical pre-proc RTLIL', 'compilation configuration'],
              'proof_semantics': 'New-defined outputs must be preserved; reference x is a dont-care'}
    write_json(out / 'run_report.json', report)
    # Never leave success from a previous run looking like the current result.
    write_json(out / 'verification.json', {'status': 'not_run'})
    write_json(out / 'local_verification.json', {'status': 'not_run'})
    for name in ('new_region_preproc.rtlil', 'new_region_spec.json', 'new_region_synth.json',
                 'extraction.json', 'region_contract.json', 'region_request.json', 'region_plan.json', 'stitched.json'):
        (out / name).unlink(missing_ok=True)
    try:
        if getattr(args, 'max_expansions', 5) < 0:
            raise ValueError('max-expansions must be nonnegative')
        plugin = build(args.yosys)
        t = time.perf_counter()
        base_dir, base_manifest = elaborate(root, args.case, 'base', args.yosys, args.timeout,
                                            plugin, reuse=not args.frontend)
        catalog = prepare(base_dir, plugin, args.yosys, args.timeout, rebuild=args.frontend)
        report['stages']['base_setup'] = {'seconds': time.perf_counter() - t, 'cached': not args.frontend,
                                         'certified_contracts': sum(c['status'] == 'certified'
                                                                    for c in catalog['contracts'].values()),
                                         'catalog': str(base_dir / 'contracts/catalog.json')}
        if getattr(args, 'setup_base_only', False):
            report.update(status='setup_complete')
            return 0
        # New is first accessed AFTER offline Base certification completes.
        t = time.perf_counter()
        new_dir, new_manifest = elaborate(root, args.case, 'new', args.yosys, args.timeout, plugin)
        report['stages']['new_elaboration'] = {'seconds': time.perf_counter() - t,
                                               'global_proc': False, 'global_flatten': False}
        indexes = [json.loads((p / 'source_index.json').read_text()) for p in (base_dir, new_dir)]
        works = [root / 'results' / args.case / 'work' / v for v in ('base', 'new')]
        texts = [{p: (work / p).read_text() for p in manifest['identity']['inputs']}
                 for work, manifest in zip(works, (base_manifest, new_manifest))]
        diff = diff_sources(*texts)
        write_json(out / 'rtl_changes.json', diff)
        selection = locate(diff, indexes, works)
        write_json(out / 'rtl_selection.json', selection)
        base_instances, new_instances = [{tuple(i['path']): i for i in index['instances']} for index in indexes]
        if not same_interface(base_instances[()], new_instances[()]):
            raise Unsupported('top-level interface changed')
        choices = envelopes(indexes[0], selection)
        if selection['path'] is None:
            choices = [(None, [], 'no active change')]
            # A missed source hint cannot grant reuse: top proof is mandatory,
            # and a failure promotes to the Base top envelope.
            choices += envelopes(indexes[0], {'path': [], 'targets': []})
        base = json.loads((base_dir / 'design_flat.json').read_text())
        top = base_manifest['top']
        total_base = sum(c['type'] != '$scopeinfo' for c in base['modules'][top]['cells'].values())
        attempts_root = out / 'attempts' / str(time.time_ns())
        limit = getattr(args, 'max_expansions', 5)
        for number, (key, path, kind) in enumerate(choices[:limit + 1]):
            directory = attempts_root / f'{number:03d}'
            directory.mkdir(parents=True)
            attempt = {'instance_path': path, 'kind': kind, 'contract_id': key, 'artifacts': str(directory)}
            report['attempts'].append(attempt)
            t = time.perf_counter()
            try:
                if key is None:
                    candidate = base
                    contract = None
                else:
                    contract = catalog['contracts'].get(key)
                    if not contract or contract['status'] != 'certified':
                        raise Unsupported((contract or {}).get('reason', 'no offline certificate for atomic outputs'))
                    if tuple(path) not in new_instances or not same_interface(base_instances[tuple(path)], new_instances[tuple(path)]):
                        raise Unsupported('module interface/instance changed; promote to parent')
                    write_json(directory / 'region_contract.json', contract)
                    extract_and_lower(new_dir / 'new_preproc.rtlil', contract['request'], directory,
                                      plugin, args.yosys, args.timeout)
                    local = prove(directory / 'new_region_synth.json', directory / 'new_region_spec.json',
                                  directory / 'local_proof', args.yosys, args.timeout)
                    attempt['local_verification'] = local
                    if local['status'] != 'passed':
                        raise Unsupported(f'local synthesis proof {local["status"]}')
                    replacement = json.loads((directory / 'new_region_synth.json').read_text())
                    ports = replacement['modules']['incremental_region']['ports']
                    for fixed in contract['fixed_outputs']:
                        if ports[fixed['port']]['bits'] != [fixed['value']]:
                            raise Unsupported(f'optimized constant output changed: {fixed["port"]}; promote parent')
                    for group in contract['aliased_outputs']:
                        if any(ports[p]['bits'] != ports[group[0]]['bits'] for p in group[1:]):
                            raise Unsupported('optimized Base output aliases split; promote parent')
                    candidate = stitch_region(base, contract['plan'], replacement)
                write_json(directory / 'stitched.json', candidate)
                attempt['online_region_seconds'] = time.perf_counter() - t
                controls = verify_unchanged_controls(base_dir / 'design_flat.json', directory / 'stitched.json',
                                                     top, directory / 'control_proof', args.yosys, args.timeout)
                attempt['state_controls'] = controls
                if controls['status'] != 'passed':
                    raise Unsupported('changed or unproved clock/reset/enable semantics')
                verification = verify(directory / 'stitched.json', new_dir / 'new_preproc.rtlil', top,
                                      directory / 'verification', args.yosys, args.timeout)
                attempt['verification'] = verification
                write_json(out / 'verification.json', verification)
                if verification['status'] != 'passed':
                    raise Unsupported(f'whole New RTL proof {verification["status"]}; promote envelope')
                for name in ('new_region_preproc.rtlil', 'new_region_spec.json', 'new_region_synth.json',
                             'extraction.json', 'region_contract.json', 'region_request.json', 'stitched.json'):
                    if (directory / name).exists(): shutil.copy2(directory / name, out / name)
                if contract: write_json(out / 'region_plan.json', contract['plan'])
                report.update(status='success', verified=True, selected_instance=path,
                              replaced_base_cells=len(contract['base_cells']) if contract else 0,
                              retained_base_cells=total_base - (len(contract['base_cells']) if contract else 0),
                              duplicated_shared_fanin=len(contract['retained_shared_fanin']) if contract else 0,
                              new_region_cells=len(replacement['modules']['incremental_region']['cells']) if contract else 0,
                              expansion_rounds=number, candidate_sha256=digest(out / 'stitched.json'))
                write_json(out / 'local_verification.json', attempt.get('local_verification',
                           {'status': 'not_needed', 'reason': 'candidate reuses Base unchanged'}))
                attempt['status'] = 'passed'
                break
            except (Unsupported, ValueError, KeyError) as exc:
                attempt.update(status='unsupported_or_unproved', reason=str(exc))
                print(f'Promoting RTL region {".".join(path) or "<top>"}: {exc}', flush=True)
        if not report['verified']:
            raise Unsupported('no certified supported envelope passed all proofs; inspect attempts in run_report.json')
    except (OSError, ValueError, KeyError) as exc:
        report.update(status='unsupported' if isinstance(exc, Unsupported) else 'failed', error=str(exc))
    finally:
        report['total_seconds'] = time.perf_counter() - started
        write_json(out / 'run_report.json', report)
        print(f'RTL-direct run: {report["status"]}; report: {out / "run_report.json"}', flush=True)
    return 0 if report['verified'] else 1
