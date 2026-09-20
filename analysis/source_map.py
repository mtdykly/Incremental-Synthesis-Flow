"""Conservative many-to-many source hints, never a proof of reuse."""
from dataclasses import asdict, dataclass
from pathlib import Path
import re


@dataclass(frozen=True, order=True)
class SourceSpan:
    path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int

    def overlaps(self, start, end):
        return self.start_line <= end and start <= self.end_line


def normalize_path(path, source_root=None):
    path = path.replace('\\', '/')
    if source_root:
        prefix = str(Path(source_root).resolve()).replace('\\', '/').rstrip('/') + '/'
        if path.startswith(prefix):
            return path[len(prefix):]
    return re.sub(r'^.*?/work/(?:base|new)/', '', path).removeprefix('./')


def parse_source_spans(src, source_root=None):
    spans = set()
    for item in (src if isinstance(src, str) else '').split('|'):
        match = re.fullmatch(r'(.+):(\d+)(?:\.(\d+))?(?:-(\d+)(?:\.(\d+))?)?', item.strip())
        if match:
            path, sl, sc, el, ec = match.groups()
            spans.add(SourceSpan(normalize_path(path, source_root), int(sl), int(sc or 0),
                                 int(el or sl), int(ec or sc or 0)))
    return sorted(spans)


def hierarchy_instances(hierarchy, top, source_root=None):
    modules = hierarchy['modules']
    result = []

    def visit(kind, path, ancestors, instance=None):
        if kind in ancestors:
            raise ValueError('recursive module hierarchy is unsupported')
        mod = modules[kind]
        attrs = mod.get('attributes', {})
        result.append({'path': path, 'type': kind, 'module': attrs.get('hdlname', kind),
                       'sources': [asdict(s) for s in parse_source_spans(attrs.get('src'), source_root)],
                       'instance_sources': [asdict(s) for s in parse_source_spans(
                           (instance or {}).get('attributes', {}).get('src'), source_root)],
                       'ports': mod.get('ports', {})})
        for name, cell in sorted(mod.get('cells', {}).items()):
            if cell['type'] in modules:
                visit(cell['type'], '.'.join(filter(None, (path, name))), ancestors | {kind}, cell)

    visit(top, '', set())
    return result


def build_source_index(graph, hierarchy, source_root=None):
    from canonical import canonical_scope
    instances = hierarchy_instances(hierarchy, graph.top, source_root)
    cells, by_file, missing = {}, {}, []
    rtlil_objects, objects_by_file, objects_by_alias = {}, {}, {}
    for inst in instances:
        module = hierarchy['modules'][inst['type']]
        local_aliases = {}
        for net, data in module.get('netnames', {}).items():
            if not data.get('hide_name', 0):
                for bit in data['bits']:
                    if isinstance(bit, int):
                        local_aliases.setdefault(bit, set()).add('.'.join(filter(None, (inst['path'], net))))
        for name, cell in sorted(module.get('cells', {}).items()):
            object_id = inst['path'] + '::' + name
            spans = parse_source_spans(cell.get('attributes', {}).get('src'), source_root)
            rtlil_objects[object_id] = {'instance': inst['path'], 'module': inst['module'],
                                       'type': cell['type'], 'sources': [asdict(s) for s in spans]}
            output_aliases = {alias for pin, bits in cell['connections'].items()
                              if cell['port_directions'][pin] == 'output' for bit in bits
                              for alias in local_aliases.get(bit, [])}
            for alias in output_aliases:
                objects_by_alias.setdefault(alias, set()).add(object_id)
            rtlil_objects[object_id]['output_aliases'] = sorted(output_aliases)
            for span in spans:
                objects_by_file.setdefault(span.path, []).append((object_id, inst['path'], span))
    flat_aliases = {}
    for net, data in graph.module_data.get('netnames', {}).items():
        if not data.get('hide_name', 0):
            for bit in data['bits']:
                if isinstance(bit, int):
                    flat_aliases.setdefault(bit, set()).add(net)
    direct_count = 0
    for name, cell in sorted(graph.cells.items()):
        scope = canonical_scope(name, cell).replace(' ', '.')
        if scope == '<global>':
            scope = ''
        owners = [x for x in instances if scope == x['path'] or scope.startswith(x['path'] + '.')]
        owner = max(owners, key=lambda x: len(x['path'])) if owners else instances[0]
        spans = parse_source_spans(cell.get('attributes', {}).get('src'), source_root)
        direct = list(spans)
        direct_count += bool(direct)
        alias_origins = {obj for _, _, bit in graph.cell_output_bits(name)
                         for alias in flat_aliases.get(bit, []) for obj in objects_by_alias.get(alias, [])}
        # A public output alias can survive when opt_expr replaces a cell and
        # drops src. These are many-to-many origin hints, never identities.
        spans = sorted(set(spans) | {SourceSpan(**origin) for obj in alias_origins
                                    for origin in rtlil_objects[obj]['sources']})
        cells[name] = {'type': cell['type'], 'scope': scope, 'instance': owner['path'],
                       'module': owner['module'], 'sources': [asdict(s) for s in spans],
                       'direct_sources': [asdict(s) for s in direct],
                       'rtlil_object_hints': sorted(alias_origins | {obj for span in spans
                           for obj, instance, origin in objects_by_file.get(span.path, [])
                           if instance == owner['path'] and origin.overlaps(span.start_line, span.end_line)})}
        if not spans:
            missing.append(name)
        for span in spans:
            by_file.setdefault(span.path, []).append({'cell': name, **asdict(span)})
    return {'schema_version': 1, 'top': graph.top, 'cells': cells, 'by_file': by_file,
            'instances': instances, 'rtlil_objects': rtlil_objects, 'missing_source_cells': missing,
            'coverage': {'cells': len(cells), 'with_source': direct_count, 'with_source_hints': len(cells) - len(missing)},
            'precision': 'source hints; optimization provenance is incomplete'}


def cells_overlapping(index, path, start, end):
    return {entry['cell'] for entry in index['by_file'].get(path, [])
            if entry['start_line'] <= end and start <= entry['end_line']}
