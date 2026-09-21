"""Native pre-proc extraction integration tests; legacy full-New flow is not used."""
import json
from pathlib import Path
import shutil
import sys

import pytest
from test_rtl_guided import rtl_args
from test_correctness import ROOT
from run_incremental_case import run_case

sys.path.insert(0, str(ROOT / 'scripts/synthesis'))
from build_eco_plugin import build

NATIVE = shutil.which('yosys')
SDK = NATIVE and Path(NATIVE).with_name('yosys-config').exists()
pytestmark = pytest.mark.skipif(not SDK, reason='native Yosys with matching plugin SDK required')


@pytest.fixture(scope='module', autouse=True)
def compiled_plugin():
    if SDK:
        return build(NATIVE)


def args_for(tmp_path, old, new, **kwargs):
    old = {'rtl/top.sv': old} if isinstance(old, str) else old
    new = {'rtl/top.sv': new} if isinstance(new, str) else new
    args = rtl_args(tmp_path, old, new)
    args.rtl_direct = True
    args.rtl_guided = False
    args.seed_mode = None
    args.yosys = NATIVE
    args.max_expansions = 6
    for k, v in kwargs.items(): setattr(args, k, v)
    return args


def output(tmp_path, name='run_report.json'):
    return json.loads((tmp_path / 'results/tiny/incremental' / name).read_text())


def passed(tmp_path, args):
    assert run_case(args) == 0, output(tmp_path)
    report = output(tmp_path)
    assert report['verified'] and not report['requires_full_new_netlist']
    assert output(tmp_path, 'verification.json')['status'] == 'passed'
    assert not (tmp_path / 'results/tiny/new/design_flat.json').exists()
    script = (tmp_path / 'results/tiny/new/rtl_direct/elaborate.ys').read_text().splitlines()
    assert not any(s.startswith(('proc', 'flatten', 'opt', 'write_json')) for s in script)
    out = tmp_path / 'results/tiny/incremental'
    for name in ('new_region_preproc.rtlil', 'new_region_synth.json', 'stitched.json'):
        assert (out / name).is_file()
    return report


def test_assign_only_replaces_affected_output(tmp_path):
    old = 'module top(input a,b,c,output y,z);\nassign y=a&b;\nassign z=b^c;\nendmodule\n'
    report = passed(tmp_path, args_for(tmp_path, old, old.replace('a&b', 'a|b')))
    assert report['replaced_base_cells'] == 1 and report['retained_base_cells'] == 1
    assert output(tmp_path, 'extraction.json')['atomic_processes'] == []


def test_whole_process_preserves_defaults_nested_case_and_overrides(tmp_path):
    old = '''module top(input [1:0] a,b,c, input s,t, output logic [1:0] y, output z);
always_comb begin
 y=a;
 if(s) begin
   case(b)
     0: y=b;
     1: y=c;
     default: y=a^b;
   endcase
 end
 if(t) y=c;
end
assign z=s^t;
endmodule
'''
    report = passed(tmp_path, args_for(tmp_path, old, old.replace('1: y=c', '1: y=a|c')))
    assert report['retained_base_cells'] >= 1
    extraction = output(tmp_path, 'extraction.json')
    assert len(extraction['atomic_processes']) == 1
    preproc = (tmp_path / 'results/tiny/incremental/new_region_preproc.rtlil').read_text()
    assert 'process ' in preproc and 'switch ' in preproc and 'sync always' in preproc


def test_macro_change_expands_to_expression(tmp_path):
    old = {'rtl/top.sv': '`include "defs.vh"\nmodule top(input a,b,output y);\nassign y=`OP;\nendmodule\n',
           'rtl/defs.vh': '`define OP (a&b)\n'}
    new = {**old, 'rtl/defs.vh': '`define OP (a|b)\n'}
    passed(tmp_path, args_for(tmp_path, old, new))
    assert output(tmp_path, 'rtl_changes.json')['changed_macros'] == ['OP']


def test_new_dependency_uses_predeclared_stable_input(tmp_path):
    old = 'module top(input a,b,enable,output y);\nassign y=a&b;\nendmodule\n'
    passed(tmp_path, args_for(tmp_path, old, old.replace('a&b', '(a&b)|enable')))
    inputs = output(tmp_path, 'region_request.json')['inputs']
    assert any(x['signal'] == 'enable' for x in inputs)


