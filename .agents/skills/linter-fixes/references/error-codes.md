# Error Code Reference

## Ruff / pycodestyle

| Code | Meaning | Recommended Fix |
|------|---------|-----------------|
| **E501** | Line too long (>88 chars) | Wrap the line. For SQL or prompt strings, add `# noqa: E501 - SQL` or `# noqa: E501 - prompt context`. |

## flake8-bandit

| Code | Meaning | Recommended Fix |
|------|---------|-----------------|
| **S101** | Use of `assert` detected | `assert` is forbidden in production source (`src/`); it is stripped by `python -O`. Replace with an explicit exception: `if value is None: raise TypeError("…")`. For mypy type-narrowing of `X \| None`, write a `TypeGuard` helper or an `if … is None: raise` guard so the type checker narrows without `assert`. `assert` is allowed in test code (`tests/`). |
| **S608** | Possible SQL injection via string formatting | If the identifier is safe (e.g., quoted table name from config), add `# noqa: S608 - <reason>`. Otherwise refactor to use parameterized queries. |

## wemake-python-styleguide (WPS)

| Code | Meaning | Recommended Fix |
|------|---------|-----------------|
| **WPS202** | Too many module members | Split the module into a package with focused sub-modules. Suppression via config is forbidden unless human-approved. |
| **WPS210** | Too many local variables (>5) | Inline intermediate variables into the expression that consumes them. Prefer generator expressions inside `join()` or similar calls. |
| **WPS211** | Too many arguments (>10) | Group related parameters into a dataclass (e.g., `TenantAssets`) and pass it as a single argument. Never suppress with `# noqa`. |
| **WPS221** | Too high Jones complexity | Simplify the expression or extract a helper. For unavoidable SQL CTE chains, `# noqa: WPS221 - SQL CTE chain` is acceptable. |
| **WPS226** | String literal over-use (>3) | Extract the repeated literal into a module-level `Final` constant (e.g., `_COMMA_SEP: Final[str] = ", "`). |
| **WPS235** | Too many imported names from one module (>8) | Import the module itself instead of individual names: `from package import module` then use `module.name`. Never suppress with `# noqa`. |
| **WPS402** | Too many `noqa` comments in a file | First try to reduce line lengths so fewer `# noqa: E501` are needed. If the file legitimately needs many (e.g., SQL query builders), add a `per-file-ignores` entry in `pyproject.toml` with human approval. |
| **WPS430** | Found nested function | Extract the nested function to module level or pass needed context as parameters. |
| **WPS432** | Found magic number | Extract the number into a named constant with a descriptive name. |

## mypy

| Code | Meaning | Recommended Fix |
|------|---------|-----------------|
| **[import-untyped]** | Importing an untyped third-party module | Add `# type: ignore[import-untyped]` on the import line. |
| **[override]** | Incompatible override signature | Align the child method signature with the parent. Use `@override` decorator for clarity. |
| **[arg-type]** | Argument type mismatch | Fix the type or add a cast. Do not use `# type: ignore` without a specific error code. |
| **[return-value]** | Return type mismatch | Fix the return expression or update the annotation. |

## Examples

### WPS226 — String literal over-use

**Before** (4× `", "` triggers WPS226):

```python
joined = ", ".join(names)
cols = ", ".join(columns)
vals = ", ".join(values)
sets = ", ".join(assignments)
```

**After** (extract to constant):

```python
from typing import Final

_COMMA_SEP: Final[str] = ", "

joined = _COMMA_SEP.join(names)
cols = _COMMA_SEP.join(columns)
vals = _COMMA_SEP.join(values)
sets = _COMMA_SEP.join(assignments)
```

### WPS210 — Too many local variables

**Before** (6 locals, limit is 5):

```python
def build_query(rows: Sequence[Row], table: str) -> str:
    columns = Schema.column_names_str
    literals = _COMMA_SEP.join(Schema.literal(r) for r in rows)
    on_clause = " AND ".join(f"t.{c} = s.{c}" for c in Schema.keys)
    mutable = [c for c in Schema.columns if c not in Schema.keys]
    update_set = _COMMA_SEP.join(f"{c} = s.{c}" for c in mutable)
    source = _COMMA_SEP.join(f"s.{c}" for c in Schema.columns)
    return f"MERGE INTO {table} ..."
```

**After** (inline `mutable` into `join()`):

