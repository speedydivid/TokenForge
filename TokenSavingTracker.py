
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any


DEFAULT_DB = Path.home() / ".tokenforge" / "memory.db"


def estimate_tokens(text: str) -> int:
    """Return a rough token estimate, not a model-specific count."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def compress_output(
    text: str,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """
    Reduce oversized tool output while preserving the beginning and end.
    Repeated adjacent lines are collapsed before truncation.
    """
    if max_chars < 1:
        raise ValueError("max_chars must be positive")

    original = text
    lines = text.splitlines()
    compacted: list[str] = []

    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j] == lines[i]:
            j += 1

        count = j - i
        compacted.extend(lines[i:i + min(count, 2)])

        if count > 2:
            compacted.append(
                f"... [{count - 2} repeated lines omitted]"
            )

        i = j

    result = "\n".join(compacted)

    if len(result) > max_chars:
        marker = "\n... [TokenForge omitted middle] ...\n"

        if max_chars <= len(marker):
            result = result[:max_chars]
        else:
            remaining = max_chars - len(marker)
            head = (remaining * 2) // 3
            tail = remaining - head

            result = (
                result[:head]
                + marker
                + (result[-tail:] if tail else "")
            )

    before = estimate_tokens(original)
    after = estimate_tokens(result)

    return {
        "text": result,
        "original_chars": len(original),
        "optimized_chars": len(result),
        "estimated_tokens_before": before,
        "estimated_tokens_after": after,
        "estimated_tokens_saved": max(0, before - after),
        "reduction_percent": round(
            max(0, 1 - len(result) / max(1, len(original))) * 100,
            2,
        ),
    }


class MemoryStore:
    """Persistent local memory backed by SQLite and FTS5."""

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.fts_available = False
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                )
            """)

            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_session
                ON memories(session_id)
            """)

            try:
                db.execute("""
                    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                    USING fts5(
                        content,
                        session_id UNINDEXED
                    )
                """)

                db.execute("""
                    CREATE TRIGGER IF NOT EXISTS memory_insert
                    AFTER INSERT ON memories
                    BEGIN
                        INSERT INTO memories_fts(rowid, content, session_id)
                        VALUES (new.id, new.content, new.session_id);
                    END
                """)

                # Synchronize FTS with existing records.
                db.execute("""
                    INSERT INTO memories_fts(rowid, content, session_id)
                    SELECT m.id, m.content, m.session_id
                    FROM memories m
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM memories_fts f
                        WHERE f.rowid = m.id
                    )
                """)

                self.fts_available = True

            except sqlite3.OperationalError:
                self.fts_available = False

    def add(
        self,
        content: str,
        session_id: str = "default",
    ) -> int:
        """Save a memory and return its record ID."""
        content = content.strip()

        if not content:
            raise ValueError("Memory cannot be empty")

        digest = hashlib.sha256(
            f"{session_id}\0{content}".encode("utf-8")
        ).hexdigest()

        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO memories
                (session_id, content, content_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, content, digest, time.time()),
            )

            row = db.execute(
                "SELECT id FROM memories WHERE content_hash = ?",
                (digest,),
            ).fetchone()

            return int(row["id"])

    def search(
        self,
        query: str,
        session_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search memory with FTS5 or a LIKE-based fallback."""
        query = query.strip()

        if not query:
            return []

        limit = max(1, min(limit, 100))

        with self._connect() as db:
            if self.fts_available:
                words = re.findall(r"[\w-]+", query)

                if words:
                    expression = " AND ".join(
                        '"' + word.replace('"', '""') + '"'
                        for word in words
                    )

                    sql = """
                        SELECT m.id, m.session_id, m.content, m.created_at
                        FROM memories_fts f
                        JOIN memories m ON m.id = f.rowid
                        WHERE memories_fts MATCH ?
                    """
                    params: list[Any] = [expression]

                    if session_id is not None:
                        sql += " AND m.session_id = ?"
                        params.append(session_id)

                    sql += " ORDER BY m.created_at DESC LIMIT ?"
                    params.append(limit)

                    try:
                        return [
                            dict(row)
                            for row in db.execute(sql, params).fetchall()
                        ]
                    except sqlite3.OperationalError:
                        pass

            sql = """
                SELECT id, session_id, content, created_at
                FROM memories
                WHERE content LIKE ?
            """
            params = [f"%{query}%"]

            if session_id is not None:
                sql += " AND session_id = ?"
                params.append(session_id)

            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            return [
                dict(row)
                for row in db.execute(sql, params).fetchall()
            ]

    def recent(
        self,
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return the most recently stored memories."""
        limit = max(1, min(limit, 100))

        sql = """
            SELECT id, session_id, content, created_at
            FROM memories
        """
        params: list[Any] = []

        if session_id is not None:
            sql += " WHERE session_id = ?"
            params.append(session_id)

        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._connect() as db:
            return [
                dict(row)
                for row in db.execute(sql, params).fetchall()
            ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="TokenForge context optimization engine"
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    optimize = commands.add_parser(
        "optimize",
        help="Compress a text file",
    )
    optimize.add_argument("file")
    optimize.add_argument("--max-chars", type=int, default=12000)

    add = commands.add_parser(
        "add",
        help="Save a memory",
    )
    add.add_argument("text")
    add.add_argument("--session", default="default")
    add.add_argument("--db", default=str(DEFAULT_DB))

    search = commands.add_parser(
        "search",
        help="Search stored memories",
    )
    search.add_argument("query")
    search.add_argument("--session")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--db", default=str(DEFAULT_DB))

    args = parser.parse_args()

    if args.command == "optimize":
        text = Path(args.file).read_text(encoding="utf-8")
        result = compress_output(text, args.max_chars)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "add":
        store = MemoryStore(args.db)
        memory_id = store.add(args.text, args.session)
        print(f"Memory saved: {memory_id}")

    elif args.command == "search":
        store = MemoryStore(args.db)
        results = store.search(
            args.query,
            session_id=args.session,
            limit=args.limit,
        )
        print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
