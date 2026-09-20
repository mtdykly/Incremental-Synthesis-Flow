import argparse
import copy
import json
from pathlib import Path
import pytest
from test_correctness import YOSYS, cell, design, graphs, register
from source_map import parse_source_spans, build_source_index
from rtl_diff import diff_sources
from rtl_region import expansion_from_failure
from canonical_matcher import canonical_match
from region import plan_regions
from test_formal_pipeline import case_args
from run_incremental_case import run_case


def test_full_source_spans_and_multiple_origins():
    spans = parse_source_spans('C:/project/work/base/rtl/a.sv:4.2-9.7|rtl/b.sv:20|bad')
    assert [(s.path, s.start_line, s.end_line) for s in spans] == [('rtl/a.sv', 4, 9), ('rtl/b.sv', 20, 20)]
    assert spans[0].start_column == 2 and spans[0].end_column == 7
    assert spans[0].overlaps(8, 10) and not spans[0].overlaps(10, 12)


def test_diff_ignores_comments_but_preserves_string_values():
    old = {'rtl/a.sv': 'module top;\nassign y = a; // before\nendmodule\n'}
    new = {'rtl/a.sv': '// header\nmodule top;\nassign y=a; /* after */\nendmodule\n'}
    result = diff_sources(old, new)
    assert result['files'][0]['comment_or_format_only']
    result = diff_sources({'a.sv': 'localparam S="a b";'}, {'a.sv': 'localparam S="ab";'})
    assert result['files'][0]['hunks']


def test_macro_dependency_transitive_and_unused():
    base = {'defs.vh': '`define A 0\n`define B (`A)\n`define UNUSED 2\n',
            'top.sv': 'module top(output y);\nassign y=`B;\nendmodule\n'}
    new = {**base, 'defs.vh': base['defs.vh'].replace('A 0', 'A 1').replace('UNUSED 2', 'UNUSED 3')}
    result = diff_sources(base, new)
    assert result['changed_macros'] == ['A', 'UNUSED']
    assert result['dependent_macros'] == ['A', 'B', 'UNUSED']
    assert [(u['macro'], u['start']) for u in result['macro_uses']['new']] == [('B', 2)]


def test_external_empty_region_cannot_retain_unchecked_cells(tmp_path):
    b, n = graphs(tmp_path, design(), design({'op': cell('$or')}))
    plan = plan_regions(b, n, canonical_match(b, n), [], [])
    assert plan['base_cells'] == plan['new_cells'] == ['op']
    with pytest.raises(ValueError, match='unknown cells'):
        plan_regions(b, n, canonical_match(b, n), ['typo'], [])


def rtl_args(tmp_path, base, new, **options):
    case = tmp_path / 'benchmarks/cases/tiny'
    case.mkdir(parents=True)
    (case / 'design.yaml').write_text('top: top\nsource_dirs: [rtl]\ninclude_dirs: [rtl]\n')
    for version, sources in [('base', base), ('new', new)]:
        for filename, text in sources.items():
            path = tmp_path / 'results/tiny/work' / version / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    return argparse.Namespace(root=str(tmp_path), case='tiny', yosys=YOSYS, timeout=30,
                              topology_rounds=2, formal=False, frontend=True, replacement=None,
                              rtl_guided=True, seed_mode=options.get('seed_mode', 'hybrid'), max_expansions=2)


def read_analysis(tmp_path, name):
    return json.loads((tmp_path / 'results/tiny/analysis' / name).read_text())


@pytest.mark.skipif(not YOSYS, reason='Yosys required')
@pytest.mark.parametrize('before,after', [
    ('a ^ 4\'h0', 'a ^ 4\'h1'), ('a + b', 'a + c'),
    ('s ? a : b', '!s ? a : b'), ('a + 0', 'a'),
])
def test_rtl_combinational_ecos_verify(tmp_path, before, after):
    template = 'module top(input [3:0] a,b,c, input s, output [3:0] y);\nassign y = EXPR;\nendmodule\n'
    args = rtl_args(tmp_path, {'rtl/top.sv': template.replace('EXPR', before)},
                    {'rtl/top.sv': template.replace('EXPR', after)})
    assert run_case(args) == 0
    plan = read_analysis(tmp_path, 'region_plan.json')
    assert plan['rtl_guidance']['requires_full_new_netlist']
    assert read_analysis(tmp_path, 'rtl_changes.json')['files'][0]['hunks']


