"""Protocol-based text-to-SQL pipeline with unified retry logic.

Ported verbatim from core-agent ``agents/shared/pipeline.py`` (only the module
docstring changed). Orchestrates transpile -> validate with LLM-assisted retry.
"""

import dataclasses
import traceback
from typing import Protocol

import structlog

logger = structlog.get_logger(__name__)

_DEFAULT_MAX_RETRIES = 3
_MIN_RETRIES = 1


class SqlTranspiler(Protocol):
    """Transpile / normalize SQL for the target dialect.

    Implementations raise on transpile errors (e.g. ``SqlglotError``).
    """

    def transpile(self, postgresql_sql: str) -> str: ...


class SqlValidator(Protocol):
    """Validate SQL via dry-run (e.g. EXPLAIN).

    Returns an error description string on retryable failure, or ``None``
    on success.  Non-retryable errors (auth, connection) must be raised
    directly so they propagate immediately through the pipeline.
    """

    async def avalidate(self, sql: str) -> str | None: ...


class SqlFixer(Protocol):
    """Fix invalid SQL using LLM assistance.

    Implementations are pre-configured with a target dialect and table
    context so callers only supply per-invocation parameters.
    """

    async def afix(
        self,
        incorrect_sql: str,
        error_message: str,
        user_statement: str,
        table_schema: str,
    ) -> str: ...


@dataclasses.dataclass(frozen=True, slots=True)
class _StageResult:
    """Internal result of a single retry stage (transpile or validate)."""

    sql: str
    attempts: int


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of a transpile -> validate cycle."""

    final_sql: str
    transpile_attempts: int
    validate_attempts: int


class TextToSqlPipeline:
    """Orchestrate transpile -> validate with unified retry logic."""

    def __init__(
        self,
        transpiler: SqlTranspiler,
        validator: SqlValidator,
        transpile_fixer: SqlFixer,
        validate_fixer: SqlFixer,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        if max_retries < _MIN_RETRIES:
            msg = f"max_retries must be >= {_MIN_RETRIES}, got {max_retries}"
            raise ValueError(msg)
        self._transpiler = transpiler
        self._validator = validator
        self._transpile_fixer = transpile_fixer
        self._validate_fixer = validate_fixer
        self._max_retries = max_retries

    async def aexecute(
        self,
        postgresql_sql: str,
        user_statement: str,
        table_schema: str,
    ) -> PipelineResult:
        """Run transpile -> validate with LLM-assisted retry on failures."""
        transpile_result = await self._atranspile_with_retries(
            postgresql_sql,
            user_statement,
            table_schema,
        )
        validate_result = await self._avalidate_with_retries(
            transpile_result.sql,
            user_statement,
            table_schema,
        )
        return PipelineResult(
            final_sql=validate_result.sql,
            transpile_attempts=transpile_result.attempts,
            validate_attempts=validate_result.attempts,
        )

    async def _atranspile_with_retries(
        self,
        postgresql_sql: str,
        user_statement: str,
        table_schema: str,
    ) -> _StageResult:
        """Transpile with up to *max_retries* LLM-assisted fix attempts."""
        candidate = postgresql_sql
        for attempt in range(self._max_retries):
            try:
                return _StageResult(
                    sql=self._transpiler.transpile(candidate),
                    attempts=attempt + 1,
                )
            except Exception as err:
                if attempt >= self._max_retries - 1:
                    raise RuntimeError(
                        f"Failed to transpile SQL query after {self._max_retries} "
                        f"attempts:\n{candidate}",
                    ) from err
                logger.exception(
                    "Transpile attempt failed, invoking fixer",
                    attempt=attempt,
                )
                candidate = await self._transpile_fixer.afix(
                    incorrect_sql=candidate,
                    error_message=traceback.format_exc(),
                    user_statement=user_statement,
                    table_schema=table_schema,
                )
        msg = "Unreachable: retry loop exited without return or raise"
        raise RuntimeError(msg)

    async def _avalidate_with_retries(
        self,
        target_sql: str,
        user_statement: str,
        table_schema: str,
    ) -> _StageResult:
        """Validate with up to *max_retries* LLM-assisted fix attempts."""
        candidate = target_sql
        for attempt in range(self._max_retries):
            error = await self._validator.avalidate(candidate)
            if error is None:
                return _StageResult(sql=candidate, attempts=attempt + 1)
            if attempt >= self._max_retries - 1:
                raise RuntimeError(
                    f"Failed to fix SQL query after {self._max_retries} "
                    f"attempts:\n{candidate}",
                )
            logger.warning(
                "Validation attempt failed, invoking fixer",
                attempt=attempt,
                error=error,
            )
            candidate = await self._validate_fixer.afix(
                incorrect_sql=candidate,
                error_message=error,
                user_statement=user_statement,
                table_schema=table_schema,
            )
        msg = "Unreachable: retry loop exited without return or raise"
        raise RuntimeError(msg)
