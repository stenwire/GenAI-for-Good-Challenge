"""SQLite, opened through a read-only URI (plan §7.7)."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from municipal_qa.readers.base import PassageBuilder, is_documentation_name, register
from municipal_qa.schema import ColumnProfile, Passage, SourceInfo, SourceProfile, TableProfile


def connect_ro(path: str | Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(Path(path).resolve()))}?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def list_tables(con: sqlite3.Connection) -> list[str]:
    rows = con.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


class SqliteReader:
    name = "sqlite"
    suffixes = (".sqlite", ".sqlite3", ".db")

    def profile(self, src: SourceInfo) -> SourceProfile:
        tables, lines = [], []
        with closing(connect_ro(src.path)) as con:
            for t in list_tables(con):
                info = con.execute(f"PRAGMA table_info({_q(t)})").fetchall()
                names = [r[1] for r in info]
                n_rows = con.execute(f"SELECT count(*) FROM {_q(t)}").fetchone()[0]
                cols = []
                for name, decl in ((r[1], r[2]) for r in info):
                    nulls = con.execute(
                        f"SELECT count(*) FROM {_q(t)} WHERE {_q(name)} IS NULL"
                    ).fetchone()[0]
                    ex = con.execute(
                        f"SELECT DISTINCT {_q(name)} FROM {_q(t)} "
                        f"WHERE {_q(name)} IS NOT NULL LIMIT 5"
                    ).fetchall()
                    cols.append(
                        ColumnProfile(
                            name=name,
                            dtype=(decl or "untyped").lower(),
                            n_null=nulls,
                            n_unique=None,
                            examples=[str(e[0])[:80] for e in ex],
                        )
                    )
                tables.append(TableProfile(table_name=t, n_rows=n_rows, columns=cols))
                lines.append(f"{t} ({n_rows:,} rows: {', '.join(names)})")
        return SourceProfile(
            source=src,
            reader=self.name,
            kind="database",
            tables=tables,
            summary=(f"{src.original_file}: SQLite, {len(tables)} tables: " + "; ".join(lines))[
                :1200
            ],
            is_documentation=is_documentation_name(src.original_file),
            warnings=["unique counts not computed for SQLite tables"],
        )

    def passages(self, src: SourceInfo) -> list[Passage]:
        pb = PassageBuilder(src.source_id)
        with closing(connect_ro(src.path)) as con:
            for t in list_tables(con):
                names = [r[1] for r in con.execute(f"PRAGMA table_info({_q(t)})").fetchall()]
                pb.add(f"table {t} columns: {', '.join(names)}", "header", "sql", f"table {t}")
        return pb.items

    def load_table(self, src: SourceInfo, table: str | None = None) -> pd.DataFrame:
        with closing(connect_ro(src.path)) as con:
            tables = list_tables(con)
            table = table or tables[0]
            if table not in tables:
                raise KeyError(f"no table {table!r}; tables: {tables}")
            try:
                return pd.read_sql_query(f"SELECT rowid AS rowid, * FROM {_q(table)}", con)
            except pd.errors.DatabaseError:
                return pd.read_sql_query(f"SELECT * FROM {_q(table)}", con)


register(SqliteReader())
