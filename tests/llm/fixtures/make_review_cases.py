"""Generates parity/cases/llm_review.yaml (run from the repo root):

    python tests/llm/fixtures/make_review_cases.py parity/cases/llm_review.yaml

Then regenerate the goldens with ``python -m parity generate --area llm_review``.

These are the adversarial-review cases for the llm area: input shapes, joins,
error paths and argument checks that parity/cases/llm.yaml did not reach.
Network cases replay tests/llm/mocks (the mock file names are the ones R's
httptest2 computes for each request; see make_mocks.py).
"""

import sys

import yaml

H = "__import__('tests.llm.parity_helpers', fromlist=['_'])"
PD = "__import__('pandas')"
NP = "__import__('numpy')"
cases = []


def catch_r(r):
    """R: the value (or ``list(error = msg)``) plus every warning text."""
    return (
        "local({ws <- character(0); v <- tryCatch(withCallingHandlers("
        + r
        + ", warning = function(w) {ws <<- c(ws, conditionMessage(w)); "
        "invokeRestart('muffleWarning')}), error = function(e) list(error = conditionMessage(e))); "
        "list(value = v, warnings = ws)})"
    )


def catch_py(py):
    return f"{H}.catch(lambda: {py})"


def expr_case(id, r, py, catch=True, **kw):
    """A case comparing ``r`` with ``py``; by default error and warning texts too."""
    if catch:
        r, py = catch_r(r), catch_py(py)
    c = {
        "id": id,
        "r": "base::identity",
        "py": "tests.llm.parity_helpers.identity",
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }
    c.update(kw)
    cases.append(c)


def opt_case(id, r, py, ropts="list()", popts="{}", **kw):
    expr_case(id, f"withr::with_options({ropts}, {r})", f"{H}.scoped(lambda: {py}, {popts})", **kw)


MOCK = "tests/llm/mocks"
KEYS_R = (
    "c(GROQ_API_KEY = 'test-key', GEMINI_API_KEY = 'test-key', GOOGLE_API_KEY = NA, "
    "ANTHROPIC_API_KEY = 'test-key', ANTHROPIC_BASE_URL = NA, OPENAI_API_KEY = 'test-key', "
    "MISTRAL_API_KEY = 'test-key', OLLAMA_BASE_URL = NA)"
)
KEYS_PY = (
    "{'GROQ_API_KEY': 'test-key', 'GEMINI_API_KEY': 'test-key', 'GOOGLE_API_KEY': None, "
    "'ANTHROPIC_API_KEY': 'test-key', 'ANTHROPIC_BASE_URL': None, 'OPENAI_API_KEY': 'test-key', "
    "'MISTRAL_API_KEY': 'test-key', 'OLLAMA_BASE_URL': None}"
)
OPTS_R = (
    "list(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE, metacheck.llm_reasoning = NULL, "
    "metacheck.llm_max_tokens = NULL, metacheck.llm_max_calls = 30L)"
)
OPTS_PY = (
    f"{{**{H}.LLM_ON, 'metacheck.llm_reasoning': None, 'metacheck.llm_max_tokens': None, "
    f"'metacheck.llm_max_calls': {H}.RInt(30)}}"
)
ON_R = "list(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE)"
ON_PY = f"{H}.LLM_ON"


def net_case(id, r, py, mock_dir=MOCK, **kw):
    expr_case(
        id,
        f"withr::with_options({OPTS_R}, withr::with_envvar({KEYS_R}, {r}))",
        f"{H}.scoped(lambda: {py}, {OPTS_PY}, {KEYS_PY})",
        mock_dir=mock_dir,
        **kw,
    )


NUM = "'Is this a number? Answer TRUE or FALSE'"
GM = "model = 'groq/test-model'"
GM_PY = "model='groq/test-model'"
TS_R = (
    "ellmer::type_object(variables = ellmer::type_array(ellmer::type_object("
    "variable_name = ellmer::type_string('Exact variable name/code in the data file'), "
    "label = ellmer::type_string('Verbatim description text from the codebook', required = FALSE), "
    "kind = ellmer::type_enum(c('a', 'b', NA), 'Kind', required = FALSE))))"
)
TS_PY = (
    f"{H}.T.type_object(variables={H}.T.type_array({H}.T.type_object("
    f"variable_name={H}.T.type_string('Exact variable name/code in the data file'), "
    f"label={H}.T.type_string('Verbatim description text from the codebook', required=False), "
    f"kind={H}.T.type_enum(['a', 'b', None], 'Kind', required=False))))"
)
XM = "model = 'groq/openai/gpt-oss-20b'"
XM_PY = "model='groq/openai/gpt-oss-20b'"
XP = "'Extract variables'"

