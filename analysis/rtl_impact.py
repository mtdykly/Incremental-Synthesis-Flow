"""Translate RTL hints into explained seeds and a separate functional fanout."""
from source_map import cells_overlapping
from region import is_hard_boundary
from rtl_diff import strip_comments
import re


def locate_seeds(changes, indexes, graphs, texts):
    result = {'schema_version': 1, 'base_cells': {}, 'new_cells': {}, 'unmapped_hints': [],
              'ignored_unreachable': [], 'functional_fanout': {}}
    for side in ('base', 'new'):
        index, graph = indexes[side], graphs[side]
        seeds = result[side + '_cells']

        def add(names, reason):
            for name in sorted(names):
                reasons = seeds.setdefault(name, {'reasons': []})['reasons']
                if reason not in reasons:
                    reasons.append(reason)

        def locate(path, start, end, reason, broad=False, connection=False):
            owners = [inst for inst in index['instances'] if any(
                s['path'] == path and s['start_line'] <= end and start <= s['end_line']
                for s in inst['sources'])]
            direct = cells_overlapping(index, path, start, end)
            # Instance rewiring affects that child's boundary; avoid selecting
            # all siblings solely because the parent module contains the edit.
            children = [inst for inst in index['instances'] if inst['path'] and any(
                s['path'] == path and s['start_line'] <= end and start <= s['end_line']
                for s in inst['instance_sources'])] if connection else []
            if children:
                owners, broad = children, True
            if not owners and not direct:
                result['ignored_unreachable'].append({'side': side, **reason})
                return
            direct_src = {name for name in direct if any(
                span['path'] == path and span['start_line'] <= end and start <= span['end_line']
                for span in index['cells'][name]['direct_sources'])}
            add(direct_src, {'type': 'direct_src_overlap', **reason})
            add(direct - direct_src, {'type': 'rtlil_origin_overlap', **reason})
            if broad:
                paths = [o['path'] for o in owners]
                names = {n for n, c in index['cells'].items() if any(
                    not p or c['instance'] == p or c['instance'].startswith(p + '.') for p in paths)}
                add(names, {'type': 'context_dependency', **reason})
            elif not direct:
                result['unmapped_hints'].append({'side': side, **reason,
                    'handling': 'no surviving source object; checked netlist closure remains mandatory'})

        for file in changes['files']:
            path = file['path']
            if file['comment_or_format_only']:
                continue
            key = 'old_ranges' if side == 'base' else 'new_ranges'
            broad = bool(file['interface_changed'] or set(file['flags']) & {
                'parameter_or_generate', 'width_or_type', 'include_or_conditional'})
            for start, end in file[key]:
                # A changed macro definition is propagated through uses below.
                lines = strip_comments(texts[side].get(path, '')).splitlines()[start-1:end]
                if lines and all(not x.strip() or x.lstrip().startswith('`define') for x in lines):
                    continue
                locate(path, start, end, {'path': path, 'range': [start, end], 'cause': 'rtl_edit'},
                       broad, 'instance_connection' in file['flags'])
        for use in changes['macro_uses'][side]:
            line = texts[side][use['path']].splitlines()[use['start']-1]
            broad = use['conditional'] or bool(re.search(r'\b(parameter|localparam|generate|input|output|wire|logic|reg)\b', line))
            locate(use['path'], use['start'], use['end'],
                   {'path': use['path'], 'range': [use['start'], use['end']],
                    'cause': 'macro_dependency', 'macro': use['macro']}, broad)
        # This diagnostic cone is not the region: consumers may remain reusable.
        visited, state_sinks, pending = set(), set(), list(seeds)
        while pending:
            node = pending.pop()
            if node in visited or node in state_sinks:
                continue
            if is_hard_boundary(graph.cells[node]['type']):
                state_sinks.add(node)
                continue
            visited.add(node)
            pending.extend(graph.graph.successors(node))
        result['functional_fanout'][side] = {'combinational_cells': sorted(visited),
                                             'state_sinks': sorted(state_sinks)}
    return result
