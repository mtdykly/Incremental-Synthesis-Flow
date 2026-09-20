"""RTL hints select regions; checked correspondences delimit safe reuse.

The first implementation still uses the complete New generic netlist. Neither
RTL mode nor hybrid mode is allowed to retain an unchecked cell outside a seed.
"""
import json
from pathlib import Path
import time
from region import is_hard_boundary, plan_regions
from rtl_sources import load_bound_inputs, RTL_SUFFIXES, digest
from rtl_diff import diff_sources
from source_map import build_source_index
from rtl_impact import locate_seeds


def seed_mode(args):
    return getattr(args, 'seed_mode', None) or ('hybrid' if getattr(args, 'rtl_guided', False) else 'netlist')


def close_seeds(graph, seeds, reusable):
    region, pending, visited = set(), list(seeds), set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        if is_hard_boundary(graph.cells[node]['type']):
            # An RTL always block can cover an unchanged register. Rebuild its
            # combinational inputs, never put that register in a local region.
            pending.extend(p for p in graph.graph.predecessors(node) if p not in reusable)
            continue
        region.add(node)
        pending.extend(n for n in list(graph.graph.predecessors(node)) + list(graph.graph.successors(node))
                       if n not in reusable)
    return region


def changed_control_states(base, new, match):
    """Keep v1 restricted to unchanged clock/reset/enable functions."""
    changed = {'base': [], 'new': []}
    for side, graph in (('base', base), ('new', new)):
        reusable = {m[side] for m in match['matches']}
        for name in sorted(reusable):
            cell = graph.cells[name]
            if not is_hard_boundary(cell['type']):
                continue
            pending = [graph.bit_driver[str(bit)][0] for pin, _, bit in graph.cell_input_bits(name)
                       if pin != 'D' and isinstance(bit, int) and str(bit) in graph.bit_driver]
            visited = set()
            while pending:
                node = pending.pop()
                if node in visited:
                    continue
                visited.add(node)
                if node not in reusable:
                    changed[side].append(name)
                    break
                if not is_hard_boundary(graph.cells[node]['type']):
                    pending.extend(graph.graph.predecessors(node))
    return changed


def build_rtl_plan(root, case, base, new, match, mode, extra=None):
    start = time.perf_counter()
    out = Path(root) / 'results' / case / 'analysis'
    out.mkdir(parents=True, exist_ok=True)
    bound = {s: load_bound_inputs(root, case, s) for s in ('base', 'new')}
    for field in ('frontend_python_sha256', 'yosys_version'):
        if bound['base']['manifest'].get(field) != bound['new']['manifest'].get(field):
            raise ValueError(f'Base/New frontend mismatch in {field}; rerun --frontend')
    graphs = {'base': base, 'new': new}
    indexes = {s: build_source_index(graphs[s], bound[s]['hierarchy'], bound[s]['work']) for s in bound}
    texts = {s: bound[s]['texts'] for s in bound}
    changes = diff_sources(texts['base'], texts['new'],
                           bound['base']['manifest'].get('worktree_commit'),
                           bound['new']['manifest'].get('worktree_commit'))
    inactive = {}
    for side in bound:
        work = bound[side]['work']
        inactive[side] = {str(p.relative_to(work)): digest(p) for p in work.rglob('*')
                          if p.is_file() and p.suffix in RTL_SUFFIXES
                          and str(p.relative_to(work)) not in texts[side]}
    changes['excluded_changed_files'] = [name for name in sorted(set(inactive['base']) | set(inactive['new']))
                                         if inactive['base'].get(name) != inactive['new'].get(name)
                                         and name not in texts['base'] and name not in texts['new']]
    for file in changes['files']:
        file['reachable_from_top'] = {s: any(span['path'] == file['path']
            for inst in indexes[s]['instances'] for span in inst['sources']) for s in indexes}
    changes['input_manifests'] = {s: bound[s]['manifest'] for s in bound}
    impact = locate_seeds(changes, indexes, graphs, texts)
    baseline = plan_regions(base, new, match)
    regions, metrics = {}, {}
    for side, graph in graphs.items():
        seeds = set(impact[side + '_cells'])
        reusable = {m[side] for m in match['matches']}
        reference = set(baseline[side + '_cells'])
        comb_seeds = {c for c in seeds if not is_hard_boundary(graph.cells[c]['type'])}
        initial = seeds | (reference if mode == 'hybrid' else set())
        initial |= set((extra or {}).get(side, []))
        regions[side] = close_seeds(graph, initial, reusable)
        overlap = len(comb_seeds & reference)
        metrics[side] = {'rtl_seed_cells': len(seeds), 'rtl_combinational_seed_cells': len(comb_seeds),
                         'netlist_reference_cells': len(reference),
                         'seed_overlap_reference': overlap,
                         'reference_recall': overlap / len(reference) if reference else None,
                         'reference_precision': overlap / len(comb_seeds) if comb_seeds else None,
                         'safety_completion_cells': sorted(reference - regions[side])}
    plan = plan_regions(base, new, match, regions['base'], regions['new'])
    controls = changed_control_states(base, new, match)
    if any(controls.values()):
        plan['stitchable'] = False
    plan['unsupported_control_changes'] = controls
    plan['rtl_guidance'] = {'seed_mode': mode, 'requires_full_new_netlist': True,
                            'metrics': metrics, 'provenance_coverage': {s: indexes[s]['coverage'] for s in indexes},
                            'location_seconds': time.perf_counter() - start,
                            'reference_kind': 'conservative netlist-diff region, not a minimum oracle',
                            'unmapped_hints': len(impact['unmapped_hints']),
                            'boundary_rule': 'only checked matches may remain; provenance is never reuse permission'}
    for filename, data in [('rtl_changes.json', changes), ('rtl_seed_cells.json', impact),
                           ('rtl_region.json', plan['rtl_guidance'])] + [
                               (f'source_index_{s}.json', index) for s, index in indexes.items()]:
        (out / filename).write_text(json.dumps(data, indent=2))
    return plan


