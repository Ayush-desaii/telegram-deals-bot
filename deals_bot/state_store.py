"""Durable SQLite snapshots on a separate Git branch, with non-force CAS pushes.

Uses Git object plumbing so the code checkout/index are never changed. Authentication
comes from checkout's credential helper; credentials are never placed in state.
"""
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile

import config
from database import Database


class GitStateStore:
    def __init__(self, repo, db, branch=config.STATE_BRANCH):
        self.repo = Path(repo).resolve()
        self.db = db
        self.ref = f"refs/heads/{branch}"
        self.tip = None

    def git(self, *args, input=None, env=None):
        result = subprocess.run(
            ["git", "-c", f"safe.directory={self.repo.as_posix()}",
             "-c", "user.name=deals-state-bot", "-c", "user.email=deals-state@users.noreply.github.com",
             *args], cwd=self.repo, input=input, capture_output=True, env=env, timeout=90,
        )
        if result.returncode:
            raise RuntimeError(f"State Git operation failed ({args[0]}); posting stopped")
        return result.stdout

    def restore(self, legacy_paths=()):
        result = self.git("ls-remote", "--heads", "origin", self.ref).decode().strip()
        if result:
            self.git("fetch", "--no-tags", "origin", self.ref)
            self.tip = self.git("rev-parse", "FETCH_HEAD").decode().strip()
            data = self.git("show", f"{self.tip}:deals_bot.db")
            self.db.path.parent.mkdir(parents=True, exist_ok=True)
            self.db.path.write_bytes(data)
            with self.db.connection() as conn:
                if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("Invalid state database")
        self.db.initialize()
        if not result:
            self.db.import_legacy(legacy_paths)

    def save(self):
        with tempfile.TemporaryDirectory(prefix="deals-state-") as directory:
            snapshot = Path(directory) / "deals_bot.db"
            target = sqlite3.connect(snapshot)
            try:
                with self.db.connection() as source:
                    source.backup(target)
            finally:
                target.close()
            blob = self.git("hash-object", "-w", "--stdin", input=snapshot.read_bytes()).decode().strip()
            if self.tip and blob == self.git("rev-parse", f"{self.tip}:deals_bot.db").decode().strip():
                return
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
            self.git("read-tree", "--empty", env=env)
            self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},deals_bot.db", env=env)
            tree = self.git("write-tree", env=env).decode().strip()
            parent = ["-p", self.tip] if self.tip else []
            commit = self.git("commit-tree", tree, *parent, input=b"Persist deals bot state\n").decode().strip()
            # Normal push refuses to overwrite a concurrent writer's newer state.
            self.git("push", "origin", f"{commit}:{self.ref}")
            self.tip = commit


def preview_database(destination, source):
    """Copy local state using SQLite backup; never migrate/write the source."""
    db = Database(destination)
    source = Path(source)
    if source.exists():
        conn = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        target = sqlite3.connect(destination)
        try:
            conn.backup(target)
        finally:
            target.close()
            conn.close()
    db.initialize()
    return db
