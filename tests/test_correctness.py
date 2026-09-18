import copy
import json
import shutil
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis'))
sys.path.insert(0, str(ROOT))
from canonical_matcher import canonical_match
from netlist_graph import NetlistGraph
from region import plan_regions
from region_netlist import extract_region, stitch_region, _module_max_bit
from structure_check import check_module
from run_incremental import _write_local_synthesis, _write_equivalence
from yosys_runner import run_yosys

YOSYS = shutil.which('yosys') or shutil.which('yowasp-yosys')


def cell(kind='$xor', a=2, b='0', y=4):
    return {'type': kind, 'parameters': {'A_SIGNED': '0', 'B_SIGNED': '0',
            'A_WIDTH': '1', 'B_WIDTH': '1', 'Y_WIDTH': '1'},
            'attributes': {'src': 'rtl/top.sv:3.1-3.20', 'scopename': 'logic'},
            'port_directions': {'A': 'input', 'B': 'input', 'Y': 'output'},
            'connections': {'A': [a], 'B': [b], 'Y': [y]}}


def design(cells=None, outputs=None):
    return {'modules': {'top': {'attributes': {'top': '1'}, 'ports': {
        'a': {'direction': 'input', 'bits': [2]}, 'b': {'direction': 'input', 'bits': [3]},
        **{n: {'direction': 'output', 'bits': [b]} for n, b in (outputs or {'y': 4}).items()}},
        'cells': {'op': cell()} if cells is None else cells, 'netnames': {}}}}


def graphs(tmp_path, bd, nd):
    result = []
    for name, data in [('base', bd), ('new', nd)]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(data))
        result.append(NetlistGraph(path, 'top'))
    return result


def flow(tmp_path, bd, nd):
    base, new = graphs(tmp_path, bd, nd)
    match = canonical_match(base, new)
    plan = plan_regions(base, new, match)
    assert plan['stitchable'], plan
    replacement = extract_region(new, plan['new_cells'], plan['new_boundary'])
    return plan, replacement, stitch_region(bd, plan, replacement)


@pytest.mark.parametrize('pin,value', [('B', '1'), ('A', 3)])
def test_same_source_change_is_not_reused(tmp_path, pin, value):
    bd, nd = design(), design()
    nd['modules']['top']['cells']['op']['connections'][pin] = [value]
    plan, _, stitched = flow(tmp_path, bd, nd)
    assert plan['base_cells'] == ['op']
    assert next(iter(stitched['modules']['top']['cells'].values()))['connections'][pin] == [value]


@pytest.mark.parametrize('old,new', [(2, 3), ('0', '1'), (2, '0'), ('0', 2)])
def test_cell_free_output_rewire(tmp_path, old, new):
    _, _, result = flow(tmp_path, design({}, {'y': old}), design({}, {'y': new}))
    assert result['modules']['top']['ports']['y']['bits'] == [new]


@pytest.mark.parametrize('value', ['0', 2])
def test_optimized_constant_and_input_alias(tmp_path, value):
    bd, nd = design(), design({'op': cell('$and', b='0')})
    plan, replacement, _ = flow(tmp_path, bd, nd)
    mod = replacement['modules']['incremental_region']
    mod['cells'] = {}
    mod['ports']['region_out_0000']['bits'] = [value]
    result = stitch_region(bd, plan, replacement)
    assert result['modules']['top']['ports']['y']['bits'] == [value]
    assert result['modules']['top']['netnames']['y']['bits'] == [value]


def test_shared_outputs_and_split_alias(tmp_path):
    bd = design({}, {'y': 2, 'z': 2})
    nd = design({}, {'y': 2, 'z': 3})
    _, _, result = flow(tmp_path, bd, nd)
    assert result['modules']['top']['ports']['y']['bits'] == [2]
    assert result['modules']['top']['ports']['z']['bits'] == [3]
    b, _ = graphs(tmp_path, bd, nd)
    assert b.output_aliases['2'] == ['y[0]', 'z[0]']


def test_merged_region_outputs(tmp_path):
    bd = design({'op': cell(y=4), 'op2': cell(a=3, y=5)}, {'y': 4, 'z': 5})
    nd = design({'op': cell('$and', b='1', y=4)}, {'y': 4, 'z': 4})
    plan, replacement, _ = flow(tmp_path, bd, nd)
    mod = replacement['modules']['incremental_region']
    mod['cells'] = {}
    for port in mod['ports'].values():
        if port['direction'] == 'output':
            port['bits'] = [2]
    result = stitch_region(bd, plan, replacement)
    assert result['modules']['top']['ports']['y']['bits'] == [2]
    assert result['modules']['top']['ports']['z']['bits'] == [2]


