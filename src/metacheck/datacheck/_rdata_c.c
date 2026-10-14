/*
 * A C version of the reader of R's serialization format in _files_rdata.py.
 *
 * It builds exactly the objects the Python reader (`_Stream`) builds, from the
 * same classes (`RObject`, `Latin1Str`, numpy arrays of their own), so
 * everything downstream is unchanged; it only does the walk over the stream in
 * C. It reads XDR ("X\n") and native-binary ("B\n") streams. Whatever it does
 * not handle (ASCII streams, an object nested deeper than MAX_DEPTH, a stream
 * that is cut short or malformed) is an error, and the caller then reads the
 * stream again in Python, which gives the result or R's error message. ALTREP
 * objects are rebuilt by the Python `_altrep()`.
 *
 * The extension is optional: setup() is called once by _files_rdata.py with the
 * Python classes and functions it needs. It uses the limited C API (3.11), so
 * one build serves every supported Python version.
 */
#define Py_LIMITED_API 0x030B0000
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

/* SEXPTYPEs */
#define NILSXP 0
#define SYMSXP 1
#define LISTSXP 2
#define CLOSXP 3
#define ENVSXP 4
#define PROMSXP 5
#define LANGSXP 6
#define SPECIALSXP 7
#define BUILTINSXP 8
#define CHARSXP 9
#define LGLSXP 10
#define INTSXP 13
#define REALSXP 14
#define CPLXSXP 15
#define STRSXP 16
#define DOTSXP 17
#define VECSXP 19
#define EXPRSXP 20
#define BCODESXP 21
#define EXTPTRSXP 22
#define WEAKREFSXP 23
#define RAWSXP 24
#define OBJSXP 25
/* pseudo-SEXPTYPEs of the serialization format (R's src/main/serialize.c) */
#define REFSXP 255
#define NILVALUE_SXP 254
#define GLOBALENV_SXP 253
#define UNBOUNDVALUE_SXP 252
#define MISSINGARG_SXP 251
#define BASENAMESPACE_SXP 250
#define NAMESPACESXP 249
#define PACKAGESXP 248
#define PERSISTSXP 247
#define CLASSREFSXP 246
#define GENERICREFSXP 245
#define BCREPDEF 244
#define BCREPREF 243
#define EMPTYENV_SXP 242
#define BASEENV_SXP 241
#define ATTRLANGSXP 240
#define ATTRLISTSXP 239
#define ALTREP_SXP 238

#define LATIN1_MASK (1 << 2)
#define HAS_ATTR (1 << 9)
#define HAS_TAG (1 << 10)

/* Deeper objects are left to the Python reader (C stack: about 3 frames of a
   few hundred bytes per level, well inside a 1 MB thread stack). */
#define MAX_DEPTH 1500

/* set by setup() */
static PyObject *RObject_cls = NULL;   /* _files_rdata.RObject */
static PyObject *new_args = NULL;      /* (RObject,) for object.__new__ */
static PyObject *object_new = NULL;    /* object.__new__ */
static PyObject *Latin1Str_cls = NULL; /* _files_rdata.Latin1Str */
static PyObject *altrep_fn = NULL;     /* _files_rdata._altrep */
static PyObject *np_empty = NULL;      /* numpy.empty */
static PyObject *dt_int32 = NULL, *dt_float64 = NULL, *dt_complex128 = NULL;

/* interned names */
static PyObject *s_type, *s_value, *s_attrs, *s_name, *s_raw;
static PyObject *s_enclos, *s_frame, *s_hashtab, *s_colon, *s_empty;
static PyObject *s_emptyenv, *s_baseenv, *s_globalenv, *s_base;

static int host_big_endian = 0;

typedef struct {
    const unsigned char *p;
    Py_ssize_t n;
    Py_ssize_t pos;
    int big; /* XDR: big-endian */
    int depth;
    PyObject *refs; /* list */
} Stream;

static PyObject *read_item(Stream *s);
static PyObject *read_item_flags(Stream *s, int32_t flags);

static int fail(const char *msg) {
    PyErr_SetString(PyExc_ValueError, msg);
    return -1;
}

static int need(Stream *s, Py_ssize_t k) {
    if (k < 0 || k > s->n - s->pos)
        return fail("read error");
    return 0;
}

static int read_int(Stream *s, int32_t *out) {
    if (need(s, 4) < 0)
        return -1;
    const unsigned char *b = s->p + s->pos;
    uint32_t v;
    if (s->big)
        v = ((uint32_t)b[0] << 24) | ((uint32_t)b[1] << 16) | ((uint32_t)b[2] << 8) | (uint32_t)b[3];
    else
        v = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
    s->pos += 4;
    *out = (int32_t)v;
    return 0;
}

