"""Trigger RomM's library scan, because an upload alone does not register a ROM.

This module exists to close a gap in RomM's REST API, not to add a
feature. `POST /api/roms/upload/{id}/complete` assembles the chunks,
renames the file into the library, logs, and returns:

    log.info(f"Chunked upload complete: {file_location}")
    return Response(status_code=status.HTTP_201_CREATED)

No database row. The file is on disk and the library does not know it
exists. Verified against RomM 4.9.2: after a successful chunked upload,
`GET /api/roms?platform_ids=1` still answers `total=0`.

**There is no REST path to fix this.** `POST /api/tasks/run/scan_library`
and `sync_folder_scan` both answer 400 `"cannot be run"` -- only cleanup
tasks are runnable that way. RomM's own web UI does not use REST either;
after every upload it emits a **socket.io** event, which is what this
module replicates:

    u.setScanning(!0), A.connected||A.connect(),
    setTimeout(()=>{ A.emit(`scan`,{platforms:[e],type:`quick`,apis:...}) }, 2e3)

Upstream contract (read from RomM 4.9.2 at
`backend/endpoints/sockets/scan.py`, `backend/handler/socket_handler.py`):

* the server mounts socket.io at `/ws/socket.io`, not the `/socket.io`
  default a socket.io client would otherwise assume;
* `scan_handler(sid, options)` reads `platforms` (integer platform ids),
  `platform_fs_slugs`, `type` (a `ScanType` name, default `"quick"`),
  `roms_ids`, `apis` (metadata sources), `launchbox_remote_enabled` and
  `playmatch_enabled`;
* completion is broadcast as `scan:done` carrying `ScanStats`, failure as
  `scan:done_ko` carrying a message. Both are emitted by the RQ worker
  through the shared Redis client manager, so they arrive as broadcasts
  rather than as a reply to this client specifically -- which is why the
  handlers are subscriptions, not an ack callback.

**The scan is scoped as narrowly as the protocol allows**: the single
platform just uploaded to, `type="quick"`, and an empty `apis` list. A
full scan on a real library is hours of work and an unrequested metadata
sweep against IGDB; neither is an acceptable side effect of importing one
ROM.

**Waiting is not optional.** The point of the scan is that the caller can
then confirm the ROM landed. Emitting and returning immediately would
hand the importer a library that has not been rebuilt yet, and it would
report a ROM missing that was merely late. So `scan_platform` blocks on
`scan:done` and raises on anything else, including its own timeout.

A note on credentials: RomM 4.9.2's `scan_handler` has no authorization
gate -- there is no `reject_unauthorized_scan` in this version, and the
socket server accepts any connection. Both the bearer header and the
connect-time `auth` payload are sent anyway, because versions that *do*
gate it check one or the other, and sending credentials to the same
server the REST client is already authenticated against costs nothing.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Protocol

# RomM mounts its socket.io ASGI app here (SocketHandler(path="/ws/socket.io")).
# A client left on the "/socket.io" default 404s forever.
SOCKETIO_PATH = "/ws/socket.io"

SCAN_EVENT = "scan"
SCAN_DONE_EVENT = "scan:done"
SCAN_DONE_KO_EVENT = "scan:done_ko"

# A quick scan of one platform holding one new file is near-instant, but it
# is queued behind whatever else RomM's high-priority worker is doing, and
# a first scan of a platform still has to stat every file already in it.
DEFAULT_SCAN_TIMEOUT = 600.0

# Long enough to cover a cold RomM still booting its workers; short enough
# that an unreachable host fails the job instead of stalling the queue.
DEFAULT_CONNECT_TIMEOUT = 30.0


class ScanError(Exception):
    """The library scan could not be triggered, or did not finish cleanly."""


class Scanner(Protocol):
    """What `romm_hub.importer` needs from a scanner. Kept narrow so a test
    can satisfy it without a socket."""

    def scan_platform(self, platform_id: int) -> Any: ...


def _default_client_factory():
    # Imported lazily so that `import romm_hub.importer` does not drag in
    # socket.io for the many code paths that never scan -- and so the
    # dependency's absence is reported as a ScanError naming the package
    # rather than an ImportError from an unrelated module.
    try:
        import socketio
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ScanError(
            "the python-socketio package is required to register an upload "
            "with RomM's library (RomM has no REST endpoint for it); install "
            f"romm-hub's dependencies: {exc}"
        ) from exc
    return socketio.Client()


class SocketIOScanner:
    """Triggers a scan over socket.io and blocks until RomM says it finished."""

    def __init__(
        self,
        romm,
        *,
        timeout: float = DEFAULT_SCAN_TIMEOUT,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        client_factory: Callable[[], Any] = _default_client_factory,
    ):
        self._romm = romm
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._client_factory = client_factory

    def scan_platform(self, platform_id: int) -> Any:
        """Emit a quick scan for `platform_id` and wait for `scan:done`.

        Returns RomM's `ScanStats` payload. Raises `ScanError` if the
        socket cannot be opened, if RomM answers `scan:done_ko` (which is
        also how it reports "A scan is already in progress"), or if
        nothing arrives inside the timeout.
        """
        token = self._romm.bearer_token()
        base_url = str(self._romm.base_url).rstrip("/")

        client = self._client_factory()
        finished = threading.Event()
        # Written by a socket.io background thread, read by this one after
        # `finished` is set. The Event's set/wait pair is the barrier that
        # publishes them safely.
        outcome: dict[str, Any] = {}

        def _on_done(*args):
            outcome["stats"] = args[0] if args else {}
            finished.set()

        def _on_done_ko(*args):
            outcome["error"] = args[0] if args else "(no reason given)"
            finished.set()

        # Subscribed BEFORE connecting or emitting. A quick scan of a small
        # platform can complete before `emit()` has even returned, and a
        # handler registered afterwards would miss the event entirely --
        # the scan would succeed and this would still time out.
        client.on(SCAN_DONE_EVENT, _on_done)
        client.on(SCAN_DONE_KO_EVENT, _on_done_ko)

        try:
            try:
                client.connect(
                    base_url,
                    socketio_path=SOCKETIO_PATH,
                    headers={"Authorization": f"Bearer {token}"},
                    auth={"token": token},
                    wait_timeout=self._connect_timeout,
                )
            except ScanError:
                raise
            except Exception as exc:
                raise ScanError(
                    f"could not open a socket.io connection to RomM at "
                    f"{base_url}{SOCKETIO_PATH} to register the upload: {exc}"
                ) from exc

            try:
                client.emit(SCAN_EVENT, self._options(platform_id))
            except Exception as exc:
                raise ScanError(
                    f"could not ask RomM to scan platform {platform_id}: {exc}"
                ) from exc

            if not finished.wait(self._timeout):
                raise ScanError(
                    f"timed out after {self._timeout:g}s waiting for RomM to "
                    f"report the library scan of platform {platform_id} "
                    f"finished (expected a {SCAN_DONE_EVENT!r} event)"
                )

            if "error" in outcome:
                raise ScanError(
                    f"RomM refused or failed the library scan of platform "
                    f"{platform_id}: {outcome['error']}"
                )
            return outcome.get("stats", {})
        finally:
            # A leaked connection holds a socket.io session open on the
            # server for its full ping timeout. Its own failure must never
            # replace the real result -- including the exception on the way
            # out of the try block above.
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _options(platform_id: int) -> dict[str, Any]:
        """The `options` dict `scan_handler` reads, with every key it looks
        at set explicitly rather than left to a server-side default."""
        return {
            "platforms": [platform_id],
            "platform_fs_slugs": [],
            "type": "quick",
            "roms_ids": [],
            # Empty on purpose: registering the file is the whole job here.
            # A populated list turns every import into an IGDB/MobyGames
            # sweep the user did not ask for.
            "apis": [],
            "launchbox_remote_enabled": False,
            "playmatch_enabled": False,
        }