def register(name='ff', reset=3, d=2):
    c = {'type': '$adff', 'parameters': {'WIDTH': '1', 'CLK_POLARITY': '1',
         'ARST_POLARITY': '1', 'ARST_VALUE': '0'}, 'attributes': {},
         'port_directions': {'CLK': 'input', 'ARST': 'input', 'D': 'input', 'Q': 'output'},
         'connections': {'CLK': [2], 'ARST': [reset], 'D': [d], 'Q': [4]}}
    data = design({name: c})
    data['modules']['top']['netnames']['state'] = {'hide_name': 0, 'bits': [4], 'attributes': {'init': '0'}}
    return data


def test_renamed_register_and_changed_d(tmp_path):
    plan, _, result = flow(tmp_path, register(), register('renamed', d=3))
    assert plan['retained_pairs'] == {'ff': 'renamed'}
    assert result['modules']['top']['cells']['ff']['connections']['D'] == [3]


@pytest.mark.parametrize('change', ['reset', 'init'])
def test_state_semantics_change_rejected(tmp_path, change):
    bd, nd = register(), register('renamed', reset=2 if change == 'reset' else 3)
    if change == 'init':
        nd['modules']['top']['netnames']['state']['attributes']['init'] = '1'
    b, n = graphs(tmp_path, bd, nd)
    assert not plan_regions(b, n, canonical_match(b, n))['stitchable']


def test_top_interface_change_rejected(tmp_path):
    bd, nd = design(), design()
    nd['modules']['top']['ports']['a']['bits'].append(9)
    b, n = graphs(tmp_path, bd, nd)
    assert not plan_regions(b, n, canonical_match(b, n))['stitchable']


def test_mux_pin_order_is_not_commutative(tmp_path):
    c = {'type': '$mux', 'parameters': {'WIDTH': '1'}, 'attributes': {},
         'port_directions': {'A': 'input', 'B': 'input', 'S': 'input', 'Y': 'output'},
         'connections': {'A': [2], 'B': [3], 'S': [2], 'Y': [4]}}
    nc = copy.deepcopy(c)
    nc['connections']['A'], nc['connections']['B'] = [3], [2]
    b, n = graphs(tmp_path, design({'op': c}), design({'op': nc}))
    assert not canonical_match(b, n)['matches']


def test_shared_logic_survives_one_changed_sink(tmp_path):
    bd = design({'shared': cell('$and', 2, 3, 4), 'tail': cell('$xor', 4, '0', 5)}, {'y': 4, 'z': 5})
    nd = copy.deepcopy(bd)
    nd['modules']['top']['cells']['tail']['connections']['B'] = ['1']
    plan, _, result = flow(tmp_path, bd, nd)
    assert plan['base_cells'] == ['tail']
    assert result['modules']['top']['cells']['shared'] == bd['modules']['top']['cells']['shared']


@pytest.mark.parametrize('kind,reused', [('$add', True), ('$sub', False), ('$shl', False)])
def test_operand_order(tmp_path, kind, reused):
    b, n = graphs(tmp_path, design({'op': cell(kind, 2, 3)}), design({'op': cell(kind, 3, 2)}))
    assert bool(canonical_match(b, n)['matches']) == reused


def test_structural_errors_and_allocator(tmp_path):
    mod = design()['modules']['top']
    mod['netnames']['reserved'] = {'bits': [5000]}
    assert _module_max_bit(mod) == 5000
    mod['cells']['op']['connections']['A'] = [999]
    with pytest.raises(ValueError, match='undriven'):
        check_module(mod)
    mod['cells']['op']['connections']['A'] = [4]
    with pytest.raises(ValueError, match='cycle'):
        check_module(mod)
    mod['cells']['other'] = cell()
    with pytest.raises(ValueError, match='multiple drivers'):
        check_module(mod)
    with pytest.raises(ValueError, match='multiple drivers'):
        graphs(tmp_path, {'modules': {'top': mod}}, design())


def test_bad_replacement_interface(tmp_path):
    bd, nd = design(), design({'op': cell('$and')})
    plan, replacement, _ = flow(tmp_path, bd, nd)
    replacement['modules']['incremental_region']['ports']['region_out_0000']['bits'] = [4, 5]
    with pytest.raises(ValueError, match='invalid replacement port'):
        stitch_region(bd, plan, replacement)