static int read_length(Stream *s, Py_ssize_t *out) {
    int32_t n;
    if (read_int(s, &n) < 0)
        return -1;
    if (n < -1)
        return fail("negative serialized length for vector");
    if (n == -1) {
        int32_t hi, lo;
        if (read_int(s, &hi) < 0 || read_int(s, &lo) < 0)
            return -1;
        int64_t v = ((int64_t)hi << 32) + (int64_t)(uint32_t)lo;
        if (v < 0 || v > (int64_t)PY_SSIZE_T_MAX)
            return fail("read error");
        *out = (Py_ssize_t)v;
        return 0;
    }
    *out = n;
    return 0;
}

/* An RObject with its four fields set (references are borrowed; NULL is None). */
static PyObject *robject(int type, PyObject *value, PyObject *attrs, PyObject *name) {
    PyObject *o = PyObject_Call(object_new, new_args, NULL);
    if (o == NULL)
        return NULL;
    PyObject *t = PyLong_FromLong(type);
    PyObject *a = attrs;
    if (a == NULL)
        a = PyDict_New();
    else
        Py_INCREF(a);
    if (t == NULL || a == NULL || PyObject_SetAttr(o, s_type, t) < 0 ||
        PyObject_SetAttr(o, s_value, value ? value : Py_None) < 0 ||
        PyObject_SetAttr(o, s_attrs, a) < 0 ||
        PyObject_SetAttr(o, s_name, name ? name : Py_None) < 0) {
        Py_XDECREF(t);
        Py_XDECREF(a);
        Py_DECREF(o);
        return NULL;
    }
    Py_DECREF(t);
    Py_DECREF(a);
    return o;
}

static int is_robject(PyObject *o) {
    return PyObject_TypeCheck(o, (PyTypeObject *)RObject_cls);
}

/* An RObject's `type` field, or -1 (no exception) when it is not an int. */
static long robject_type(PyObject *o) {
    PyObject *t = PyObject_GetAttr(o, s_type);
    if (t == NULL)
        return -2;
    long v = PyLong_Check(t) ? PyLong_AsLong(t) : -1;
    Py_DECREF(t);
    if (v == -1 && PyErr_Occurred())
        return -2;
    return v;
}

/* `tag.name if isinstance(tag, RObject) else None` (new reference) */
static PyObject *tag_name(PyObject *tag) {
    if (is_robject(tag))
        return PyObject_GetAttr(tag, s_name);
    Py_RETURN_NONE;
}

/* _attrs(): a pairlist's tagged values as a dict (new reference). */
static PyObject *attrs_of(PyObject *pl) {
    PyObject *d = PyDict_New();
    if (d == NULL || !is_robject(pl))
        return d;
    long t = robject_type(pl);
    if (t == -2)
        goto error;
    if (t != LISTSXP)
        return d;
    PyObject *v = PyObject_GetAttr(pl, s_value);
    if (v == NULL)
        goto error;
    if (v == Py_None) {
        Py_DECREF(v);
        return d;
    }
    if (!PyList_Check(v)) {
        Py_DECREF(v);
        fail("unexpected attribute list");
        goto error;
    }
    Py_ssize_t n = PyList_Size(v);
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *cell = PyList_GetItem(v, i);
        if (!PyTuple_Check(cell) || PyTuple_Size(cell) != 2) {
            Py_DECREF(v);
            fail("unexpected attribute list");
            goto error;
        }
        PyObject *tag = PyTuple_GetItem(cell, 0);
        if (tag != Py_None && PyDict_SetItem(d, tag, PyTuple_GetItem(cell, 1)) < 0) {
            Py_DECREF(v);
            goto error;
        }
    }
    Py_DECREF(v);
    return d;
error:
    Py_DECREF(d);
    return NULL;
}

static int append_ref(Stream *s, PyObject *o) {
    return PyList_Append(s->refs, o);
}

/* _decode_char() */
static PyObject *decode_char(const char *b, Py_ssize_t n, uint32_t levs) {
    if (levs & LATIN1_MASK) {
        PyObject *u = PyUnicode_DecodeUTF8(b, n, NULL);
        if (u != NULL) {
            Py_DECREF(u);
            return PyUnicode_DecodeLatin1(b, n, NULL);
        }
        if (!PyErr_ExceptionMatches(PyExc_UnicodeDecodeError))
            return NULL;
        PyErr_Clear();
        PyObject *text = PyUnicode_DecodeLatin1(b, n, NULL);
        if (text == NULL)
            return NULL;
        PyObject *out = PyObject_CallFunctionObjArgs(Latin1Str_cls, text, NULL);
        Py_DECREF(text);
        if (out == NULL)
            return NULL;
        PyObject *raw = PyBytes_FromStringAndSize(b, n);
        if (raw == NULL || PyObject_SetAttr(out, s_raw, raw) < 0) {
            Py_XDECREF(raw);
            Py_DECREF(out);
            return NULL;
        }
        Py_DECREF(raw);
        return out;
    }
    return PyUnicode_DecodeUTF8(b, n, "surrogateescape");
}

