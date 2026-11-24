import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.business import DomainIssue

SCHEMA = """CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, report TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', revision INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', reviewed TEXT
);"""

VERSIONS = """CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, seq INTEGER NOT NULL, created TEXT NOT NULL,
    cutoff INTEGER NOT NULL, source_sha TEXT NOT NULL, run_id TEXT NOT NULL,
    previous TEXT, note TEXT NOT NULL, payload TEXT NOT NULL
);"""

VERSION_SEQUENCE = "CREATE UNIQUE INDEX IF NOT EXISTS versions_seq ON versions(seq);"


class Store:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute(SCHEMA)
            db.execute(VERSIONS)
            db.execute(VERSION_SEQUENCE)
            yield db

    def register(self, run_id, report):
        with self.connect() as db:
            db.execute(
                "INSERT INTO runs(id,report,created) VALUES (?,?,?) ON CONFLICT(id) DO NOTHING",
                (
                    run_id,
                    json.dumps(report, ensure_ascii=False),
                    report["manifest"]["created"],
                ),
            )
        return self.load(run_id)

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise DomainIssue("RUN_NOT_FOUND")
        result = dict(row)
        result["report"] = json.loads(result["report"])
        return result

    def list(self):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT id,created,status,revision,note FROM runs ORDER BY created DESC,id LIMIT 50"
                )
            ]

    def revise(self, run_id, revision, status, note, now):
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE runs SET status=?,note=?,reviewed=?,revision=revision+1 WHERE id=? AND revision=?",
                (status, note, now, run_id, revision),
            )
            if cursor.rowcount != 1:
                raise DomainIssue("STALE_REVIEW")
        return self.load(run_id)

    def save_version(self, record):
        """只插入一次；同一来源同一截止点重放回读既有签版。"""
        for _ in range(8):
            with self.connect() as db:
                existing = db.execute(
                    "SELECT id FROM versions WHERE id=?", (record["id"],)
                ).fetchone()
                if existing is not None:
                    break
                db.execute("BEGIN IMMEDIATE")
                latest = db.execute(
                    "SELECT id,payload FROM versions ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                parent = latest["id"] if latest else None
                payload = record["payload"]
                if parent != record["previous"]:
                    payload = self._rechain(payload, parent, latest)
                seq = db.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 AS value FROM versions"
                ).fetchone()["value"]
                try:
                    db.execute(
                        "INSERT INTO versions(id,seq,created,cutoff,source_sha,run_id,previous,note,payload)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            record["id"],
                            seq,
                            record["created"],
                            record["cutoff"],
                            record["source_sha"],
                            record["run_id"] or "",
                            parent,
                            record["note"],
                            json.dumps(payload, ensure_ascii=False),
                        ),
                    )
                except sqlite3.IntegrityError:
                    continue
            return self.version(record["id"])
        return self.version(record["id"])

    @staticmethod
    def _rechain(payload, parent, latest):
        """并发签发时以实际上一签版为准重算差异。"""
        from app.modules.ledger import diff

        previous = json.loads(latest["payload"])["book"] if latest else None
        payload = dict(payload)
        payload["manifest"] = dict(payload["manifest"], previous=parent)
        payload["diff"] = diff(previous, payload["book"])
        return payload

    def version(self, version_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM versions WHERE id=?", (version_id,)
            ).fetchone()
            if row is None:
                raise DomainIssue("VERSION_NOT_FOUND")
            previous = db.execute(
                "SELECT id,seq FROM versions WHERE seq<? ORDER BY seq DESC LIMIT 1",
                (row["seq"],),
            ).fetchone()
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["previous"] = previous["id"] if previous else None
        result["previous_seq"] = previous["seq"] if previous else None
        return result

    def previous_version(self):
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM versions ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        return self.version(row["id"]) if row else None

    def versions(self):
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT v.id,v.seq,v.created,v.cutoff,v.source_sha,v.run_id,v.note,"
                    "(SELECT id FROM versions p WHERE p.seq<v.seq ORDER BY p.seq DESC LIMIT 1) AS previous "
                    "FROM versions v ORDER BY v.seq DESC"
                )
            ]
