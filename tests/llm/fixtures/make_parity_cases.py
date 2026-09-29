"""Generates parity/cases/llm.yaml (run from the repo root):

    python tests/llm/fixtures/make_parity_cases.py parity/cases/llm.yaml

Then regenerate the goldens with ``python -m parity generate --area llm``.
"""

import sys

import yaml

H = "__import__('tests.llm.parity_helpers', fromlist=['_'])"
cases = []


def fn_case(id, r, py, args=None, **kw):
    c = {"id": id, "r": r, "py": py}
    if args is not None:
        c["args"] = args
    c.update(kw)
    cases.append(c)


def expr_case(id, r, py, **kw):
    c = {
        "id": id,
        "r": "base::identity",
        "py": "tests.llm.parity_helpers.identity",
        "args": {"x": {"$expr": {"r": r, "py": py}}},
    }
    c.update(kw)
    cases.append(c)


def rcond(msg, status=None, json=None, text=None, wrap=None, timeout=False):
    if timeout:
        return f"structure(class = c('curl_error_operation_timedout', 'error', 'condition'), list(message = {msg!r}, call = NULL))".replace(
            '"', "'"
        )
    code = f"local({{e <- simpleError({rq(msg)})"
    if status is not None:
        if json is not None:
            code += f"; e$resp <- httr2::response_json(status_code = {status}L, body = {json})"
        else:
            code += f"; e$resp <- httr2::response(status_code = {status}L, headers = list('content-type' = 'text/plain'), body = charToRaw({rq(text or '')}))"
    if wrap is not None:
        code += f"; w <- simpleError({rq(wrap)}); w$parent <- e; e <- w"
    code += "; e})"
    return code


def rq(s):
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def pycond(msg, status=None, pyjson=None, text=None, wrap=None, timeout=False):
    args = [repr(msg)]
    if status is not None:
        args.append(f"status={status}")
    if pyjson is not None:
        args.append(f"json={pyjson}")
    if text is not None:
        args.append(f"text={text!r}")
    if wrap is not None:
        args.append(f"wrap={wrap!r}")
    if timeout:
        args.append("timeout=True")
    return f"{H}.cond({', '.join(args)})"


# ---- sanitise ---------------------------------------------------------------
SAN = ("metacheck:::.llm_sanitise_text", "metacheck.llm.core._llm_sanitise_text")
fn_case(
    "sanitise.controls",
    *SAN,
    {
        "x": {
            "$chr": [
                "plain",
                "tab\there",
                "ctrl\x01\x07x\x1f",
                "cr\r\nlf",
                "caf\u00e9 \u4e2d",
                "",
                "del\x7fkeep",
            ]
        }
    },
)
fn_case("sanitise.numbers", *SAN, {"x": {"$dbl": [1, 2.5, 100000, 0.1, 123456.7]}})
expr_case(
    "sanitise.na",
    "metacheck:::.llm_sanitise_text(c('a', NA, 'b\\x02'))",
    f"{H}.K._llm_sanitise_text(['a', None, 'b\\x02'])",
)
fn_case("sanitise.null", *SAN, {"x": {"$null": True}})
fn_case("sanitise.empty", *SAN, {"x": {"$chr": []}})

# ---- json_retryable ---------------------------------------------------------
JR = "metacheck:::.llm_json_retryable", "metacheck.llm.core._llm_json_retryable"
for i, (msg, st, rj, pj) in enumerate(
    [
        ("Failed to generate JSON", None, None, None),
        ('lexical error: invalid char in json text. ```json { "studies": [] }', None, None, None),
        ("HTTP 401 Unauthorized", None, None, None),
        (
            "HTTP 400 Bad Request.",
            400,
            "list(error = list(message = 'Failed to validate JSON'))",
            "{'error': {'message': 'Failed to validate JSON'}}",
        ),
        ("parse error: premature EOF", None, None, None),
        ("MALFORMED NUMBER, a digit is required", None, None, None),
        ("boom", None, None, None),
    ]
):
    expr_case(
        f"json_retryable.{i + 1}",
        f"metacheck:::.llm_json_retryable({rcond(msg, st, rj)})",
        f"{H}.K._llm_json_retryable({pycond(msg, st, pj)})",
    )

# ---- error_message ----------------------------------------------------------
EM = [
    ("plain", "boom", None, None, None, None, None),
    (
        "error_object",
        "HTTP 400 Bad Request.",
        400,
        "list(error = list(message = 'Please reduce the length of the messages.'))",
        "{'error': {'message': 'Please reduce the length of the messages.'}}",
        None,
        None,
    ),
    (
        "parent",
        "HTTP 400 Bad Request.",
        400,
        "list(error = list(message = 'Please reduce the length.'))",
        "{'error': {'message': 'Please reduce the length.'}}",
        None,
        "Failed to call chat API.",
    ),
    (
        "error_string",
        "HTTP 429 Too Many Requests.",
        429,
        "list(error = 'Rate limit reached')",
        "{'error': 'Rate limit reached'}",
        None,
        None,
    ),
    (
        "body_message",
        "HTTP 400 Bad Request.",
        400,
        "list(message = 'Invalid model')",
        "{'message': 'Invalid model'}",
        None,
        None,
    ),
    (
        "error_number",
        "HTTP 500 Internal Server Error.",
        500,
        "list(error = 5L)",
        "{'error': 5}",
        None,
        None,
    ),
    ("text_body", "HTTP 502 Bad Gateway.", 502, None, None, "upstream exploded", None),
    ("empty_body", "HTTP 503 Service Unavailable.", 503, None, None, "", None),
    (
        "long_body",
        "HTTP 400 Bad Request.",
        400,
        "list(error = list(message = strrep('abcdefghij', 60)))",
        "{'error': {'message': 'abcdefghij' * 60}}",
        None,
        None,
    ),
    (
        "error_no_message",
        "HTTP 400 Bad Request.",
        400,
        "list(error = list(code = 'x'), message = 'top level')",
        "{'error': {'code': 'x'}, 'message': 'top level'}",
        None,
        None,
    ),
]
for name, msg, st, rj, pj, text, wrap in EM:
    expr_case(
        f"error_message.{name}",
        f"metacheck:::.llm_error_message({rcond(msg, st, rj, text, wrap)})",
        f"{H}.K._llm_error_message({pycond(msg, st, pj, text, wrap)})",
    )

