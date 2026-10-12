"""The indexed-document core (docs/design/ARCHITECTURE.md §2).

* :mod:`metacheck.core.doc`: :class:`~metacheck.core.doc.Doc`, one paper's
  sentences indexed once, and :class:`~metacheck.core.doc.Docs`, the papers of
  an input;
* :mod:`metacheck.core.patterns`: R patterns as values
  (:class:`~metacheck.core.patterns.Pat`,
  :class:`~metacheck.core.patterns.PatternSet`);
* :mod:`metacheck.core.hits`: :class:`~metacheck.core.hits.Hits`, the rows a
  search chain found;
* :mod:`metacheck.core.groups`: the paragraph, header, section and paper views
  of ``text_search()``'s grouped return modes;
* :mod:`metacheck.core.scope`: trusted scopes and work counters.

The façades (``text_search()``, ``extract_*()`` and the readers) are built on
it. The core imports only the foundation (``_r``, ``_values``, ``papers.model``,
``papers.schema``, ...), never the façades.
"""
