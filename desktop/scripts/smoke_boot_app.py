"""Boot StreamCurves once from a given interpreter and require an HTTP 200.

This is the env payload gate: build-env-payload.ps1 runs it against the RELOCATED interpreter
(in CI and locally) so that pruning mistakes, non-relocatable paths or broken wheels are caught
before anything publishes. The app runs the way the installed shell runs it: from
<apps-root>/stream-curves (its library at <apps-root>/library), with the shell's desktop
environment and without the developer-only STAF_LIBRARY_* variables.

Usage:
    python smoke_boot_app.py --python <python.exe> --apps-root <dir with stream-curves/ + library/>
                             [--app-dir stream-curves] [--imports geopandas,rasterio,...] [--timeout 240]

Exit code 0 only if the import check and the app boot succeed.
"""
from __future__ import annotations

import argparse
import collections
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

DEFAULT_IMPORTS = (
    "shiny,shinywidgets,shinyswatch,faicons,ipyleaflet,pandas,numpy,scipy,statsmodels,skmisc,"
    "pyarrow,matplotlib,plotnine,plotly,openpyxl,yaml,requests,pynhd,py3dep,pygeohydro,"
    "pygeoutils,geopandas,pyogrio,shapely,pyproj,rasterio,rioxarray,xarray"
)

FORBIDDEN_IMPORTS = ["aiodns", "pycares"]  # pruned from the payload; present => broken Windows DNS

# Installed mode strips these (AppEnvironment.LibraryVariables); the gate boots the same way.
LIBRARY_VARIABLES = ["STAF_LIBRARY_ROOT", "STAF_LIBRARY_PUBLISH", "STAF_LIBRARY_MAINTAINER"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def smoke_env() -> dict[str, str]:
    env = os.environ.copy()
    root = os.path.join(tempfile.gettempdir(), "streamcurves-smoke")
    cache = os.path.join(root, "cache")
    os.makedirs(cache, exist_ok=True)
    for name in LIBRARY_VARIABLES:
        env.pop(name, None)
    env.update(
        STREAMCURVES_DESKTOP="1",
        STREAMCURVES_DATA_ROOT=root,
        HYRIVER_CACHE_NAME=os.path.join(cache, "hyriver.sqlite"),
        MPLCONFIGDIR=os.path.join(cache, "matplotlib"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
    )
    return env


def check_imports(python: str, imports: list[str]) -> None:
    print(f"[smoke] import check: {', '.join(imports)} (forbidden: {', '.join(FORBIDDEN_IMPORTS)})", flush=True)
    code = "import importlib, sys\n" + "\n".join(
        f"importlib.import_module({mod!r})" for mod in imports
    ) + "\n" + "\n".join(
        "try:\n"
        f"    importlib.import_module({mod!r})\n"
        f"    raise SystemExit('FORBIDDEN module importable: {mod} (prune.txt not applied?)')\n"
        "except ImportError:\n"
        "    pass"
        for mod in FORBIDDEN_IMPORTS
    ) + "\nprint('imports OK', sys.version)"
    result = subprocess.run(
        [python, "-c", code],
        capture_output=True,
        text=True,
        timeout=600,
        env=smoke_env(),
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.returncode != 0:
        raise SystemExit(f"[smoke] import check FAILED ({result.returncode})")


def boot_app(python: str, apps_root: str, app_dir: str, timeout: float) -> None:
    cwd = os.path.join(apps_root, app_dir)
    if not os.path.isfile(os.path.join(cwd, "app.py")):
        raise SystemExit(f"[smoke] app.py missing in {cwd}")
    if not os.path.isdir(os.path.join(apps_root, "library")):
        raise SystemExit(f"[smoke] library/ missing beside {app_dir}/ in {apps_root}")

    port = free_port()
    print(f"[smoke] booting {app_dir} on :{port} ...", flush=True)
    proc = subprocess.Popen(
        [python, "-u", "-m", "shiny", "run", "--host", "127.0.0.1", "--port", str(port), "app.py"],
        cwd=cwd,
        env=smoke_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    # Drain the merged output continuously: a chatty startup must never fill the pipe and
    # stall the server, and the tail is the diagnosis when the gate fails.
    tail: collections.deque[str] = collections.deque(maxlen=60)
    drain = threading.Thread(target=lambda: tail.extend(proc.stdout), daemon=True)
    drain.start()
    # Loopback only: never route the probe through a machine-wide proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            if proc.poll() is not None:
                drain.join(timeout=5)
                raise SystemExit(
                    f"[smoke] {app_dir} exited early (code {proc.returncode})\n" + "".join(tail))
            try:
                with opener.open(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                    if resp.status != 200:
                        raise SystemExit(f"[smoke] {app_dir} answered HTTP {resp.status}, expected 200")
                    body = resp.read()
                    print(f"[smoke] {app_dir} answered HTTP 200 ({len(body)} bytes) after "
                          f"{time.monotonic() - started:.0f}s", flush=True)
                    return
            except urllib.error.HTTPError as err:
                # The server is up but failing: no point waiting for the timeout.
                raise SystemExit(f"[smoke] {app_dir} answered HTTP {err.code}, expected 200\n" + "".join(tail))
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        raise SystemExit(f"[smoke] {app_dir} did not answer within {timeout:.0f}s\n" + "".join(tail))
    finally:
        proc.kill()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", required=True)
    parser.add_argument("--apps-root", required=True)
    parser.add_argument("--app-dir", default="stream-curves")
    parser.add_argument("--imports", default=DEFAULT_IMPORTS)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()

    python = os.path.abspath(args.python)
    if not os.path.isfile(python):
        raise SystemExit(f"[smoke] python not found: {python}")

    if args.imports:
        check_imports(python, [m.strip() for m in args.imports.split(",") if m.strip()])
    boot_app(python, os.path.abspath(args.apps_root), args.app_dir, args.timeout)
    print("[smoke] ALL OK", flush=True)


if __name__ == "__main__":
    main()
