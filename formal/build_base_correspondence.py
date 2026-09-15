#!/usr/bin/env python3
"""Discover proven named combinational boundaries between frontend and mapping.

Registers/memories require a separate state correspondence and are reported as
unknown by this discovery command. Records do not authorize mapped stitching.
"""
import argparse
import copy
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'analysis'))
sys.path.insert(0, str(ROOT))
from netlist_graph import NetlistGraph
from region import is_hard_boundary
from yosys_runner import quote, run_yosys
from formal.build_match_problem import design_digest


def cone_module(graph, bits, all_inputs):
    selected, visiting = set(), set()
    def visit(bit):
        if isinstance(bit, str) or str(bit) in graph.primary_inputs:
            return
        driver = graph.bit_driver.get(str(bit))
        if not driver:
            raise ValueError('undriven cone input')
        name = driver[0]
        if name in visiting:
            raise ValueError('combinational cycle')
        if name in selected:
            return
        if is_hard_boundary(graph.cells[name]['type']):
            raise ValueError('cone requires a separately proven state mapping')
        visiting.add(name)
        for _, _, value in graph.cell_input_bits(name):
            visit(value)
        visiting.remove(name)
        selected.add(name)
    for bit in bits:
        visit(bit)
    ports = {f'i{i}': {'direction': 'input', 'bits': [graph.input_bits[label]]}
             for i, label in enumerate(all_inputs)}
    ports['result'] = {'direction': 'output', 'bits': bits[:]}
    return {'attributes': {}, 'ports': ports, 'cells': {n: copy.deepcopy(graph.cells[n]) for n in sorted(selected)},
            'netnames': {}}


def discover(base, mapped, output, yosys='yosys', timeout=30):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report = {'design_digest': design_digest(base, mapped), 'boundaries': [], 'mapped_reuse_enabled': False}
    if set(base.input_bits) != set(mapped.input_bits):
        raise ValueError('top-level inputs differ')
    inputs = sorted(base.input_bits)
    bn, mn = base.module_data.get('netnames', {}), mapped.module_data.get('netnames', {})
    for name in sorted(set(bn) & set(mn)):
        if bn[name].get('hide_name', 0) or mn[name].get('hide_name', 0):
            continue
        bb, mb = bn[name]['bits'], mn[name]['bits']
        if len(bb) != len(mb):
            continue
        entry = {'name': name, 'base_bits': bb, 'mapped_bits': mb, 'status': 'unknown'}
        try:
            data = {'modules': {'gold': cone_module(base, bb, inputs), 'gate': cone_module(mapped, mb, inputs)}}
            i = len(report['boundaries'])
            path = output / f'boundary_{i}.json'
            path.write_text(json.dumps(data))
            script = path.with_suffix('.ys')
            script.write_text(f'read_json {quote(path)}\nmiter -equiv -flatten gold gate miter\n'
                              'hierarchy -top miter\ntechmap\nopt\nsat -prove trigger 0 miter\n')
            result = run_yosys(script, path.with_suffix('.log'), yosys, timeout)
            entry.update(result)
            log = path.with_suffix('.log').read_text()
            if result['status'] == 'passed':
                entry['status'] = ('proven' if 'no model found: SUCCESS!' in log else
                                   'disproven' if 'model found: FAIL!' in log else 'unknown')
        except ValueError as exc:
            entry['reason'] = str(exc)
        report['boundaries'].append(entry)
    (output / 'base_correspondence.json').write_text(json.dumps(report, indent=2))
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('frontend')
    p.add_argument('mapped')
    p.add_argument('--top', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--yosys', default='yosys')
    p.add_argument('--timeout', type=int, default=30)
    a = p.parse_args()
    discover(NetlistGraph(a.frontend, a.top), NetlistGraph(a.mapped, a.top), a.output, a.yosys, a.timeout)


if __name__ == '__main__':
    main()
