"""SQLite-backed job queue for imports.

The reason this module exists: an in-flight import (a multi-GB
download, a half-finished chunked upload) must survive a Hub process
restart rather than being silently stranded. State therefore lives in a
sqlite3 file, not in memory -- a fresh `JobQueue` instance opened
against the same file sees exactly the same jobs and states.

The connection is opened in autocommit mode (`isolation_level=None`) so
`claim_next` can issue its own explicit `BEGIN IMMEDIATE` / `COMMIT` /
`ROLLBACK` without Python's sqlite3 module silently opening an implicit
transaction underneath it. That explicit transaction is what makes
`claim_next` safe when two callers (e.g. two Hub processes, or a crashed
one restarting) race against the same db file: `BEGIN IMMEDIATE`
acquires the write lock before the SELECT that picks the job, so a
second caller's `BEGIN IMMEDIATE` blocks until the first has committed
its claim and moved on.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class JobState(str, Enum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING = "UPLOADING"
    DONE = "DONE"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


# States that mean "a process was actively working on this job" -- if the
# Hub restarts while a job sits in one of these, nothing is going to finish
# the work, so reset_stale() gives them back to the pending pool.
_STALE_STATES = (JobState.DOWNLOADING, JobState.UPLOADING)


@dataclass
class Job:
    id: int
    plugin: str
    source_id: str
    title: str
    platform: str
    state: JobState
    error: str | None = None
    local_path: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plugin TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    platform TEXT NOT NULL,
    state TEXT NOT NULL,
    error TEXT,
    local_path TEXT
)
"""


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        plugin=row["plugin"],
        source_id=row["source_id"],
        title=row["title"],
        platform=row["platform"],
        state=JobState(row["state"]),
        error=row["error"],
        local_path=row["local_path"],
    )


class JobQueue:
    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        # The schema must be creatable on first use against a path whose
        # parent directories don't exist yet -- sqlite3.connect() will
        # happily create the file itself, but not any missing directories.
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "JobQueue":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def enqueue(self, plugin: str, source_id: str, title: str, platform: str) -> Job:
        cur = self._conn.execute(
            "INSERT INTO jobs (plugin, source_id, title, platform, state, error, local_path) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL)",
            (plugin, source_id, title, platform, JobState.PENDING.value),
        )
        return self.get(cur.lastrowid)

    def claim_next(self) -> Job | None:
        """Atomically pick the oldest PENDING job and mark it DOWNLOADING
        (the first real work an import does). See the module docstring
        for why this needs its own explicit transaction."""
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            row = cur.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY id LIMIT 1",
                (JobState.PENDING.value,),
            ).fetchone()
            if row is None:
                cur.execute("COMMIT")
                return None
            cur.execute(
                "UPDATE jobs SET state = ? WHERE id = ?",
                (JobState.DOWNLOADING.value, row["id"]),
            )
            cur.execute("COMMIT")
        except BaseException:
            cur.execute("ROLLBACK")
            raise
        return self.get(row["id"])

    def set_state(
        self,
        job_id: int,
        state: JobState,
        error: str | None = None,
        local_path: str | None = None,
    ) -> None:
        """Update a job's state. `error`/`local_path` are only overwritten
        when a caller actually passes a value -- omitting them preserves
        whatever was recorded before (e.g. the local_path saved when a
        download finished must survive the later transition to
        UPLOADING, which has no reason to pass it again)."""
        state_value = state.value if isinstance(state, JobState) else JobState(state).value
        if error is not None:
            self._conn.execute(
                "UPDATE jobs SET state = ?, error = ? WHERE id = ?",
                (state_value, error, job_id),
            )
        else:
            self._conn.execute(
                "UPDATE jobs SET state = ? WHERE id = ?",
                (state_value, job_id),
            )
        if local_path is not None:
            self._conn.execute(
                "UPDATE jobs SET local_path = ? WHERE id = ?",
                (local_path, job_id),
            )

    def set_platform(self, job_id: int, platform: str) -> None:
        """Record the platform an import actually resolved.

        A job is enqueued before the plugin has been asked what to fetch,
        so at that point the only platform available is whatever the
        search result carried -- often nothing. The plugin's FetchPlan is
        the authoritative answer, and it arrives afterwards. Without this
        the persisted row would keep the guess, which matters precisely
        because these rows outlive the process that made them.
        """
        self._conn.execute(
            "UPDATE jobs SET platform = ? WHERE id = ?", (platform, job_id)
        )

    def get(self, job_id: int) -> Job | None:
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_job(row)

    def list(self, state: JobState | None = None) -> list[Job]:
        if state is None:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY id").fetchall()
        else:
            state_value = state.value if isinstance(state, JobState) else JobState(state).value
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE state = ? ORDER BY id", (state_value,)
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def reset_stale(self) -> list[Job]:
        """Return DOWNLOADING/UPLOADING jobs to PENDING. Those states mean
        a process was actively working on the job -- if the Hub restarted,
        nothing is coming back to finish it, so it goes back in the pool
        rather than staying stranded forever."""
        placeholders = ",".join("?" for _ in _STALE_STATES)
        stale_values = [s.value for s in _STALE_STATES]
        rows = self._conn.execute(
            f"SELECT id FROM jobs WHERE state IN ({placeholders})", stale_values
        ).fetchall()
        ids = [row["id"] for row in rows]
        if ids:
            id_placeholders = ",".join("?" for _ in ids)
            self._conn.execute(
                f"UPDATE jobs SET state = ? WHERE id IN ({id_placeholders})",
                [JobState.PENDING.value, *ids],
            )
        return [self.get(i) for i in ids]
