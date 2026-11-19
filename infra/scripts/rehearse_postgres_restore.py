#!/usr/bin/env python3
"""Run a local PostgreSQL backup + restore rehearsal and emit evidence."""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit


@dataclass(frozen=True)
class TableSnapshot:
    name: str
    source_count: int
    restored_count: int


def _normalize_postgres_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return database_url


def _replace_database(database_url: str, database_name: str) -> str:
    parts = urlsplit(database_url)
    return urlunsplit(
        SplitResult(
            scheme=parts.scheme,
            netloc=parts.netloc,
            path=f"/{database_name}",
            query=parts.query,
            fragment=parts.fragment,
        )
    )


def _run(command: list[str], *, capture_output: bool = False) -> str:
    completed = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture_output,
    )
    if capture_output:
        return completed.stdout.strip()
    return ""


def _quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _query_value(database_url: str, sql: str) -> str:
    return _run(["psql", database_url, "-Atqc", sql], capture_output=True)


def _query_table_list(database_url: str) -> list[str]:
    output = _query_value(
        database_url,
        """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
        """,
    )
    return [line for line in output.splitlines() if line]


def _query_table_count(database_url: str, table_name: str) -> int:
    sql = f"SELECT count(*) FROM public.{_quote_ident(table_name)}"
    return int(_query_value(database_url, sql))


def _drop_database(source_database_url: str, database_name: str) -> None:
    sql = f"DROP DATABASE IF EXISTS {_quote_ident(database_name)} WITH (FORCE)"
    _run(["psql", source_database_url, "-v", "ON_ERROR_STOP=1", "-c", sql])


def _create_database(source_database_url: str, database_name: str) -> None:
    sql = f"CREATE DATABASE {_quote_ident(database_name)}"
    _run(["psql", source_database_url, "-v", "ON_ERROR_STOP=1", "-c", sql])


