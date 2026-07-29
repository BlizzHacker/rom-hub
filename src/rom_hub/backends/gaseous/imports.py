"""Wait for Gaseous to actually import what was just uploaded.

This is Gaseous' equivalent of the socket.io scan RomM needs, and it is
required for the same reason: `POST /Roms` does not put a ROM in the
library. It writes the file into
`Data/Upload/<session-guid>/<filename>`, records an `ImportStateItem` in
the `Pending` state, and returns the session guid. Nothing is in
`Games_Roms` yet and `GET /Games/{id}/roms` cannot see it.

The registration is done later by the `ImportQueueProcessor` background
task, which hashes the file, looks up its signature, files it into the
library and only then writes the database row. Measured on
v2.0.0-rc.3: the state went `Pending` -> `Processing` -> `Completed`
about a minute after the upload returned 200.

So a caller that uploaded and immediately listed would find nothing and
correctly conclude the ROM had not landed -- which is why `run_import`
calls `scan_platform` between the two, and why this module blocks rather
than firing and forgetting.

**The background queue only runs once first-run setup is finished.** On a
fresh server every task sits at `NeverStarted` until
`POST /FirstSetup/1` has set the datasources, and an upload made before
that point stays `Pending` forever. That is a server misconfiguration
rather than an import failure, and `wait_for` says so by name when it
times out, because "the import did not complete" sends an operator
looking at the ROM instead of at the setup wizard.

The polling interval is a compromise measured against the real thing:
`ImportQueueProcessor` has a one-minute interval, so a tight loop only
burns requests, and a long one adds latency to a job the operator is
watching.
"""

from __future__ import annotations

import time
from typing import Callable, Iterable

from .client import GaseousClient, GaseousError

#: States that mean the queue has not finished with this session yet.
PENDING_STATES = frozenset({"Pending", "Queued", "Processing"})

#: The one terminal state `ImportStateItem.ImportState` actually defines.
#: The controller's own doc-comment advertises "Skipped" and "Failed" too,
#: but the enum in `gaseous-lib/Models/ImportState.cs` carries neither, so
#: anything not pending is treated as finished rather than matched against
#: a list of successes that would silently grow stale.
COMPLETED_STATE = "Completed"

DEFAULT_POLL_INTERVAL = 5.0

# Generous on purpose. The queue's own interval is 60s, the file is
# hashed and signature-matched inside the task, and a first import on a
# cold server is the slow case. A timeout here fails a job whose bytes
# already reached the server, so it should mean "something is wrong", not
# "this was a big file".
DEFAULT_TIMEOUT = 900.0


class ImportWaiter:
    """Blocks until Gaseous has finished importing the tracked sessions."""

    def __init__(
        self,
        client: GaseousClient,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
        sleep: Callable[[float], None] | None = None,
        monotonic: Callable[[], float] | None = None,
    ):
        self._client = client
        self._poll_interval = poll_interval
        self._timeout = timeout
        # Injected so a test can drive the clock without sleeping. The
        # alternative -- a test that really waits -- is a test nobody runs.
        self._sleep = sleep or time.sleep
        self._monotonic = monotonic or time.monotonic

    def wait_for(self, session_ids: Iterable[str]) -> None:
        """Return once every session in `session_ids` has left the queue.

        A session that has vanished from the listing counts as finished:
        `ImportGame.RemoveOldImportStates` prunes the queue on a timer, so
        an absent session is one that completed a while ago, not one that
        was lost. Treating absence as still-pending would hang on exactly
        the imports that went fine.

        Raises `GaseousError` if a tracked session reports an
        `errorMessage`, or if the wait times out.
        """
        wanted = {str(session) for session in session_ids if session}
        if not wanted:
            return

        deadline = self._monotonic() + self._timeout
        last_states: dict[str, str] = {}

        while True:
            states = self._client.import_states()
            by_session = {
                str(item.get("sessionId")): item
                for item in states
                if item.get("sessionId")
            }

            outstanding = []
            for session in wanted:
                item = by_session.get(session)
                if item is None:
                    continue
                error = item.get("errorMessage")
                if error:
                    raise GaseousError(
                        f"Gaseous failed to import "
                        f"{item.get('fileName') or session!r}: {error}"
                    )
                state = str(item.get("state") or "")
                last_states[session] = state
                if state in PENDING_STATES:
                    outstanding.append(session)

            if not outstanding:
                return

            if self._monotonic() >= deadline:
                stuck = ", ".join(
                    f"{session} ({last_states.get(session, 'unknown')})"
                    for session in sorted(outstanding)
                )
                raise GaseousError(
                    f"Gaseous did not finish importing after "
                    f"{self._timeout:.0f}s: {stuck}. The bytes did reach the "
                    f"server -- do not upload them again. If every import "
                    f"sits at 'Pending', the ImportQueueProcessor background "
                    f"task is not running, which on a fresh server means "
                    f"first-run setup was never completed."
                )

            self._sleep(self._poll_interval)
