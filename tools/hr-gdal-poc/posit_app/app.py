"""NHDPlus HR direct-access benchmark for Posit Connect Cloud.

Checks what the deployed environment offers (GDAL version, OpenFileGDB, /vsicurl/
and /vsizip/, S3 throughput, CPU, memory, disk), then reads one flowline and its
catchment straight from the USGS zipped FileGDB on S3, or from a once-downloaded
local copy, and times every step. Results also go to the log as JSON lines
(``ENVIRONMENT {...}``, ``BENCHMARK {...}``).
"""
from __future__ import annotations

import asyncio
import json

from shiny import App, reactive, render, ui

import gdalpoc

app_ui = ui.page_fluid(
    ui.h3("NHDPlus HR direct access benchmark"),
    ui.p("Reads one NHDPlus HR flowline and its catchment straight from the USGS zipped File "
         "Geodatabase on S3 with GDAL, and times every step. Each run starts in a fresh process, "
         "so caches start cold; later clicks in the same run show the warm cost."),
    ui.layout_columns(
        ui.card(
            ui.card_header("Environment"),
            ui.input_action_button("check", "Check environment"),
            ui.output_text_verbatim("env_out"),
        ),
        ui.card(
            ui.card_header("Benchmark"),
            ui.input_text("vpu", "Region (VPU code, blank to look it up)", "0710"),
            ui.layout_columns(
                ui.input_numeric("lat", "Latitude", 41.016806, step=0.0001),
                ui.input_numeric("lon", "Longitude", -93.76691, step=0.0001),
            ),
            ui.input_select("mode", "Mode", {
                "remote": "Remote: query the zip on S3 in place",
                "download": "Download: fetch the zip once, then query locally",
                "both": "Both, one after the other"}),
            ui.input_select("cache", "GDAL cache", {"0": "GDAL default", "1024": "1 GB"}),
            ui.input_numeric("clicks", "Clicks (point, 300 m, 10 km, point again)", 3, min=1, max=4),
            ui.input_action_button("run", "Run benchmark", class_="btn-primary"),
        ),
        col_widths=(5, 7),
    ),
    ui.output_text_verbatim("status"),
    ui.output_text_verbatim("result_out"),
)


def _environment() -> dict:
    info = gdalpoc.environment()
    pkgs = gdalpoc.list_packages()
    info["packages_on_s3"] = len(pkgs)
    info["vsi_check"] = gdalpoc.vsi_check(min(pkgs, key=lambda p: p["bytes"]))
    big = [p for p in pkgs if p["vpu"] == "1709"] or pkgs
    info["s3_speed"] = gdalpoc.s3_speed(big[0]["url"])
    print("ENVIRONMENT " + json.dumps(info), flush=True)
    return info


def _bench(params: dict) -> list:
    pkgs = gdalpoc.list_packages()
    vpu = (params["vpu"] or "").strip()
    if not vpu:
        found = gdalpoc.vpus_at(params["lon"], params["lat"])
        if not found:
            raise ValueError("no USGS package outline holds that point")
        vpu = found[0]
    pkg = next((p for p in pkgs if p["vpu"] == vpu), None)
    if pkg is None:
        raise ValueError(f"no USGS package for VPU {vpu}")
    modes = ["remote", "download"] if params["mode"] == "both" else [params["mode"]]
    out = []
    for mode in modes:
        res = gdalpoc.run(package=pkg, lat=params["lat"], lon=params["lon"], mode=mode,
                          cache_mb=params["cache"], clicks=params["clicks"], fresh=True)
        print("BENCHMARK " + json.dumps(res, default=str), flush=True)
        out.append(res)
    return out


def server(input, output, session):
    @reactive.extended_task
    async def env_task():
        return await asyncio.to_thread(_environment)

    @reactive.extended_task
    async def bench_task(params):
        return await asyncio.to_thread(_bench, params)

    @reactive.effect
    @reactive.event(input.check)
    def _start_check():
        env_task()

    @reactive.effect
    @reactive.event(input.run)
    def _start_bench():
        bench_task({"vpu": input.vpu(), "lat": float(input.lat()), "lon": float(input.lon()),
                    "mode": input.mode(), "cache": int(input.cache()), "clicks": int(input.clicks())})

    @render.text
    def env_out():
        status = env_task.status()
        if status == "running":
            return "Checking (about 10 to 30 s)."
        if status == "error":
            try:
                env_task.result()
            except Exception as exc:
                return f"The check failed: {exc}"
        if status == "success":
            return json.dumps(env_task.result(), indent=1)
        return "Click Check environment."

    @render.text
    def status():
        state = bench_task.status()
        if state == "running":
            return "Benchmark running. A remote run can take several minutes per click."
        if state == "error":
            try:
                bench_task.result()
            except Exception as exc:
                return f"The benchmark failed: {exc}"
        if state == "success":
            return "Benchmark finished."
        return "Set a region and point, then Run benchmark."

    @render.text
    def result_out():
        if bench_task.status() != "success":
            return ""
        return "\n\n".join(gdalpoc.table(r) + (f"\n  error: {r['error'][-600:]}" if r.get("error") else "")
                           for r in bench_task.result())


app = App(app_ui, server)