def _build_evidence_markdown(
    *,
    generated_at: datetime,
    operator: str,
    source_database_url: str,
    restore_database_name: str,
    dump_path: Path,
    dump_sha256: str,
    dump_size_bytes: int,
    alembic_version: str,
    source_table_count: int,
    tables: list[TableSnapshot],
) -> str:
    sanitized_source = urlsplit(source_database_url)
    header = [
        "# PostgreSQL Restore Rehearsal",
        "",
        f"- Generated at: `{generated_at.isoformat()}`",
        f"- Operator: `{operator}`",
        f"- Source host: `{sanitized_source.hostname}`",
        f"- Source port: `{sanitized_source.port or 5432}`",
        f"- Source database: `{sanitized_source.path.lstrip('/')}`",
        f"- Restore database: `{restore_database_name}`",
        f"- Dump file: `{dump_path}`",
        f"- Dump SHA-256: `{dump_sha256}`",
        f"- Dump size (bytes): `{dump_size_bytes}`",
        f"- Alembic version: `{alembic_version}`",
        f"- Verified public tables: `{source_table_count}`",
        "- Result: `PASS`",
        "",
        "## Notes",
        "- Postgres is treated as the authoritative transactional store.",
        "- Redis is not restored by this rehearsal because it is treated as rebuildable runtime state (cache, broker, revocation, rate limits).",
        "- POD asset recovery is covered separately by S3 versioning and bucket controls in Terraform.",
        "",
        "## Table parity sample",
        "",
        "| table | source rows | restored rows |",
        "|---|---:|---:|",
    ]
    rows = [
        f"| `{table.name}` | {table.source_count} | {table.restored_count} |"
        for table in tables[:12]
    ]
    footer = [
        "",
        "Full parity across all public tables was verified by the rehearsal script.",
    ]
    return "\n".join(header + rows + footer) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="Source database URL. Defaults to DATABASE_URL.",
    )
    parser.add_argument(
        "--evidence-path",
        type=Path,
        required=True,
        help="Markdown path to write the rehearsal evidence to.",
    )
    parser.add_argument(
        "--maintenance-url",
        default=os.environ.get("MAINTENANCE_DATABASE_URL", ""),
        help=(
            "Administrative PostgreSQL connection used for CREATE/DROP DATABASE. "
            "Defaults to MAINTENANCE_DATABASE_URL, then falls back to --database-url."
        ),
    )
    parser.add_argument(
        "--keep-dump",
        action="store_true",
        help="Retain the pg_dump artifact instead of deleting it after the rehearsal.",
    )
    parser.add_argument(
        "--keep-restore-db",
        action="store_true",
        help="Retain the temporary restored database after the rehearsal.",
    )
    args = parser.parse_args()

    if not args.database_url:
        parser.error("DATABASE_URL is required (or pass --database-url).")

    source_database_url = _normalize_postgres_url(args.database_url)
    maintenance_database_url = _normalize_postgres_url(
        args.maintenance_url or args.database_url
    )
    source_database_name = _query_value(source_database_url, "SELECT current_database()")
    source_database_user = _query_value(source_database_url, "SELECT current_user")
    restore_database_name = (
        f"{source_database_name}_restore_rehearsal_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    restore_database_url = _replace_database(source_database_url, restore_database_name)
    args.evidence_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="restore-rehearsal-") as temp_dir:
        dump_path = Path(temp_dir) / f"{source_database_name}.dump"
        try:
            _run(
                [
                    "pg_dump",
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--file",
                    str(dump_path),
                    source_database_url,
                ]
            )
            dump_sha256 = hashlib.sha256(dump_path.read_bytes()).hexdigest()
            dump_size_bytes = dump_path.stat().st_size

            _drop_database(maintenance_database_url, restore_database_name)
            _run(
                [
                    "psql",
                    maintenance_database_url,
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-c",
                    (
                        f"CREATE DATABASE {_quote_ident(restore_database_name)} "
                        f"OWNER {_quote_ident(source_database_user)}"
                    ),
                ]
            )
            _run(
                [
                    "pg_restore",
                    "--no-owner",
                    "--no-privileges",
                    "--clean",
                    "--if-exists",
                    "--dbname",
                    restore_database_url,
                    str(dump_path),
                ]
            )

            source_tables = _query_table_list(source_database_url)
            restored_tables = _query_table_list(restore_database_url)
            if source_tables != restored_tables:
                raise RuntimeError(
                    "Source and restored databases expose different public tables:\n"
                    f"source={source_tables}\nrestored={restored_tables}"
                )

            snapshots: list[TableSnapshot] = []
            for table_name in source_tables:
                source_count = _query_table_count(source_database_url, table_name)
                restored_count = _query_table_count(restore_database_url, table_name)
                if source_count != restored_count:
                    raise RuntimeError(
                        f"Row-count mismatch for table {table_name}: "
                        f"source={source_count}, restored={restored_count}"
                    )
                snapshots.append(
                    TableSnapshot(
                        name=table_name,
                        source_count=source_count,
                        restored_count=restored_count,
                    )
                )

            alembic_version = _query_value(
                restore_database_url,
                "SELECT version_num FROM alembic_version",
            )
            generated_at = datetime.now(timezone.utc)
            evidence_markdown = _build_evidence_markdown(
                generated_at=generated_at,
                operator=os.environ.get("USER", "unknown"),
                source_database_url=source_database_url,
                restore_database_name=restore_database_name,
                dump_path=dump_path,
                dump_sha256=dump_sha256,
                dump_size_bytes=dump_size_bytes,
                alembic_version=alembic_version,
                source_table_count=len(source_tables),
                tables=snapshots,
            )
            args.evidence_path.write_text(evidence_markdown, encoding="utf-8")

            print(
                "Restore rehearsal completed successfully.\n"
                f"Source DB: {source_database_name}\n"
                f"Restore DB: {restore_database_name}\n"
                f"Evidence: {args.evidence_path}\n"
                f"Dump SHA-256: {dump_sha256}"
            )

            if args.keep_dump:
                retained_dump = args.evidence_path.parent / dump_path.name
                retained_dump.write_bytes(dump_path.read_bytes())
                print(f"Retained dump artifact at {retained_dump}")
            return 0
        finally:
            if not args.keep_restore_db:
                try:
                    _drop_database(maintenance_database_url, restore_database_name)
                except subprocess.CalledProcessError as exc:
                    print(
                        "WARNING: failed to drop restore rehearsal database: "
                        f"{shlex.join(exc.cmd)}",
                        file=sys.stderr,
                    )


if __name__ == "__main__":
    raise SystemExit(main())
