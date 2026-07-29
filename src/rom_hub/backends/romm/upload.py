"""Chunked upload orchestration for RomM's /api/roms/upload/* endpoints.

RomM's server is told up front, via `x-upload-total-chunks`, exactly how
many PUT chunks to expect for an upload session. If the number of PUTs
sent does not match that header, the server waits forever for a chunk
that never arrives -- this is the failure mode most worth guarding
against, so `total_chunks` is always computed as `math.ceil(size /
chunk_size)` from the same size used to start the session, and the loop
below sends exactly that many chunks. An empty file would mean
`total_chunks == 0`, which is the same hang by a different name, so it is
rejected client-side before any request is made at all.

The server does NOT trust our chosen `chunk_size` for the byte length of
each chunk. Reading RomM's own source
(backend/endpoints/roms/upload.py::_expected_chunk_size), it derives its
own expected size from the two headers we already sent:

    chunk_size = ceil(total_size / total_chunks)
    expected(i) = chunk_size            if i < total_chunks - 1
                = total_size - chunk_size * (total_chunks - 1)   otherwise

and rejects any chunk whose length doesn't match. This only equals our
requested `chunk_size` when `total_size` divides evenly by it, so every
other upload must slice using the server's own formula, not the
originally-requested `chunk_size`.

Any failure -- a chunk PUT, or even the final /complete -- calls
POST /api/roms/upload/{id}/cancel before re-raising as RommError, so a
half-uploaded file does not linger server-side.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

from rom_hub.backends.romm.client import RommClient, RommError

DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024


def upload_file(
    client: RommClient,
    path: Path,
    platform_id: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Upload `path` to RomM via the three-step chunked upload API.

    `platform_id` must already be the integer id (from
    `client.platform_id(slug)`), never a slug. The file is streamed in
    `chunk_size` pieces -- never read into memory whole -- which is the
    entire point of chunking for multi-GB ROMs.

    Returns the body of the `/complete` response, which against a real
    RomM is `{}`: that endpoint answers a bare 201 with no body, so it
    carries **no rom id**. A caller that needs the new rom's id must look
    it up in the library by hash -- `rom_hub.importer` does exactly
    that, and uses the same lookup as a post-condition check, since a 201
    only proves the request was accepted, not that the ROM landed.

    Raises `RommError` (after calling `/cancel`, if an upload session was
    ever opened) on any failure.
    """
    path = Path(path)
    total_size = path.stat().st_size
    if total_size == 0:
        raise RommError(f"cannot upload empty file: {path}")

    total_chunks = math.ceil(total_size / chunk_size)
    # The server's own expected chunk size -- see module docstring. Not
    # the `chunk_size` argument: that only chose total_chunks.
    server_chunk_size = (total_size + total_chunks - 1) // total_chunks

    start = client.start_upload(
        platform_id=platform_id,
        filename=path.name,
        total_size=total_size,
        total_chunks=total_chunks,
    )
    if "upload_id" not in start:
        raise RommError(
            "RomM upload/start response is missing 'upload_id'; "
            f"keys present: {sorted(start.keys())}"
        )
    upload_id = start["upload_id"]

    try:
        with path.open("rb") as fh:
            for index in range(total_chunks):
                if index < total_chunks - 1:
                    size = server_chunk_size
                else:
                    size = total_size - server_chunk_size * (total_chunks - 1)
                chunk = fh.read(size)
                client.upload_chunk(upload_id, index, chunk)
                if progress is not None:
                    progress(index + 1, total_chunks)
        return client.complete_upload(upload_id)
    except Exception as exc:
        try:
            client.cancel_upload(upload_id)
        except RommError:
            pass  # the original failure is what matters; don't mask it
        if isinstance(exc, RommError):
            raise
        raise RommError(f"upload of {path} failed: {exc}") from exc
