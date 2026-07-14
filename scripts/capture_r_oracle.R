#!/usr/bin/env Rscript

# Capture the field-aware R Metacheck oracle used by Pytacheck's aggregate parity tests.
# This is a developer-only script: R is deliberately absent from the production package/image.

DEFAULT_MODULES <- c(
  "power",
  "marginal",
  "stat_check",
  "stat_effect_size",
  "stat_p_exact",
  "stat_p_nonsig"
)

TABLE_FIELDS <- list(
  power = c(
    "text", "paragraph_id", "section_id", "paper_id", "header", "section_type",
    "power_type", "complete", "power_id"
  ),
  marginal = c(
    "text", "text_id", "paragraph_id", "section_id", "paper_id", "header", "section_type"
  ),
  stat_check = c(
    "test_type", "df1", "df2", "test_comp", "test_value", "p_comp", "reported_p",
    "computed_p", "raw", "error", "decision_error", "text", "text_id", "paragraph_id",
    "section_id", "paper_id", "header", "section_type"
  ),
  stat_effect_size = c(
    "paper_id", "text_id", "test", "test_text", "es", "section_id", "paragraph_id", "text",
    "d_reported", "d_reported_text", "t_value", "df", "d_implied_paired_dz",
    "d_implied_paired_drm_r05", "d_implied_indep_equal_n",
    "d_implied_indep_unequal_min", "d_implied_indep_unequal_max", "d_implied_n",
    "d_coherence", "d_coherence_assumption", "d_coherence_note", "f_reported",
    "f_reported_text", "df1", "df2", "eta_implied_partial", "omega_implied_partial",
    "eta_coherence", "eta_coherence_assumption", "eta_coherence_note"
  ),
  stat_p_exact = c(
    "text", "text_id", "paragraph_id", "section_id", "paper_id", "header", "section_type",
    "p_comp", "p_value", "expanded", "imprecise", "zero"
  ),
  stat_p_nonsig = c(
    "text", "text_id", "paragraph_id", "section_id", "paper_id", "header", "section_type",
    "p_comp", "p_value", "significance", "expanded"
  )
)

SUMMARY_FIELDS <- list(
  power = c("paper_id", "power_n", "power_complete"),
  marginal = c("paper_id", "marginal"),
  stat_check = c(
    "paper_id", "statcheck_found", "statcheck_errors", "statcheck_decision_errors"
  ),
  stat_effect_size = c(
    "paper_id", "ttests_with_es", "ttests_without_es", "Ftests_with_es", "Ftests_without_es"
  ),
  stat_p_exact = c("paper_id", "n_imprecise", "n_zero"),
  stat_p_nonsig = c("paper_id", "n_nonsignificant")
)

FLOAT_FIELDS <- list(
  stat_check = c("computed_p")
)

PROVIDER_CREDENTIAL_ENV <- c(
  "OSF_PAT", "SCIVRS_API_KEY", "REGCHECK_API_TOKEN", "OLLAMA_BASE_URL", "GROQ_API_KEY",
  "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
  "CLOUDFLARE_API_KEY", "DEEPSEEK_API_KEY", "HUGGINGFACE_API_KEY", "MISTRAL_API_KEY",
  "OPENROUTER_API_KEY", "PERPLEXITY_API_KEY", "PORTKEY_API_KEY", "AZURE_OPENAI_ENDPOINT",
  "DATABRICKS_HOST", "GITHUB_PAT"
)

usage <- function() {
  paste(
    "Usage:",
    "Rscript scripts/capture_r_oracle.R",
    "--metacheck /path/to/metacheck",
    "--input /path/to/paper.json",
    "--output /path/to/expected.json"
  )
}

parse_args <- function(args) {
  allowed <- c("--metacheck", "--input", "--output")
  if (length(args) != 6L || any(args[c(TRUE, FALSE)] != allowed)) {
    stop(usage(), call. = FALSE)
  }
  values <- args[c(FALSE, TRUE)]
  names(values) <- sub("^--", "", allowed)
  values
}

require_package <- function(package) {
  if (!requireNamespace(package, quietly = TRUE)) {
    stop("The developer package '", package, "' is required to capture the R oracle.", call. = FALSE)
  }
}