# ---- systemic ---------------------------------------------------------------
SY = [
    (
        "401",
        "HTTP 401 Unauthorized.",
        401,
        "list(error = list(message = 'Invalid API Key'))",
        "{'error': {'message': 'Invalid API Key'}}",
        None,
        None,
        False,
    ),
    ("403", "HTTP 403 Forbidden.", 403, "list(error = 'no')", "{'error': 'no'}", None, None, False),
    ("500", "HTTP 500 Internal Server Error.", 500, None, None, "x", None, False),
    ("503", "HTTP 503 Service Unavailable.", 503, None, None, "x", None, False),
    (
        "400",
        "HTTP 400 Bad Request.",
        400,
        "list(error = list(message = 'bad'))",
        "{'error': {'message': 'bad'}}",
        None,
        None,
        False,
    ),
    ("429", "HTTP 429 Too Many Requests.", 429, None, None, "slow down", None, False),
    (
        "parent_401",
        "HTTP 401 Unauthorized.",
        401,
        "list(error = list(message = 'k'))",
        "{'error': {'message': 'k'}}",
        None,
        "Failed to call chat API.",
        False,
    ),
    (
        "resolve",
        "Failed to perform HTTP request.\nCaused by error in `curl::curl_fetch_memory()`:\n! Could not resolve host: api.groq.com",
        None,
        None,
        None,
        None,
        None,
        False,
    ),
    (
        "refused",
        "Failed to connect to localhost port 11434: Connection refused",
        None,
        None,
        None,
        None,
        None,
        False,
    ),
    (
        "timeout_class",
        "Timeout was reached: Operation timed out after 180001 milliseconds",
        None,
        None,
        None,
        None,
        None,
        True,
    ),
    (
        "json_error",
        "lexical error: invalid char in json text.",
        None,
        None,
        None,
        None,
        None,
        False,
    ),
    ("plain", "boom", None, None, None, None, None, False),
]
for name, msg, st, rj, pj, text, wrap, to in SY:
    expr_case(
        f"systemic.{name}",
        f"metacheck:::.llm_is_systemic_error({rcond(msg, st, rj, text, wrap, to)})",
        f"{H}.K._llm_is_systemic_error({pycond(msg, st, pj, text, wrap, to)})",
    )

# ---- apply_reasoning ----------------------------------------------------------
AR = [
    ("gpt_oss", "list()", "{}", "groq/openai/gpt-oss-20b", None, "list()", "{}"),
    ("gpt_oss_none", "list()", "{}", "groq/openai/gpt-oss-120b", "none", "list()", "{}"),
    (
        "gpt_oss_high",
        "list(temperature = 0)",
        "{'temperature': 0.0}",
        "groq/openai/gpt-oss-20b",
        "high",
        "list()",
        "{}",
    ),
    ("qwen3", "list()", "{}", "groq/qwen/qwen3-32b", None, "list()", "{}"),
    ("qwen3_medium", "list()", "{}", "groq/qwen/Qwen3-32B", "medium", "list()", "{}"),
    ("mistral", "list()", "{}", "mistral/mistral-small-latest", None, "list()", "{}"),
    ("mistral_high", "list()", "{}", "Mistral/mistral-medium-3-5", "high", "list()", "{}"),
    (
        "ollama_qwen3",
        "list(temperature = 0, max_tokens = 4096)",
        "{'temperature': 0.0, 'max_tokens': 4096.0}",
        "ollama/qwen3:8b",
        None,
        "list()",
        "{}",
    ),
    ("ollama_qwen3_medium", "list()", "{}", "ollama/qwen3:8b", "medium", "list()", "{}"),
    (
        "ollama_think_set",
        "list(think = TRUE)",
        "{'think': True}",
        "ollama/deepseek-r1:7b",
        "none",
        "list()",
        "{}",
    ),
    ("ollama_deepseek", "list()", "{}", "ollama/deepseek-r1:7b", "low", "list()", "{}"),
    ("ollama_plain", "list()", "{}", "ollama/qwen2.5:3b", None, "list()", "{}"),
    ("other", "list(seed = 1)", "{'seed': 1.0}", "groq/llama-3.1-8b-instant", None, "list()", "{}"),
    (
        "api_args_set",
        "list()",
        "{}",
        "groq/openai/gpt-oss-20b",
        None,
        "list(reasoning_effort = 'medium')",
        "{'reasoning_effort': 'medium'}",
    ),
]
for name, rp, pp, model, eff, ra, pa in AR:
    ropt = "NULL" if eff is None else rq(eff)
    popt = "None" if eff is None else repr(eff)
    expr_case(
        f"apply_reasoning.{name}",
        f"withr::with_options(list(metacheck.llm_reasoning = {ropt}), metacheck:::.llm_apply_reasoning({rp}, {rq(model)}, api_args = {ra}))",
        f"{H}.scoped(lambda: {H}.K._llm_apply_reasoning({pp}, {model!r}, api_args={pa}), {{'metacheck.llm_reasoning': {popt}}})",
    )

