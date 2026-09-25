# R side of the codebook_check parity cases (parity/cases/mod_codebook.yaml).
#
# codebook_check reads data_check's output through get_prev_outputs(). The
# fixtures (tests/mod_codebook/fixtures/make_fixtures.R) store that output as
# canonical parity JSON; cbc_prev() rebuilds it as a metacheck_module_output
# whose paper is a test paper (or any paper passed in), so module_run() chains
# codebook_check onto it exactly as in a report pipeline. The Python twin is
# tests/mod_codebook/helpers.py.

cbc_fixtures <- "tests/mod_codebook/fixtures"

# Canonical parity JSON (parity/r/canonical.R) -> R value.
cbc_decode <- function(x) {
  if (is.null(x) || identical(x$t, "null")) return(NULL)
  t <- x$t
  if (t %in% c("chr", "dbl", "int", "lgl")) {
    v <- x$v
    conv <- switch(t,
      chr = function(e) if (is.null(e)) NA_character_ else as.character(e),
      dbl = function(e) {
        if (is.null(e)) NA_real_
        else if (identical(e, "NaN")) NaN
        else if (identical(e, "Inf")) Inf
        else if (identical(e, "-Inf")) -Inf
        else as.double(e)
      },
      int = function(e) if (is.null(e)) NA_integer_ else as.integer(e),
      lgl = function(e) if (is.null(e)) NA else as.logical(e))
    proto <- switch(t, chr = NA_character_, dbl = NA_real_, int = NA_integer_,
                    lgl = NA)
    out <- if (length(v)) vapply(v, conv, proto) else proto[0]
    out <- unname(out)
    if (!is.null(x$names)) names(out) <- unlist(x$names)
    return(out)
  }
  if (t == "df") {
    cols <- lapply(x$v, cbc_decode)
    n <- x$nrow
    cols <- lapply(cols, function(col) if (is.null(col)) rep(NA, n) else col)
    names(cols) <- unlist(x$names)
    return(structure(cols, class = "data.frame", row.names = seq_len(n)))
  }
  if (t == "list") {
    out <- lapply(x$v, cbc_decode)
    if (!is.null(x$names)) names(out) <- unlist(x$names)
    return(out)
  }
  stop("cannot decode canonical type ", t)
}

# The stored data_check output of a fixture scenario.
cbc_dc <- function(scenario) {
  path <- file.path(cbc_fixtures, "dc", paste0(scenario, ".json"))
  jsonlite::fromJSON(path, simplifyVector = FALSE)
}

# A test paper of the scenario's text with a fixed id (test_paper() ids are random).
cbc_paper <- function(scenario, pid = "p1") {
  dc <- cbc_dc(scenario)
  p <- test_paper(as.character(unlist(dc$text)))
  p$paper_id <- pid
  p
}

.cbc_stamp <- function(df, pid) {
  if (!is.null(df) && "paper_id" %in% names(df)) df$paper_id <- rep(pid, nrow(df))
  df
}

# The data_check pieces of one scenario, stamped with `pid`.
cbc_pieces <- function(scenario, pid = "p1") {
  dc <- cbc_dc(scenario)
  list(table = .cbc_stamp(cbc_decode(dc$table), pid),
       structure = .cbc_stamp(cbc_decode(dc$structure), pid),
       previews = cbc_decode(dc$previews))
}

.cbc_output <- function(paper, pieces, pids) {
  structure(list(
    module = "data_check",
    title = "Data Check",
    section = "results",
    table = pieces$table,
    report = "",
    traffic_light = "info",
    summary_text = NULL,
    summary_table = data.frame(paper_id = pids),
    paper = paper,
    prev_outputs = list(),
    structure = pieces$structure,
    previews = pieces$previews
  ), class = "metacheck_module_output")
}