/* A CHARSXP's payload after its flags: str, or None for NA_character_. */
static PyObject *read_charsxp_body(Stream *s, int32_t flags) {
    int32_t n;
    if (read_int(s, &n) < 0)
        return NULL;
    PyObject *out;
    if (n == -1) {
        out = Py_None;
        Py_INCREF(out);
    } else {
        if (n < 0 || need(s, n) < 0) {
            if (n < 0)
                fail("read error");
            return NULL;
        }
        out = decode_char((const char *)s->p + s->pos, n, (uint32_t)flags >> 12);
        if (out == NULL)
            return NULL;
        s->pos += n;
    }
    if (flags & HAS_ATTR) {
        PyObject *ignored = read_item(s);
        if (ignored == NULL) {
            Py_DECREF(out);
            return NULL;
        }
        Py_DECREF(ignored);
    }
    return out;
}

/* _charsxp(): an element of a character vector. */
static PyObject *read_string_elt(Stream *s) {
    int32_t flags;
    if (read_int(s, &flags) < 0)
        return NULL;
    if ((flags & 0xFF) != CHARSXP) {
        PyObject *v = read_item_flags(s, flags);
        if (v == NULL)
            return NULL;
        if (v == Py_None || PyUnicode_Check(v))
            return v;
        Py_DECREF(v);
        Py_RETURN_NONE;
    }
    return read_charsxp_body(s, flags);
}

/* _string_vec(): the strings of a PERSISTSXP, PACKAGESXP or NAMESPACESXP. */
static PyObject *read_string_vec(Stream *s) {
    int32_t zero, n;
    if (read_int(s, &zero) < 0)
        return NULL;
    if (zero != 0) {
        fail("names in persistent strings are not supported yet");
        return NULL;
    }
    if (read_int(s, &n) < 0)
        return NULL;
    PyObject *out = PyList_New(0);
    if (out == NULL)
        return NULL;
    for (int32_t i = 0; i < n; i++) {
        PyObject *v = read_item(s);
        if (v == NULL || PyList_Append(out, v) < 0) {
            Py_XDECREF(v);
            Py_DECREF(out);
            return NULL;
        }
        Py_DECREF(v);
    }
    return out;
}

/* An atomic vector's payload as a native-endian numpy array: `width`-byte
   values, `count` of them, swapped in place when the stream's byte order is
   not the machine's. */
static PyObject *read_numbers(Stream *s, Py_ssize_t count, int width, PyObject *dtype,
                              Py_ssize_t length) {
    if (count > PY_SSIZE_T_MAX / width)
        return (fail("read error"), NULL);
    Py_ssize_t nbytes = count * width;
    if (need(s, nbytes) < 0)
        return NULL;
    PyObject *n = PyLong_FromSsize_t(length);
    if (n == NULL)
        return NULL;
    PyObject *arr = PyObject_CallFunctionObjArgs(np_empty, n, dtype, NULL);
    Py_DECREF(n);
    if (arr == NULL)
        return NULL;
    Py_buffer view;
    if (PyObject_GetBuffer(arr, &view, PyBUF_WRITABLE | PyBUF_C_CONTIGUOUS) < 0) {
        Py_DECREF(arr);
        return NULL;
    }
    if (view.len != nbytes) {
        PyBuffer_Release(&view);
        Py_DECREF(arr);
        return (fail("read error"), NULL);
    }
    unsigned char *dst = (unsigned char *)view.buf;
    const unsigned char *src = s->p + s->pos;
    if (nbytes > 0) {
        if (s->big == host_big_endian) {
            memcpy(dst, src, (size_t)nbytes);
        } else if (width == 4) {
            for (Py_ssize_t i = 0; i < nbytes; i += 4) {
                dst[i] = src[i + 3];
                dst[i + 1] = src[i + 2];
                dst[i + 2] = src[i + 1];
                dst[i + 3] = src[i];
            }
        } else {
            for (Py_ssize_t i = 0; i < nbytes; i += 8) {
                for (int k = 0; k < 8; k++)
                    dst[i + k] = src[i + 7 - k];
            }
        }
    }
    PyBuffer_Release(&view);
    s->pos += nbytes;
    return arr;
}

/* `":".join(v or "" for v in names)` */
static PyObject *join_names(PyObject *names) {
    Py_ssize_t n = PyList_Size(names);
    PyObject *parts = PyList_New(n);
    if (parts == NULL)
        return NULL;
    for (Py_ssize_t i = 0; i < n; i++) {
        PyObject *v = PyList_GetItem(names, i);
        if (v == Py_None)
            v = s_empty;
        else if (!PyUnicode_Check(v)) {
            Py_DECREF(parts);
            fail("unexpected namespace name");
            return NULL;
        }
        Py_INCREF(v);
        PyList_SetItem(parts, i, v);
    }
    PyObject *out = PyUnicode_Join(s_colon, parts);
    Py_DECREF(parts);
    return out;
}