# ---- unnest_result ------------------------------------------------------------
UN = ("metacheck:::.unnest_result", "metacheck.llm.core._unnest_result")
fn_case("unnest_result.object", *UN, {"result": {"n_letters": 5, "is_number": False}})
fn_case("unnest_result.object_null", *UN, {"result": {"a": "x", "b": None, "c": 1.5}})
fn_case("unnest_result.wrapper_empty", *UN, {"result": {"variables": []}})
fn_case(
    "unnest_result.wrapper_rows",
    *UN,
    {"result": {"variables": [{"a": 1, "b": None}, {"a": 2, "b": "x"}]}},
)
fn_case(
    "unnest_result.wrapper_ragged", *UN, {"result": {"variables": [{"a": 1}, {"b": "y", "a": 3}]}}
)
fn_case("unnest_result.nested_object", *UN, {"result": {"obj": {"a": 1, "b": "x"}, "n": 2}})
fn_case("unnest_result.single_nested", *UN, {"result": {"obj": {"a": 1, "b": "x"}}})
fn_case("unnest_result.single_nested_one", *UN, {"result": {"obj": {"only": "x"}}})
fn_case("unnest_result.vector", *UN, {"result": {"x": 1, "y": ["a", "b"]}})
fn_case("unnest_result.differing_rows", *UN, {"result": {"x": [1, 2], "y": ["a", "b", "c"]}})
fn_case("unnest_result.data_frame", *UN, {"result": {"$df": {"a": [1, 2], "b": ["x", "y"]}}})
fn_case("unnest_result.names", *UN, {"result": {"power type": 1, "1a": 2, ".error": 3, "if": 4}})
expr_case(
    "unnest_result.wrapped_frame",
    "metacheck:::.unnest_result(list(power_analyses = data.frame(power_type = c('apriori', 'posthoc'), n = c(1, NA))))",
    f"{H}.K._unnest_result({{'power_analyses': __import__('pandas').DataFrame({{'power_type': ['apriori', 'posthoc'], 'n': [1.0, float('nan')]}})}})",
)
expr_case(
    "unnest_result.wrapped_frame_one_col",
    "metacheck:::.unnest_result(list(power_analyses = data.frame(power_type = c('apriori', 'posthoc'))))",
    f"{H}.K._unnest_result({{'power_analyses': __import__('pandas').DataFrame({{'power_type': ['apriori', 'posthoc']}})}})",
)

# ---- cache key ------------------------------------------------------------------
CK = ("metacheck:::.llm_cache_key", "metacheck.llm.cache._llm_cache_key")
fn_case(
    "cache_key.basic",
    *CK,
    {
        "text": "hi",
        "system_prompt": "sys",
        "type": {"$null": True},
        "model": "groq/x",
        "params": {"temperature": 0.0},
    },
)
fn_case(
    "cache_key.order_ab",
    *CK,
    {
        "text": "hi",
        "system_prompt": "s",
        "type": {"$null": True},
        "model": "m",
        "params": {"b": 2.0, "a": 1.0},
    },
)
fn_case(
    "cache_key.order_ba",
    *CK,
    {
        "text": "hi",
        "system_prompt": "s",
        "type": {"$null": True},
        "model": "m",
        "params": {"a": 1.0, "b": 2.0},
    },
)
fn_case(
    "cache_key.empty_params",
    *CK,
    {
        "text": "hi",
        "system_prompt": "s",
        "type": {"$null": True},
        "model": "m",
        "params": {"$list": []},
    },
)
expr_case(
    "cache_key.na_text",
    "metacheck:::.llm_cache_key(NA_character_, 's', NULL, 'm', list(temperature = 0))",
    f"{H}.C._llm_cache_key(None, 's', None, 'm', {{'temperature': 0.0}})",
)
fn_case(
    "cache_key.unicode",
    *CK,
    {
        "text": "caf\u00e9 \u2014 \u201cquoted\u201d \u4e2d\u6587 it's a ? mark\t\\ back",
        "system_prompt": "Is this \u00e9?\nAnswer 'TRUE'.",
        "type": {"$null": True},
        "model": "groq/openai/gpt-oss-20b",
        "params": {"temperature": 0.0, "max_tokens": 4096.0},
    },
)
fn_case(
    "cache_key.prompt_vector",
    *CK,
    {
        "text": "hi",
        "system_prompt": {"$chr": ["one", "two"]},
        "type": {"$null": True},
        "model": "m",
        "params": {"temperature": 0.0},
    },
)
expr_case(
    "cache_key.ellmer_params",
    "metacheck:::.llm_cache_key('text', 'prompt', NULL, 'ollama/qwen3:8b', ellmer::params(temperature = 0, max_tokens = 4096, seed = 42, stop_sequences = c('a', 'b'), think = FALSE))",
    f"{H}.C._llm_cache_key('text', 'prompt', None, 'ollama/qwen3:8b', {H}.L.params(temperature=0.0, max_tokens=4096, seed=42, stop_sequences=['a', 'b'], think=False))",
)
expr_case(
    "cache_key.integer_param",
    "metacheck:::.llm_cache_key('text', 'prompt', NULL, 'm', list(temperature = 0, max_tokens = 8192L))",
    f"{H}.C._llm_cache_key('text', 'prompt', None, 'm', {{'temperature': 0.0, 'max_tokens': {H}.RInt(8192)}})",
)
TS_R = "ellmer::type_object(variables = ellmer::type_array(ellmer::type_object(variable_name = ellmer::type_string('Exact variable name/code in the data file'), label = ellmer::type_string('Verbatim description text from the codebook', required = FALSE), kind = ellmer::type_enum(c('a', 'b', NA), 'Kind', required = FALSE))))"
TS_PY = f"{H}.T.type_object(variables={H}.T.type_array({H}.T.type_object(variable_name={H}.T.type_string('Exact variable name/code in the data file'), label={H}.T.type_string('Verbatim description text from the codebook', required=False), kind={H}.T.type_enum(['a', 'b', None], 'Kind', required=False))))"
expr_case(
    "cache_key.type",
    f"metacheck:::.llm_cache_key('text', 'prompt', {TS_R}, 'groq/m', list(temperature = 0, max_tokens = 4096))",
    f"{H}.C._llm_cache_key('text', 'prompt', {TS_PY}, 'groq/m', {{'temperature': 0.0, 'max_tokens': 4096.0}})",
)
expr_case(
    "cache_key.type_schema_dict",
    "metacheck:::.llm_cache_key('text', 'prompt', ellmer::type_object(results = ellmer::type_array(ellmer::type_object(index = ellmer::type_integer('The number'), value = ellmer::type_string('The value')))), 'groq/m', list(temperature = 0))",
    f"{H}.C._llm_cache_key('text', 'prompt', {{'type': 'object', 'properties': {{'results': {{'type': 'array', 'items': {{'type': 'object', 'properties': {{'index': {{'type': 'integer', 'description': 'The number'}}, 'value': {{'type': 'string', 'description': 'The value'}}}}, 'required': ['index', 'value']}}}}}}, 'required': ['results']}}, 'groq/m', {{'temperature': 0.0}})",
)

