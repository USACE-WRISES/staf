## Golden fixture exporter for the Python port's parity tests.
##
## Runs the R pipeline (D:/Code/Work/stream-curves) on the bundled
## OSAM_summarydata.xlsx and writes JSON fixtures into this repo's
## tests/golden/. Run from anywhere:
##
##   Rscript D:/Code/Work/stream-curves-python/scripts/export_golden.R
##
## Data frames serialize with dataframe="columns", na="null", digits=NA
## (max precision); Python reads them with tests/golden_io.py.
## NOTE: the analysis frame is named `dat` (not `df`) because `df <<-` from a
## top-level tryCatch promise hits base::df's locked binding.

suppressPackageStartupMessages({
  library(dplyr); library(tidyr); library(tibble); library(purrr)
  library(stringr); library(forcats); library(readxl); library(yaml)
  library(jsonlite); library(leaps); library(MASS); library(lmtest)
  library(car); library(broom); library(ggplot2)
})

set.seed(42)

r_repo <- "D:/Code/Work/stream-curves"
py_repo <- "D:/Code/Work/stream-curves-python"
golden_dir <- file.path(py_repo, "tests", "golden")
dir.create(golden_dir, recursive = TRUE, showWarnings = FALSE)

setwd(r_repo)

pipeline_files <- c(
  "00_plot_theme.R", "00_input_workbook.R", "01_load_data.R", "02_clean_data.R",
  "03_derive_variables.R", "04_metric_precheck.R", "05_stratification_screening.R",
  "05b_effect_size.R", "05c_pattern_stability.R", "05d_cross_metric_consistency.R",
  "05e_feasibility.R", "06_stratification_decision.R", "07_model_candidates.R",
  "08_model_selection.R", "09_diagnostics.R", "10_reference_curves.R",
  "11_cross_metric.R", "12_regional_curves.R", "13_oh_parameter_map.R",
  "14_oh_list_of_metrics.R", "15_oh_sqt_workbook.R", "16_terrain_3dep.R",
  "17_xs_geometry.R", "18_geomorph.R", "19_metric_map.R", "20_deep_export.R",
  "21_staf_metric_library.R"
)
for (f in pipeline_files) source(file.path("R", f))

wj <- function(name, x) {
  path <- file.path(golden_dir, paste0(name, ".json"))
  jsonlite::write_json(
    x, path,
    dataframe = "columns", na = "null", digits = NA, auto_unbox = FALSE,
    null = "null", force = TRUE
  )
  cat(sprintf("  wrote %s\n", basename(path)))
}

section <- function(name, expr) {
  cat(sprintf("== %s ==\n", name))
  tryCatch(expr, error = function(e) {
    cat(sprintf("  !! FAILED: %s\n", conditionMessage(e)))
    assign("golden_failures", c(get("golden_failures", envir = .GlobalEnv), name),
           envir = .GlobalEnv)
    NULL
  })
}
golden_failures <- character()

## Drop list-columns (serialized separately) so frames JSON cleanly.
drop_list_cols <- function(x) {
  if (is.null(x)) return(x)
  keep <- !vapply(x, is.list, logical(1))
  x[, keep, drop = FALSE]
}

## -- 01 workbook bundle ------------------------------------------------------
bundle <- NULL
section("01 workbook", {
  bundle <<- read_input_workbook("OSAM_summarydata.xlsx")
  mapping <- bundle$discipline_function_mapping
  wj("01_bundle_meta", list(
    metric_config = bundle$metric_config,
    strat_config = bundle$strat_config,
    predictor_config = bundle$predictor_config,
    factor_recode_config = bundle$factor_recode_config,
    site_mask_config = bundle$site_mask_config,
    discipline_function_mapping = mapping,
    covers_all_metrics = isTRUE(attr(mapping, "covers_all_metrics")),
    raw_data_dim = dim(bundle$raw_data),
    raw_data_names = names(bundle$raw_data)
  ))
})
stopifnot(!is.null(bundle))
mc <- bundle$metric_config
sc <- bundle$strat_config
pc <- bundle$predictor_config
frc <- bundle$factor_recode_config

## -- 02 clean + derive -------------------------------------------------------
dat <- NULL
section("02 clean+derive", {
  cleaned <- clean_data(bundle$raw_data, mc, sc, frc)
  dat <<- derive_variables(cleaned$data, frc, pc, sc)
  wj("02_qa_log", cleaned$qa_log)
  wj("02_derived", dat)
})
stopifnot(!is.null(dat))

## -- 03 precheck -------------------------------------------------------------
section("03 precheck", wj("03_precheck", run_metric_precheck(dat, mc)))

## -- 04 screening ------------------------------------------------------------
scr <- NULL
section("04 screening", {
  scr <<- run_all_stratification_screening(dat, mc, sc)
  wj("04_screening", drop_list_cols(scr$results))
  wj("04_pairwise", drop_list_cols(scr$pairwise))
})

## -- 05 effect sizes ---------------------------------------------------------
effects_all <- NULL
section("05 effects", {
  effects_all <<- purrr::map_dfr(names(mc), function(m) {
    strats <- mc[[m]]$allowed_stratifications
    if (is.null(strats) || length(strats) == 0) return(NULL)
    tryCatch(compute_effect_sizes(dat, m, strats, mc, sc), error = function(e) NULL)
  })
  wj("05_effects", effects_all)
})