# ---- llm(): input shapes (unstructured) ----------------------------------------------
net_case(
    "llm.input.factor_col",
    f"llm(data.frame(txt = factor(c('hello', '12', 'hello'))), {NUM}, text_col = 'txt', {GM})",
    f"{H}.L.llm({PD}.DataFrame({{'txt': {PD}.Categorical(['hello', '12', 'hello'])}}), {NUM}, text_col='txt', {GM_PY})",
)
net_case(
    "llm.input.column_clash",
    f"llm(data.frame(text = c('hello', '12'), answer = c('x', 'y'), error = c(1L, 2L)), {NUM}, {GM})",
    f"{H}.L.llm({PD}.DataFrame({{'text': ['hello', '12'], 'answer': ['x', 'y'], 'error': {PD}.array([1, 2], dtype='Int64')}}), {NUM}, {GM_PY})",
)
net_case(
    "llm.input.null",
    f"llm(NULL, {NUM}, {GM})",
    f"{H}.L.llm(None, {NUM}, {GM_PY})",
)
net_case(
    "llm.input.empty_df",
    f"llm(data.frame(text = character(0)), {NUM}, {GM})",
    f"{H}.L.llm({PD}.DataFrame({{'text': {PD}.Series([], dtype='string')}}), {NUM}, {GM_PY})",
)
net_case(
    "llm.input.empty_vector",
    f"llm(character(0), {NUM}, {GM})",
    f"{H}.L.llm([], {NUM}, {GM_PY})",
)
net_case(
    "llm.input.tibble_index",
    f"llm(tibble::tibble(id = 3:1, text = c('12', 'hello', '12')), {NUM}, {GM})",
    f"{H}.L.llm({PD}.DataFrame({{'id': {PD}.array([3, 2, 1], dtype='Int64'), 'text': ['12', 'hello', '12']}}, index=[10, 5, 7]), {NUM}, {GM_PY})",
)
net_case(
    "llm.input.numpy",
    f"llm(c('hello', '12'), {NUM}, {GM})",
    f"{H}.L.llm({NP}.array(['hello', '12']), {NUM}, {GM_PY})",
)
net_case(
    "llm.input.series",
    f"llm(c('12', 'hello'), {NUM}, {GM})",
    f"{H}.L.llm({PD}.Series(['12', 'hello'], index=[4, 2]), {NUM}, {GM_PY})",
)
net_case(
    "llm.input.tuple",
    f"llm(c('hello', '12'), {NUM}, {GM})",
    f"{H}.L.llm(('hello', '12'), {NUM}, {GM_PY})",
)
net_case(
    "llm.input.na_only",
    f"llm(NA_character_, {NUM}, {GM})",
    f"{H}.L.llm([None], {NUM}, {GM_PY})",
)
net_case(
    "llm.input.duplicate_errors_no_warning",
    f"llm(c('unauthorized', 'unauthorized'), 'Sys', {GM})",
    f"{H}.L.llm(['unauthorized', 'unauthorized'], 'Sys', {GM_PY})",
)
net_case(
    "llm.input.custom_col_vector",
    f"llm(c('hello', '12'), {NUM}, text_col = 'sentence', {GM})",
    f"{H}.L.llm(['hello', '12'], {NUM}, text_col='sentence', {GM_PY})",
)
net_case(
    "llm.input.text_col_answer",
    f"llm(c('hello', '12'), {NUM}, text_col = 'answer', {GM})",
    f"{H}.L.llm(['hello', '12'], {NUM}, text_col='answer', {GM_PY})",
)
net_case(
    "llm.input.logical",
    f"llm(c(TRUE, FALSE), {NUM}, {GM})",
    f"{H}.L.llm([True, False], {NUM}, {GM_PY})",
)