/* _pairlist(): a pairlist, call, closure, promise or ... as one RObject whose
   value lists the (tag, value) cells, a non-NULL tail last. */
static PyObject *read_pairlist(Stream *s, int32_t flags) {
    PyObject *items = PyList_New(0);
    PyObject *attrs = NULL, *tail = NULL, *head = NULL;
    int first = -1;
    if (items == NULL)
        return NULL;
    int t = flags & 0xFF;
    while (t == LISTSXP || t == LANGSXP || t == CLOSXP || t == PROMSXP || t == DOTSXP) {
        PyObject *a = NULL, *tag = NULL, *car = NULL, *cell = NULL, *name = NULL;
        if (flags & HAS_ATTR) {
            if ((a = read_item(s)) == NULL)
                goto error;
        }
        if (flags & HAS_TAG) {
            if ((tag = read_item(s)) == NULL) {
                Py_XDECREF(a);
                goto error;
            }
        }
        car = read_item(s);
        if (car == NULL) {
            Py_XDECREF(a);
            Py_XDECREF(tag);
            goto error;
        }
        if (first < 0) {
            first = t;
            attrs = attrs_of(a ? a : Py_None);
            if (attrs == NULL) {
                Py_XDECREF(a);
                Py_XDECREF(tag);
                Py_DECREF(car);
                goto error;
            }
        }
        Py_XDECREF(a);
        name = tag ? tag_name(tag) : (Py_INCREF(Py_None), Py_None);
        Py_XDECREF(tag);
        if (name == NULL) {
            Py_DECREF(car);
            goto error;
        }
        cell = PyTuple_Pack(2, name, car);
        Py_DECREF(name);
        Py_DECREF(car);
        if (cell == NULL || PyList_Append(items, cell) < 0) {
            Py_XDECREF(cell);
            goto error;
        }
        Py_DECREF(cell);
        if (read_int(s, &flags) < 0)
            goto error;
        t = flags & 0xFF;
    }
    tail = read_item_flags(s, flags);
    if (tail == NULL)
        goto error;
    if (tail != Py_None) {
        PyObject *cell = PyTuple_Pack(2, Py_None, tail);
        if (cell == NULL || PyList_Append(items, cell) < 0) {
            Py_XDECREF(cell);
            goto error;
        }
        Py_DECREF(cell);
    }
    head = robject(first, items, attrs, NULL);
error:
    Py_XDECREF(tail);
    Py_XDECREF(attrs);
    Py_DECREF(items);
    return head;
}

static PyObject *read_bc1(Stream *s, PyObject *reps);

