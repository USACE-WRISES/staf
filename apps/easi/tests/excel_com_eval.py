"""Recalculate the EASI calculator in the installed Excel for a list of cases.

    py -3.12 tests/excel_com_eval.py <workbook.xlsx> <cases.json> <results.json>

``cases.json`` is a list of ``{"entries": {name: [sheet, addr, value]},
"outputs": {name: [sheet, addr]}}``. Excel runs invisibly on a temporary copy
of the workbook; nothing is saved. Values come back as JSON (a blank as "").
Run through ``tests/calculator_eval.ExcelComBackend``.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path


def main(workbook: str, cases_path: str, results_path: str) -> None:
    import pythoncom  # noqa: F401  (pywin32)
    import win32com.client

    cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp()) / Path(workbook).name
    shutil.copyfile(workbook, tmp)
    app = win32com.client.DispatchEx("Excel.Application")
    app.Visible = False
    app.DisplayAlerts = False
    results = []
    try:
        wb = app.Workbooks.Open(str(tmp), ReadOnly=True, UpdateLinks=0)
        sheets = {ws.Name: ws for ws in wb.Worksheets}
        for case in cases:
            for name, (sheet, addr, value) in case["entries"].items():
                cell = sheets[sheet].Range(addr)
                if value == "" or value is None:
                    cell.ClearContents()
                else:
                    cell.Value = value
            app.CalculateFull()
            out = {}
            for name, (sheet, addr) in case["outputs"].items():
                v = sheets[sheet].Range(addr).Value
                if v is None:
                    v = ""
                out[name] = v
            results.append(out)
        wb.Close(SaveChanges=False)
    finally:
        app.Quit()
    Path(results_path).write_text(json.dumps(results, default=str), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:4])