# ---- cap -------------------------------------------------------------------------
CS = ("metacheck:::.cap_size_str", "metacheck.llm.cap_prompt._cap_size_str")
for i, v in enumerate(
    [None, 0, -5, 512, 1023, 1024, 1536, 1048576, 5.4e9, 1e13, 1e18, 1073741823.9]
):
    fn_case(f"cap_size_str.{i + 1}", *CS, {"bytes": {"$NA": True} if v is None else v})
fn_case(
    "cap_report.basic",
    "cap_report",
    "metacheck.llm.cap_report",
    {
        "message": "The `max_size` cap of 5 MB skipped data.zip (5.4 GB); set max_size >= 5.4e9 to include it."
    },
)


# ---- settings --------------------------------------------------------------------
def opt_case(id, r, py, ropts="list()", popts="{}"):
    expr_case(id, f"withr::with_options({ropts}, {r})", f"{H}.scoped(lambda: {py}, {popts})")


opt_case(
    "llm_use.set_true",
    "llm_use(TRUE)",
    f"{H}.L.llm_use(True)",
    "list(metacheck.llm.use = FALSE)",
    "{'metacheck.llm.use': False}",
)
opt_case(
    "llm_use.get",
    "llm_use()",
    f"{H}.L.llm_use()",
    "list(metacheck.llm.use = TRUE)",
    "{'metacheck.llm.use': True}",
)
opt_case(
    "llm_use.string_true",
    "llm_use('TRUE')",
    f"{H}.L.llm_use('TRUE')",
    "list(metacheck.llm.use = FALSE)",
    "{'metacheck.llm.use': False}",
)
opt_case(
    "llm_use.zero",
    "llm_use(0)",
    f"{H}.L.llm_use(0)",
    "list(metacheck.llm.use = TRUE)",
    "{'metacheck.llm.use': True}",
)
opt_case(
    "llm_use.error",
    "llm_use('no')",
    f"{H}.L.llm_use('no')",
    "list(metacheck.llm.use = FALSE)",
    "{'metacheck.llm.use': False}",
)
opt_case(
    "llm_model.get",
    "llm_model()",
    f"{H}.L.llm_model()",
    "list(metacheck.llm.model = 'groq/llama-3.1-8b-instant')",
    "{'metacheck.llm.model': 'groq/llama-3.1-8b-instant'}",
)
opt_case(
    "llm_model.set",
    "{llm_model('google_gemini/gemini-2.5-flash'); llm_model()}",
    f"({H}.L.llm_model('google_gemini/gemini-2.5-flash'), {H}.L.llm_model())[1]",
    "list(metacheck.llm.model = 'groq')",
    "{'metacheck.llm.model': 'groq'}",
)
opt_case(
    "llm_model.unset",
    "{llm_model(NULL); llm_model()}",
    f"({H}.L.llm_model(None), {H}.L.llm_model())[1]",
    "list(metacheck.llm.model = 'groq')",
    "{'metacheck.llm.model': 'groq'}",
)
opt_case(
    "llm_model.error",
    "llm_model(TRUE)",
    f"{H}.L.llm_model(True)",
    "list(metacheck.llm.model = 'groq')",
    "{'metacheck.llm.model': 'groq'}",
)
opt_case(
    "llm_max_calls.get",
    "llm_max_calls()",
    f"{H}.L.llm_max_calls()",
    "list(metacheck.llm_max_calls = 30L)",
    f"{{'metacheck.llm_max_calls': {H}.RInt(30)}}",
)
opt_case(
    "llm_max_calls.set",
    "llm_max_calls(8)",
    f"{H}.L.llm_max_calls(8)",
    "list(metacheck.llm_max_calls = 30L)",
    f"{{'metacheck.llm_max_calls': {H}.RInt(30)}}",
)
opt_case(
    "llm_max_calls.truncate",
    "llm_max_calls(2.9)",
    f"{H}.L.llm_max_calls(2.9)",
    "list(metacheck.llm_max_calls = 30L)",
    f"{{'metacheck.llm_max_calls': {H}.RInt(30)}}",
)
opt_case(
    "llm_max_calls.zero",
    "llm_max_calls(0)",
    f"{H}.L.llm_max_calls(0)",
    "list(metacheck.llm_max_calls = 30L)",
    f"{{'metacheck.llm_max_calls': {H}.RInt(30)}}",
)
opt_case(
    "llm_max_calls.error",
    "llm_max_calls('a')",
    f"{H}.L.llm_max_calls('a')",
    "list(metacheck.llm_max_calls = 30L)",
    f"{{'metacheck.llm_max_calls': {H}.RInt(30)}}",
)
opt_case(
    "llm_max_tokens.unset",
    "llm_max_tokens()",
    f"{H}.L.llm_max_tokens()",
    "list(metacheck.llm_max_tokens = NULL)",
    "{'metacheck.llm_max_tokens': None}",
)
opt_case(
    "llm_max_tokens.set",
    "llm_max_tokens(8192)",
    f"{H}.L.llm_max_tokens(8192)",
    "list(metacheck.llm_max_tokens = NULL)",
    "{'metacheck.llm_max_tokens': None}",
)
opt_case(
    "llm_max_tokens.error",
    "llm_max_tokens(TRUE)",
    f"{H}.L.llm_max_tokens(True)",
    "list(metacheck.llm_max_tokens = NULL)",
    "{'metacheck.llm_max_tokens': None}",
)
opt_case(
    "llm_timeout.default",
    "llm_timeout()",
    f"{H}.L.llm_timeout()",
    "list(metacheck.llm_timeout = NULL)",
    "{'metacheck.llm_timeout': None}",
)
opt_case(
    "llm_timeout.set",
    "llm_timeout(120)",
    f"{H}.L.llm_timeout(120)",
    "list(metacheck.llm_timeout = NULL)",
    "{'metacheck.llm_timeout': None}",
)
opt_case(
    "llm_timeout.error_negative",
    "llm_timeout(-1)",
    f"{H}.L.llm_timeout(-1)",
    "list(metacheck.llm_timeout = NULL)",
    "{'metacheck.llm_timeout': None}",
)
opt_case(
    "llm_timeout.error_string",
    "llm_timeout('a')",
    f"{H}.L.llm_timeout('a')",
    "list(metacheck.llm_timeout = NULL)",
    "{'metacheck.llm_timeout': None}",
)
opt_case(
    "llm_reasoning.unset",
    "llm_reasoning()",
    f"{H}.L.llm_reasoning()",
    "list(metacheck.llm_reasoning = NULL)",
    "{'metacheck.llm_reasoning': None}",
)
opt_case(
    "llm_reasoning.partial",
    "llm_reasoning('lo')",
    f"{H}.L.llm_reasoning('lo')",
    "list(metacheck.llm_reasoning = NULL)",
    "{'metacheck.llm_reasoning': None}",
)
opt_case(
    "llm_reasoning.none",
    "llm_reasoning('none')",
    f"{H}.L.llm_reasoning('none')",
    "list(metacheck.llm_reasoning = NULL)",
    "{'metacheck.llm_reasoning': None}",
)
opt_case(
    "llm_reasoning.error",
    "llm_reasoning('extreme')",
    f"{H}.L.llm_reasoning('extreme')",
    "list(metacheck.llm_reasoning = NULL)",
    "{'metacheck.llm_reasoning': None}",
)
opt_case(
    "llm_cache.get",
    "llm_cache()",
    f"{H}.L.llm_cache()",
    "list(metacheck.llm.cache = NULL)",
    "{'metacheck.llm.cache': None}",
)
opt_case(
    "llm_cache.set_false",
    "{llm_cache(FALSE); llm_cache()}",
    f"({H}.L.llm_cache(False), {H}.L.llm_cache())[1]",
    "list(metacheck.llm.cache = TRUE)",
    "{'metacheck.llm.cache': True}",
)
opt_case(
    "llm_cache.error",
    "llm_cache('yes')",
    f"{H}.L.llm_cache('yes')",
    "list(metacheck.llm.cache = TRUE)",
    "{'metacheck.llm.cache': True}",
)