@pytest.mark.skipif(not YOSYS, reason='Yosys required')
@pytest.mark.parametrize('change', ['comment', 'dead_module', 'unused_macro', 'rename'])
def test_nonfunctional_rtl_edits_do_not_force_whole_design(tmp_path, change):
    top = '`include "defs.vh"\nmodule top(input a,b, output y);\nwire t;\nassign t=a&b;\nassign y=t;\nendmodule\n'
    base = {'rtl/top.sv': top, 'rtl/defs.vh': '`define UNUSED 0\n',
            'rtl/dead.sv': 'module dead(input a,b, output y); assign y=a&b; endmodule\n'}
    new = copy.deepcopy(base)
    if change == 'comment': new['rtl/top.sv'] = '// comment\n' + top
    if change == 'dead_module': new['rtl/dead.sv'] = new['rtl/dead.sv'].replace('a&b', 'a|b')
    if change == 'unused_macro': new['rtl/defs.vh'] = '`define UNUSED 1\n'
    if change == 'rename': new['rtl/top.sv'] = top.replace('wire t;', 'wire renamed;').replace('assign t=', 'assign renamed=').replace('=t;', '=renamed;')
    args = rtl_args(tmp_path, base, new, seed_mode='rtl')
    assert run_case(args) == 0
    impact = read_analysis(tmp_path, 'rtl_seed_cells.json')
    if change != 'rename':
        assert not impact['base_cells'] and not impact['new_cells']
        assert not read_analysis(tmp_path, 'region_plan.json')['new_cells']


@pytest.mark.skipif(not YOSYS, reason='Yosys required')
def test_active_macro_seeds_usage_and_excludes_other_sources(tmp_path):
    base = {'rtl/defs.vh': '`define MASK 0\n',
            'rtl/top.sv': '`include "defs.vh"\nmodule top(input a,output y);\nassign y=a ^ `MASK;\nendmodule\n',
            'other/uncompiled.sv': 'module unused; endmodule\n'}
    new = {**base, 'rtl/defs.vh': '`define MASK 1\n', 'other/uncompiled.sv': '// changed\nmodule unused; endmodule\n'}
    assert run_case(rtl_args(tmp_path, base, new)) == 0
    diff = read_analysis(tmp_path, 'rtl_changes.json')
    assert diff['excluded_changed_files'] == ['other/uncompiled.sv']
    reasons = [r for c in read_analysis(tmp_path, 'rtl_seed_cells.json')['new_cells'].values() for r in c['reasons']]
    assert any(r.get('macro') == 'MASK' and r['cause'] == 'macro_dependency'
               and r['type'] == 'rtlil_origin_overlap' for r in reasons)


@pytest.mark.skipif(not YOSYS, reason='Yosys required')
def test_one_instance_rewire_keeps_sibling_out_of_seeds(tmp_path):
    top = '''module top(input a,b,c, output y,z);
leaf left(.a(a), .b(b), .y(y));
leaf right(.a(a), .b(b), .y(z));
endmodule
'''
    leaf = 'module leaf(input a,b,output y);\nassign y=a&b;\nendmodule\n'
    base = {'rtl/top.sv': top, 'rtl/leaf.sv': leaf}
    new = {**base, 'rtl/top.sv': top.replace('left(.a(a)', 'left(.a(c)')}
    assert run_case(rtl_args(tmp_path, base, new)) == 0
    index = read_analysis(tmp_path, 'source_index_new.json')
    seeds = read_analysis(tmp_path, 'rtl_seed_cells.json')['new_cells']
    assert seeds and all(index['cells'][n]['instance'] == 'left' for n in seeds)
    assert {i['path'] for i in index['instances']} == {'', 'left', 'right'}
    assert any(c['rtlil_object_hints'] for c in index['cells'].values())


@pytest.mark.skipif(not YOSYS, reason='Yosys required')
@pytest.mark.parametrize('before,after', [
    ('parameter P=0', 'parameter P=1'),
    ('wire signed [3:0] t=a;', 'wire [3:0] t=a;'),
])
def test_parameter_generate_and_signedness_dependencies(tmp_path, before, after):
    if 'parameter' in before:
        top = 'module top(input a,b,output y);\nparameter P=0;\ngenerate if(P) assign y=a|b; else assign y=a&b; endgenerate\nendmodule\n'
    else:
        top = 'module top(input [3:0] a,input [1:0] s,output [3:0] y);\nwire signed [3:0] t=a;\nassign y=t >>> s;\nendmodule\n'
    assert run_case(rtl_args(tmp_path, {'rtl/top.sv': top}, {'rtl/top.sv': top.replace(before, after)})) == 0
    assert any('context_dependency' == r['type'] for c in read_analysis(tmp_path, 'rtl_seed_cells.json')['new_cells'].values()
               for r in c['reasons'])


@pytest.mark.skipif(not YOSYS, reason='Yosys required')
@pytest.mark.parametrize('edit', ['enable', 'reset', 'width'])
def test_state_control_or_interface_change_stops(tmp_path, edit):
    top = 'module top(input clk,rst,en,mode,d,output reg q);\nwire go=en&mode;\nalways @(posedge clk or posedge rst) if(rst) q<=0; else if(go) q<=d;\nendmodule\n'
    new = top.replace('en&mode', 'en|mode') if edit == 'enable' else top.replace('posedge rst', 'negedge rst').replace('if(rst)', 'if(!rst)') if edit == 'reset' else top.replace('output reg q', 'output reg [1:0] q')
    assert run_case(rtl_args(tmp_path, {'rtl/top.sv': top}, {'rtl/top.sv': new})) != 0
    plan = read_analysis(tmp_path, 'region_plan.json')
    assert not plan['stitchable']