/* _bclang(): a call or pairlist in a byte-code constant pool. */
static PyObject *read_bclang(Stream *s, int32_t type, PyObject *reps) {
    if (type == BCREPREF) {
        int32_t i;
        if (read_int(s, &i) < 0)
            return NULL;
        if (i < 0 || i >= PyList_Size(reps))
            return (fail("read error"), NULL);
        PyObject *v = PyList_GetItem(reps, i);
        Py_INCREF(v);
        return v;
    }
    if (!(type == BCREPDEF || type == LANGSXP || type == LISTSXP || type == ATTRLANGSXP ||
          type == ATTRLISTSXP))
        return read_item(s);
    int32_t pos = -1;
    if (type == BCREPDEF) {
        if (read_int(s, &pos) < 0 || read_int(s, &type) < 0)
            return NULL;
    }
    int has_attr = type == ATTRLANGSXP || type == ATTRLISTSXP;
    int t = (type == ATTRLANGSXP || type == LANGSXP) ? LANGSXP : LISTSXP;
    PyObject *ans = robject(t, NULL, NULL, NULL);
    PyObject *tag = NULL, *car = NULL, *cdr = NULL, *items = NULL, *name = NULL, *cell = NULL;
    if (ans == NULL)
        return NULL;
    if (pos >= 0) {
        if (pos >= PyList_Size(reps)) {
            fail("read error");
            goto error;
        }
        Py_INCREF(ans);
        PyList_SetItem(reps, pos, ans);
    }
    if (has_attr) {
        PyObject *a = read_item(s);
        if (a == NULL)
            goto error;
        PyObject *d = attrs_of(a);
        Py_DECREF(a);
        if (d == NULL)
            goto error;
        int rc = PyObject_SetAttr(ans, s_attrs, d);
        Py_DECREF(d);
        if (rc < 0)
            goto error;
    }
    int32_t ty;
    if ((tag = read_item(s)) == NULL || read_int(s, &ty) < 0 ||
        (car = read_bclang(s, ty, reps)) == NULL || read_int(s, &ty) < 0 ||
        (cdr = read_bclang(s, ty, reps)) == NULL)
        goto error;
    if ((name = tag_name(tag)) == NULL)
        goto error;
    if ((cell = PyTuple_Pack(2, name, car)) == NULL)
        goto error;
    if ((items = PyList_New(1)) == NULL)
        goto error;
    PyList_SetItem(items, 0, cell);
    cell = NULL;
    if (cdr != ans && is_robject(cdr)) {
        long ct = robject_type(cdr);
        if (ct == -2)
            goto error;
        if (ct == LISTSXP || ct == LANGSXP) {
            PyObject *rest = PyObject_GetAttr(cdr, s_value);
            if (rest == NULL)
                goto error;
            int truthy = PyObject_IsTrue(rest);
            if (truthy < 0) {
                Py_DECREF(rest);
                goto error;
            }
            if (truthy) {
                PyObject *more = PySequence_List(rest);
                Py_DECREF(rest);
                if (more == NULL)
                    goto error;
                Py_ssize_t n = PyList_Size(more);
                for (Py_ssize_t i = 0; i < n; i++) {
                    if (PyList_Append(items, PyList_GetItem(more, i)) < 0) {
                        Py_DECREF(more);
                        goto error;
                    }
                }
                Py_DECREF(more);
            } else {
                Py_DECREF(rest);
            }
        }
    }
    if (PyObject_SetAttr(ans, s_value, items) < 0)
        goto error;
    Py_DECREF(tag);
    Py_DECREF(car);
    Py_DECREF(cdr);
    Py_DECREF(name);
    Py_DECREF(items);
    return ans;
error:
    Py_XDECREF(tag);
    Py_XDECREF(car);
    Py_XDECREF(cdr);
    Py_XDECREF(name);
    Py_XDECREF(cell);
    Py_XDECREF(items);
    Py_DECREF(ans);
    return NULL;
}

/* _bc1(): byte code as RObject(BCODESXP, value=(code, consts)). */
static PyObject *read_bc1(Stream *s, PyObject *reps) {
    PyObject *code = read_item(s);
    if (code == NULL)
        return NULL;
    int32_t n;
    PyObject *consts = PyList_New(0);
    if (consts == NULL || read_int(s, &n) < 0)
        goto error;
    for (int32_t i = 0; i < n; i++) {
        int32_t t;
        PyObject *c;
        if (read_int(s, &t) < 0)
            goto error;
        if (t == BCODESXP)
            c = read_bc1(s, reps);
        else if (t == LANGSXP || t == LISTSXP || t == BCREPDEF || t == BCREPREF ||
                 t == ATTRLANGSXP || t == ATTRLISTSXP)
            c = read_bclang(s, t, reps);
        else
            c = read_item(s);
        if (c == NULL || PyList_Append(consts, c) < 0) {
            Py_XDECREF(c);
            goto error;
        }
        Py_DECREF(c);
    }
    PyObject *value = PyTuple_Pack(2, code, consts);
    Py_DECREF(code);
    Py_DECREF(consts);
    if (value == NULL)
        return NULL;
    PyObject *out = robject(BCODESXP, value, NULL, NULL);
    Py_DECREF(value);
    return out;
error:
    Py_DECREF(code);
    Py_XDECREF(consts);
    return NULL;
}

static PyObject *read_item(Stream *s) {
    int32_t flags;
    if (read_int(s, &flags) < 0)
        return NULL;
    return read_item_flags(s, flags);
}