# data_check's output for `scenario`, on `paper` (default: the scenario's test paper).
cbc_prev <- function(scenario, paper = NULL, pid = NULL, drop = character(0)) {
  if (is.null(paper)) paper <- cbc_paper(scenario, pid %||% "p1")
  pid <- pid %||% paper$paper_id
  pieces <- cbc_pieces(scenario, pid)
  pieces[drop] <- list(NULL)
  .cbc_output(paper, pieces, pid)
}

# data_check keys previews by file name: a later file of the same name replaces
# the earlier one (file_previews[[f$file_name]] <<- df).
.cbc_merge_previews <- function(parts) {
  out <- list()
  for (p in parts) for (nm in names(p)) out[[nm]] <- p[[nm]]
  out
}

# data_check's output for a paper list: one scenario per paper (ids p1, p2, ...).
cbc_prev_list <- function(scenarios) {
  pids <- paste0("p", seq_along(scenarios))
  papers <- lapply(seq_along(scenarios), function(i) cbc_paper(scenarios[[i]], pids[[i]]))
  pl <- do.call(paperlist, papers)
  parts <- lapply(seq_along(scenarios), function(i) cbc_pieces(scenarios[[i]], pids[[i]]))
  pieces <- list(
    table = dplyr::bind_rows(lapply(parts, `[[`, "table")),
    structure = dplyr::bind_rows(lapply(parts, `[[`, "structure")),
    previews = .cbc_merge_previews(lapply(parts, `[[`, "previews")))
  .cbc_output(pl, pieces, pids)
}

# module_run(<data_check output>, "codebook_check", ...).
cbc_run <- function(scenario, ..., paper = NULL) {
  module_run(cbc_prev(scenario, paper = paper), "codebook_check", ...)
}

# data_check's output for a real paper list: scenario i's data belongs to paper i.
cbc_prev_papers <- function(papers, scenarios) {
  pids <- names(papers)
  parts <- lapply(seq_along(scenarios), function(i) cbc_pieces(scenarios[[i]], pids[[i]]))
  pieces <- list(
    table = dplyr::bind_rows(lapply(parts, `[[`, "table")),
    structure = dplyr::bind_rows(lapply(parts, `[[`, "structure")),
    previews = .cbc_merge_previews(lapply(parts, `[[`, "previews")))
  .cbc_output(papers, pieces, pids)
}

# The data of every scroll_table() in a module report: the `table <- ...` line
# of each R chunk (also those pasted inside the tabset string), evaluated.
cbc_report_tables <- function(mo) {
  txt <- paste(mo$report, collapse = "\n")
  chunks <- regmatches(txt, gregexpr("(?s)```\\{r\\}.*?\n```", txt, perl = TRUE))[[1]]
  lapply(chunks, function(s) {
    code <- sub("^```\\{r\\}", "", sub("```$", "", s))
    e <- new.env()
    eval(parse(text = code)[[1]], e)
    df <- as.data.frame(e$table, check.names = FALSE)
    rownames(df) <- NULL
    df
  })
}

# module_run(<data_check output of `scenario`>, "codebook_check") report tables.
cbc_run_tables <- function(scenario, ...) {
  cbc_report_tables(module_run(cbc_prev(scenario), "codebook_check", ...))
}

# ── LLM tiers: a deterministic mock of llm() ─────────────────────────────────
# The same rules as tests/mod_codebook/helpers.py::mock_llm(), driven by a spec
# from fixtures/llm_mock.json. Array-wrapped answers carry ellmer's
# "<wrapper>." column prefix, which the module strips.
cbc_mock_spec <- function(name) {
  jsonlite::fromJSON(file.path(cbc_fixtures, "llm_mock.json"), simplifyVector = FALSE)[[name]]
}

.cbc_str <- function(x) as.character(unlist(x))

