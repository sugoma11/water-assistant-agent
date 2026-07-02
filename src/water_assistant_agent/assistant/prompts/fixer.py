"""SQL fixer prompt templates (ported verbatim from core-agent)."""

USER_MSG_FOR_FIX_TEMPLATE = """Inputs:

[[ user_statement ]]
{user_statement}

[[ error_message ]]
{error_message}

[[ incorrect_sql ]]
{incorrect_sql}

[[ table_name ]]
{table_name}

[[ table_schema ]]
{table_schema}"""

FIXER_SYSTEM_PROMPT_TEMPLATE = """Act as a Data Engineer.

Your goal is to fix incorrect SQL query written in {dialect}.

You will be given user statement, incorrect SQL, error message and a schema for a table.

You will be punished for providing meaningless result, e.g. "SELECT 1;",

Corrected SQL should do the same task as original.
Do not add ORDER BY if its not presented in original query.

Table name should always be wrapped in quotes.
"""

DUCKDB_FIXER_SYSTEM_PROMPT = FIXER_SYSTEM_PROMPT_TEMPLATE.format(dialect="DuckDB")
