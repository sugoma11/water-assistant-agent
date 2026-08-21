"""The reference-card store: eleven YAML cards and the projection they drift against.

:mod:`.store` is the schema and the reader; :mod:`.rendered` is the projection of
``rules_constants.py`` and ``roofs.py`` a ``provenance: rendered`` card's
generated blocks must equal. Neither imports ADK and neither performs I/O beyond
the packaged card files (``agent_architecture.md`` §3.2).
"""
