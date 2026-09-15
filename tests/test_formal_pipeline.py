import argparse
import copy
import json
from pathlib import Path
import pytest
from test_correctness import ROOT, YOSYS, cell, design, graphs, register
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
    for name in ['frontend_flat.json', 'frontend_flat.rtlil', 'design_flat.json', 'mapped.json', 'mapped.v']:
        assert (out / name).is_file()
    report = discover(__import__('netlist_graph').NetlistGraph(out / 'frontend_flat.json', 'top'),
                      __import__('netlist_graph').NetlistGraph(out / 'mapped.json', 'top'),
                      tmp_path / 'boundaries', YOSYS)
    assert any(b['name'] == 't' and b['status'] == 'proven' for b in report['boundaries'])
