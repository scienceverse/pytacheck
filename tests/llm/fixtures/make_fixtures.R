# Regenerates the R-derived fixtures of tests/llm (run from the repository root):
#   LANG=C.UTF-8 Rscript tests/llm/fixtures/make_fixtures.R
# * yajl_errors.json   jsonlite::parse_json() error messages for malformed JSON
# * type_prints.json   capture.output(print(<ellmer type>)) for type specs
# * request_bodies.json the request (mock path + JSON body) llm() sends per provider
# * rds/*.rds          saveRDS() output for the cache's R object reader
suppressPackageStartupMessages(library(metacheck))
out <- "tests/llm/fixtures"

# yajl ---------------------------------------------------------------------------
bad <- c('{"a":1', '```json\n{"a":1}\n```', 'hello', '', '{"a":1}x', '{"a" 1}', '[1,2',
         '[1 2]', '{1:2}', '{"a":01}', '{"a":1.}', '{"a":-}', '{"a":1e}', '{"a":"\\q"}',
         '{"a":tru}', '"abc', '{"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa": nul}',
         '[1,]', '{"a":1,}', '  {"a":[1,2,{"b":x}]}', 'Here is the JSON: {"a": 1}',
         '{"a":"ééééé" x}', '{"a":"x\ty"}', '{"a":1}{"b":2}', '[]]',
         '{"a" : 1 "b": 2}', 'tr', '[1.', '{"a":"\\u12G4"}', '{"a":[1,2,3], "b": {"c": [true, false, nul]}}')
yajl <- lapply(bad, function(t) list(input = t, error = tryCatch({jsonlite::parse_json(t); NULL},
                                                              error = function(e) conditionMessage(e))))
writeLines(jsonlite::toJSON(yajl, auto_unbox = TRUE, null = "null", pretty = TRUE),
           file.path(out, "yajl_errors.json"), useBytes = TRUE)

# type prints ---------------------------------------------------------------------
e <- new.env()
sys.source(system.file("modules/power.R", package = "metacheck"), envir = e)
types <- list(
  power = e$.power_type_spec(),
  basic = ellmer::type_object(construct = ellmer::type_string("The construct"),
                              confidence = ellmer::type_string("high, medium, or low."),
                              n = ellmer::type_number(), b = ellmer::type_boolean("x", required = FALSE),
                              e = ellmer::type_enum(c("a", "b"))),
  array = ellmer::type_array(ellmer::type_string()),
  enum10 = ellmer::type_enum(letters[1:10]),
  enum30 = ellmer::type_enum(paste0("value_", 1:30), "desc"),
  schema = ellmer::type_from_schema('{"type":"object","properties":{"a":{"type":"string","x":1,"y":2.5,"z":true,"w":null,"v":[1,2]}},"required":[]}'),
  empty = ellmer::type_object(),
  quoted = ellmer::type_object(.description = "desc \"q\"\nnew\tline é中", a = ellmer::type_enum(c("x", NA))),
  long = ellmer::type_object(a = ellmer::type_string(strrep("abcdefghij", 13)),
                             b = ellmer::type_string(strrep("x", 126)), c = ellmer::type_string(strrep("y", 127))),
  nested = ellmer::type_object(scales = ellmer::type_array(ellmer::type_object(
    scale = ellmer::type_string("s"),
    columns = ellmer::type_array(ellmer::type_string("An exact item column name belonging to this scale.")),
    meta = ellmer::type_object(x = ellmer::type_integer()))))
)
prints <- lapply(types, function(t) utils::capture.output(print(t)))
writeLines(jsonlite::toJSON(prints, auto_unbox = FALSE, pretty = TRUE), file.path(out, "type_prints.json"),
           useBytes = TRUE)

# request bodies ----------------------------------------------------------------------
options(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE,
        metacheck.llm.vllm.base_url = "https://vllm.example.test/v1")
Sys.setenv(GROQ_API_KEY = "k", GEMINI_API_KEY = "k", ANTHROPIC_API_KEY = "k", OPENAI_API_KEY = "k",
           MISTRAL_API_KEY = "k", DEEPSEEK_API_KEY = "k", OPENROUTER_API_KEY = "k", VLLM_API_KEY = "k")
