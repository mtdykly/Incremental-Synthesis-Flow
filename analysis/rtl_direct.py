"""Locate pre-proc atomic regions from RTL hints; never consumes a New generic graph."""
import json

from region_contract import contract_id, layouts
from source_map import parse_source_spans


def overlap(src, ranges, work):
    return any(s.path == path and s.overlaps(a, b) for s in parse_source_spans(src, work)
               for path, a, b in ranges)


def locate(diff, indexes, works):
    source_hints = []
    for side, index, work in zip(('base', 'new'), indexes, works):
        key = 'old_ranges' if side == 'base' else 'new_ranges'
        ranges = [(f['path'], a, b) for f in diff['files'] for a, b in f[key]]
        ranges += [(u['path'], u['start'], u['end']) for u in diff['macro_uses'][side]]
        source_hints += [{'path': i['path'], 'side': side} for i in index['instances']
                         if overlap(i['src'], ranges, work)]
    # Source spans explain changes; elaborated object semantics determine the
    # active targets. This catches assignments whose process src marks only the
    # opening always line, and ignores comments/auto-generated cell numbering.
    maps = [{tuple(i['path']): i for i in index['instances']} for index in indexes]
    hits = []
    for path in sorted(set(maps[0]) | set(maps[1])):
        before, after = [m.get(path) for m in maps]
        if before is None or after is None:
            hits.append({'path': list(path[:-1]), 'envelope': True, 'outputs': [],
                         'cause': 'instance added or removed'})
            continue
        if not same_interface(before, after):
            hits.append({'path': list(path[:-1]) if path else [], 'envelope': True, 'outputs': [],
                         'cause': 'elaborated interface changed'})
            continue
        def objects(inst):
            result = {}
            wires = layouts(inst)
            for obj in inst['objects']:
                outputs = tuple(sorted((o['signal'], o['offset']) for o in obj.get('outputs', [])
                                       if not wires[o['signal']]['input']))
                if not outputs:
                    continue  # anonymous RHS expressions are embedded in their consuming unit
                key = (obj['kind'], outputs)
                result.setdefault(key, []).append(json.dumps(obj.get('semantic'), sort_keys=True))
            return {k: sorted(v) for k, v in result.items()}
        old, new = objects(before), objects(after)
        changed = [k for k in set(old) | set(new) if old.get(k) != new.get(k)]
        if changed:
            targets = sorted({o for _, outputs in changed for o in outputs})
            hits.append({'path': list(path), 'envelope': any(k[0] == 'instance' for k in changed),
                         'outputs': [{'signal': n, 'offset': i} for n, i in targets],
                         'cause': 'pre-proc RTLIL object semantics changed',
                         'object_kinds': sorted({k[0] for k in changed})})
    if not hits:
        return {'path': None, 'targets': [], 'hits': [], 'source_hints': source_hints, 'reason': 'no active pre-proc semantic change; still requires whole-design proof'}
    paths = [h['path'] for h in hits]
    common = list(paths[0])
    while any(p[:len(common)] != common for p in paths): common.pop()
    envelope = any(h['path'] != common or h['envelope'] for h in hits)
    targets = [] if envelope else [{'signal': n, 'offset': i} for n, i in sorted(
        {(o['signal'], o['offset']) for h in hits for o in h['outputs']})]
    return {'path': common, 'targets': targets, 'hits': hits, 'source_hints': source_hints,
            'reason': 'pre-proc RTLIL structural semantic difference; Base-certified dependency closure'}


def envelopes(index, selection):
    instances = {tuple(i['path']): i for i in index['instances']}
    path = selection['path']
    if path is None:
        return []
    options = []
    if selection['targets']:
        options.append((contract_id(path, selection['targets']), path, 'atomic outputs'))
    while True:
        instance = instances.get(tuple(path))
        if instance:
            outputs = [{'signal': w['signal'], 'offset': i} for w in instance['wires']
                       if w['output'] for i in range(w['width'])]
            options.append((contract_id(path, outputs), path, 'module envelope'))
        if not path: break
        path = path[:-1]
    return list({key: (key, p, kind) for key, p, kind in options}.values())


def same_interface(base, new):
    signature = lambda inst: {w['signal']: tuple(w[k] for k in (
        'input', 'output', 'width', 'signed', 'start_offset', 'upto'))
        for w in inst['wires'] if w['input'] or w['output']}
    return signature(base) == signature(new)