/* The objects that read their attributes after the payload: the payload. */
static PyObject *read_payload(Stream *s, int t, PyObject **made) {
    Py_ssize_t n;
    *made = NULL;
    switch (t) {
    case LGLSXP:
    case INTSXP:
        if (read_length(s, &n) < 0)
            return NULL;
        return read_numbers(s, n, 4, dt_int32, n);
    case REALSXP:
        if (read_length(s, &n) < 0)
            return NULL;
        return read_numbers(s, n, 8, dt_float64, n);
    case CPLXSXP:
        if (read_length(s, &n) < 0)
            return NULL;
        if (n > PY_SSIZE_T_MAX / 2)
            return (fail("read error"), NULL);
        /* the real and imaginary parts in turn, as complex128 */
        return read_numbers(s, 2 * n, 8, dt_complex128, n);
    case STRSXP: {
        if (read_length(s, &n) < 0)
            return NULL;
        if (n > s->n - s->pos) /* each element takes at least 4 bytes */
            return (fail("read error"), NULL);
        PyObject *out = PyList_New(n);
        if (out == NULL)
            return NULL;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *v = read_string_elt(s);
            if (v == NULL) {
                Py_DECREF(out);
                return NULL;
            }
            PyList_SetItem(out, i, v);
        }
        return out;
    }
    case VECSXP:
    case EXPRSXP: {
        if (read_length(s, &n) < 0)
            return NULL;
        if (n > s->n - s->pos)
            return (fail("read error"), NULL);
        PyObject *out = PyList_New(n);
        if (out == NULL)
            return NULL;
        for (Py_ssize_t i = 0; i < n; i++) {
            PyObject *v = read_item(s);
            if (v == NULL) {
                Py_DECREF(out);
                return NULL;
            }
            PyList_SetItem(out, i, v);
        }
        return out;
    }
    case RAWSXP: {
        if (read_length(s, &n) < 0 || need(s, n) < 0)
            return NULL;
        PyObject *out = PyBytes_FromStringAndSize((const char *)s->p + s->pos, n);
        if (out != NULL)
            s->pos += n;
        return out;
    }
    case BCODESXP: {
        int32_t nreps;
        if (read_int(s, &nreps) < 0)
            return NULL;
        if (nreps > s->n - s->pos)
            return (fail("read error"), NULL);
        PyObject *reps = PyList_New(nreps > 0 ? nreps : 0);
        if (reps == NULL)
            return NULL;
        for (int32_t i = 0; i < nreps; i++) {
            Py_INCREF(Py_None);
            PyList_SetItem(reps, i, Py_None);
        }
        PyObject *bc = read_bc1(s, reps);
        Py_DECREF(reps);
        *made = bc;
        return bc;
    }
    case SPECIALSXP:
    case BUILTINSXP: {
        int32_t k;
        if (read_int(s, &k) < 0)
            return NULL;
        if (k < 0 || need(s, k) < 0)
            return (k < 0 ? fail("read error") : 0, NULL);
        PyObject *name = PyUnicode_DecodeLatin1((const char *)s->p + s->pos, k, NULL);
        if (name == NULL)
            return NULL;
        s->pos += k;
        *made = robject(t, NULL, NULL, name);
        Py_DECREF(name);
        return *made;
    }
    case OBJSXP:
        *made = robject(OBJSXP, NULL, NULL, NULL);
        return *made;
    case EXTPTRSXP: {
        PyObject *o = robject(EXTPTRSXP, NULL, NULL, NULL);
        if (o == NULL)
            return NULL;
        if (append_ref(s, o) < 0) {
            Py_DECREF(o);
            return NULL;
        }
        PyObject *prot = read_item(s);
        PyObject *tag = prot ? read_item(s) : NULL;
        PyObject *v = tag ? PyTuple_Pack(2, prot, tag) : NULL;
        Py_XDECREF(prot);
        Py_XDECREF(tag);
        if (v == NULL || PyObject_SetAttr(o, s_value, v) < 0) {
            Py_XDECREF(v);
            Py_DECREF(o);
            return NULL;
        }
        Py_DECREF(v);
        *made = o;
        return o;
    }
    case WEAKREFSXP: {
        PyObject *o = robject(WEAKREFSXP, NULL, NULL, NULL);
        if (o != NULL && append_ref(s, o) < 0) {
            Py_DECREF(o);
            return NULL;
        }
        *made = o;
        return o;
    }
    case CLASSREFSXP:
    case GENERICREFSXP:
        return (fail("this version of R cannot read class references"), NULL);
    default:
        return (fail("ReadItem: unknown type"), NULL);
    }
}

