"""Tests for streamcurves.metric_map (port of R/19_metric_map.R).

Exercises the REAL bundled config/metric_map.yaml + source catalogs.
"""

from streamcurves import metric_map as MM


def test_entries_columns_and_20_pairs():
    df = MM.metric_map_entries()
    assert list(df.columns) == MM.ENTRY_COLUMNS
    assert len(df) > 0
    pairs = (df["discipline"].astype(str) + " / " + df["function_name"].astype(str)).unique()
    assert len(pairs) == 20


def test_validate_no_warnings_against_bundled_catalogs():
    # The bundled map validates cleanly (20 pairs, known sources/roles, every code
    # present in its source catalog).
    assert MM.metric_map_validate() == []


def test_default_role():
    assert MM.metric_map_default_role("nrsa") == "metric"
    assert MM.metric_map_default_role("streamcat") == "both"
    assert MM.metric_map_default_role("streamstats") == "both"


def test_default_codes_and_codes_unique():
    defaults = MM.metric_map_default_codes("streamcat")
    allcodes = MM.metric_map_codes("streamcat")
    assert len(defaults) == len(set(defaults))  # unique
    assert set(defaults).issubset(set(allcodes))
    assert "pctimp2019" in allcodes


def test_function_for_tolerates_prefix_and_suffix():
    # StreamStats compiled column carries an ss_ prefix over the catalog code.
    base = MM.metric_map_function_for("DRNAREA")
    assert base is not None
    assert MM.metric_map_function_for("ss_DRNAREA") == base
    # StreamCAT compiled column carries a ws/cat suffix over the catalog code.
    runoff = MM.metric_map_function_for("runoff")
    assert runoff is not None
    assert MM.metric_map_function_for("runoffws") == runoff
    assert MM.metric_map_function_for("runoffcat") == runoff
    # unmapped code -> None
    assert MM.metric_map_function_for("__nope__") is None


def test_function_label():
    ff = MM.metric_map_function_for("pctimp2019")
    assert MM.metric_map_function_label("pctimp2019") == f"{ff['discipline']}: {ff['function_name']}"
    assert MM.metric_map_function_label("__nope__") == ""


def test_role_for_tolerance_and_missing():
    assert MM.metric_map_role_for("ss_DRNAREA") == MM.metric_map_role_for("DRNAREA")
    assert MM.metric_map_role_for("__nope__") is None
    # NRSA measured indicator defaults to metric role
    assert MM.metric_map_role_for("pctimp2019") in ("metric", "predictor", "both")


def test_functions_for_returns_every_function_a_code_serves():
    # bfi informs two functions (listed once under each).
    bfi = {f["function_name"] for f in MM.metric_map_functions_for("bfi")}
    assert bfi == {"Streamflow regime", "Low flow and baseflow dynamics"}
    # single-function metric
    assert [f["function_name"] for f in MM.metric_map_functions_for("chem_COND")] == [
        "Water and soil quality"
    ]
    # same ss_/ws/cat tolerance as the singular helper
    assert MM.metric_map_functions_for("ss_DRNAREA") == MM.metric_map_functions_for("DRNAREA")
    assert MM.metric_map_functions_for("__nope__") == []


def test_mmw_drainage_area_metric_registered():
    assert "mmw_da_sqmi" in MM._mmw_core_metrics_codes()
    # the bundled map (now including mmw_da_sqmi) still validates cleanly
    assert MM.metric_map_validate() == []
    # mapped under Hydrology / Reach inflow, role predictor (like DRNAREA)
    assert [f["function_name"] for f in MM.metric_map_functions_for("mmw_da_sqmi")] == ["Reach inflow"]
    assert MM.metric_map_role_for("mmw_da_sqmi") == "predictor"