# ---- llm() argument errors (no network) ---------------------------------------------
ON_R = "list(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE)"
ON_PY = f"{H}.LLM_ON"
opt_case(
    "llm.error.not_enabled",
    "llm('hi', 'repeat this', model = 'groq')",
    f"{H}.L.llm('hi', 'repeat this', model='groq')",
    "list(metacheck.llm.use = FALSE)",
    "{'metacheck.llm.use': False}",
)
opt_case(
    "llm.error.no_model",
    "llm('hi', 'repeat this', model = NULL)",
    f"{H}.L.llm('hi', 'repeat this', model=None)",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.params_not_list",
    "llm('hi', 'repeat this', model = 'groq/x', params = 1)",
    f"{H}.L.llm('hi', 'repeat this', model='groq/x', params=1)",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.top_p_string",
    "llm('hi', 'repeat this', model = 'groq/x', params = list(top_p = 'a'))",
    f"{H}.L.llm('hi', 'repeat this', model='groq/x', params={{'top_p': 'a'}})",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.top_p_negative",
    "llm('hi', 'repeat this', model = 'groq/x', params = list(top_p = -3))",
    f"{H}.L.llm('hi', 'repeat this', model='groq/x', params={{'top_p': -3}})",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.error.max_calls",
    "llm(data.frame(text = paste('t', 1:20)), 'summarise', model = 'groq/x')",
    f"{H}.L.llm(__import__('pandas').DataFrame({{'text': ['t ' + str(i) for i in range(1, 21)]}}), 'summarise', model='groq/x')",
    "list(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE, metacheck.llm_max_calls = 8L)",
    f"{{**{H}.LLM_ON, 'metacheck.llm_max_calls': {H}.RInt(8)}}",
)
opt_case(
    "llm.error.no_text_col",
    "llm(data.frame(x = 'a'), 'summarise', model = 'groq/x')",
    f"{H}.L.llm(__import__('pandas').DataFrame({{'x': ['a']}}), 'summarise', model='groq/x')",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.unknown_provider",
    "llm('hi', 'repeat this', model = 'not a model')",
    f"{H}.L.llm('hi', 'repeat this', model='not a model')",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.unknown_provider_rows",
    "llm(c('hi', 'there'), 'repeat this', model = 'nope/x')",
    f"{H}.L.llm(['hi', 'there'], 'repeat this', model='nope/x')",
    ON_R,
    ON_PY,
)
opt_case(
    "llm.vllm_no_base_url",
    "llm('hello', 'Answer TRUE', model = 'vllm/GLM-5.2-NVFP4')",
    f"{H}.L.llm('hello', 'Answer TRUE', model='vllm/GLM-5.2-NVFP4')",
    "list(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE, metacheck.llm.vllm.base_url = NULL)",
    f"{{**{H}.LLM_ON, 'metacheck.llm.vllm.base_url': None}}",
)
opt_case(
    "llm.vllm_empty_model",
    "llm('hello', 'Answer TRUE', model = 'vllm/')",
    f"{H}.L.llm('hello', 'Answer TRUE', model='vllm/')",
    "list(metacheck.llm.use = TRUE, metacheck.llm.cache = FALSE, metacheck.llm.vllm.base_url = 'https://x.test/v1')",
    f"{{**{H}.LLM_ON, 'metacheck.llm.vllm.base_url': 'https://x.test/v1'}}",
)
expr_case(
    "llm.missing_key",
    f"withr::with_options({ON_R}, withr::with_envvar(c(GROQ_API_KEY = NA), llm('hello', 'Sys', model = 'groq/x')))",
    f"{H}.scoped(lambda: {H}.L.llm('hello', 'Sys', model='groq/x'), {ON_PY}, {{'GROQ_API_KEY': None}})",
)
expr_case(
    "llm.structured_unknown_provider",
    f"withr::with_options({ON_R}, llm(c('a', 'b', 'a'), 'Extract', type = {TS_R}, model = 'nope/x'))",
    f"{H}.scoped(lambda: {H}.L.llm(['a', 'b', 'a'], 'Extract', type={TS_PY}, model='nope/x'), {ON_PY})",
)