@pytest.mark.skipif(not YOSYS, reason='Yosys required for actual synthesis and SAT')
@pytest.mark.parametrize('nd', [design({'op': cell('$and', b='0')}),
                              design({'op': cell('$and', b='1')}),
                              design({'op': cell(b='1')}), design({}, {'y': 3}),
                              design({'op': cell('$and', b='1')}, {'y': 4, 'z': 4})])
def test_real_yosys_match_synthesize_stitch_prove(tmp_path, nd):
    bd = design(outputs={k: 4 for k in nd['modules']['top']['ports'] if k not in {'a', 'b'}})
    plan, replacement, _ = flow(tmp_path, bd, nd)
    inp, out = tmp_path / 'region.json', tmp_path / 'optimized.json'
    inp.write_text(json.dumps(replacement))
    script = tmp_path / 'synth.ys'
    _write_local_synthesis(script, inp, out)
    assert run_yosys(script, tmp_path / 'synth.log', YOSYS)['status'] == 'passed'
    stitched = stitch_region(bd, plan, json.loads(out.read_text()))
    candidate = tmp_path / 'stitched.json'
    candidate.write_text(json.dumps(stitched))
    _write_equivalence(tmp_path / 'equiv.ys', candidate, tmp_path / 'new.json', 'top')
    assert run_yosys(tmp_path / 'equiv.ys', tmp_path / 'equiv.log', YOSYS)['status'] == 'passed'


@pytest.mark.parametrize('prefix,hidden', [('$auto$enable$', 0), ('internal', 1)])
def test_shifted_enable_names_follow_state_pins(tmp_path, prefix, hidden):
    def regfile(offset, bit_offset):
        data = design({}, {'y': 4 + bit_offset, 'z': 5 + bit_offset})
        mod = data['modules']['top']
        for i in range(2):
            q, en = 4 + i + bit_offset, 6 + i + bit_offset
            ff = copy.deepcopy(register()['modules']['top']['cells']['ff'])
            ff['type'] = '$adffe'
            ff['parameters']['EN_POLARITY'] = '1'
            ff['port_directions']['EN'] = 'input'
            ff['connections'].update(Q=[q], EN=[en])
            mod['cells'][f'slice${offset + i}'] = ff
            driver = cell(a=2 + i, y=en)
            driver.update(hide_name=hidden, attributes={})
            mod['cells'][f'{prefix}{offset + i}'] = driver
            mod['netnames'][f'register[{i}]'] = {'hide_name': 0, 'bits': [q]}
        return data

    b, n = graphs(tmp_path, regfile(1, 0), regfile(0, 100))
    match = canonical_match(b, n)
    mapping = {m['base']: m['new'] for m in match['correspondences']}
    for i in range(2):
        assert mapping[f'{prefix}{1 + i}'] == f'{prefix}{i}'
    assert len(match['matches']) == 4
    assert plan_regions(b, n, match)['stitchable']


@pytest.mark.parametrize('base_hidden,new_hidden,prefix,expected', [
    (0, 0, 'named', True), (1, 0, 'named', False),
    (0, 1, 'named', False), (0, 0, '$auto$logic$', False),
])
def test_name_only_identity_requires_stable_names(tmp_path, base_hidden, new_hidden, prefix, expected):
    # Unconnected, structurally ambiguous cells leave only the name fallback.
    bd = design({f'{prefix}{i}': cell(y=4 + i) for i in range(2)}, {'y': 2})
    nd = copy.deepcopy(bd)
    for data, hidden in [(bd, base_hidden), (nd, new_hidden)]:
        for c in data['modules']['top']['cells'].values():
            c.update(hide_name=hidden, attributes={})
    b, n = graphs(tmp_path, bd, nd)
    assert bool(canonical_match(b, n)['correspondences']) == expected


def test_changed_enable_input_is_rejected(tmp_path):
    bd, nd = register(), register('renamed')
    for data, name, en in [(bd, 'ff', 2), (nd, 'renamed', 3)]:
        ff = data['modules']['top']['cells'][name]
        ff['type'] = '$adffe'
        ff['parameters']['EN_POLARITY'] = '1'
        ff['port_directions']['EN'] = 'input'
        ff['connections']['EN'] = [en]
    b, n = graphs(tmp_path, bd, nd)
    assert not plan_regions(b, n, canonical_match(b, n))['stitchable']
