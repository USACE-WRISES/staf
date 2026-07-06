"""Boot every STAF app once from a given interpreter and require an HTTP answer.

This is the payload gate: it runs against the RELOCATED env build in CI (and locally) so that
pruning mistakes, non-relocatable paths, or broken wheels are caught before anything publishes.

Usage:
    python smoke_boot_apps.py --python <python.exe> --apps-root <dir> [--apps easi,sfari,deep,stream-curves]
                              [--imports geopandas,rasterio,...] [--timeout 240]

Exit code 0 only if the import check and every app boot succeed.
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

DEFAULT_APPS = "easi,sfari,deep,stream-curves"
DEFAULT_IMPORTS = "geopandas,rasterio,shapely,pyproj,shiny,shinywidgets,ipyleaflet,pynhd,py3dep,pygeohydro,plotnine,statsmodels,reportlab"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_imports(python: str, imports: list[str]) -> None:
    print(f"[smoke] import check: {', '.join(imports)}", flush=True)
    code = "import importlib, sys\n" + "\n".join(
        f"importlib.import_module({mod!r})" for mod in imports
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


def smoke_env() -> dict[str, str]:
    env = os.environ.copy()
    cache = os.path.join(tempfile.gettempdir(), "staf-smoke-cache")
    os.makedirs(cache, exist_ok=True)
    env.update(
        HYRIVER_CACHE_NAME=os.path.join(cache, "smoke_hyriver.sqlite"),
        MPLCONFIGDIR=os.path.join(cache, "mpl"),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        PYTHONUTF8="1",
    )
    return env


def boot_app(python: str, apps_root: str, app_dir: str, timeout: float) -> None:
    port = free_port()
    cwd = os.path.join(apps_root, app_dir)
    if not os.path.isdir(cwd):
        raise SystemExit(f"[smoke] app dir missing: {cwd}")

    print(f"[smoke] booting {app_dir} on :{port} …", flush=True)
    proc = subprocess.Popen(
        [python, "-u", "-m", "shiny", "run", "--host", "127.0.0.1", "--port", str(port), "app.py"],
        cwd=cwd,
        env=smoke_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail: list[str] = []
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise SystemExit(
                    f"[smoke] {app_dir} exited early (code {proc.returncode})\n{out[-4000:]}"
                )
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                    if resp.status < 500:
                        print(f"[smoke] {app_dir} answered HTTP {resp.status}", flush=True)
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        raise SystemExit(f"[smoke] {app_dir} did not answer within {timeout}s\n" + "".join(tail[-40:]))
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
    parser.add_argument("--apps", default=DEFAULT_APPS)
    parser.add_argument("--imports", default=DEFAULT_IMPORTS)
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()

    python = os.path.abspath(args.python)
    if not os.path.isfile(python):
        raise SystemExit(f"[smoke] python not found: {python}")

    if args.imports:
        check_imports(python, [m.strip() for m in args.imports.split(",") if m.strip()])
    for app_dir in [a.strip() for a in args.apps.split(",") if a.strip()]:
        boot_app(python, os.path.abspath(args.apps_root), app_dir, args.timeout)
    print("[smoke] ALL OK", flush=True)


if __name__ == "__main__":
    main()