# ---- llm(): structured input shapes -----------------------------------------------------
net_case(
    "llm.structured.column_clash",
    f"llm(data.frame(text = c('two vars', 'none'), variables.label = c('in1', 'in2'), check.names = FALSE), {XP}, type = {TS_R}, {XM})",
    f"{H}.L.llm({PD}.DataFrame({{'text': ['two vars', 'none'], 'variables.label': ['in1', 'in2']}}), {XP}, type={TS_PY}, {XM_PY})",
)
net_case(
    "llm.structured.factor_dups",
    f"llm(data.frame(id = 1:3, txt = factor(c('none', 'two vars', 'none'))), {XP}, type = {TS_R}, text_col = 'txt', {XM})",
    f"{H}.L.llm({PD}.DataFrame({{'id': {PD}.array([1, 2, 3], dtype='Int64'), 'txt': {PD}.Categorical(['none', 'two vars', 'none'])}}), {XP}, type={TS_PY}, text_col='txt', {XM_PY})",
)
net_case(
    "llm.structured.reasoning_with_error",
    f"llm(c('two vars', 'bad schema'), {XP}, type = {TS_R}, {XM}, capture_reasoning = TRUE)",
    f"{H}.L.llm(['two vars', 'bad schema'], {XP}, type={TS_PY}, {XM_PY}, capture_reasoning=True)",
)
net_case(
    "llm.structured.only_errors",
    f"llm(c('bad schema', 'bad schema'), {XP}, type = {TS_R}, {XM})",
    f"{H}.L.llm(['bad schema', 'bad schema'], {XP}, type={TS_PY}, {XM_PY})",
)
net_case(
    "llm.structured.empty_and_error",
    f"llm(c('none', 'bad schema'), {XP}, type = {TS_R}, {XM})",
    f"{H}.L.llm(['none', 'bad schema'], {XP}, type={TS_PY}, {XM_PY})",
)

# ---- chat(): provider/model name parsing --------------------------------------------------
expr_case(
    "chat.model_trailing_slash",
    f"withr::with_envvar({KEYS_R}, ellmer::chat('groq/')$get_model())",
    f"{H}.scoped(lambda: {H}.P.chat('groq/').model.name, {{}}, {KEYS_PY})",
)
expr_case(
    "chat.model_double_slash",
    f"withr::with_envvar({KEYS_R}, ellmer::chat('groq/a//b')$get_model())",
    f"{H}.scoped(lambda: {H}.P.chat('groq/a//b').model.name, {{}}, {KEYS_PY})",
)
expr_case(
    "chat.model_slashes_end",
    f"withr::with_envvar({KEYS_R}, ellmer::chat('groq/a//')$get_model())",
    f"{H}.scoped(lambda: {H}.P.chat('groq/a//').model.name, {{}}, {KEYS_PY})",
)
expr_case(
    "chat.internal_fn",
    f"withr::with_envvar({KEYS_R}, ellmer::chat('body/x'))",
    f"{H}.scoped(lambda: {H}.P.chat('body/x'), {{}}, {KEYS_PY})",
)

