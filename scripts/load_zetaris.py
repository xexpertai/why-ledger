#!/usr/bin/env python3
"""Print (or apply) Lightning SQL to register Why-Ledger SYNTHETIC sources in Zetaris.

Default is DRY-RUN: statements are printed one block at a time. Pass ``--apply`` to
execute via JDBC when ``ZETARIS_LIVE=jdbc``, or REST when ``ZETARIS_LIVE=1`` and
``ZETARIS_API_KEY`` is set.

Local CSV files are copied into the platform ``customer-data`` volume at
``/home/zetaris/customer-data/shared/whyledger/`` (paths verified on Zetaris 2.4.3.1).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_OUT = ROOT / "data" / "out"
LIGHTNING_DB = "WHY_LEDGER_SYN"
CONTAINER = "whyledger"
FILE_BASE = "/home/zetaris/customer-data/shared/whyledger"
DOCKER_CONTAINER = os.environ.get("ZETARIS_DRIVER_CONTAINER", "zetaris-driver-1")


def _ensure_derived_csv(data_dir: Path) -> None:
    """Write bureau.csv and policy_docs.csv from JSON when missing (for FILESTORE)."""
    bureau_csv = data_dir / "bureau.csv"
    if not bureau_csv.is_file() and (data_dir / "bureau.json").is_file():
        payload = json.loads((data_dir / "bureau.json").read_text(encoding="utf-8"))
        rows = payload.get("rows", [])
        if rows:
            with bureau_csv.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
    docs_csv = data_dir / "policy_docs.csv"
    chunks_path = data_dir / "policy_docs_chunks.json"
    if not docs_csv.is_file() and chunks_path.is_file():
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
        if isinstance(chunks, list) and chunks:
            with docs_csv.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(chunks[0].keys()))
                writer.writeheader()
                writer.writerows(chunks)


def _copy_to_volume(data_dir: Path) -> None:
    """docker cp CSVs into the driver customer-data shared tree."""
    _ensure_derived_csv(data_dir)
    remote_dir = f"{FILE_BASE}"
    subprocess.run(
        ["docker", "exec", DOCKER_CONTAINER, "mkdir", "-p", remote_dir],
        check=True,
    )
    for name in (
        "applications.csv",
        "payments.csv",
        "policy_rules.csv",
        "bureau.csv",
        "policy_docs.csv",
    ):
        local = data_dir / name
        if not local.is_file():
            raise FileNotFoundError(f"missing {local} (run: make data)")
        subprocess.run(
            [
                "docker",
                "cp",
                str(local),
                f"{DOCKER_CONTAINER}:{remote_dir}/{name}",
            ],
            check=True,
        )
    subprocess.run(
        [
            "docker",
            "exec",
            "-u",
            "root",
            DOCKER_CONTAINER,
            "chown",
            "-R",
            "zetaris:zetaris",
            remote_dir,
        ],
        check=True,
    )


def _filestore_ddl(table: str, filename: str) -> str:
    path = f"{FILE_BASE}/{filename}"
    return f"""CREATE LIGHTNING FILESTORE TABLE {table} FROM {LIGHTNING_DB} FORMAT CSV OPTIONS (
  path "{path}",
  inferSchema "true",
  header "true"
)"""


def _statements() -> list[tuple[str, str]]:
    return [
        (
            f"Drop existing file tables (idempotent)",
            f"DROP LIGHTNING DATABASE TABLE {LIGHTNING_DB}.applications",
        ),
        (
            "Drop payments",
            f"DROP LIGHTNING DATABASE TABLE {LIGHTNING_DB}.payments",
        ),
        (
            "Drop bureau",
            f"DROP LIGHTNING DATABASE TABLE {LIGHTNING_DB}.bureau",
        ),
        (
            "Drop policy_rules",
            f"DROP LIGHTNING DATABASE TABLE {LIGHTNING_DB}.policy_rules",
        ),
        (
            "Drop policy_docs",
            f"DROP LIGHTNING DATABASE TABLE {LIGHTNING_DB}.policy_docs",
        ),
        (
            "Create Lightning database (SYNTHETIC)",
            f"CREATE LIGHTNING DATABASE {LIGHTNING_DB} DESCRIBE BY 'SYNTHETIC Why-Ledger demo dataset'",
        ),
        (
            "SchemaStore container",
            f"CREATE SCHEMASTORE CONTAINER {CONTAINER}",
        ),
        (
            "Register applications CSV",
            _filestore_ddl("applications", "applications.csv"),
        ),
        (
            "Register payments CSV",
            _filestore_ddl("payments", "payments.csv"),
        ),
        (
            "Register bureau CSV (from bureau.json rows)",
            _filestore_ddl("bureau", "bureau.csv"),
        ),
        (
            "Register policy_rules CSV",
            _filestore_ddl("policy_rules", "policy_rules.csv"),
        ),
        (
            "Register policy_docs CSV",
            _filestore_ddl("policy_docs", "policy_docs.csv"),
        ),
        (
            "Unified semantic view decision_facts",
            f"""CREATE SCHEMASTORE VIEW decision_facts WITH CONTAINER {CONTAINER} AS