def test_parent_promotion_for_changed_child_interface(tmp_path):
    top = 'module top(input a,b,c,output y);\nleaf u(.a(a),.b(b),.y(y));\nendmodule\n'
    old = {'rtl/top.sv': top, 'rtl/leaf.sv': 'module leaf(input a,b,output y);\nassign y=a&b;\nendmodule\n'}
    new = {'rtl/top.sv': top.replace('.b(b)', '.b(b),.c(c)'),
           'rtl/leaf.sv': old['rtl/leaf.sv'].replace('input a,b', 'input a,b,c').replace('a&b', '(a&b)|c')}
    report = passed(tmp_path, args_for(tmp_path, old, new))
    assert report['selected_instance'] == []


def test_uniquified_instances_and_rewire(tmp_path):
    old = '''module leaf(input a,b,output y);
assign y=a&b;
endmodule
module top(input a,b,c,output y,z);
leaf left(.a(a),.b(b),.y(y));
leaf right(.a(c),.b(b),.y(z));
endmodule
'''
    passed(tmp_path, args_for(tmp_path, old, old.replace('left(.a(a)', 'left(.a(c)')))
    index = json.loads((tmp_path / 'results/tiny/new/rtl_direct/source_index.json').read_text())
    modules = {tuple(i['path']): i['module'] for i in index['instances']}
    assert modules[('left',)] != modules[('right',)]


@pytest.mark.parametrize('new', [
    'module top(input a,b,s,output reg y);\nalways @* if(s) y=a|b;\nendmodule\n',
    'module top(input a,b,s,output reg y);\nalways @(posedge s) y=a|b;\nendmodule\n',
    'module top(input a,b,s,output [1:0] y);\nassign y={a,b};\nendmodule\n',
])
def test_unsupported_latch_state_or_top_interface_never_passes(tmp_path, new):
    old = 'module top(input a,b,s,output y);\nassign y=s?(a&b):b;\nendmodule\n'
    assert run_case(args_for(tmp_path, old, new)) != 0
    assert not output(tmp_path)['verified']
    assert output(tmp_path, 'verification.json')['status'] != 'passed'


def test_offline_base_setup_does_not_require_new_rtl(tmp_path):
    old = 'module top(input a,b,output y);\nassign y=a&b;\nendmodule\n'
    args = args_for(tmp_path, old, {})
    args.setup_base_only = True
    assert run_case(args) == 0
    assert output(tmp_path)['status'] == 'setup_complete'
    assert not (tmp_path / 'results/tiny/new').exists()
    p = tmp_path / 'results/tiny/work/new/rtl/top.sv'
    p.parent.mkdir(parents=True)
    p.write_text(old.replace('a&b', 'a|b'))
    args.setup_base_only = False
    args.frontend = False
    passed(tmp_path, args)
    assert output(tmp_path)['stages']['base_setup']['cached']


@pytest.mark.parametrize('change', ['rtl', 'config', 'artifact', 'certificate'])
def test_stale_base_contract_is_rejected(tmp_path, change):
    old = 'module top(input a,b,output y);\nassign y=a&b;\nendmodule\n'
    args = args_for(tmp_path, old, old.replace('a&b', 'a|b'))
    passed(tmp_path, args)
    base = tmp_path / 'results/tiny/base/rtl_direct'
    target = {'rtl': tmp_path / 'results/tiny/work/base/rtl/top.sv',
              'config': tmp_path / 'benchmarks/cases/tiny/design.yaml',
              'artifact': base / 'design_flat.json',
              'certificate': next((base / 'contracts').glob('*/contract.json'))}[change]
    target.write_text(target.read_text() + '\n ')
    args.frontend = False
    assert run_case(args) != 0
    assert 'stale' in output(tmp_path)['error']
    assert output(tmp_path, 'verification.json')['status'] == 'not_run'


def test_poison_full_new_netlist_is_ignored(tmp_path):
    old = 'module top(input a,b,output y);\nassign y=a&b;\nendmodule\n'
    args = args_for(tmp_path, old, old.replace('a&b', 'a|b'))
    poison = tmp_path / 'results/tiny/new/design_flat.json'
    poison.parent.mkdir(parents=True)
    poison.write_text('THIS IS NOT A NETLIST')
    assert run_case(args) == 0, output(tmp_path)
    assert poison.read_text() == 'THIS IS NOT A NETLIST'