# ---- llm() against recorded provider responses (tests/llm/mocks) ------------------
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


def net_case(id, r, py, mock_dir=MOCK, **kw):
    expr_case(
        id,
        f"withr::with_options({OPTS_R}, withr::with_envvar({KEYS_R}, {r}))",
        f"{H}.scoped(lambda: {py}, {OPTS_PY}, {KEYS_PY})",
        mock_dir=mock_dir,
        **kw,
    )


PD = "__import__('pandas')"
NUM = "'Is this a number? Answer TRUE or FALSE'"
net_case(
    "llm.groq.plain",
    f"llm(c('hello', '12', 'hello'), {NUM}, model = 'groq/test-model')",
    f"{H}.L.llm(['hello', '12', 'hello'], {NUM}, model='groq/test-model')",
)
net_case(
    "llm.groq.data_frame",
    f"llm(data.frame(id = 1:4, txt = c('hello', '12', NA, 'hello')), {NUM}, text_col = 'txt', model = 'groq/test-model')",
    f"{H}.L.llm({PD}.DataFrame({{'id': {PD}.array([1, 2, 3, 4], dtype='Int64'), 'txt': {PD}.array(['hello', '12', None, 'hello'], dtype='string')}}), {NUM}, text_col='txt', model='groq/test-model')",
)
net_case(
    "llm.groq.sanitised_key",
    f"llm(c('hel\\x01lo', '12'), {NUM}, model = 'groq/test-model')",
    f"{H}.L.llm(['hel\\x01lo', '12'], {NUM}, model='groq/test-model')",
)
net_case(
    "llm.groq.error401",
    "llm('unauthorized', 'Sys', model = 'groq/test-model')",
    f"{H}.L.llm('unauthorized', 'Sys', model='groq/test-model')",
)
net_case(
    "llm.groq.error400_rows",
    "llm(c('too long', 'unauthorized'), 'Sys', model = 'groq/test-model')",
    f"{H}.L.llm(['too long', 'unauthorized'], 'Sys', model='groq/test-model')",
)
net_case(
    "llm.groq.mixed_error",
    "llm(c('hello', 'unauthorized'), 'Sys', model = 'groq/test-model')",
    f"{H}.L.llm(['hello', 'unauthorized'], 'Sys', model='groq/test-model')",
)
net_case(
    "llm.groq.numeric_text",
    "llm(c(1, 2), 'Sys', model = 'groq/test-model')",
    f"{H}.L.llm([1.0, 2.0], 'Sys', model='groq/test-model')",
)
TS2_R = TS_R
TS2_PY = TS_PY
net_case(
    "llm.groq.structured",
    f"llm(c('two vars', 'none', 'bad schema', 'two vars'), 'Extract variables', type = {TS_R}, model = 'groq/openai/gpt-oss-20b')",
    f"{H}.L.llm(['two vars', 'none', 'bad schema', 'two vars'], 'Extract variables', type={TS_PY}, model='groq/openai/gpt-oss-20b')",
)
net_case(
    "llm.groq.structured_all_empty",
    f"llm(data.frame(text = c('none', 'none again')), 'Extract variables', type = {TS_R}, model = 'groq/openai/gpt-oss-20b')",
    f"{H}.L.llm({PD}.DataFrame({{'text': ['none', 'none again']}}), 'Extract variables', type={TS_PY}, model='groq/openai/gpt-oss-20b')",
)
net_case(
    "llm.groq.malformed",
    f"llm('fenced', 'Extract variables', type = {TS_R}, model = 'groq/openai/gpt-oss-20b')",
    f"{H}.L.llm('fenced', 'Extract variables', type={TS_PY}, model='groq/openai/gpt-oss-20b')",
)
net_case(
    "llm.groq.reasoning",
    f"llm(c('two vars', 'none'), 'Extract variables', type = {TS_R}, model = 'groq/openai/gpt-oss-20b', capture_reasoning = TRUE)",
    f"{H}.L.llm(['two vars', 'none'], 'Extract variables', type={TS_PY}, model='groq/openai/gpt-oss-20b', capture_reasoning=True)",
)
TE_R = "ellmer::type_object(tags = ellmer::type_array(ellmer::type_enum(c('x', 'y', 'z'))), meta = ellmer::type_object(a = ellmer::type_integer(), b = ellmer::type_string()), text = ellmer::type_string())"
TE_PY = f"{H}.T.type_object(tags={H}.T.type_array({H}.T.type_enum(['x', 'y', 'z'])), meta={H}.T.type_object(a={H}.T.type_integer(), b={H}.T.type_string()), text={H}.T.type_string())"
net_case(
    "llm.groq.enum_nested",
    f"llm('tags', 'Tag it', type = {TE_R}, model = 'groq/test-model')",
    f"{H}.L.llm('tags', 'Tag it', type={TE_PY}, model='groq/test-model')",
)
net_case(
    "llm.groq.array_top",
    "llm('list', 'List things', type = ellmer::type_array(ellmer::type_string()), model = 'groq/test-model')",
    f"{H}.L.llm('list', 'List things', type={H}.T.type_array({H}.T.type_string()), model='groq/test-model')",
)
net_case(
    "llm.groq.params",
    "llm('hello', 'Sys', model = 'groq/test-model', params = list(seed = 8675309, top_p = 0.9, stop_sequences = c('END', 'STOP')))",
    f"{H}.L.llm('hello', 'Sys', model='groq/test-model', params={{'seed': 8675309, 'top_p': 0.9, 'stop_sequences': ['END', 'STOP']}})",
)
net_case(
    "llm.gemini.plain",
    "llm(c('A', 'B'), 'Is this a vowel? Answer only TRUE or FALSE.', model = 'google_gemini/gemini-2.5-flash')",
    f"{H}.L.llm(['A', 'B'], 'Is this a vowel? Answer only TRUE or FALSE.', model='google_gemini/gemini-2.5-flash')",
)
net_case(
    "llm.gemini.structured",
    f"llm('two vars', 'Extract variables', type = {TS_R}, model = 'google_gemini/gemini-2.5-flash', capture_reasoning = TRUE)",
    f"{H}.L.llm('two vars', 'Extract variables', type={TS_PY}, model='google_gemini/gemini-2.5-flash', capture_reasoning=True)",
)
net_case(
    "llm.anthropic.plain",
    "llm('hello', 'Sys', model = 'anthropic/claude-sonnet-4-5')",
    f"{H}.L.llm('hello', 'Sys', model='anthropic/claude-sonnet-4-5')",
)
net_case(
    "llm.anthropic.structured",
    f"llm('two vars', 'Extract variables', type = {TS_R}, model = 'anthropic/claude-sonnet-4-5')",
    f"{H}.L.llm('two vars', 'Extract variables', type={TS_PY}, model='anthropic/claude-sonnet-4-5')",
)
net_case(
    "llm.anthropic.tool",
    f"llm('two vars', 'Extract variables', type = {TS_R}, model = 'anthropic/claude-3-5-haiku-latest')",
    f"{H}.L.llm('two vars', 'Extract variables', type={TS_PY}, model='anthropic/claude-3-5-haiku-latest')",
)
net_case(
    "llm.openai.plain",
    "llm('hello', 'Sys', model = 'openai/gpt-4.1-mini')",
    f"{H}.L.llm('hello', 'Sys', model='openai/gpt-4.1-mini')",
)
net_case(
    "llm.openai.structured",
    f"llm('two vars', 'Extract variables', type = {TS_R}, model = 'openai/gpt-5-mini', capture_reasoning = TRUE)",
    f"{H}.L.llm('two vars', 'Extract variables', type={TS_PY}, model='openai/gpt-5-mini', capture_reasoning=True)",
)
net_case(
    "llm.mistral.plain",
    "llm('hello', 'Sys', model = 'mistral/mistral-small-latest')",
    f"{H}.L.llm('hello', 'Sys', model='mistral/mistral-small-latest')",
)
VOWEL = "'Is this a vowel? Answer only \\'TRUE\\' or \\'FALSE\\'.'"
VOWEL_PY = "\"Is this a vowel? Answer only 'TRUE' or 'FALSE'.\""
net_case(
    "llm.ollama.native",
    f"llm('A', {VOWEL}, model = 'ollama/qwen2.5:3b')",
    f"{H}.L.llm('A', {VOWEL_PY}, model='ollama/qwen2.5:3b')",
    mock_dir="apis",
)
net_case(
    "llm.ollama.default_model",
    f"llm('A', {VOWEL}, model = 'ollama')",
    f"{H}.L.llm('A', {VOWEL_PY}, model='ollama')",
    mock_dir="apis",
)
net_case(
    "llm.ollama.notamodel",
    f"llm('A', {VOWEL}, model = 'ollama/notamodel')",
    f"{H}.L.llm('A', {VOWEL_PY}, model='ollama/notamodel')",
    mock_dir="apis",
)
fn_case(
    "ollama_native.basic",
    "metacheck:::.llm_ollama_native",
    "metacheck.llm.core._llm_ollama_native",
    {
        "text": "A",
        "system_prompt": "Is this a vowel? Answer only 'TRUE' or 'FALSE'.",
        "model": "qwen2.5:3b",
    },
    mock_dir="apis",
)
fn_case(
    "ollama_native.notamodel",
    "metacheck:::.llm_ollama_native",
    "metacheck.llm.core._llm_ollama_native",
    {
        "text": "A",
        "system_prompt": "Is this a vowel? Answer only 'TRUE' or 'FALSE'.",
        "model": "notamodel",
    },
    mock_dir="apis",
)
fn_case(
    "llm_model_list.ollama",
    "llm_model_list",
    "metacheck.llm.llm_model_list",
    {"platform": "ollama"},
    mock_dir="apis",
)
fn_case(
    "llm_model_list.invalid",
    "llm_model_list",
    "metacheck.llm.llm_model_list",
    {"platform": "notamodel"},
)
fn_case(
    "llm_model_list_groq.mock",
    "metacheck:::.llm_model_list_groq",
    "metacheck.llm.core._llm_model_list_groq",
    {},
    mock_dir="apis",
)