sha256_file <- function(path) {
  connection <- file(path, open = "rb")
  on.exit(close(connection), add = TRUE)
  unclass(as.character(openssl::sha256(connection)))
}

git_output <- function(checkout, args) {
  output <- system2(
    "git",
    c("-C", shQuote(checkout), args),
    stdout = TRUE,
    stderr = TRUE
  )
  status <- attr(output, "status")
  if (!is.null(status) && status != 0L) {
    stop("git command failed: ", paste(output, collapse = "\n"), call. = FALSE)
  }
  output
}

relevant_source_status <- function(checkout) {
  git_output(
    checkout,
    c(
      "status", "--porcelain", "--untracked-files=all", "--", "DESCRIPTION", "R", "inst/modules"
    )
  )
}

sanitize_capture_environment <- function() {
  Sys.unsetenv(PROVIDER_CREDENTIAL_ENV)
  deny_proxy <- "http://127.0.0.1:9"
  Sys.setenv(
    "HTTP_PROXY" = deny_proxy,
    "HTTPS_PROXY" = deny_proxy,
    "ALL_PROXY" = deny_proxy,
    "NO_PROXY" = "",
    "http_proxy" = deny_proxy,
    "https_proxy" = deny_proxy,
    "all_proxy" = deny_proxy,
    "no_proxy" = "",
    "R_DEFAULT_INTERNET_TIMEOUT" = "1"
  )
}

scalar_value <- function(value) {
  if (is.null(value) || length(value) == 0L || all(is.na(value))) {
    return(NULL)
  }
  if (is.factor(value)) {
    value <- as.character(value)
  }
  if (is.integer(value)) {
    return(as.integer(value[[1L]]))
  }
  if (is.numeric(value)) {
    return(as.numeric(value[[1L]]))
  }
  if (is.logical(value)) {
    return(as.logical(value[[1L]]))
  }
  as.character(value[[1L]])
}

select_rows <- function(table, fields) {
  if (is.null(table) || nrow(table) == 0L) {
    return(list())
  }
  missing_fields <- setdiff(fields, names(table))
  if (length(missing_fields) > 0L) {
    stop("Oracle table is missing fields: ", paste(missing_fields, collapse = ", "), call. = FALSE)
  }
  lapply(seq_len(nrow(table)), function(index) {
    values <- lapply(fields, function(field) {
      column <- table[[field]]
      value <- if (is.list(column)) column[[index]] else column[index]
      scalar_value(value)
    })
    stats::setNames(values, fields)
  })
}

block_llm_calls <- function() {
  options(
    metacheck.llm.use = FALSE,
    metacheck.llm.model = NULL,
    metacheck.llm_max_calls = 0L
  )
  namespace <- asNamespace("metacheck")
  if (exists("llm", envir = namespace, inherits = FALSE)) {
    unlockBinding("llm", namespace)
    assign(
      "llm",
      function(...) stop("LLM calls are disabled while capturing the R oracle.", call. = FALSE),
      envir = namespace
    )
    lockBinding("llm", namespace)
  }
}

args <- parse_args(commandArgs(trailingOnly = TRUE))
for (package in c("jsonlite", "openssl", "pkgload")) {
  require_package(package)
}

metacheck_checkout <- normalizePath(args[["metacheck"]], mustWork = TRUE)
input_path <- normalizePath(args[["input"]], mustWork = TRUE)
output_path <- normalizePath(args[["output"]], mustWork = FALSE)

if (!file.exists(file.path(metacheck_checkout, "DESCRIPTION"))) {
  stop("--metacheck must point to a Metacheck source checkout.", call. = FALSE)
}

relevant_status <- relevant_source_status(metacheck_checkout)
if (length(relevant_status) > 0L) {
  stop(
    "Refusing to capture from dirty Metacheck source:\n",
    paste(relevant_status, collapse = "\n"),
    call. = FALSE
  )
}

sanitize_capture_environment()
# attach=FALSE avoids Metacheck's interactive startup/version check, which contacts GitHub.
pkgload::load_all(metacheck_checkout, quiet = TRUE, attach = FALSE)
block_llm_calls()

actual_defaults <- metacheck::module_defaults()
if (!identical(actual_defaults, DEFAULT_MODULES)) {
  stop(
    "Metacheck default module drift: expected ", paste(DEFAULT_MODULES, collapse = ", "),
    "; got ", paste(actual_defaults, collapse = ", "),
    call. = FALSE
  )
}