# ---- settings ------------------------------------------------------------------------
opt_case(
    "llm_model_list.invalid_dups",
    "llm_model_list(c('bad', 'ollama', 'bad', 'worse'))",
    f"{H}.L.llm_model_list(['bad', 'ollama', 'bad', 'worse'])",
)
opt_case(
    "llm_max_calls.overflow",
    "llm_max_calls(1e10)",
    f"{H}.L.llm_max_calls(1e10)",
    "list(metacheck.llm_max_calls = 30L)",
    f"{{'metacheck.llm_max_calls': {H}.RInt(30)}}",
)
opt_case(
    "llm_max_calls.negative_fraction",
    "llm_max_calls(-0.5)",
    f"{H}.L.llm_max_calls(-0.5)",
    "list(metacheck.llm_max_calls = 30L)",
    f"{{'metacheck.llm_max_calls': {H}.RInt(30)}}",
)
opt_case(
    "llm_max_tokens.fraction",
    "llm_max_tokens(8191.9)",
    f"{H}.L.llm_max_tokens(8191.9)",
    "list(metacheck.llm_max_tokens = NULL)",
    "{'metacheck.llm_max_tokens': None}",
)
opt_case(
    "llm_reasoning.vector",
    "llm_reasoning(c('low', 'high'))",
    f"{H}.L.llm_reasoning(['low', 'high'])",
    "list(metacheck.llm_reasoning = NULL)",
    "{'metacheck.llm_reasoning': None}",
)
opt_case(
    "llm_reasoning.empty",
    "llm_reasoning('')",
    f"{H}.L.llm_reasoning('')",
    "list(metacheck.llm_reasoning = NULL)",
    "{'metacheck.llm_reasoning': None}",
)
opt_case(
    "llm_timeout.inf",
    "llm_timeout(Inf)",
    f"{H}.L.llm_timeout(float('inf'))",
    "list(metacheck.llm_timeout = NULL)",
    "{'metacheck.llm_timeout': None}",
)
opt_case(
    "llm_timeout.logical",
    "llm_timeout(TRUE)",
    f"{H}.L.llm_timeout(True)",
    "list(metacheck.llm_timeout = NULL)",
    "{'metacheck.llm_timeout': None}",
)
opt_case(
    "llm_use.na",
    "llm_use(NA)",
    f"{H}.L.llm_use(float('nan'))",
    "list(metacheck.llm.use = FALSE)",
    "{'metacheck.llm.use': False}",
)
opt_case(
    "llm_use.string_yes",
    "llm_use('yes')",
    f"{H}.L.llm_use('yes')",
    "list(metacheck.llm.use = FALSE)",
    "{'metacheck.llm.use': False}",
)
opt_case(
    "llm_use.string_one",
    "llm_use('1')",
    f"{H}.L.llm_use('1')",
    "list(metacheck.llm.use = FALSE)",
    "{'metacheck.llm.use': False}",
)
opt_case(
    "llm_cache.na",
    "llm_cache(NA)",
    f"{H}.L.llm_cache(None if False else float('nan'))",
)
opt_case(
    "llm_cache.number",
    "llm_cache(1)",
    f"{H}.L.llm_cache(1)",
)
opt_case(
    "llm.error.model_vector",
    "llm('hi', 'repeat this', model = c('groq/a', 'groq/b'))",
    f"{H}.L.llm('hi', 'repeat this', model=['groq/a', 'groq/b'])",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.model_empty",
    "llm('hi', 'repeat this', model = '')",
    f"{H}.L.llm('hi', 'repeat this', model='')",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.max_tokens_zero",
    "llm('hi', 'repeat this', model = 'groq/x', params = list(max_tokens = 0))",
    f"{H}.L.llm('hi', 'repeat this', model='groq/x', params={{'max_tokens': 0}})",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.seed_fraction",
    "llm('hi', 'repeat this', model = 'groq/x', params = list(seed = 1.5))",
    f"{H}.L.llm('hi', 'repeat this', model='groq/x', params={{'seed': 1.5}})",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.stop_numeric",
    "llm('hi', 'repeat this', model = 'groq/x', params = list(stop_sequences = 1))",
    f"{H}.L.llm('hi', 'repeat this', model='groq/x', params={{'stop_sequences': 1}})",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.log_probs_string",
    "llm('hi', 'repeat this', model = 'groq/x', params = list(log_probs = 'yes'))",
    f"{H}.L.llm('hi', 'repeat this', model='groq/x', params={{'log_probs': 'yes'}})",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.max_calls_boundary",
    "llm(paste('t', 1:3), 'summarise', model = 'groq/x')",
    f"{H}.L.llm(['t 1', 't 2', 't 3'], 'summarise', model='groq/x')",
    "list(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE, metacheck.llm_max_calls = 2L)",
    f"{{**{H}.LLM_ON, 'metacheck.llm_max_calls': {H}.RInt(2)}}",
)

# ---- helpers ----------------------------------------------------------------------------
expr_case(
    "cache_key.null_params",
    "metacheck:::.llm_cache_key('', '', NULL, 'm', NULL)",
    f"{H}.C._llm_cache_key('', '', None, 'm', None)",
)
expr_case(
    "cache_key.int_params",
    "metacheck:::.llm_cache_key('t', 's', NULL, 'groq/m', ellmer::params(temperature = 0, max_tokens = 8192L, seed = 8675309L, think = FALSE))",
    f"{H}.C._llm_cache_key('t', 's', None, 'groq/m', {H}.P.params(temperature=0.0, max_tokens={H}.RInt(8192), seed={H}.RInt(8675309), think=False))",
)


