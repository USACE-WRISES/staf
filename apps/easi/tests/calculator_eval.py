"""Evaluate the generated EASI calculator without opening Excel by hand.

Two backends share one interface, ``evaluate(entries, outputs)``: every entry
cell of the workbook is set (blank entries as the empty string, which the
formulas treat exactly like an empty cell), the workbook recalculates, and the
named output cells come back as plain Python values (a blank as None).

* ``FormulasBackend`` uses the pure-Python ``formulas`` package (dev-only pin
  in requirements-dev.txt): the everyday regression gate.
* ``ExcelComBackend`` drives the installed Excel through pywin32 in a
  subprocess (``tests/excel_com_eval.py`` under ``py -3.12``, which carries
  pywin32 on this workstation). Opt in with ``EASI_EXCEL_PARITY=1``; it is the
  authoritative release gate because Excel is the target runtime.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl

ENTRY_PREFIXES = ("in_", "ctx_", "ov_", "site_")


def _plain(value):
    """numpy scalars and [[x]] arrays to Python values; "" to None."""
    try:
        import numpy as np
    except Exception:  # pragma: no cover
        np = None
    while isinstance(value, (list, tuple)) and len(value) == 1:
        value = value[0]
    if np is not None and isinstance(value, np.ndarray):
        value = value.tolist()
        while isinstance(value, list) and len(value) == 1:
            value = value[0]
    if np is not None and isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, str) and value == "":
        return None
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return value
    return value


class Names:
    def __init__(self, path: Path):
        wb = openpyxl.load_workbook(path, read_only=False)
        self.names = {k: v.attr_text for k, v in wb.defined_names.items()}
        self.entries = sorted(n for n in self.names if n.startswith(ENTRY_PREFIXES))

    def split(self, name: str) -> tuple[str, str]:
        sheet, addr = self.names[name].split("!")
        return sheet.strip("'"), addr.replace("$", "")


class FormulasBackend:
    name = "formulas"

    def __init__(self, path: Path):
        import formulas
        self.path = Path(path)
        self.names = Names(self.path)
        self.model = formulas.ExcelModel().loads(str(self.path)).finish()
        self.book = self.path.name

    def key(self, name: str) -> str:
        sheet, addr = self.names.split(name)
        return f"'[{self.book}]{sheet.upper()}'!{addr}"

    def evaluate(self, entries: dict, outputs: list[str]) -> dict:
        inputs = {self.key(n): "" for n in self.names.entries}
        for n, v in entries.items():
            inputs[self.key(n)] = "" if v is None else v
        wanted = [self.key(o) for o in outputs]
        sol = self.model.calculate(inputs=inputs, outputs=wanted)
        out = {}
        for o, k in zip(outputs, wanted):
            cell = sol.get(k)
            out[o] = _plain(getattr(cell, "value", cell))
        return out


class ExcelComBackend:
    name = "excel"
    HELPER = Path(__file__).with_name("excel_com_eval.py")

    def __init__(self, path: Path, launcher=("py", "-3.12")):
        self.path = Path(path)
        self.names = Names(self.path)
        self.launcher = list(launcher)

    @classmethod
    def available(cls) -> bool:
        if os.environ.get("EASI_EXCEL_PARITY") != "1" or sys.platform != "win32":
            return False
        if shutil.which("py") is None:
            return False
        probe = subprocess.run(["py", "-3.12", "-c", "import win32com.client"], capture_output=True)
        return probe.returncode == 0

    def evaluate_many(self, cases: list[tuple[dict, list[str]]]) -> list[dict]:
        """All cases in one Excel session (opening Excel per case is slow)."""
        payload = []
        for entries, outputs in cases:
            filled = {n: "" for n in self.names.entries}
            filled.update({n: ("" if v is None else v) for n, v in entries.items()})
            payload.append({"entries": {n: list(self.names.split(n)) + [v] for n, v in filled.items()},
                            "outputs": {o: list(self.names.split(o)) for o in outputs}})
        with tempfile.TemporaryDirectory() as tmp:
            inp, out = Path(tmp) / "cases.json", Path(tmp) / "results.json"
            inp.write_text(json.dumps(payload), encoding="utf-8")
            subprocess.run(self.launcher + [str(self.HELPER), str(self.path), str(inp), str(out)],
                           check=True, capture_output=True, text=True, timeout=3600)
            results = json.loads(out.read_text(encoding="utf-8"))
        return [{k: _plain(v) for k, v in r.items()} for r in results]

    def evaluate(self, entries: dict, outputs: list[str]) -> dict:
        return self.evaluate_many([(entries, outputs)])[0]