static PyObject *read_item_flags(Stream *s, int32_t flags) {
    int t = flags & 0xFF;
    PyObject *out = NULL;
    if (++s->depth > MAX_DEPTH) {
        fail("nested too deeply for the C reader");
        goto done;
    }
    switch (t) {
    case NILVALUE_SXP:
        out = Py_None;
        Py_INCREF(out);
        goto done;
    case EMPTYENV_SXP:
        out = robject(ENVSXP, NULL, NULL, s_emptyenv);
        goto done;
    case BASEENV_SXP:
        out = robject(ENVSXP, NULL, NULL, s_baseenv);
        goto done;
    case GLOBALENV_SXP:
        out = robject(ENVSXP, NULL, NULL, s_globalenv);
        goto done;
    case BASENAMESPACE_SXP:
        out = robject(ENVSXP, NULL, NULL, s_base);
        goto done;
    case UNBOUNDVALUE_SXP:
    case MISSINGARG_SXP:
        out = robject(t, NULL, NULL, NULL);
        goto done;
    case REFSXP: {
        /* arithmetic shift, as Python's `flags >> 8` */
        int32_t i = flags < 0 ? ~((~flags) >> 8) : flags >> 8;
        if (i == 0 && read_int(s, &i) < 0)
            goto done;
        if (i < 1 || i > PyList_Size(s->refs)) {
            fail("read error");
            goto done;
        }
        out = PyList_GetItem(s->refs, i - 1);
        Py_INCREF(out);
        goto done;
    }
    case PERSISTSXP: {
        PyObject *names = read_string_vec(s);
        if (names == NULL)
            goto done;
        out = robject(PERSISTSXP, names, NULL, NULL);
        Py_DECREF(names);
        if (out != NULL && append_ref(s, out) < 0)
            Py_CLEAR(out);
        goto done;
    }
    case ALTREP_SXP: {
        PyObject *info = read_item(s);
        PyObject *state = info ? read_item(s) : NULL;
        PyObject *attr = state ? read_item(s) : NULL;
        if (attr != NULL)
            out = PyObject_CallFunctionObjArgs(altrep_fn, info, state, attr, NULL);
        Py_XDECREF(info);
        Py_XDECREF(state);
        Py_XDECREF(attr);
        goto done;
    }
    case SYMSXP: {
        PyObject *pname = read_item(s);
        if (pname == NULL)
            goto done;
        out = robject(SYMSXP, NULL, NULL, PyUnicode_Check(pname) ? pname : s_empty);
        Py_DECREF(pname);
        if (out != NULL && append_ref(s, out) < 0)
            Py_CLEAR(out);
        goto done;
    }
    case PACKAGESXP:
    case NAMESPACESXP: {
        PyObject *names = read_string_vec(s);
        if (names == NULL)
            goto done;
        PyObject *name = join_names(names);
        Py_DECREF(names);
        if (name == NULL)
            goto done;
        out = robject(ENVSXP, NULL, NULL, name);
        Py_DECREF(name);
        if (out != NULL && append_ref(s, out) < 0)
            Py_CLEAR(out);
        goto done;
    }
    case ENVSXP: {
        int32_t locked;
        PyObject *enclos = NULL, *frame = NULL, *hashtab = NULL, *attrib = NULL;
        PyObject *value = NULL, *attrs = NULL;
        if (read_int(s, &locked) < 0)
            goto done;
        PyObject *env = robject(ENVSXP, NULL, NULL, NULL);
        if (env == NULL)
            goto done;
        if (append_ref(s, env) < 0 || (enclos = read_item(s)) == NULL ||
            (frame = read_item(s)) == NULL || (hashtab = read_item(s)) == NULL ||
            (attrib = read_item(s)) == NULL || (value = PyDict_New()) == NULL ||
            PyDict_SetItem(value, s_enclos, enclos) < 0 ||
            PyDict_SetItem(value, s_frame, frame) < 0 ||
            PyDict_SetItem(value, s_hashtab, hashtab) < 0 ||
            PyObject_SetAttr(env, s_value, value) < 0 || (attrs = attrs_of(attrib)) == NULL ||
            PyObject_SetAttr(env, s_attrs, attrs) < 0) {
            Py_DECREF(env);
        } else {
            out = env;
        }
        Py_XDECREF(enclos);
        Py_XDECREF(frame);
        Py_XDECREF(hashtab);
        Py_XDECREF(attrib);
        Py_XDECREF(value);
        Py_XDECREF(attrs);
        goto done;
    }
    case LISTSXP:
    case LANGSXP:
    case CLOSXP:
    case PROMSXP:
    case DOTSXP:
        out = read_pairlist(s, flags);
        goto done;
    case CHARSXP:
        out = read_charsxp_body(s, flags);
        goto done;
    default:
        break;
    }
    /* the remaining types read their attributes after the payload */
    PyObject *made;
    PyObject *payload = read_payload(s, t, &made);
    if (payload == NULL)
        goto done;
    PyObject *attrs = NULL;
    if (flags & HAS_ATTR) {
        PyObject *a = read_item(s);
        if (a == NULL) {
            Py_DECREF(payload);
            goto done;
        }
        attrs = attrs_of(a);
        Py_DECREF(a);
        if (attrs == NULL) {
            Py_DECREF(payload);
            goto done;
        }
    }
    if (made != NULL) {
        /* already an RObject (in the reference table for some types) */
        if (attrs != NULL && PyObject_SetAttr(made, s_attrs, attrs) < 0) {
            Py_DECREF(attrs);
            Py_DECREF(made);
            goto done;
        }
        out = made;
    } else {
        out = robject(t, payload, attrs, NULL);
        Py_DECREF(payload);
    }
    Py_XDECREF(attrs);
done:
    s->depth--;
    return out;
}