def rerr(status, body_json):
    return (
        "local({e <- simpleError('HTTP error'); "
        f"e$resp <- httr2::response_json(status_code = {status}L, body = {body_json}); "
        "metacheck:::.llm_error_message(e)})"
    )


def pyerr(status, pyjson):
    return f"{H}.K._llm_error_message({H}.cond('HTTP error', status={status}, json={pyjson}))"


expr_case(
    "error_message.error_array_with_message",
    rerr(400, "list(error = list('a', 'b'), message = 'top level')"),
    pyerr(400, "{'error': ['a', 'b'], 'message': 'top level'}"),
)
expr_case(
    "error_message.partial_errors",
    rerr(400, "list(errors = list(message = 'partial match'))"),
    pyerr(400, "{'errors': {'message': 'partial match'}}"),
)
expr_case(
    "error_message.partial_msg",
    rerr(400, "list(error = list(messages = 'inner partial'))"),
    pyerr(400, "{'error': {'messages': 'inner partial'}}"),
)
expr_case(
    "error_message.top_array",
    rerr(400, "list('x', 'y')"),
    pyerr(400, "['x', 'y']"),
)
expr_case(
    "error_message.message_number",
    rerr(400, "list(error = list(message = 42))"),
    pyerr(400, "{'error': {'message': 42}}"),
)
expr_case(
    "error_message.error_null_message",
    rerr(400, "list(error = NULL, message = 'fallback')"),
    pyerr(400, "{'error': None, 'message': 'fallback'}"),
)
expr_case(
    "error_message.unicode_long",
    rerr(400, "list(error = strrep('\\u00e9\\u4e2d', 260))"),
    pyerr(400, "{'error': '\\u00e9\\u4e2d' * 260}"),
)

# ---- structured replies: ellmer's conversion of odd JSON (tests/llm/mocks) ------------
# Replies are written by make_review_mocks.py; "probe <x>" texts each have one.
PT_R = (
    "ellmer::type_object(items = ellmer::type_array(ellmer::type_object("
    "name = ellmer::type_string('n'), "
    "count = ellmer::type_integer('c', required = FALSE), "
    "score = ellmer::type_number('s', required = FALSE), "
    "flag = ellmer::type_boolean('f', required = FALSE), "
    "kind = ellmer::type_enum(c('x', 'y'), 'k', required = FALSE), "
    "tags = ellmer::type_array(ellmer::type_string(), required = FALSE))))"
)
PT_PY = (
    f"{H}.T.type_object(items={H}.T.type_array({H}.T.type_object("
    f"name={H}.T.type_string('n'), "
    f"count={H}.T.type_integer('c', required=False), "
    f"score={H}.T.type_number('s', required=False), "
    f"flag={H}.T.type_boolean('f', required=False), "
    f"kind={H}.T.type_enum(['x', 'y'], 'k', required=False), "
    f"tags={H}.T.type_array({H}.T.type_string(), required=False))))"
)
PM = "model = 'groq/probe-model'"
PM_PY = "model='groq/probe-model'"
PROBES = [
    "probe types", "probe nulls", "probe missing", "probe overflow", "probe strings",
    "probe object", "probe truncated", "probe empty", "probe extra",
]  # fmt: skip
for _t in PROBES:
    net_case(
        "structured." + _t.replace(" ", "_"),
        f"llm('{_t}', 'Probe', type = {PT_R}, {PM})",
        f"{H}.L.llm('{_t}', 'Probe', type={PT_PY}, {PM_PY})",
    )