def test_changed_state_enable_is_unsupported(tmp_path):
    old = '''module top(input clk,a,b,d,output reg q);
wire en;
assign en=a&b;
always @(posedge clk) if(en) q<=d;
endmodule
'''
    assert run_case(args_for(tmp_path, old, old.replace('a&b', 'a|b'))) != 0
    assert not output(tmp_path)['verified']


def test_unchanged_register_and_changed_d_cone(tmp_path):
    old = '''module comb(input a,b,output y);
assign y=a&b;
endmodule
module top(input clk,a,b,output reg q);
wire d;
comb u(a,b,d);
always @(posedge clk) q<=d;
endmodule
'''
    report = passed(tmp_path, args_for(tmp_path, old, old.replace('a&b', 'a|b')))
    assert report['selected_instance'] == ['u']
    assert report['retained_base_cells'] == 1


def test_alias_split_at_top_outputs(tmp_path):
    old = 'module top(input a,b,output y,z);\nassign y=a&b;\nassign z=a&b;\nendmodule\n'
    new = old.replace('assign y=a&b', 'assign y=a|b')
    passed(tmp_path, args_for(tmp_path, old, new))


def test_semantic_diff_ignores_comments_and_autoidx(tmp_path):
    old = '''module top(input a,b,c,output logic y,output z);
always_comb y=a&b;
assign z=b^c;
endmodule
'''
    args = args_for(tmp_path, old, '// shifted source locations\n' + old)
    assert run_case(args) == 0, output(tmp_path)
    assert output(tmp_path)['replaced_base_cells'] == 0
    assert output(tmp_path, 'rtl_selection.json')['hits'] == []
    assert not (tmp_path / 'results/tiny/new/design_flat.json').exists()


def test_semantic_diff_finds_assignment_without_statement_src(tmp_path):
    old = '''module top(input a,b,c,output logic y,output z);
always_comb begin
 y=a;
end
assign z=b^c;
endmodule
'''
    report = passed(tmp_path, args_for(tmp_path, old, old.replace('y=a;', 'y=b;')))
    assert report['retained_base_cells'] == 1
    assert len(output(tmp_path, 'extraction.json')['atomic_processes']) == 1
    assert output(tmp_path, 'rtl_selection.json')['hits'][0]['cause'].startswith('pre-proc')


def test_new_dependency_absorbs_its_local_producer(tmp_path):
    old = '''module top(input a,b,c,output y,z);
wire enable;
assign enable=b^c;
assign y=a&b;
assign z=~c;
endmodule
'''
    passed(tmp_path, args_for(tmp_path, old, old.replace('y=a&b;', 'y=(a&b)|enable;')))
    replacement = output(tmp_path, 'new_region_synth.json')['modules']['incremental_region']
    assert {'$xor', '$or'} <= {c['type'] for c in replacement['cells'].values()}


def test_final_proof_rejects_corrupted_stitch(tmp_path, monkeypatch):
    import run_rtl_direct_case as direct
    original = direct.stitch_region
    def corrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        result['modules']['top']['ports']['y']['bits'] = ['0']
        result['modules']['top']['netnames']['y']['bits'] = ['0']
        return result
    monkeypatch.setattr(direct, 'stitch_region', corrupt)
    old = 'module top(input a,b,output y);\nassign y=a&b;\nendmodule\n'
    assert run_case(args_for(tmp_path, old, old.replace('a&b', 'a|b'))) != 0
    assert not output(tmp_path)['verified']
    assert output(tmp_path, 'verification.json')['status'] != 'passed'
    assert not (tmp_path / 'results/tiny/incremental/stitched.json').exists()


def test_region_proof_preserves_defined_results_with_reference_x(tmp_path):
    old = "module top(input a,b,s,output logic y);\nalways_comb if(s) y=a&b; else y=1'bx;\nendmodule\n"
    passed(tmp_path, args_for(tmp_path, old, old.replace('a&b', 'a|b')))
    assert output(tmp_path, 'local_verification.json')['status'] == 'passed'


def test_signed_nonzero_ascending_boundary_layout(tmp_path):
    old = "module top(input signed [4:7] a,b,output signed [4:7] y);\nassign y=a+b;\nendmodule\n"
    passed(tmp_path, args_for(tmp_path, old, old.replace('a+b', 'a-b')))
    request = output(tmp_path, 'region_request.json')
    assert all(i['signed'] and i['upto'] and i['start_offset'] == 4 for i in request['inputs'])
