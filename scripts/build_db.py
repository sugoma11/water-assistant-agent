"""Build data/water.duckdb from the frozen CSVs under data/timeseries/.

Run once when source files arrive. Re-run if cleaning logic changes.
"""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "data" / "timeseries"
DB_PATH = ROOT / "data" / "water.duckdb"

SOURCES = [
    # (table, file, time_col, has_dup_timestamps, drop_prefixes)
    ("outflow",   "Outflow.txt",         "Date", False, ("Zeitlysi_Efflux.y", "Sensorlysi_Efflux.y")),
    ("radiation", "Radiation.txt",       "time", True,  ("CNR", "CMP", "CGR")),
    ("swc",       "SWC.txt",             "time", True,  ()),
    ("tsoil",     "Tsoil.txt",           "time", True,  ()),
    ("wetter",    "Wetter_Campbell.txt", "Date", False, ()),
]


def sanitize(name: str) -> str:
    return name.replace(".", "_").rstrip("_")


def drop_columns(cols, drop_prefixes):
    if not drop_prefixes:
        return cols
    return [c for c in cols if not any(c.startswith(p) for p in drop_prefixes)]


def build_table(con, table, file_path, time_col, has_dups, drop_prefixes=()):
    desc = con.execute(
        "SELECT * FROM read_csv(?, delim=';', decimal_separator=',', "
        "header=true, nullstr=['NA','']) LIMIT 0",
        [str(file_path)],
    ).description
    cols = [d[0] for d in desc]
    value_cols = drop_columns([c for c in cols if c != time_col], drop_prefixes)

    src_select = ", ".join(f'"{c}"' for c in value_cols)
    final_select = ", ".join(
        f'MEDIAN("{c}") AS "{sanitize(c)}"' if has_dups
        else f'"{c}" AS "{sanitize(c)}"'
        for c in value_cols
    )
    group_by = "GROUP BY timestamp" if has_dups else ""

    con.execute(f"""
        CREATE OR REPLACE TABLE {table} AS
        WITH src AS (
            SELECT CAST("{time_col}" AS TIMESTAMP) AS timestamp,
                   {src_select}
            FROM read_csv('{file_path}',
                  delim=';', decimal_separator=',', header=true,
                  nullstr=['NA',''])
        )
        SELECT timestamp, {final_select}
        FROM src
        {group_by}
        ORDER BY timestamp;
    """)


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    for table, fname, tcol, dups, drop_pfx in SOURCES:
        build_table(con, table, SRC_DIR / fname, tcol, dups, drop_pfx)
        n = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        unique_ts = con.execute(
            f"SELECT COUNT(DISTINCT timestamp) FROM {table}"
        ).fetchone()[0]
        print(f"{table:10s}: {n:>6} rows, {unique_ts:>6} unique timestamps")
    con.close()


if __name__ == "__main__":
    main()
