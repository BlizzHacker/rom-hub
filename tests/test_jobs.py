"""SQLite-backed job queue.

The reason this exists: an in-flight import (a multi-GB download, a
half-finished chunked upload) must survive a Hub process restart rather
than being silently stranded. State therefore has to live in a file, not
in memory -- every test here works against a real sqlite3 file on disk,
never an in-memory/mocked store, because the whole point is durability
across a fresh `JobQueue` instance.
"""

import threading
from pathlib import Path

from rom_hub.jobs import Job, JobQueue, JobState


def test_enqueue_then_claim_next_returns_it_and_marks_it_non_pending(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite3")
    job = q.enqueue(plugin="archive-org", source_id="abc", title="Game", platform="dos")
    assert job.state == JobState.PENDING

    claimed = q.claim_next()

    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.state != JobState.PENDING
    # The queue's own record must reflect the claim, not just the return value.
    assert q.get(job.id).state != JobState.PENDING


def test_claim_next_on_an_empty_queue_returns_none(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite3")
    assert q.claim_next() is None


def test_state_survives_a_new_jobqueue_instance_on_the_same_file(tmp_path):
    """This is the requirement the whole task exists for: a Hub restart
    creates a brand new JobQueue object, but it must see the exact same
    jobs and states as before the restart."""
    db_path = tmp_path / "jobs.sqlite3"
    q1 = JobQueue(db_path)
    job = q1.enqueue(plugin="archive-org", source_id="abc", title="Game", platform="dos")
    q1.claim_next()
    q1.set_state(job.id, JobState.UPLOADING, local_path="var/downloads/1/g.zip")

    # Simulate a process restart: a fresh JobQueue instance, same file.
    q2 = JobQueue(db_path)
    reloaded = q2.get(job.id)

    assert reloaded is not None
    assert reloaded.state == JobState.UPLOADING
    assert reloaded.local_path == "var/downloads/1/g.zip"
    assert reloaded.plugin == "archive-org"
    assert reloaded.source_id == "abc"
    assert reloaded.title == "Game"
    assert reloaded.platform == "dos"


def test_reset_stale_moves_downloading_and_uploading_jobs_back_to_pending(tmp_path):
    """A restart mid-import must not strand a job in DOWNLOADING/UPLOADING
    forever -- those states mean 'a process was working on this and
    died', so reset_stale() gives them back to the pending pool."""
    q = JobQueue(tmp_path / "jobs.sqlite3")
    downloading = q.enqueue("p", "1", "t1", "dos")
    uploading = q.enqueue("p", "2", "t2", "dos")
    done = q.enqueue("p", "3", "t3", "dos")
    failed = q.enqueue("p", "4", "t4", "dos")
    still_pending = q.enqueue("p", "5", "t5", "dos")

    q.set_state(downloading.id, JobState.DOWNLOADING)
    q.set_state(uploading.id, JobState.UPLOADING)
    q.set_state(done.id, JobState.DONE)
    q.set_state(failed.id, JobState.FAILED, error="boom")

    reset = q.reset_stale()

    reset_ids = {j.id for j in reset}
    assert reset_ids == {downloading.id, uploading.id}
    assert all(j.state == JobState.PENDING for j in reset)

    assert q.get(downloading.id).state == JobState.PENDING
    assert q.get(uploading.id).state == JobState.PENDING
    # States that don't mean "a process was working on this" are untouched.
    assert q.get(done.id).state == JobState.DONE
    assert q.get(failed.id).state == JobState.FAILED
    assert q.get(still_pending.id).state == JobState.PENDING


def test_set_state_failed_persists_the_error_text(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite3")
    job = q.enqueue("p", "1", "t", "dos")

    q.set_state(job.id, JobState.FAILED, error="platform slug 'dos' has no match")

    reloaded = q.get(job.id)
    assert reloaded.state == JobState.FAILED
    assert reloaded.error == "platform slug 'dos' has no match"


def test_set_platform_overwrites_the_platform_the_job_was_enqueued_with(tmp_path):
    """A job is enqueued before the plugin has said what platform it is
    for, so the row starts with whatever the search result carried."""
    q = JobQueue(tmp_path / "jobs.sqlite3")
    job = q.enqueue("p", "1", "t", "")

    q.set_platform(job.id, "dos")

    assert q.get(job.id).platform == "dos"
    with JobQueue(tmp_path / "jobs.sqlite3") as reopened:
        assert reopened.get(job.id).platform == "dos"


def test_schema_is_created_on_first_use_against_a_path_that_does_not_exist_yet(tmp_path):
    db_path = tmp_path / "does" / "not" / "exist" / "jobs.sqlite3"
    assert not db_path.exists()

    q = JobQueue(db_path)
    job = q.enqueue("p", "1", "t", "dos")

    assert db_path.exists()
    assert q.get(job.id) is not None


def test_get_of_unknown_job_returns_none(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite3")
    assert q.get(999) is None


def test_list_returns_all_jobs_and_can_filter_by_state(tmp_path):
    q = JobQueue(tmp_path / "jobs.sqlite3")
    a = q.enqueue("p", "1", "t1", "dos")
    b = q.enqueue("p", "2", "t2", "dos")
    q.set_state(b.id, JobState.FAILED, error="x")

    all_jobs = q.list()
    assert {j.id for j in all_jobs} == {a.id, b.id}

    failed_only = q.list(state=JobState.FAILED)
    assert [j.id for j in failed_only] == [b.id]

    pending_only = q.list(state=JobState.PENDING)
    assert [j.id for j in pending_only] == [a.id]


def test_local_path_and_error_survive_a_later_set_state_that_omits_them(tmp_path):
    """Task 7's pipeline records local_path once a download finishes, then
    later transitions the same job to UPLOADING/DONE without re-passing
    it. If set_state clobbered it back to None on every call, the whole
    point of persisting progress would be lost."""
    q = JobQueue(tmp_path / "jobs.sqlite3")
    job = q.enqueue("p", "1", "t", "dos")

    q.set_state(job.id, JobState.DOWNLOADING)
    q.set_state(job.id, JobState.UPLOADING, local_path="var/downloads/1/g.zip")
    q.set_state(job.id, JobState.DONE)

    reloaded = q.get(job.id)
    assert reloaded.state == JobState.DONE
    assert reloaded.local_path == "var/downloads/1/g.zip"


def test_claim_next_is_race_safe_across_two_queue_instances(tmp_path):
    """claim_next must use a transaction so two callers racing against the
    same db file cannot both claim the same job."""
    db_path = tmp_path / "jobs.sqlite3"
    seed = JobQueue(db_path)
    job = seed.enqueue("p", "1", "t", "dos")
    seed.close()

    results: list[Job | None] = []
    lock = threading.Lock()

    def worker():
        q = JobQueue(db_path)
        try:
            claimed = q.claim_next()
        finally:
            q.close()
        with lock:
            results.append(claimed)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claimed_jobs = [r for r in results if r is not None]
    assert len(claimed_jobs) == 1
    assert claimed_jobs[0].id == job.id


def test_notes_survive_a_reopen_and_are_not_the_error_column(tmp_path):
    """A skipped optional step is recorded on the row it happened to, and
    it is not an error: a DONE import filed under `error` would read as a
    broken one in `rom-hub jobs`."""
    db_path = tmp_path / "jobs.sqlite3"
    q = JobQueue(db_path)
    job = q.enqueue("archive-org", "abc", "Game", "dos")
    q.set_notes(job.id, "grouping was skipped: no collections")
    q.set_state(job.id, JobState.DONE)
    q.close()

    reopened = JobQueue(db_path)
    try:
        reloaded = reopened.get(job.id)
    finally:
        reopened.close()
    assert reloaded.state == JobState.DONE
    assert reloaded.notes == "grouping was skipped: no collections"
    assert reloaded.error is None


def test_a_db_written_before_notes_existed_is_migrated_not_broken(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` does nothing to a table that already
    exists, so every read of a new column against an existing jobs.db
    would raise. The db is long-lived by design -- that is the whole
    reason this is sqlite and not a dict."""
    import sqlite3

    db_path = tmp_path / "legacy.sqlite3"
    legacy = sqlite3.connect(str(db_path))
    legacy.execute(
        "CREATE TABLE jobs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, plugin TEXT NOT NULL, "
        "source_id TEXT NOT NULL, title TEXT NOT NULL, platform TEXT NOT NULL, "
        "state TEXT NOT NULL, error TEXT, local_path TEXT)"
    )
    legacy.execute(
        "INSERT INTO jobs (plugin, source_id, title, platform, state) "
        "VALUES ('archive-org', 'abc', 'Game', 'dos', 'DONE')"
    )
    legacy.commit()
    legacy.close()

    q = JobQueue(db_path)
    try:
        job = q.list()[0]
        assert job.notes is None
        q.set_notes(job.id, "grouping was skipped")
        assert q.get(job.id).notes == "grouping was skipped"
    finally:
        q.close()