paper <- metacheck::read(input_path)
module_output <- metacheck::report_module_run(
  paper,
  DEFAULT_MODULES,
  args = list(power = list(use_llm = FALSE))
)
if (!identical(names(module_output), DEFAULT_MODULES)) {
  stop("R module output order drifted from the validated defaults.", call. = FALSE)
}

aggregate_summary <- module_output[[length(module_output)]]$summary_table
if (is.null(aggregate_summary) || nrow(aggregate_summary) != 1L) {
  stop("Expected exactly one aggregate summary row from the R module pipeline.", call. = FALSE)
}

results <- stats::setNames(lapply(DEFAULT_MODULES, function(module_name) {
  output <- module_output[[module_name]]
  list(
    module = module_name,
    title = scalar_value(output$title),
    traffic_light = scalar_value(output$traffic_light),
    table = select_rows(output$table, TABLE_FIELDS[[module_name]]),
    summary_table = select_rows(aggregate_summary, SUMMARY_FIELDS[[module_name]])
  )
}), DEFAULT_MODULES)

module_paths <- stats::setNames(
  lapply(DEFAULT_MODULES, metacheck:::module_find),
  DEFAULT_MODULES
)
module_hashes <- stats::setNames(
  lapply(module_paths, sha256_file),
  DEFAULT_MODULES
)
oracle <- list(
  provenance = list(
    oracle = "Metacheck R field-aware aggregate oracle",
    fixture_sha256 = sha256_file(input_path),
    metacheck_version = as.character(utils::packageVersion("metacheck")),
    metacheck_git_commit = git_output(metacheck_checkout, c("rev-parse", "HEAD"))[[1L]],
    metacheck_relevant_source_dirty = FALSE,
    module_sha256 = module_hashes,
    statcheck_version = as.character(utils::packageVersion("statcheck")),
    r_version = R.version.string,
    defaults = DEFAULT_MODULES,
    llm = list(enabled = FALSE, max_calls = 0L, network_model_calls = "blocked"),
    capture_command = paste(
      "Rscript scripts/capture_r_oracle.R",
      "--metacheck <METACHECK_CHECKOUT>",
      "--input tests/parity/fixtures/to_err_is_human.json",
      "--output tests/parity/fixtures/to_err_is_human.expected.json"
    )
  ),
  normalization = list(
    version = "field-aware-v1",
    table_fields = TABLE_FIELDS,
    summary_fields = SUMMARY_FIELDS,
    float_fields = FLOAT_FIELDS,
    float_abs_tolerance = 1e-10,
    rules = c(
      "Preserve validated default-module order and source row order.",
      "Project declared shared contract fields; convert R NA and absent optional values to JSON null.",
      "Compare computed_p with absolute tolerance only; compare extracted numeric, text, and categorical values exactly.",
      "Normalize Pytacheck power_complete null to the R aggregate zero produced by na_replace.",
      "Do not use text similarity or aggregate similarity scores."
    ),
    intentional_pytacheck_extensions_or_omissions = c(
      "Pytacheck retains a representative text_id on expanded power-analysis paragraphs; R drops it, so the shared oracle omits power.text_id.",
      "R-only statcheck one_tailed_in_txt and apa_factor diagnostics are not in the initial Pytacheck JSON contract.",
      "Rendered R report markup and prose are outside the JSON computation parity boundary.",
      "Pytacheck rejects adversarial extraction and output amplification with bounded production budgets.",
      "Pytacheck does not treat malformed, out-of-domain, or non-finite statistics as valid checks.",
      "Pytacheck exact-zero detection follows the reported lexical token, and star-note exemptions are match-local.",
      "Pytacheck activates partial-omega coherence even though the current R parser leaves that branch unreachable."
    )
  ),
  modules_run = DEFAULT_MODULES,
  results = results
)

dir.create(dirname(output_path), recursive = TRUE, showWarnings = FALSE)
jsonlite::write_json(
  oracle,
  output_path,
  auto_unbox = TRUE,
  pretty = TRUE,
  null = "null",
  na = "null",
  digits = 16
)
cat("Wrote field-aware R oracle to ", output_path, "\n", sep = "")