static PyObject *rdata_setup(PyObject *self, PyObject *args) {
    PyObject *robj, *latin1, *altrep, *empty, *i32, *f64, *c128;
    if (!PyArg_ParseTuple(args, "OOOOOOO", &robj, &latin1, &altrep, &empty, &i32, &f64, &c128))
        return NULL;
    PyObject *builtins = PyImport_ImportModule("builtins");
    if (builtins == NULL)
        return NULL;
    PyObject *object = PyObject_GetAttrString(builtins, "object");
    Py_DECREF(builtins);
    if (object == NULL)
        return NULL;
    PyObject *onew = PyObject_GetAttrString(object, "__new__");
    Py_DECREF(object);
    if (onew == NULL)
        return NULL;
    PyObject *nargs = PyTuple_Pack(1, robj);
    if (nargs == NULL) {
        Py_DECREF(onew);
        return NULL;
    }
    PyObject *keep[] = {onew, nargs, robj, latin1, altrep, empty, i32, f64, c128};
    PyObject **slots[] = {&object_new, &new_args, &RObject_cls, &Latin1Str_cls, &altrep_fn,
                          &np_empty, &dt_int32, &dt_float64, &dt_complex128};
    for (int i = 0; i < 9; i++) {
        PyObject *old = *slots[i];
        if (i >= 2)
            Py_INCREF(keep[i]); /* onew and nargs are new references already */
        *slots[i] = keep[i];
        Py_XDECREF(old);
    }
    Py_RETURN_NONE;
}

/* unserialize(data, offset=0): the object serialized at data[offset:]. */
static PyObject *rdata_unserialize(PyObject *self, PyObject *args) {
    PyObject *data;
    Py_ssize_t offset = 0;
    if (!PyArg_ParseTuple(args, "O!|n", &PyBytes_Type, &data, &offset))
        return NULL;
    if (RObject_cls == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "setup() has not been called");
        return NULL;
    }
    char *buf;
    Py_ssize_t len;
    if (PyBytes_AsStringAndSize(data, &buf, &len) < 0)
        return NULL;
    if (offset < 0 || offset > len)
        return (fail("read error"), NULL);
    Stream s = {(const unsigned char *)buf + offset, len - offset, 0, 0, 0, NULL};
    if (s.n < 2)
        return (fail("unknown input format"), NULL);
    if (s.p[0] == 'X' && s.p[1] == '\n')
        s.big = 1;
    else if (s.p[0] == 'B' && s.p[1] == '\n')
        s.big = 0;
    else
        return (fail("unsupported input format for the C reader"), NULL);
    s.pos = 2;
    int32_t version, writer, minimal;
    if (read_int(&s, &version) < 0 || read_int(&s, &writer) < 0 || read_int(&s, &minimal) < 0)
        return NULL;
    if (version == 3) {
        int32_t n;
        if (read_int(&s, &n) < 0)
            return NULL;
        if (n < 0 || need(&s, n) < 0)
            return (n < 0 ? fail("read error") : 0, NULL);
        s.pos += n; /* native encoding */
    } else if (version != 2) {
        return (fail("cannot read workspace version"), NULL);
    }
    s.refs = PyList_New(0);
    if (s.refs == NULL)
        return NULL;
    /* a large object is millions of new containers: the cyclic collector
       would walk them again and again while they are built */
    int gc_was = PyGC_Disable();
    PyObject *out = read_item(&s);
    if (gc_was)
        PyGC_Enable();
    Py_DECREF(s.refs);
    return out;
}

static PyMethodDef methods[] = {
    {"setup", rdata_setup, METH_VARARGS,
     "setup(RObject, Latin1Str, altrep, empty, int32, float64, complex128)"},
    {"unserialize", rdata_unserialize, METH_VARARGS,
     "unserialize(data, offset=0): the R object serialized at data[offset:]"},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_rdata_c", "R's unserialize() in C (see _files_rdata.py).", -1,
    methods, NULL, NULL, NULL, NULL,
};

static int intern(PyObject **slot, const char *text) {
    *slot = PyUnicode_InternFromString(text);
    return *slot == NULL ? -1 : 0;
}

PyMODINIT_FUNC PyInit__rdata_c(void) {
    uint16_t probe = 1;
    host_big_endian = *(unsigned char *)&probe == 0;
    if (intern(&s_type, "type") < 0 || intern(&s_value, "value") < 0 ||
        intern(&s_attrs, "attrs") < 0 || intern(&s_name, "name") < 0 ||
        intern(&s_raw, "raw") < 0 || intern(&s_enclos, "enclos") < 0 ||
        intern(&s_frame, "frame") < 0 || intern(&s_hashtab, "hashtab") < 0 ||
        intern(&s_colon, ":") < 0 || intern(&s_empty, "") < 0 ||
        intern(&s_emptyenv, "emptyenv") < 0 || intern(&s_baseenv, "baseenv") < 0 ||
        intern(&s_globalenv, "globalenv") < 0 || intern(&s_base, "base") < 0)
        return NULL;
    return PyModule_Create(&module);
}
