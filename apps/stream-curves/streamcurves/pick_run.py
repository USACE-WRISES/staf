"""A native file dialog in a child process, for a desktop run without the shell.

The desktop shell shows real Windows dialogs itself (www/desktop_bridge.js relays them). This
covers the no-shell case: the app served to a plain browser on the same machine (a dev preview).
Always a child process, never in-process tkinter: Tk wants the process's main thread, and a
wedged dialog stays killable. After HYPE's hype_app/pick_run.py.

    python -m streamcurves.pick_run '{"mode": "open", "purpose": "open_project"}'

prints one JSON line: {"purpose", "path" (str|null), "cancelled" (bool)}.
STREAMCURVES_PICK_TEST_RESULT (may be "" for a simulated cancel) answers before tkinter is
imported, so the whole round trip is testable headless.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_OPEN_TYPES = [("StreamCurves files", "*.streamcurves *.streamcurves.json *.xlsx"),
               ("StreamCurves project", "*.streamcurves"),
               ("StreamCurves session", "*.streamcurves.json"),
               ("Workbook", "*.xlsx"),
               ("All files", "*.*")]
_SAVE_TYPES = [("StreamCurves project", "*.streamcurves"), ("All files", "*.*")]


def pick(payload: dict) -> dict:
    """Show the dialog in THIS process and return the reply (the child's entry point)."""
    purpose = str(payload.get("purpose") or "")
    test = os.environ.get("STREAMCURVES_PICK_TEST_RESULT")
    if test is not None:
        path = test.strip()
        return {"purpose": purpose, "path": path or None, "cancelled": not path}
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    # The server's child has no foreground rights, so the dialog cannot steal focus; topmost
    # keeps it above the browser at least.
    root.attributes("-topmost", True)
    root.update()
    try:
        opts = {"parent": root, "title": str(payload.get("title") or "StreamCurves")}
        init_dir = str(payload.get("initial_dir") or "")
        if init_dir and os.path.isdir(init_dir):
            opts["initialdir"] = init_dir
        mode = str(payload.get("mode") or "open").lower()
        if mode == "directory":
            picked = filedialog.askdirectory(mustexist=False, **opts)
        elif mode == "save":
            init_file = str(payload.get("initial_file") or "")
            if init_file:
                opts["initialfile"] = init_file
            picked = filedialog.asksaveasfilename(defaultextension=".streamcurves",
                                                  filetypes=_SAVE_TYPES, **opts)
        else:
            picked = filedialog.askopenfilename(filetypes=_OPEN_TYPES, **opts)
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass
    picked = str(picked or "")      # cancel returns "" (or () on some Tk builds)
    return {"purpose": purpose, "path": picked or None, "cancelled": not picked}


def run_child(payload: dict, *, timeout: float = 900.0) -> dict:
    """Run the dialog in a child interpreter and return its reply (blocking; call from a
    worker thread). Raises RuntimeError when the child fails, so the caller can fall back to
    the typed-path modal."""
    proc = subprocess.run(
        [sys.executable, "-m", "streamcurves.pick_run", json.dumps(payload)],
        capture_output=True, text=True, timeout=timeout,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
    if proc.returncode != 0 or not lines:
        raise RuntimeError((proc.stderr or proc.stdout or "the file dialog failed").strip()[-400:])
    return json.loads(lines[-1])


def main(argv: list[str]) -> int:
    try:
        payload = json.loads(argv[1]) if len(argv) > 1 else {}
        print(json.dumps(pick(payload if isinstance(payload, dict) else {})), flush=True)
        return 0
    except Exception as e:  # noqa: BLE001 - the app falls back to the typed-path modal
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
