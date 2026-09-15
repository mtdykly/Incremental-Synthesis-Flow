import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "analysis"))

from netlist_graph import NetlistGraph  # noqa: E402
from region import plan_regions  # noqa: E402
from region_netlist import extract_region, stitch_region  # noqa: E402


def _design(kind):
    op = "$and" if kind == "base" else "$or"
    return {
        "modules": {
            "top": {
                "attributes": {"top": "1"},
                "ports": {
                    "a": {"direction": "input", "bits": [2]},
                    "b": {"direction": "input", "bits": [3]},
                    "c": {"direction": "input", "bits": [4]},
                    "y": {"direction": "output", "bits": [6]},
                },
                "cells": {
                    "changed": {
                        "type": op,
                        "parameters": {},
                        "attributes": {},
                        "port_directions": {"A": "input", "B": "input", "Y": "output"},
                        "connections": {"A": [2], "B": [3], "Y": [5]},
                    },
                    "stable": {
                        "type": "$xor",
                        "parameters": {},
                        "attributes": {},
                        "port_directions": {"A": "input", "B": "input", "Y": "output"},
                        "connections": {"A": [5], "B": [4], "Y": [6]},
                    },
                },
                "netnames": {},
            }
        }
    }


def _graph(tmp_path, kind):
    path = tmp_path / f"{kind}.json"
    path.write_text(json.dumps(_design(kind)))
    return NetlistGraph(path, top="top")


def _match():
    return {
        "matches": [{"base": "stable", "new": "stable", "method": "test"}],
        "base_unmatched": ["changed"],
        "new_unmatched": ["changed"],
    }


def test_plan_stops_at_reusable_xor(tmp_path):
    plan = plan_regions(_graph(tmp_path, "base"), _graph(tmp_path, "new"), _match())
    assert plan["stitchable"] is True
    assert plan["base_cells"] == ["changed"]
    assert len(plan["base_boundary"]["inputs"]) == 2
    assert len(plan["base_boundary"]["outputs"]) == 1


def test_extract_and_stitch_preserves_stable_cell(tmp_path):
    base = _graph(tmp_path, "base")
    new = _graph(tmp_path, "new")
    plan = plan_regions(base, new, _match())
    replacement = extract_region(new, plan["new_cells"], plan["new_boundary"])
    stitched = stitch_region(base.raw_data, plan, replacement)
    cells = stitched["modules"]["top"]["cells"]
    assert "changed" not in cells
    assert "stable" in cells
    inserted = [cell for name, cell in cells.items() if name.startswith("$incremental$")]
    assert len(inserted) == 1
    assert inserted[0]["type"] == "$or"
    assert inserted[0]["connections"]["Y"] == [5]


def test_changed_register_is_rejected(tmp_path):
    base = _graph(tmp_path, "base")
    new = _graph(tmp_path, "new")
    base.graph.nodes["changed"]["type"] = "$dff"
    new.graph.nodes["changed"]["type"] = "$dff"
    plan = plan_regions(base, new, _match())
    assert plan["stitchable"] is False
    assert plan["unsupported_changed_state_cells"]["base"] == ["changed"]
