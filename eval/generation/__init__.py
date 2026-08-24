"""Case generation — the filters, the template registry and the draw loop (T111).

Three modules, split by what each one owns:

* :mod:`eval.generation.filters` — ``questions.md`` §1.6's three input-validity
  predicates over the ``(table, column, window)`` set a template declares,
  evaluated through the case's own as-of view.
* :mod:`eval.generation.templates` — the catalog's 32 entries as data: the
  per-template constants §6.1 copies into a case, the parameter sampler, and the
  data the draw needs to be valid.
* :mod:`eval.generation.instantiate` — the draw loop: sample, filter, answer
  through :data:`eval.oracles.ORACLES`, and rejection-sample until every bool
  template is balanced inside its split.

**What this package does not do is write files.** Splits (T113) and emission
(T114) are the next packet's; what lands here is cases in memory and the numbers
that say the sampling worked.
"""
