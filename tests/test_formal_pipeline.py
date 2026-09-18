import argparse
import copy
import json
from pathlib import Path
import pytest
from test_correctness import ROOT, YOSYS, cell, design, graphs, register
from run_incremental import _write_local_synthesis
from canonical_matcher import canonical_match
from formal.build_match_problem import design_digest
from formal.run_matching import run_matching
from formal.merge_matches import merge_matches
from formal.verify_transition import write_transition_verification
from formal.build_base_correspondence import discover
from yosys_runner import run_yosys
import sys
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'scripts' / 'synthesis'))
from run_incremental_case import run_case
from run_frontend import generate


def case_args(tmp_path, bd, nd, executable):
    case = tmp_path / 'benchmarks/cases/tiny'
    case.mkdir(parents=True)
    (case / 'design.yaml').write_text('top: top\nsource_dirs: [rtl]\n')
    for version, data in [('base', bd), ('new', nd)]:
        out = tmp_path / 'results/tiny' / version
        out.mkdir(parents=True)
        (out / 'design_flat.json').write_text(json.dumps(data))
    return argparse.Namespace(root=str(tmp_path), case='tiny', yosys=executable, timeout=120,
                              topology_rounds=2, formal=False, frontend=False, replacement=None)


def test_missing_solver_cannot_succeed(tmp_path):
    args = case_args(tmp_path, design(), design(), 'not-a-real-yosys-command')
    assert run_case(args) != 0
    report = json.loads((tmp_path / 'results/tiny/incremental/run_report.json').read_text())
    assert report['status'] == 'failed' and not report['verified']


def test_unknown_proof_and_stale_report_not_merged(tmp_path):
    b, n = graphs(tmp_path, design(), design({'op': cell(b='1')}))
    match = canonical_match(b, n)
    report = {'design_digest': design_digest(b, n), 'jobs': [
        {'base': 'op', 'new': 'op', 'proof_kind': 'local_cell', 'status': 'unknown'}]}
    assert merge_matches(b, n, match, report)['matches'] == []
    report['design_digest'] = 'stale'
    with pytest.raises(ValueError, match='different input designs'):
        merge_matches(b, n, match, report)


@pytest.mark.skipif(not YOSYS, reason='actual Yosys proof required')
@pytest.mark.parametrize('new_type,status', [('$mul', 'proven'), ('$or', 'disproven')])
def test_formal_executes_and_merges(tmp_path, new_type, status):
    b, n = graphs(tmp_path, design({'op': cell('$and', 2, 3)}), design({'op': cell(new_type, 2, 3)}))
    match = canonical_match(b, n)
    assert match['formal_candidates'][0]['candidates'][0]['new'] == 'op'
    report = run_matching(b, n, match, tmp_path / 'formal', YOSYS)
    assert report['jobs'][0]['status'] == status
    merged = merge_matches(b, n, match, report)
    assert bool(merged['matches']) == (status == 'proven')


@pytest.mark.skipif(not YOSYS, reason='actual Yosys proof required')
def test_whole_case_and_failure_detection(tmp_path):
    args = case_args(tmp_path, register(), register('renamed', d=3), YOSYS)
    assert run_case(args) == 0
    report = json.loads((tmp_path / 'results/tiny/incremental/run_report.json').read_text())
    assert report['verified']
    # A structurally valid but functionally wrong D connection must fail.
    out = tmp_path / 'bad.ys'
    write_transition_verification(out, register(), register('renamed', d=3), 'top', {'ff': 'renamed'})
    assert run_yosys(out, tmp_path / 'bad.log', YOSYS)['status'] != 'passed'


@pytest.mark.skipif(not YOSYS, reason='actual Yosys proof required')
def test_internal_correspondence_must_be_proven(tmp_path):
    out = tmp_path / 'wrong_internal.ys'
    write_transition_verification(out, design({'op': cell('$and', 2, 3)}),
                                  design({'op': cell('$or', 2, 3)}), 'top', {'op': 'op'})
    result = run_yosys(out, tmp_path / 'wrong_internal.log', YOSYS)
    assert result['status'] != 'passed'


@pytest.mark.skipif(not YOSYS, reason='actual Yosys frontend required')
def test_reproducible_frontend_mapping_and_boundaries(tmp_path):
    args = case_args(tmp_path, design(), design(), YOSYS)
    work = tmp_path / 'results/tiny/work/base/rtl'
    work.mkdir(parents=True)
    (work / 'top.v').write_text('module top(input a,b, output y); wire t; assign t=a&b; assign y=t; endmodule\n')
    result = generate(tmp_path, 'tiny', 'base', YOSYS, mapped=True)
    assert result['status'] == 'passed'
    out = tmp_path / 'results/tiny/base'
    for name in ['design_flat.json', 'design_flat.rtlil', 'design_flat.v', 'mapped.json', 'mapped.v']:
        assert (out / name).is_file()
    report = discover(__import__('netlist_graph').NetlistGraph(out / 'design_flat.json', 'top'),
                      __import__('netlist_graph').NetlistGraph(out / 'mapped.json', 'top'),
                      tmp_path / 'boundaries', YOSYS)
    assert any(b['name'] == 't' and b['status'] == 'proven' for b in report['boundaries'])


