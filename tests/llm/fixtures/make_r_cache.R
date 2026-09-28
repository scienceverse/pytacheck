# Regenerates tests/llm/fixtures/r_cache/: LLM cache entries written by metacheck
# itself (replaying tests/llm/mocks), which tests/llm/test_cache.py checks
# pytacheck reads and replays. Run from the repository root:
#   LANG=C.UTF-8 Rscript tests/llm/fixtures/make_r_cache.R
suppressPackageStartupMessages(library(metacheck))
d <- normalizePath("tests/llm/fixtures/r_cache")
unlink(list.files(d, pattern = "\\.rds$", full.names = TRUE))
options(metacheck.llm.use = TRUE, metacheck.llm.cache = TRUE)
Sys.setenv(GROQ_API_KEY = "test-key", METACHECK_LLM_CACHE_DIR = d)
ts <- ellmer::type_object(variables = ellmer::type_array(ellmer::type_object(
  variable_name = ellmer::type_string("Exact variable name/code in the data file"),
  label = ellmer::type_string("Verbatim description text from the codebook", required = FALSE),
  kind = ellmer::type_enum(c("a", "b", NA), "Kind", required = FALSE))))
httptest2::with_mock_dir("tests/llm/mocks", {
  llm(c("hello", "12"), "Is this a number? Answer TRUE or FALSE", model = "groq/test-model")
  llm(c("two vars", "none"), "Extract variables", type = ts,
      model = "groq/openai/gpt-oss-20b", capture_reasoning = TRUE)
})
cat(list.files(d), sep = "\n")
