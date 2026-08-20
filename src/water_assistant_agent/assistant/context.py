"""``ScenarioContext`` — the single injection seam for a rollout's time and data.

Everything that depends on "now" or on the pinned database reaches it through one
object: the wall clock (or a frozen case clock), an as-of-bounded DuckDB executor,
and the HTTP response cache. Nothing here is a module-level singleton — production
builds one context at import with ``clock = site_now``; the harness builds one per
case with ``clock = lambda: case.as_of``. See ``agent_architecture.md`` §4 and
``decisions.md`` § The construction seam.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import duckdb
import structlog

from water_assistant_agent.assistant.agents.text_to_sql.executor import (
    DuckDbQueryExecutor,
)
from water_assistant_agent.assistant.ports import QueryResult

logger = structlog.get_logger(__name__)

Clock = Callable[[], datetime]
"""Zero-argument callable returning the current instant, evaluated per read.

Production passes ``site_now`` (advances every call); the harness passes a
closure returning one case's frozen ``as_of``. Nothing downstream may capture the
value this returns at construction time and reuse it — every read calls it again.
"""

# The five tables an as-of connection bounds. Also the allow-list that makes the
# name safe to interpolate into the view DDL below (`swc.py` uses the same pattern
# for column names).
_AS_OF_TABLES: tuple[str, ...] = ("outflow", "radiation", "swc", "tsoil", "wetter")


def _quoted_literal(value: str) -> str:
    """Single-quote *value* for interpolation into a DuckDB string literal."""
    return value.replace("'", "''")


def connect_asof(db_path: str, clock: Clock) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB with each of the five tables bounded at ``clock()``.

    Attaches *db_path* read-only as ``src`` and creates one
    ``main.<table> AS SELECT * FROM src.<table> WHERE timestamp <= :as_of`` view per
    table, so unqualified names — the querier's and the plot tool's — resolve to the
    bounded view while ``src.<table>`` itself still reads through to the pinned
    file's live end; the file itself is never modified.

    ``clock()`` is an instant, converted to UTC **here**, never by the caller: the
    columns are naive UTC timestamps, and comparing a timezone-aware value against
    them any other way renders the conversion in the *host's* session timezone
    (inherited from its ``TZ``), cutting the record at a different real instant per
    host (``decisions.md`` § The as-of cut).
    """
    as_of = clock()
    as_of_utc = as_of.astimezone(UTC).replace(tzinfo=None)
    as_of_literal = as_of_utc.strftime("%Y-%m-%d %H:%M:%S.%f")

    connection = duckdb.connect(":memory:")
    connection.execute(f"ATTACH '{_quoted_literal(db_path)}' AS src (READ_ONLY)")
    for table in _AS_OF_TABLES:
        connection.execute(
            f"CREATE VIEW main.{table} AS SELECT * FROM src.{table} "  # noqa: S608 - table from _AS_OF_TABLES
            f"WHERE timestamp <= TIMESTAMP '{as_of_literal}'"
        )
    return connection


class AsOfQueryExecutor:
    """A read-only DuckDB executor whose as-of bound tracks *clock*.

    ``connect_asof`` bakes the bound into the views at connection time, so a
    long-lived context (production's site-clocked singleton) would keep serving
    yesterday's views forever unless something rebuilds the connection after
    midnight. This wraps a :class:`DuckDbQueryExecutor` and rebuilds it — via the
    same view-recreating factory — whenever ``clock().date()`` has moved since the
    live connection was built, so a caller holding this one object across a date
    change needs to do nothing to see the new day on its next query. A
    frozen-clock context (the harness) never moves, so this never fires there.
    """

    def __init__(self, db_path: str, clock: Clock) -> None:
        self._db_path = db_path
        self._clock = clock
        self._bound_date = clock().date()
        self._executor = self._build_executor()

    def _build_executor(self) -> DuckDbQueryExecutor:
        return DuckDbQueryExecutor(
            connection_factory=lambda: connect_asof(self._db_path, self._clock)
        )

    def execute_query(self, query: str) -> QueryResult:
        """Execute *query* against the current as-of view, reconnecting if stale."""
        current_date = self._clock().date()
        if current_date != self._bound_date:
            logger.debug(
                "As-of date moved, rebuilding the bounded connection",
                previous=self._bound_date.isoformat(),
                current=current_date.isoformat(),
            )
            self._bound_date = current_date
            self._executor = self._build_executor()
        return self._executor.execute_query(query)


class ScenarioContext:
    """Everything one rollout needs, bound to one clock.

    ``clock`` is read fresh every time ``as_of`` or a query runs — never captured
    once and reused — so the same code path serves production's advancing clock and
    the harness's frozen one. ``db`` is a :class:`DuckDbQueryExecutor`-compatible
    executor over a **view-recreating** factory, never a bare connection.
    """

    def __init__(
        self,
        clock: Clock,
        db_path: str,
        weather_client_factory: Callable[[Any, Any], Any],
        http_cache: Any,
    ) -> None:
        self.clock = clock
        self.db = AsOfQueryExecutor(db_path, clock)
        self.cache = http_cache
        # Both halves, in this order: the station reads the as-of views, Archive
        # reads the cache. A factory taking only `db` cannot build the composite.
        self.weather = weather_client_factory(self.db, self.cache)

    @classmethod
    def bound(
        cls,
        *,
        clock: Clock,
        db: Any,
        weather: Any = None,
        cache: Any = None,
    ) -> "ScenarioContext":
        """Build a context around collaborators that already exist.

        ``__init__`` is the harness's constructor: it takes a *db_path* and builds
        the as-of executor itself, opening a DuckDB connection there and then.
        Production wants the same object shape around the collaborators it already
        has — the lazily-resolved settings executor and the site clock — and wants
        it without touching a database at import time, so it comes in through here.
        Both paths hand the identical ``ScenarioContext`` to the same factories;
        only the binding differs (``agent_architecture.md`` §4).
        """
        context = cls.__new__(cls)
        context.clock = clock
        context.db = db
        context.cache = cache
        context.weather = weather
        return context

    @property
    def as_of(self) -> datetime:
        """The instant this rollout is bound to, re-evaluated on every read."""
        return self.clock()