def expansion_from_failure(base, new, plan, outcome):
    """Grow one combinational fanin layer from named unproven obligations."""
    if outcome.get('status') != 'unknown' or not outcome.get('unproven_points'):
        return None  # Tool failures/timeouts with no named obligations are not counterexamples.
    regions = {s: set(plan[s + '_cells']) for s in ('base', 'new')}
    before = {s: set(v) for s, v in regions.items()}
    for point in outcome['unproven_points']:
        for side, graph in (('base', base), ('new', new)):
            roots = []
            name = point.get(side + '_cell')
            if name in graph.cells:
                roots.append(name)
            if side == 'new':
                roots.extend(graph.bit_driver[str(bit)][0] for bit in point.get('new_bits', [])
                             if isinstance(bit, int) and str(bit) in graph.bit_driver)
            if point.get('kind') == 'output' and side == 'base':
                roots.extend(graph.bit_driver[str(bit)][0] for bit in graph.module_data['ports'][point['port']]['bits']
                             if isinstance(bit, int) and str(bit) in graph.bit_driver)
            for root in roots:
                for node in [root, *graph.graph.predecessors(root)]:
                    if not is_hard_boundary(graph.cells[node]['type']):
                        regions[side].add(node)
    # Traverse the current region boundary on later rounds, making progress
    # even when the same output or state input remains unproven.
    if regions == before:
        for side, graph in (('base', base), ('new', new)):
            for root in before[side]:
                regions[side].update(n for n in graph.graph.predecessors(root)
                                     if not is_hard_boundary(graph.cells[n]['type']))
    if regions == before:
        return None
    return {s: sorted(v) for s, v in regions.items()}


def write_experiment_summary(out, report):
    """A compact, shareable result with tool/input identities and actual scope."""
    manifests = {}
    for side in ('base', 'new'):
        path = out.parent / side / 'source_manifest.json'
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                manifests[side] = {'error': 'unreadable source manifest'}
                continue
            manifests[side] = {k: data.get(k) for k in (
                'worktree_commit', 'framework_commit', 'yosys_version', 'config_sha256',
                'frontend_python_sha256', 'script_sha256', 'artifacts')}
    root = Path(__file__).resolve().parents[1]
    summary = {k: report.get(k) for k in (
        'status', 'verified', 'error', 'seed_mode', 'timing_scope', 'total_seconds',
        'requires_full_new_netlist', 'retained_base_cells', 'replaced_base_cells',
        'new_region_cells', 'boundary_ports', 'expansion_rounds', 'rtl_guidance')}
    summary.update(schema_version=1, inputs=manifests, speedup=None, liberty_area=None,
                   cell_count_rule='generic cells excluding $scopeinfo; not physical area',
                   implementation_sha256={str(p.relative_to(root)): digest(p)
                       for directory in ('analysis', 'formal', 'scripts') for p in sorted((root / directory).rglob('*.py'))})
    target = out.parent / 'analysis' / 'rtl_experiment.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2))