@pytest.mark.skipif(not YOSYS, reason='Yosys required')
@pytest.mark.parametrize('change', ['rtl', 'config', 'netlist', 'new_source'])
def test_stale_rtl_is_not_used_with_cached_netlist(tmp_path, change):
    top = 'module top(input a,b,output y); assign y=a&b; endmodule\n'
    args = rtl_args(tmp_path, {'rtl/top.sv': top}, {'rtl/top.sv': top})
    assert run_case(args) == 0
    args.frontend = False
    if change == 'rtl':
        (tmp_path / 'results/tiny/work/new/rtl/top.sv').write_text(top.replace('a&b', 'a|b'))
    elif change == 'config':
        path = tmp_path / 'benchmarks/cases/tiny/design.yaml'
        path.write_text(path.read_text() + '\n# changed configuration snapshot\n')
    elif change == 'netlist':
        path = tmp_path / 'results/tiny/new/design_flat.json'
        data = json.loads(path.read_text())
        data['modules']['top']['attributes']['test'] = '1'
        path.write_text(json.dumps(data))
    else:
        (tmp_path / 'results/tiny/work/new/rtl/added.v').write_text('module added; endmodule\n')
    assert run_case(args) != 0
    report = json.loads((tmp_path / 'results/tiny/incremental/run_report.json').read_text())
    assert not report['verified'] and 'stale' in report['error']


@pytest.mark.skipif(not YOSYS, reason='Yosys required')
@pytest.mark.parametrize('limit,success', [(0, False), (2, True)])
def test_real_failed_proof_expands_and_retries(tmp_path, monkeypatch, limit, success):
    # Deliberately inject an invalid reuse candidate. The actual SAT check must
    # reject it, then replacing the named failing cone must repair the design.
    import run_incremental
    def invalid_match(base, new, **kwargs):
        return {'matches': [{'base': 'op', 'new': 'op', 'reuse': True}], 'connection_changes': []}
    monkeypatch.setattr(run_incremental, 'canonical_match', invalid_match)
    args = case_args(tmp_path, design({'op': cell('$and', 2, 3)}), design({'op': cell('$or', 2, 3)}), YOSYS)
    args.max_expansions = limit
    assert (run_case(args) == 0) == success
    report = json.loads((tmp_path / 'results/tiny/incremental/run_report.json').read_text())
    assert report['verified'] == success
    assert report['attempts'][0]['verification']['status'] == 'unknown'
    assert report['attempts'][0]['verification']['unproven_points']
    if success:
        assert report['expansion_rounds'] == 1 and len(report['attempts']) == 2
        assert report['attempts'][1]['region_cells'] == {'base': 1, 'new': 1}


def test_timeout_and_state_only_failure_cannot_expand(tmp_path):
    b, n = graphs(tmp_path, register(), register())
    plan = plan_regions(b, n, canonical_match(b, n))
    assert expansion_from_failure(b, n, plan, {'status': 'unknown', 'unproven_points': []}) is None
    assert expansion_from_failure(b, n, plan, {'status': 'unknown', 'unproven_points': [
        {'kind': 'state_input', 'base_cell': 'ff', 'new_cell': 'ff', 'new_bits': [2]}]}) is None



def test_state_pin_tracing_precedes_conflicting_structural_hint(tmp_path, monkeypatch):
    import canonical_matcher
    def bank(offset, bit_offset):
        data = design({}, {'y': 4 + bit_offset, 'z': 5 + bit_offset})
        mod = data['modules']['top']
        for i in range(2):
            ff = copy.deepcopy(register()['modules']['top']['cells']['ff'])
            ff['type'] = '$adffe'
            ff['parameters']['EN_POLARITY'] = '1'
            ff['port_directions']['EN'] = 'input'
            ff['connections'].update(Q=[4 + i + bit_offset], EN=[6 + i + bit_offset])
            mod['cells'][f'ff{i}'] = ff
            driver = cell(a=2 + i, y=6 + i + bit_offset)
            driver['attributes']['scopename'] = 'bank'
            mod['cells'][f'$auto$enable${offset+i}'] = driver
            mod['netnames'][f'q{i}'] = {'bits': [4+i+bit_offset], 'hide_name': 0}
        return data
    b, n = graphs(tmp_path, bank(1, 0), bank(0, 100))
    monkeypatch.setattr(canonical_matcher, '_candidate_match', lambda *args: {'matches': [
        {'base': '$auto$enable$1', 'new': '$auto$enable$1'},
        {'base': '$auto$enable$2', 'new': '$auto$enable$0'}]})
    match = canonical_match(b, n)
    mapping = {p['base']: p['new'] for p in match['matches']}
    assert mapping['$auto$enable$1'] == '$auto$enable$0'
    assert mapping['$auto$enable$2'] == '$auto$enable$1'
    assert len(mapping) == 4
