"""The STAF deploy: Publisher's file list covers the shell, and the assembler takes each tool's own
deploy list (tests/test_assembled.py checks an assembled copy)."""
from __future__ import annotations

import ast
import importlib.util
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CONFIG = HERE / ".posit" / "publish" / "staf.toml"


def _assembler():
    spec = importlib.util.spec_from_file_location("assemble_tools", HERE / "scripts" / "assemble_tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publisher_uploads_the_shell_and_the_assembled_tools():
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["type"] == "python-shiny" and config["entrypoint"] == "app.py"
    assert config["title"] == "staf" and config["product_type"] == "connect_cloud"
    assert config["python"]["version"] == "3.12"
    assert set(config["files"]) == {"/app.py", "/requirements.txt", "/.python-version", "/staf_shell/",
                                    "/www/", "/_tools/", "/.posit/publish/staf.toml"}
    assert not any("deployments" in f for f in config["files"])
    # everything app.py imports from the folder is in the list
    tree = ast.parse((HERE / "app.py").read_text(encoding="utf-8"))
    local = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    local |= {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    for name in local:
        if (HERE / name).is_dir() or (HERE / f"{name}.py").is_file():
            assert f"/{name}/" in config["files"] or f"/{name}.py" in config["files"], name


def test_the_assembler_takes_each_tools_own_deploy_list():
    tools = _assembler()
    for tool in tools.TOOLS:
        files = tools.selection(tool, tools.tracked(tool))
        assert "app.py" in files and f"{tool}/__init__.py" in files, tool
        assert any(f.startswith("www/") for f in files) and any(f.startswith("data/") for f in files), tool
        assert not any(f.startswith(".posit/") or f in ("requirements.txt", ".python-version") for f in files)
        assert any(f.startswith(f"{tool}/_vendor/site_engine/") for f in files), tool
    easi = tools.selection("easi", tools.tracked("easi"))
    assert "local_review.py" in easi                     # EASI's app.py imports it
    deep = tools.selection("deep", tools.tracked("deep"))
    assert not any(f.startswith("data/bundles/") for f in deep)
    assert "data/deep-assessments.json" in deep