cbc_mock_llm <- function(spec) {
  function(text, system_prompt = NULL, type = NULL, text_col = "text", model = NULL,
           params = list(), phase = NULL, ...) {
    if (!is.null(phase) && phase %in% .cbc_str(spec$error_phases)) stop("mock LLM error")
    txt <- as.character(text[[text_col]][1])
    lines <- strsplit(txt, "\n", fixed = TRUE)[[1]]
    res <- switch(phase,
      "Identifying scales in the manuscript" = {
        ts <- spec$text_scales
        if (is.null(ts)) data.frame() else {
          df <- as.data.frame(lapply(ts, .cbc_str), stringsAsFactors = FALSE)
          names(df) <- paste0("scales.", names(df))
          df
        }
      },
      "Parsing codebook" = {
        def <- lines[grepl(" - ", lines, fixed = TRUE)]
        pos <- regexpr(" - ", def, fixed = TRUE)
        vars <- substr(def, 1, pos - 1)
        labs <- substr(def, pos + 3, nchar(def))
        keep <- grepl("^[A-Za-z_][A-Za-z0-9_.]*$", vars)
        data.frame(variables.variable_name = vars[keep], variables.label = labs[keep],
                   variables.experiment_context = rep("", sum(keep)),
                   check.names = FALSE, stringsAsFactors = FALSE)
      },
      "Matching codebook columns" = if (startsWith(txt, "Column: ")) {
        cand <- sub("^- ", "", lines[startsWith(lines, "- ")])
        canon <- if (identical(spec$merge$canonical, "first")) cand[1] else .cbc_str(spec$merge$canonical)
        data.frame(equivalent = isTRUE(spec$merge$equivalent), canonical = canon,
                   stringsAsFactors = FALSE)
      } else {
        blank <- which(lines == "")[1]
        cols <- sub("^[0-9]+\\. ", "", lines[seq(2, blank - 1)])
        vars <- sub("^[0-9]+\\. ", "", lines[seq(blank + 2, length(lines))])
        n <- spec$match_prefix %||% 3
        key <- function(x) substr(tolower(gsub("[^A-Za-z]", "", x)), 1, n)
        kv <- key(vars)
        pairs <- Filter(Negate(is.null), lapply(cols, function(cn) {
          k <- key(cn)
          j <- which(nzchar(kv) & kv == k)
          if (nzchar(k) && length(j)) c(cn, vars[j[1]])
        }))
        data.frame(matches.column_name = vapply(pairs, `[`, "", 1),
                   matches.codebook_variable = vapply(pairs, `[`, "", 2),
                   check.names = FALSE, stringsAsFactors = FALSE)
      },
      "Matching scales to text" = {
        grp <- lines[startsWith(lines, "- ") & grepl(": [0-9]+ columns", lines)]
        pfx <- sub("^- ([^:]+): .*$", "\\1", grp)
        pn <- spec$prefix_names
        sn <- vapply(pfx, function(p) if (!is.null(pn[[p]])) .cbc_str(pn[[p]])[1] else "", "")
        cf <- vapply(pfx, function(p) if (!is.null(pn[[p]])) .cbc_str(pn[[p]])[2] else "low", "")
        data.frame(scales.prefix = pfx, scales.scale_name = unname(sn),
                   scales.confidence = unname(cf), check.names = FALSE,
                   stringsAsFactors = FALSE)
      },
      "Labelling scales" = {
        first <- sub("^- ", "", lines[2])
        cs <- spec$constructs[[first]]
        data.frame(construct = if (is.null(cs)) "" else .cbc_str(cs)[1],
                   confidence = if (is.null(cs)) "medium" else .cbc_str(cs)[2],
                   stringsAsFactors = FALSE)
      },
      stop("unexpected phase ", phase))
    attr(res, "llm") <- list(model = "mock/cbc")
    res
  }
}

# module_run(<data_check output>, "codebook_check", ...) with llm_use(TRUE) and
# llm() mocked by the named spec.
cbc_llm_run <- function(scenario, spec, ..., prev = NULL) {
  prev <- prev %||% cbc_prev(scenario)
  testthat::with_mocked_bindings(
    withr::with_options(list(metacheck.llm.use = TRUE),
                        module_run(prev, "codebook_check", ...)),
    llm = cbc_mock_llm(cbc_mock_spec(spec)), .package = "metacheck")
}