# ---- cache flows ---------------------------------------------------------------------
CACHE_OPTS_R = OPTS_R.replace("metacheck.llm.cache = FALSE", "metacheck.llm.cache = TRUE")
CACHE_OPTS_PY = OPTS_PY[:-1] + ", 'metacheck.llm.cache': True}"


def cache_case(id, r_body, py_body, **kw):
    expr_case(
        id,
        f"local({{d <- tempfile('llmcache'); dir.create(d); on.exit(unlink(d, recursive = TRUE)); "
        f"withr::with_options({CACHE_OPTS_R}, withr::with_envvar(c({KEYS_R[2:-1]}, METACHECK_LLM_CACHE_DIR = d), {r_body}))}})",
        f"{H}.with_cache_dir(lambda d: {H}.scoped(lambda: {py_body}, {CACHE_OPTS_PY}, {KEYS_PY}))",
        mock_dir=MOCK,
        **kw,
    )


cache_case(
    "llm.cache.plain",
    f"list(first = llm(c('hello', '12'), {NUM}, model = 'groq/test-model'), second = llm(c('12', 'hello'), {NUM}, model = 'groq/test-model'), files = sort(list.files(d)))",
    f"{{'first': {H}.L.llm(['hello', '12'], {NUM}, model='groq/test-model'), 'second': {H}.L.llm(['12', 'hello'], {NUM}, model='groq/test-model'), 'files': {H}.cache_files(d)}}",
)
cache_case(
    "llm.cache.structured",
    f"list(first = llm(c('two vars', 'none'), 'Extract variables', type = {TS_R}, model = 'groq/openai/gpt-oss-20b'), second = llm(c('none', 'two vars'), 'Extract variables', type = {TS_R}, model = 'groq/openai/gpt-oss-20b'), files = sort(list.files(d)))",
    f"{{'first': {H}.L.llm(['two vars', 'none'], 'Extract variables', type={TS_PY}, model='groq/openai/gpt-oss-20b'), 'second': {H}.L.llm(['none', 'two vars'], 'Extract variables', type={TS_PY}, model='groq/openai/gpt-oss-20b'), 'files': {H}.cache_files(d)}}",
)
cache_case(
    "llm.cache.errors_not_cached",
    "list(r = llm('unauthorized', 'Sys', model = 'groq/test-model'), files = list.files(d))",
    f"{{'r': {H}.L.llm('unauthorized', 'Sys', model='groq/test-model'), 'files': {H}.cache_files(d)}}",
)
cache_case(
    "cache.put_get",
    "{k <- metacheck:::.llm_cache_key('hi', 'sys', NULL, 'm', list()); metacheck:::.llm_cache_put(k, data.frame(answer = 'yes', .join_key. = 'hi', n = 2L, x = 1.5, ok = TRUE), raw = list(reasoning = 'because')); h <- metacheck:::.llm_cache_get(k); list(key = k, hit = h[c('df', 'raw', 'thinking', 'version')], miss = metacheck:::.llm_cache_get('no-such-key'), cleared = llm_cache_clear(), again = llm_cache_clear())}",
    f"(lambda k: ({H}.C._llm_cache_put(k, {PD}.DataFrame({{'answer': {PD}.array(['yes'], dtype='string'), '.join_key.': {PD}.array(['hi'], dtype='string'), 'n': {PD}.array([2], dtype='Int64'), 'x': [1.5], 'ok': {PD}.array([True], dtype='boolean')}}), raw={{'reasoning': 'because'}}), {{'key': k, 'hit': {{kk: {H}.C._llm_cache_get(k)[kk] for kk in ('df', 'raw', 'thinking', 'version')}}, 'miss': {H}.C._llm_cache_get('no-such-key'), 'cleared': {H}.L.llm_cache_clear(), 'again': {H}.L.llm_cache_clear()}})[1])({H}.C._llm_cache_key('hi', 'sys', None, 'm', {{}}))",
)

with open(sys.argv[1], "w", encoding="utf-8") as fh:
    fh.write("# LLM client parity cases (R/llm.R, R/llm-cache.R, R/cap-prompt.R).\n")
    fh.write("# Cases that touch LLM options or API keys run inside withr::with_options()/\n")
    fh.write("# with_envvar() in R and tests/llm/parity_helpers.scoped() in Python, so no\n")
    fh.write("# state leaks into other areas. Network cases replay tests/llm/mocks.\n")
    yaml.dump(
        {"area": "llm", "cases": cases},
        fh,
        default_style='"',
        allow_unicode=True,
        sort_keys=False,
        width=100000,
    )
print(len(cases), "cases")
