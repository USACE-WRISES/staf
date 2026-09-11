"""The panel's source: every queued job is well formed, every output id has
its render function, and the two new tabs share the worker controls."""
from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"
JOB_KINDS = {"national", "chunk", "tiles", "stage", "publish"}
CHUNK_KEYS = {"job", "kind", "value", "stages", "huc8s", "force", "keep_dem_windows"}


def _tree() -> ast.Module:
    return ast.parse(APP.read_text(encoding="utf-8"))


def _is_queue_append(node: ast.Call) -> bool:
    fn = node.func
    return (isinstance(fn, ast.Attribute) and fn.attr == "append" and isinstance(fn.value, ast.Call)
            and isinstance(fn.value.func, ast.Name) and fn.value.func.id == "Queue")


def _literal_keys(node: ast.Dict) -> dict:
    return {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}


def test_every_queued_job_is_well_formed():
    tree = _tree()
    found = 0
    for node in ast.walk(tree):
        candidates: list = []
        if isinstance(node, ast.Call) and _is_queue_append(node):
            candidates = list(node.args)
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id == "jobs":
                candidates = [node.value]
        for arg in candidates:
            for d in (n for n in ast.walk(arg) if isinstance(n, ast.Dict)):
                fields = _literal_keys(d)
                if "job" not in fields:
                    continue
                found += 1
                assert isinstance(fields["job"], ast.Constant), ast.dump(d)
                kind = fields["job"].value
                assert kind in JOB_KINDS, kind
                if kind == "chunk":
                    assert {"kind", "value"} <= set(fields) <= CHUNK_KEYS, set(fields)
                elif kind == "tiles":
                    assert "vpu" in fields
                elif kind == "national":
                    assert set(fields) <= {"job", "steps"}, set(fields)
                else:
                    assert set(fields) <= {"job", "dry_run"}, set(fields)
    assert found >= 8, found


def test_every_output_has_a_render_function_and_the_tabs_share_the_controls():
    tree = _tree()
    outputs = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "output_ui"
                and node.args and isinstance(node.args[0], ast.Constant)):
            outputs.add(node.args[0].value)
    server = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "server")
    defined = {n.name for n in ast.walk(server) if isinstance(n, ast.FunctionDef)}
    missing = outputs - defined
    assert not missing, missing
    assert {"national_table", "downloads_table", "disk_summary", "catalog_card", "bandwidth_card",
            "xs_progress", "xs_table", "worker_status_data", "worker_status_xs"} <= outputs
    source = APP.read_text(encoding="utf-8")
    assert source.count('_controls("_d")') == 1 and source.count('_controls("_x")') == 1
    assert 'for _suffix in ("_d", "_x"):' in source