net_case(
    "structured.probe_all",
    f"llm(c('probe types', 'probe empty', 'probe nulls', 'probe types'), 'Probe', type = {PT_R}, {PM})",
    f"{H}.L.llm(['probe types', 'probe empty', 'probe nulls', 'probe types'], 'Probe', type={PT_PY}, {PM_PY})",
)
# a single object (no array to unnest)
ST_R = (
    "ellmer::type_object(name = ellmer::type_string('n', required = FALSE), "
    "n = ellmer::type_integer('i', required = FALSE), "
    "tags = ellmer::type_array(ellmer::type_enum(c('x', 'y')), required = FALSE), "
    "sub = ellmer::type_object(a = ellmer::type_number(required = FALSE), .required = FALSE))"
)
ST_PY = (
    f"{H}.T.type_object(name={H}.T.type_string('n', required=False), "
    f"n={H}.T.type_integer('i', required=False), "
    f"tags={H}.T.type_array({H}.T.type_enum(['x', 'y']), required=False), "
    f"sub={H}.T.type_object(a={H}.T.type_number(required=False), required=False))"
)
SINGLES = ["single full", "single nulls", "single missing", "single tags"]
for _t in SINGLES:
    net_case(
        "structured." + _t.replace(" ", "_"),
        f"llm('{_t}', 'Single', type = {ST_R}, {PM})",
        f"{H}.L.llm('{_t}', 'Single', type={ST_PY}, {PM_PY})",
    )
net_case(
    "structured.single_mixed",
    f"llm(c('single full', 'single nulls', 'single tags'), 'Single', type = {ST_R}, {PM})",
    f"{H}.L.llm(['single full', 'single nulls', 'single tags'], 'Single', type={ST_PY}, {PM_PY})",
)

# ---- llm.yaml cases that raise or warn: compare the texts too ----------------------------
# (parity only checks that both sides raise, and never compares warnings)
_MSG_IDS = [
    "llm.cache.errors_not_cached", "llm.error.max_calls", "llm.error.no_model",
    "llm.error.no_text_col", "llm.error.not_enabled", "llm.error.params_not_list",
    "llm.error.top_p_negative", "llm.error.top_p_string", "llm.groq.error401",
    "llm.groq.malformed", "llm.groq.mixed_error", "llm.groq.numeric_text",
    "llm.groq.structured", "llm.missing_key", "llm.ollama.notamodel",
    "llm.structured_unknown_provider", "llm.unknown_provider", "llm.vllm_empty_model",
    "llm.vllm_no_base_url", "llm_cache.error", "llm_max_calls.error", "llm_max_calls.zero",
    "llm_max_tokens.error", "llm_model.error", "llm_reasoning.error",
    "llm_timeout.error_negative", "llm_timeout.error_string", "llm_use.error",
    "llm.groq.error400_rows", "llm.groq.params", "llm.ollama.default_model",
]  # fmt: skip
_base = {c["id"]: c for c in yaml.safe_load(open("parity/cases/llm.yaml", encoding="utf-8"))["cases"]}
for _id in _MSG_IDS:
    _c = _base[_id]
    _e = _c["args"]["x"]["$expr"]
    _kw = {"mock_dir": _c["mock_dir"]} if "mock_dir" in _c else {}
    expr_case("msg." + _id, _e["r"], _e["py"], **_kw)

expr_case(
    "msg.cap_report.basic",
    "metacheck:::cap_report('The `max_size` cap of 5 MB skipped data.zip (5.4 GB); set max_size >= 5.4e9 to include it.')",
    f"{H}.L.cap_report('The `max_size` cap of 5 MB skipped data.zip (5.4 GB); set max_size >= 5.4e9 to include it.')",
)
expr_case(
    "msg.llm_model_list.invalid",
    "llm_model_list('notamodel')",
    f"{H}.L.llm_model_list('notamodel')",
)
expr_case(
    "msg.ollama_native.notamodel",
    "metacheck:::.llm_ollama_native('A', 'Is this a vowel? Answer only \\'TRUE\\' or \\'FALSE\\'.', model = 'notamodel')",
    f"{H}.K._llm_ollama_native('A', \"Is this a vowel? Answer only 'TRUE' or 'FALSE'.\", model='notamodel')",
    mock_dir="apis",
)

with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write("# Adversarial-review parity cases for the llm area (see\n")
    fh.write("# tests/llm/fixtures/make_review_cases.py, which generates this file).\n")
    yaml.dump(
        {"area": "llm_review", "cases": cases},
        fh,
        default_style='"',
        allow_unicode=True,
        sort_keys=False,
        width=100000,
    )
print(len(cases), "cases")
