"""Operator-provisioned, physically isolated research workspaces."""

import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from .db import Database
from .identity import subject_id


class Registry:
    def __init__(self, root: Path, issuer: str):
        self.root = root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        self.path = root / "accounts.sqlite3"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (issuer TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users (
                    subject TEXT PRIMARY KEY, workspace TEXT UNIQUE NOT NULL,
                    label TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            row = conn.execute("SELECT issuer FROM metadata").fetchone()
            if row and row[0] != issuer:
                raise ValueError("This registry belongs to a different Access issuer")
            if not row:
                conn.execute("INSERT INTO metadata VALUES (?)", (issuer,))
        os.chmod(self.path, 0o600)

    def connect(self):
        # sqlite's connection context manager does not close the connection.
        from contextlib import contextmanager

        @contextmanager
        def connection():
            conn = sqlite3.connect(self.path, timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

        return connection()

    def provision(self, subject, label):
        subject_id(subject)
        if not label.strip() or len(label) > 100 or any(ord(c) < 32 for c in label):
            raise ValueError("Use a nonempty label of at most 100 printable characters")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] >= 50:
                raise ValueError("This single-instance deployment supports at most 50 accounts")
            if conn.execute("SELECT 1 FROM users WHERE subject=?", (subject,)).fetchone():
                raise ValueError("Account already provisioned")
            workspace = uuid4().hex
            folder = self.root / workspace
            folder.mkdir(mode=0o700)
            db = Database(folder / "research.sqlite3")
            db.initialize()
            os.chmod(db.path, 0o600)
            conn.execute(
                "INSERT INTO users(subject, workspace, label) VALUES (?,?,?)",
                (subject, workspace, label.strip()),
            )
        return self.get(subject)

    def get(self, subject):
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE subject=? AND enabled=1", (subject,)
            ).fetchone()
        return dict(row) if row else None

    def list(self):
        with self.connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY created_at")]

    def disable(self, subject):
        with self.connect() as conn:
            result = conn.execute("UPDATE users SET enabled=0 WHERE subject=?", (subject,))
            if not result.rowcount:
                raise ValueError("Unknown account")

    def database(self, user):
        # Only registry-generated workspace IDs are ever turned into paths.
        workspace = user["workspace"]
        if len(workspace) != 32 or any(c not in "0123456789abcdef" for c in workspace):
            raise ValueError("Invalid registry workspace")
        return Database(self.root / workspace / "research.sqlite3")
