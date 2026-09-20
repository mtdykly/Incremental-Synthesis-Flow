"""Line/token ECO hints with module ranges and transitive macro dependencies.

This is deliberately not a SystemVerilog semantic parser. Yosys elaboration
and checked netlist boundaries remain the authority for reachability and reuse.
"""
import difflib
import re
import subprocess


LEX = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/')
TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\\\S+|[A-Za-z_$][\w$]*|\d+|\S')


def strip_comments(text):
    return LEX.sub(lambda m: m[0] if m[0].startswith('"') else re.sub(r'[^\n]', ' ', m[0]), text)


def tokens(text):
    return tuple(TOKEN.findall(strip_comments(text)))


def module_ranges(text):
    code = strip_comments(text)
    result = []
    for match in re.finditer(r'\bmodule\s+(?:automatic\s+)?(\\\S+|[\w$]+)\b([\s\S]*?)\bendmodule\b', code):
        start = code.count('\n', 0, match.start()) + 1
        end = code.count('\n', 0, match.end()) + 1
        header = match[0].split(';', 1)[0]
        # Include non-ANSI declarations. Changes are hints, not state permission.
        declarations = re.findall(r'\b(?:input|output|inout)\b[^;]*;', match[2])
        result.append({'name': match[1], 'start': start, 'end': end,
                       'interface': tokens(header + '\n' + '\n'.join(declarations))})
    return result


def macro_definitions(texts):
    result = {}
    for path, text in sorted(texts.items()):
        lines = strip_comments(text).splitlines()
        i = 0
        while i < len(lines):
            match = re.match(r'\s*`define\s+(\w+)\b(.*)', lines[i])
            if match:
                start, body = i + 1, match[2]
                while body.rstrip().endswith('\\') and i + 1 < len(lines):
                    i += 1
                    body = body.rstrip()[:-1] + lines[i]
                result.setdefault(match[1], []).append({'path': path, 'start': start, 'end': i + 1,
                                                       'body': body, 'tokens': tokens(body)})
            i += 1
    return result


def diff_sources(base_texts, new_texts, base_commit=None, new_commit=None):
    files = []
    definitions = [macro_definitions(t) for t in (base_texts, new_texts)]
    changed_macros = {name for name in set(definitions[0]) | set(definitions[1])
                      if [d['tokens'] for d in definitions[0].get(name, [])]
                      != [d['tokens'] for d in definitions[1].get(name, [])]}
    dependent_macros = set(changed_macros)
    while True:
        previous = set(dependent_macros)
        for defs in definitions:
            for name, entries in defs.items():
                if any(set(re.findall(r'`(\w+)', e['body'])) & dependent_macros for e in entries):
                    dependent_macros.add(name)
        if previous == dependent_macros:
            break
    macro_uses = {'base': [], 'new': []}
    for side, texts, defs in zip(macro_uses, (base_texts, new_texts), definitions):
        definition_lines = {(e['path'], line) for entries in defs.values() for e in entries
                            for line in range(e['start'], e['end'] + 1)}
        for path, text in sorted(texts.items()):
            for number, line in enumerate(strip_comments(text).splitlines(), 1):
                if (path, number) in definition_lines:
                    continue
                names = set(re.findall(r'`(\w+)', line))
                conditional = re.search(r'`(?:ifdef|ifndef|elsif)\s+(\w+)', line)
                if conditional:
                    names.add(conditional[1])
                for name in sorted(names & dependent_macros):
                    macro_uses[side].append({'path': path, 'start': number, 'end': number,
                                             'macro': name, 'conditional': bool(conditional)})
    for path in sorted(set(base_texts) | set(new_texts)):
        old, new = base_texts.get(path, ''), new_texts.get(path, '')
        if old == new:
            continue
        before, after = strip_comments(old).splitlines(), strip_comments(new).splitlines()
        changes = []
        for tag, a, b, c, d in difflib.SequenceMatcher(None, [tokens(x) for x in before],
                                                     [tokens(x) for x in after], autojunk=False).get_opcodes():
            if tag != 'equal':
                # Empty sides use a neighbouring line as an explicit anchor.
                changes.append({'kind': tag, 'old_range': [a + 1, b] if a < b else [max(1, a), max(1, a)],
                                'new_range': [c + 1, d] if c < d else [max(1, c), max(1, c)],
                                'old_empty': a == b, 'new_empty': c == d})
        # Globally identical tokens include comments and line wrapping only.
        if tokens(old) == tokens(new):
            changes = []
        ranges = [module_ranges(t) for t in (old, new)]
        affected = [{m['name'] for m in ms for h in changes
                     if m['start'] <= h[key][1] and h[key][0] <= m['end']}
                    for ms, key in zip(ranges, ('old_range', 'new_range'))]
        interfaces = [{m['name']: m['interface'] for m in ms} for ms in ranges]
        interface_modules = sorted(name for name in affected[0] | affected[1]
                                   if interfaces[0].get(name) != interfaces[1].get(name))
        changed_text = '\n'.join(
            '\n'.join(lines[h[key][0]-1:h[key][1]])
            for h in changes for lines, key in ((before, 'old_range'), (after, 'new_range')))
        flags = [label for label, pattern in {
            'parameter_or_generate': r'\b(parameter|localparam|generate|endgenerate|genvar)\b',
            'width_or_type': r'\b(signed|unsigned|logic|wire|reg|typedef)\b',
            'instance_connection': r'\.\s*\w+\s*\(',
            'process': r'\balways(?:_comb|_ff|_latch)?\b',
            'include_or_conditional': r'`(?:include|ifdef|ifndef|elsif|undef)\b',
        }.items() if re.search(pattern, changed_text)]
        files.append({'path': path, 'status': 'added' if path not in base_texts else 'deleted' if path not in new_texts else 'modified',
                      'active_design_input': True, 'comment_or_format_only': not changes,
                      'hunks': changes, 'old_ranges': [h['old_range'] for h in changes],
                      'new_ranges': [h['new_range'] for h in changes],
                      'modules': sorted(affected[0] | affected[1]),
                      'interface_changed': bool(interface_modules), 'interface_modules': interface_modules,
                      'flags': flags, 'changed_defines': sorted(name for name in changed_macros
                          if any(e['path'] == path for defs in definitions for e in defs.get(name, [])))})
    return {'schema_version': 1, 'base_commit': base_commit, 'new_commit': new_commit,
            'method': 'comment-aware token/line hints; not a semantic AST diff', 'files': files,
            'changed_macros': sorted(changed_macros), 'dependent_macros': sorted(dependent_macros),
            'macro_uses': macro_uses}


def get_modified_files(base_commit, new_commit, repo):
    return subprocess.check_output(['git', '-C', repo, 'diff', '--name-only', base_commit, new_commit],
                                   text=True).splitlines()