## -- 05c pattern stability (metrics with predictors) --------------------------
section("05c stability", {
  stab <- purrr::map_dfr(names(mc), function(m) {
    preds <- mc[[m]]$allowed_predictors %||% names(pc)
    if (length(preds) == 0) return(NULL)
    out <- tryCatch(
      assess_pattern_stability(dat, m, strat_key = NULL, predictor_keys = preds,
                               metric_config = mc, strat_config = sc,
                               predictor_config = pc),
      error = function(e) NULL
    )
    if (is.data.frame(out)) out else if (is.list(out) && is.data.frame(out$results)) out$results else NULL
  })
  wj("05c_stability", drop_list_cols(stab))
})

## -- 05e feasibility ---------------------------------------------------------
feas <- NULL
section("05e feasibility", {
  feas <<- assess_feasibility(dat, names(sc), sc)
  wj("05e_feasibility", feas)
})

## -- 06 decisions ------------------------------------------------------------
dec <- NULL
section("06 decisions", {
  dec <<- make_stratification_decisions(scr$results, scr$pairwise, mc, sc,
                                        effect_sizes = effects_all,
                                        feasibility = feas)
  wj("06_decisions", drop_list_cols(dec))
})

## -- 07/08 models ------------------------------------------------------------
modb <- NULL
section("07 models", {
  modb <<- run_all_model_building(dat, dec, mc, pc, sc)
  wj("07_candidates", drop_list_cols(modb$all_candidates %||% modb$candidates))
  wj("07_importance", drop_list_cols(modb$all_importance %||% modb$importance))
})
sel <- NULL
section("08 selection", {
  sel <<- select_final_models(modb$all_candidates %||% modb$candidates,
                              modb$all_importance %||% modb$importance, mc)
  wj("08_selection", drop_list_cols(sel))
})

## -- 09 diagnostics ----------------------------------------------------------
section("09 diagnostics", {
  diag_out <- run_all_diagnostics(dat, sel, dec, mc)
  out <- if (is.data.frame(diag_out)) diag_out else diag_out$summary_df %||% diag_out$summary
  wj("09_diagnostics", drop_list_cols(out))
})

## -- 10 reference curves -----------------------------------------------------
rc <- NULL
section("10 curves", {
  rc <<- run_all_reference_curves(dat, mc)
  rows <- if (is.data.frame(rc)) rc else rc$registry %||% rc$rows
  wj("10_curve_registry", reference_curve_rows_for_export(rows))
  pts <- list()
  for (i in seq_len(nrow(rows))) {
    key <- paste0(rows$metric[[i]], "@@", rows$stratum[[i]] %||% "")
    cp <- rows$curve_points[[i]]
    if (is.data.frame(cp) && nrow(cp) > 0) pts[[key]] <- cp
  }
  wj("10_curve_points", pts)
})

## -- 11 cross-metric ---------------------------------------------------------
section("11 crossmetric", {
  cm <- run_cross_metric_analysis(dat, mc)
  wj("11_crossmetric", drop_list_cols(cm$results))
  wj("11_cor_matrix", as.data.frame(cm$cor_matrix))
})

## -- 12 regional -------------------------------------------------------------
section("12 regional", {
  reg <- run_regional_curves(dat, mc)
  out <- if (is.data.frame(reg)) reg else reg$results %||% reg$model_summaries
  wj("12_regional", drop_list_cols(out))
})

## -- 20 deep bundle ----------------------------------------------------------
section("20 deep bundle", {
  rows <- if (is.data.frame(rc)) rc else rc$registry %||% rc$rows
  mapping <- bundle$discipline_function_mapping
  if (is.null(mapping) || nrow(mapping) == 0) {
    mapping <- staf_metric_library_default_mapping(names(mc), mc)
  }
  dbundle <- build_deep_assessment_bundle(
    rows, mapping, metric_config = mc,
    meta = list(assessment_id = "golden-osam", assessment_name = "Golden OSAM",
                state_code = "MN", state_name = "Minnesota",
                source_citation = "golden fixture", applicability = "parity tests")
  )
  jsonlite::write_json(dbundle, file.path(golden_dir, "20_deep_bundle.json"),
                       auto_unbox = TRUE, digits = NA, na = "null", null = "null")
  cat("  wrote 20_deep_bundle.json\n")
})

## -- 30 profiler -------------------------------------------------------------
section("30 profiler", {
  source(file.path(r_repo, "app", "helpers", "data_profiler.R"))
  raw <- readxl::read_excel("OSAM_summarydata.xlsx", sheet = "data")
  prof <- profile_columns(as.data.frame(raw))
  wj("30_profiler", drop_list_cols(prof))
  wj("30_sanitize_keys", list(input = names(raw), output = sanitize_keys(names(raw))))
})

cat("\n==============================\n")
if (length(golden_failures)) {
  cat("FAILED sections:", paste(golden_failures, collapse = ", "), "\n")
  quit(status = 1)
} else {
  cat("All golden fixtures written to", golden_dir, "\n")
}