@pytest.mark.skipif(not YOSYS, reason='actual Yosys undef proof required')
@pytest.mark.parametrize('candidate,reference,passed', [
    ('0', 'x', True), ('1', 'x', True), ('x', 'x', True),
    ('x', '0', False), ('x', '1', False),
    ('1', '0', False), ('0', '1', False),
])
def test_transition_undef_refinement_is_directional(tmp_path, candidate, reference, passed):
    script = tmp_path / 'undef.ys'
    write_transition_verification(script, design({}, {'y': candidate}),
                                  design({}, {'y': reference}), 'top', {})
    result = run_yosys(script, tmp_path / 'undef.log', YOSYS)
    assert (result['status'] == 'passed') == passed


@pytest.mark.skipif(not YOSYS, reason='actual Yosys undef proof required')
@pytest.mark.parametrize('candidate,passed', [('0', True), ('1', False), ('x', False)])
def test_transition_conditional_undef_preserves_defined_branch(tmp_path, candidate, passed):
    mux = {'type': '$mux', 'parameters': {'WIDTH': '1'}, 'attributes': {},
           'port_directions': {'A': 'input', 'B': 'input', 'S': 'input', 'Y': 'output'},
           'connections': {'A': ['0'], 'B': ['x'], 'S': [2], 'Y': [4]}}
    script = tmp_path / 'conditional.ys'
    write_transition_verification(script, design({}, {'y': candidate}),
                                  design({'mux': mux}), 'top', {})
    result = run_yosys(script, tmp_path / 'conditional.log', YOSYS)
    assert (result['status'] == 'passed') == passed


@pytest.mark.skipif(not YOSYS, reason='actual Yosys synthesis and undef proof required')
def test_synthesized_undef_branch_preserves_downstream_decode(tmp_path):
    # A reduced eco-002 cone. opt -full (even with -keepdc) in the proof
    # rewrites the reference mux tree and spuriously rejects this synthesis.
    def logic(kind, connections, **parameters):
        return {'type': kind, 'parameters': parameters, 'attributes': {},
                'port_directions': {p: 'output' if p == 'Y' else 'input' for p in connections},
                'connections': connections}

    reference = {'attributes': {}, 'netnames': {}, 'ports': {
        **{f'i{i}': {'direction': 'input', 'bits': [i + 2]} for i in range(5)},
        'sel': {'direction': 'output', 'bits': [12, 13]}}, 'cells': {
        'branch': logic('$pmux', {'A': ['x'], 'B': ['0', '1'], 'S': [2, 3], 'Y': [10]},
                        WIDTH=1, S_WIDTH=2),
        'branch_select': logic('$mux', {'A': ['0', '0'], 'B': ['1', '0'], 'S': [10], 'Y': [11, 14]},
                               WIDTH=2),
        'nextpc': logic('$pmux', {'A': ['0', '0'], 'B': ['1', '0', '0', '1', 11, 14],
                                 'S': [4, 5, 6], 'Y': [12, 13]}, WIDTH=2, S_WIDTH=3)}}
    source, mapped = tmp_path / 'region.json', tmp_path / 'mapped.json'
    source.write_text(json.dumps({'modules': {'incremental_region': reference}}))
    _write_local_synthesis(tmp_path / 'synth.ys', source, mapped)
    assert run_yosys(tmp_path / 'synth.ys', tmp_path / 'synth.log', YOSYS)['status'] == 'passed'
    candidate = json.loads(mapped.read_text())['modules']['incremental_region']
    # Stitch the synthesized region into the unchanged downstream decoder.
    for mod in (reference, candidate):
        selector = mod['ports'].pop('sel')['bits']
        output = 1 + max(bit for c in mod['cells'].values() for bits in c['connections'].values()
                         for bit in bits if isinstance(bit, int))
        mod['cells']['decode'] = logic('$eq', {'A': selector, 'B': ['1', '0'], 'Y': [output]},
                                      A_WIDTH=2, B_WIDTH=2, Y_WIDTH=1, A_SIGNED=0, B_SIGNED=0)
        mod['ports']['y'] = {'direction': 'output', 'bits': [output]}
    script = tmp_path / 'proof.ys'
    write_transition_verification(script, {'modules': {'top': candidate}},
                                  {'modules': {'top': reference}}, 'top', {'decode': 'decode'})
    assert run_yosys(script, tmp_path / 'proof.log', YOSYS)['status'] == 'passed'
