"use client";

/**
 * CopilotKit render for the `text_to_sql_agent` tool call (T026, FR15, C9, SC8).
 *
 * The backend enriches this tool's result with the executed query when one ran:
 * `{status:"success", sql, reasoning, results:{columns, rows, result_is_likely_truncated?}}`
 * (D4). On success we show the SQL as a code block and the rows as a table,
 * noting truncation. When the answer used no query — or the query failed, or the
 * result can't be parsed — there is nothing structured to show, so we fall back
 * to plain chat (the assistant's own text renders as markdown separately).
 */
import { CSSProperties } from "react";
import { format as formatSql } from "sql-formatter";

type RenderStatus = "inProgress" | "executing" | "complete";

type SqlResults = {
  columns?: string[];
  rows?: Array<Record<string, unknown>>;
  result_is_likely_truncated?: boolean;
};

type SqlToolResult = {
  status?: string;
  sql?: string;
  reasoning?: string;
  results?: SqlResults;
};

function parseResult(result: unknown): SqlToolResult | null {
  if (result == null) return null;
  if (typeof result === "object") return result as SqlToolResult;
  if (typeof result === "string") {
    try {
      const parsed = JSON.parse(result);
      return typeof parsed === "object" && parsed !== null
        ? (parsed as SqlToolResult)
        : null;
    } catch {
      return null;
    }
  }
  return null;
}

// Pretty-print the executed query as multiline SQL. sql-formatter throws on
// syntax it can't parse, so fall back to the raw string rather than crash.
function prettySql(sql: string): string {
  try {
    return formatSql(sql, { language: "postgresql" });
  } catch {
    return sql;
  }
}

function cellText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function TextToSqlResult({
  status,
  result,
}: {
  status: RenderStatus;
  result: unknown;
}) {
  if (status !== "complete") {
    return (
      <div style={pendingStyle} aria-live="polite">
        Querying the water warehouse…
      </div>
    );
  }

  const parsed = parseResult(result);
  const results = parsed?.results;
  const columns = results?.columns ?? [];
  const rows = results?.rows ?? [];

  // Plain fallback: no successful structured query result to render.
  if (parsed?.status !== "success" || columns.length === 0) {
    return null;
  }

  return (
    <div style={cardStyle}>
      {parsed.sql ? (
        <div>
          <div style={captionStyle}>SQL</div>
          <pre style={codeBlockStyle}>
            <code>{prettySql(parsed.sql)}</code>
          </pre>
        </div>
      ) : null}

      <div>
        <div style={captionStyle}>
          Results{rows.length ? ` · ${rows.length} row${rows.length === 1 ? "" : "s"}` : ""}
          {results?.result_is_likely_truncated ? " (truncated)" : ""}
        </div>
        {rows.length === 0 ? (
          <p style={emptyStyle}>No rows returned.</p>
        ) : (
          <div style={tableWrapStyle}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  {columns.map((col) => (
                    <th key={col} style={thStyle}>
                      {col}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, r) => (
                  <tr key={r}>
                    {columns.map((col) => (
                      <td key={col} style={tdStyle}>
                        {cellText(row[col])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

const cardStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "0.75rem",
  border: "1px solid var(--wa-border)",
  borderRadius: 10,
  padding: "0.85rem",
  background: "var(--wa-panel)",
  margin: "0.35rem 0",
};

const captionStyle: CSSProperties = {
  fontSize: "0.72rem",
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  color: "var(--wa-muted)",
  marginBottom: "0.35rem",
};

const codeBlockStyle: CSSProperties = {
  margin: 0,
  padding: "0.65rem 0.8rem",
  borderRadius: 8,
  background: "var(--wa-bg)",
  border: "1px solid var(--wa-border)",
  color: "#93c5fd",
  fontSize: "0.82rem",
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
  overflowX: "auto",
  whiteSpace: "pre",
};

const tableWrapStyle: CSSProperties = {
  overflowX: "auto",
  maxHeight: 340,
  overflowY: "auto",
  border: "1px solid var(--wa-border)",
  borderRadius: 8,
};

const tableStyle: CSSProperties = {
  borderCollapse: "collapse",
  fontSize: "0.82rem",
  width: "100%",
};

const thStyle: CSSProperties = {
  position: "sticky",
  top: 0,
  textAlign: "left",
  padding: "0.4rem 0.6rem",
  background: "var(--wa-bg)",
  borderBottom: "1px solid var(--wa-border)",
  color: "var(--wa-text)",
  whiteSpace: "nowrap",
};

const tdStyle: CSSProperties = {
  padding: "0.35rem 0.6rem",
  borderBottom: "1px solid var(--wa-border)",
  color: "var(--wa-text)",
  whiteSpace: "nowrap",
};

const emptyStyle: CSSProperties = {
  margin: 0,
  color: "var(--wa-muted)",
  fontSize: "0.82rem",
};

const pendingStyle: CSSProperties = {
  fontSize: "0.82rem",
  color: "var(--wa-muted)",
  fontStyle: "italic",
  margin: "0.35rem 0",
};