```python
def build_query(rows: Sequence[Row], table: str) -> str:
    columns = Schema.column_names_str
    literals = _COMMA_SEP.join(Schema.literal(r) for r in rows)
    on_clause = " AND ".join(f"t.{c} = s.{c}" for c in Schema.keys)
    update_set = _COMMA_SEP.join(
        f"{c} = s.{c}" for c in Schema.columns if c not in Schema.keys
    )
    source = _COMMA_SEP.join(f"s.{c}" for c in Schema.columns)
    return f"MERGE INTO {table} ..."
```

### WPS402 — Too many noqa comments

**When the file legitimately needs many noqa** (e.g., SQL query builders with long lines):

1. Confirm with a human that per-file suppression is appropriate.
2. Add to `pyproject.toml`:

```toml
per-file-ignores = [
    # Human-approved: SQL query builder with many inline noqa for E501/S608
    "src/contract_parsing/data_engineering/leak_finder/query.py: WPS402",
]
```

### E501 + S608 — Long SQL with string formatting

**Acceptable** (SQL line with both exemptions):

```python
query = (
    f"SELECT script_run_id FROM {table} "  # noqa: S608
    f"WHERE company_name IN ({placeholders})"
)
```

**Acceptable** (long SQL on one line):

```python
f", COALESCE(SUM(amount) FILTER (WHERE {label} = 'unlinked/null_commission'), 0)"  # noqa: E501 - SQL
```

### WPS235 — Too many imported module members

**Before** (9 names from one module, limit is 8):

```python
from contract_parsing.agents.text_to_sql_agent.assets import (
    BIGQUERY_AGENT_RESPONSE_SCHEMA,
    BIGQUERY_AGENT_RESPONSE_SCHEMA_TEXT,
    QUERY_GENERATION_MODEL,
    QUERY_GENERATION_MODEL_REASONING_EFFORT,
    get_active_client_table_name,
    get_active_id_name_pairs,
    get_active_priority_columns,
    get_active_semantic_layer,
    get_active_table_schema_dict,
)
```

**After** (import the module, use qualified access):

```python
from contract_parsing.agents.text_to_sql_agent import assets as bq_assets

# usage:
schema = bq_assets.get_active_table_schema_dict()
model = bq_assets.QUERY_GENERATION_MODEL
```

### WPS211 — Too many arguments

**Before** (11 params, limit is 10):

```python
async def invoke_student_model(
    *,
    system_prompt: str,
    user_input: str,
    student_model: str,
    table_schema: str,
    user_statement: str,
    semantic_layer: str,
    table_name: str,
    id_name_pairs: tuple[tuple[str, str], ...],
    priority_columns: tuple[tuple[str, str], ...],
    reasoning_effort: Literal["low", "medium", "high"],
    cache_control: Sequence[dict[str, str | int]],
) -> PostgresBuilderResponseWithReasoning: ...
```

**After** (group related params into a dataclass):

```python
async def invoke_student_model(
    *,
    system_prompt: str,
    user_input: str,
    student_model: str,
    user_statement: str,
    tenant_assets: TenantAssets,
    reasoning_effort: Literal["low", "medium", "high"],
    cache_control: Sequence[dict[str, str | int]],
) -> PostgresBuilderResponseWithReasoning:
    formatted_system_prompt = system_prompt.format(
        table_schema=schema_to_json(tenant_assets.table_schema),
        semantic_layer=tenant_assets.semantic_layer,
        ...
    )
```

### S101 — Use of assert in production code

**Before** (assert used for type narrowing — stripped by `python -O`):

```python
def process_page(doc: pymupdf.Document, index: int) -> str:
    page = doc[index]
    assert page is not None  # noqa: S101 — narrowing for mypy
    return page.get_text()
```

**After** (explicit guard with `TypeError`):

```python
def process_page(doc: pymupdf.Document, index: int) -> str:
    page = doc[index]
    if page is None:
        msg = f"document returned None for page index {index}"
        raise TypeError(msg)
    return page.get_text()
```

**Alternative** (TypeGuard helper for reusable narrowing):

```python
from typing import TypeGuard

def _is_page(page: pymupdf.Page | None) -> TypeGuard[pymupdf.Page]:
    return page is not None

def process_page(doc: pymupdf.Document, index: int) -> str:
    page = doc[index]
    if not _is_page(page):
        msg = f"document returned None for page index {index}"
        raise TypeError(msg)
    return page.get_text()
```