ts <- ellmer::type_object(variables = ellmer::type_array(ellmer::type_object(
  variable_name = ellmer::type_string("Exact name"),
  label = ellmer::type_string("Label", required = FALSE),
  kind = ellmer::type_enum(c("a", "b", NA), "Kind", required = FALSE),
  n = ellmer::type_integer(required = FALSE), ok = ellmer::type_boolean())))
sc <- list(
  groq_plain = function() llm("hello é \"q\"\n", "Sys prompt", model = "groq/test-model", params = list(seed = 42, top_p = 0.5, stop_sequences = "END")),
  groq_struct = function() llm("text one", "Extract", type = ts, model = "groq/openai/gpt-oss-20b"),
  groq_qwen3 = function() llm("x", "S", model = "groq/qwen/qwen3-32b"),
  gemini_plain = function() llm("hello", "Sys", model = "google_gemini/gemini-2.5-flash", params = list(seed = 1, top_k = 3)),
  gemini_struct = function() llm("hello", "Sys", type = ts, model = "google_gemini/gemini-2.5-flash"),
  anthropic_plain = function() llm("hello", "Sys", model = "anthropic/claude-sonnet-4-5"),
  anthropic_struct = function() llm("hello", "Sys", type = ts, model = "anthropic/claude-sonnet-4-5"),
  anthropic_tool = function() llm("hello", "Sys", type = ts, model = "anthropic/claude-3-5-haiku-latest"),
  openai_plain = function() llm("hello", "Sys", model = "openai/gpt-4.1-mini"),
  openai_struct = function() llm("hello", "Sys", type = ts, model = "openai/gpt-5-mini"),
  mistral_plain = function() llm("hello", "Sys", model = "mistral/mistral-small-latest", params = list(seed = 7)),
  deepseek_plain = function() llm("hello", "Sys", model = "deepseek/deepseek-chat"),
  openrouter_plain = function() llm("hello", "Sys", model = "openrouter/openai/gpt-4o"),
  vllm_plain = function() llm("hello", "Sys", model = "vllm/GLM-5"),
  groq_array_top = function() llm("hello", "Sys", type = ellmer::type_array(ellmer::type_string()), model = "groq/m"),
  gemini_default = function() llm("hello", "Sys", model = "google_gemini"),
  groq_maxtok = function() {
    withr::local_options(metacheck.llm_max_tokens = 8192L)
    llm("hello", "Sys", model = "groq/m", params = list(frequency_penalty = 0.1, presence_penalty = -0.2, log_probs = TRUE))
  }
)
empty <- tempfile("mocks")
dir.create(empty)
bodies <- lapply(names(sc), function(n) {
  r <- suppressWarnings(suppressMessages(httptest2::with_mock_dir(empty, sc[[n]]())))
  m <- if (".error_msg" %in% names(r)) r$.error_msg[1] else r$error_msg[1]
  list(name = n,
       path = sub("\\.\\*$", "", sub(".*Expected mock file: ", "", m)),
       body = sub("^An unexpected request was made:\n[A-Z]+ [^ ]+ ", "", sub("\nExpected mock file.*", "", m)))
})
writeLines(jsonlite::toJSON(bodies, auto_unbox = TRUE, pretty = TRUE), file.path(out, "request_bodies.json"),
           useBytes = TRUE)

# rds -------------------------------------------------------------------------------
saveRDS(1:10, file.path(out, "rds", "intseq.rds"))
saveRDS(data.frame(i = 1:3, d = c(1.5, NA, -2), s = c("a", NA, "é"), l = c(TRUE, NA, FALSE),
                   f = factor(c("x", "y", NA), levels = c("y", "x")), stringsAsFactors = FALSE),
        file.path(out, "rds", "frame.rds"))
saveRDS(list(a = 1L, b = list(c = "x", d = NULL), t = as.POSIXct(0, origin = "1970-01-01", tz = "UTC"),
             v = c(NaN, Inf, -Inf, NA)), file.path(out, "rds", "nested.rds"))
saveRDS(tibble::tibble(x = c("p", "q"), y = 1:2), file.path(out, "rds", "tibble.rds"))
saveRDS(c(a = 1, b = 2), file.path(out, "rds", "named_vec.rds"), compress = FALSE)
saveRDS(list(x = "bz"), file.path(out, "rds", "bzip2.rds"), compress = "bzip2")
saveRDS(list(x = "xz"), file.path(out, "rds", "xz.rds"), compress = "xz")
cat("done\n")