SELECT
  a.loan_id,
  a.applicant_id,
  a.amount,
  a.term,
  a.sector,
  a.years_in_business,
  a.declared_income,
  a.outcome,
  a.rate,
  b.score AS bureau_score,
  b.utilisation,
  b.enquiries_6m,
  b.defaults
FROM {LIGHTNING_DB.lower()}.applications a
LEFT JOIN {LIGHTNING_DB.lower()}.bureau b ON a.applicant_id = b.applicant_id""",
        ),
    ]


def _verify_counts(client) -> list[tuple[str, str]]:
    return [
        ("applications", f"SELECT COUNT(*) AS n FROM {LIGHTNING_DB}.applications"),
        ("payments", f"SELECT COUNT(*) AS n FROM {LIGHTNING_DB}.payments"),
        ("bureau", f"SELECT COUNT(*) AS n FROM {LIGHTNING_DB}.bureau"),
        ("policy_rules", f"SELECT COUNT(*) AS n FROM {LIGHTNING_DB}.policy_rules"),
        ("decision_facts", f"SELECT COUNT(*) AS n FROM {CONTAINER}.decision_facts"),
    ]


def _apply_client():
    flag = os.environ.get("ZETARIS_LIVE", "").strip().lower()
    if flag == "jdbc":
        from whyledger.zetaris_jdbc import JdbcZetarisClient

        return JdbcZetarisClient()
    if flag in ("1", "true", "yes") and os.environ.get("ZETARIS_API_KEY", "").strip():
        from whyledger.zetaris import RestZetarisClient

        return RestZetarisClient()
    raise RuntimeError("ZETARIS_LIVE=jdbc or ZETARIS_LIVE=1 with ZETARIS_API_KEY required")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load Why-Ledger tables into Zetaris (dry-run default).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy files to customer-data and execute DDL via JDBC or REST.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DATA_OUT,
        help="Directory with applications.csv, payments.csv, policy_rules.csv, bureau.json",
    )
    parser.add_argument(
        "--skip-copy",
        action="store_true",
        help="Skip docker cp (files already on customer-data volume).",
    )
    args = parser.parse_args(argv)

    if not args.data_dir.is_dir():
        print(f"data dir missing: {args.data_dir} (run: make data)", file=sys.stderr)
        return 1

    blocks = _statements()
    for title, sql in blocks:
        print(f"\n-- === {title} ===")
        print(sql)

    if not args.apply:
        print("\n-- DRY-RUN complete. Re-run with --apply to copy files and execute.")
        return 0

    if not args.skip_copy:
        print("\n-- Copying CSVs into customer-data volume ...")
        _copy_to_volume(args.data_dir)

    client = _apply_client()
    for i, (title, stmt) in enumerate(blocks, start=1):
        print(f"\n-- APPLY [{i}/{len(blocks)}] {title}")
        try:
            result = client.execute_statement(stmt)
            print(f"-- rows returned: {len(result.rows)} (query_id={result.query_id})")
        except Exception as exc:
            msg = str(exc)
            if "already exists" in msg.lower() or "AlreadyExists" in msg:
                print(f"-- skip (already exists): {title}")
            elif title.startswith("Drop") and (
                "not found" in msg.lower() or "TABLE_OR_VIEW_NOT_FOUND" in msg
            ):
                print(f"-- skip drop (not present): {title}")
            else:
                print(f"-- FAILED: {exc}", file=sys.stderr)
                return 1

    print("\n-- VERIFY counts:")
    for label, sql in _verify_counts(client):
        result = client.run_sql(sql, limit=5)
        n = result.rows[0].get("n") if result.rows else "?"
        print(f"--   {label}: {n}")

    if hasattr(client, "close"):
        client.close()
    print("\n-- APPLY complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
