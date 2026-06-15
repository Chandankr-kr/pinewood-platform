"""Bronze layer: land raw CSVs exactly as they arrived, plus ingestion
metadata. No business cleaning happens here — Bronze is the audit trail.

Design choices:
* Incremental by file. Each source file is loaded at most once; a re-run skips
  files already recorded in `meta.ingested_files` (use --force to reload).
* Schema drift safe. Newer files may add columns (e.g. `mobility_status` shows
  up in the April residents export). We evolve the Bronze table by adding the
  new column as NULL for older rows, never crashing.
* Rerunnable. Reloading a file first deletes that file's Bronze rows, so a
  second run can never duplicate data.
"""
from __future__ import annotations

import glob
import hashlib
import os
from datetime import datetime, timezone

import pandas as pd

from .common import month_from_filename

# source_system -> list of logical tables it exports
SOURCES = {
    "pcc": ["residents", "incidents", "care_history"],
    "yardi": ["units", "leases"],
    "adp": ["shifts"],
    "gbp": ["reviews"],
    "hubspot": ["leads"],
}


def _row_hash(row: pd.Series, business_cols: list[str]) -> str:
    payload = "|".join("" if pd.isna(row[c]) else str(row[c]) for c in business_cols)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _ensure_meta(con):
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS meta.ingested_files (
            source_system VARCHAR,
            table_name    VARCHAR,
            source_file   VARCHAR,
            source_month  VARCHAR,
            rows_in       BIGINT,
            ingested_at   TIMESTAMP,
            PRIMARY KEY (source_file)
        )
        """
    )


def _already_loaded(con, source_file: str) -> bool:
    return (
        con.execute(
            "SELECT COUNT(*) FROM meta.ingested_files WHERE source_file = ?",
            [source_file],
        ).fetchone()[0]
        > 0
    )


def _append_with_drift(con, table: str, df: pd.DataFrame):
    """Append df to bronze.<table>, evolving the schema if columns differ."""
    exists = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema='bronze' AND table_name=?",
        [table],
    ).fetchone()[0]

    con.register("incoming_df", df)
    if not exists:
        con.execute(f"CREATE TABLE bronze.{table} AS SELECT * FROM incoming_df")
        con.unregister("incoming_df")
        return

    existing_cols = [
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='bronze' AND table_name=? ORDER BY ordinal_position",
            [table],
        ).fetchall()
    ]
    # New columns in the incoming file -> add to the table (NULL for old rows).
    for col in df.columns:
        if col not in existing_cols:
            con.execute(f'ALTER TABLE bronze.{table} ADD COLUMN "{col}" VARCHAR')
            existing_cols.append(col)
    # Insert aligning on the full column set (missing -> NULL).
    select_list = ", ".join(
        f'incoming_df."{c}"' if c in df.columns else f'NULL AS "{c}"'
        for c in existing_cols
    )
    col_list = ", ".join(f'"{c}"' for c in existing_cols)
    con.execute(
        f"INSERT INTO bronze.{table} ({col_list}) SELECT {select_list} FROM incoming_df"
    )
    con.unregister("incoming_df")


def ingest(con, cfg: dict, force: bool = False, run_log: dict | None = None) -> dict:
    _ensure_meta(con)
    data_dir = cfg["data_dir"]
    stats: dict[str, dict] = {}
    ingested_at = datetime.now(timezone.utc)

    for source, tables in SOURCES.items():
        for table in tables:
            bronze_table = f"{source}_{table}"
            pattern = os.path.join(data_dir, f"{source}_{table}_*.csv")
            files = sorted(glob.glob(pattern))
            loaded_rows = 0
            loaded_files = 0
            for path in files:
                source_file = os.path.basename(path)
                if _already_loaded(con, source_file):
                    if not force:
                        continue
                    # Reload: drop this file's prior rows for idempotency.
                    con.execute(
                        f"DELETE FROM bronze.{bronze_table} "
                        f"WHERE _source_file = ?",
                        [source_file],
                    )
                    con.execute(
                        "DELETE FROM meta.ingested_files WHERE source_file = ?",
                        [source_file],
                    )

                # Read everything as string to preserve raw fidelity.
                df = pd.read_csv(path, dtype=str, keep_default_na=False)
                business_cols = list(df.columns)
                df["_source_system"] = source
                df["_source_table"] = table
                df["_source_file"] = source_file
                df["_source_month"] = month_from_filename(path)
                df["_ingested_at"] = ingested_at
                df["_row_hash"] = df.apply(
                    lambda r: _row_hash(r, business_cols), axis=1
                )

                _append_with_drift(con, bronze_table, df)
                con.execute(
                    "INSERT INTO meta.ingested_files VALUES (?,?,?,?,?,?)",
                    [
                        source,
                        bronze_table,
                        source_file,
                        month_from_filename(path),
                        len(df),
                        ingested_at,
                    ],
                )
                loaded_rows += len(df)
                loaded_files += 1

            stats[bronze_table] = {
                "files_loaded_this_run": loaded_files,
                "rows_loaded_this_run": loaded_rows,
                "total_rows": con.execute(
                    f"SELECT COUNT(*) FROM bronze.{bronze_table}"
                ).fetchone()[0]
                if con.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema='bronze' AND table_name=?",
                    [bronze_table],
                ).fetchone()[0]
                else 0,
            }

    if run_log is not None:
        run_log["bronze"] = stats
    return stats
