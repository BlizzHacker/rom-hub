"""Getting bytes into a Retrom library, over Retrom's own WebDAV service.

## Retrom has no upload API. This is the finding, not a workaround.

`GameService` is `GetGames`/`UpdateGames`/`DeleteGames` and the same three
for game files; `PlatformService` is Get/Update/Delete. There is no
`CreateGame`, no `CreatePlatform`, and no RPC anywhere in
`packages/codegen/protos/retrom/services/` that carries file content. The
REST service is three read routes. Retrom's library is **derived from the
filesystem**: `LibraryService/UpdateLibrary` walks the configured content
directories and inserts a `Platform` row per directory and a `Game` row per
entry at game depth (`packages/grpc-service/src/library/content_resolver/`).

So a ROM is imported by *putting a file where a scan will find it*. That is
the model, and `LibraryBackend` should express it rather than pretend
Retrom accepts uploads.

## Which is why WebDAV is not a side channel

Retrom serves three things on one port and routes by request
(`packages/service/src/lib.rs`): gRPC by content type, **WebDAV under
`/dav`**, and REST for everything else. The DAV handler is
`LocalFs` rooted at `RetromDirs::data_dir()` with a `MemLs` lock system and
no method restriction, so `PUT` and `MKCOL` are both served. It is
Retrom's own supported way to write into Retrom's own storage.

Measured against Retrom 0.8.4:

    PUT /dav/library/dosbox/rubik.zip   -> 201 Created
    GET /dav/library/dosbox/rubik.zip   -> the bytes back

## The one condition, and why it is checked rather than assumed

WebDAV is rooted at the **data** directory. A content directory that lives
somewhere else entirely -- the stock compose file mounts libraries at
`/lib1` and `/lib2`, with data at `/app/data` -- is not reachable over
`/dav` at all, and no amount of trying makes it so. A deployment that wants
rom-hub to file ROMs for it puts (or bind-mounts) its content directory
under `RETROM_DATA_DIR`.

`dav_path_for` therefore *probes* rather than assuming: it walks the
platform's absolute path from the longest suffix down, asking WebDAV which
one exists. If none does, the ROM is not uploaded and the operator is told
exactly what to change -- before anything is downloaded.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import httpx

from rom_hub import env

from .client import (
    CUSTOM_LAYOUT,
    MULTI_FILE_GAME,
    SINGLE_FILE_GAME,
    RetromClient,
    RetromError,
)

#: Where `packages/service/src/lib.rs` mounts the WebDAV router, and the
#: prefix `webdav_service(Some("/dav"))` strips before hitting the
#: filesystem.
DAV_PREFIX = "/dav"

#: Streamed, because a ROM can be several GB and none of it belongs in
#: memory. Matches `importer.STREAM_CHUNK_BYTES`.
_CHUNK_BYTES = 1024 * 1024

#: A long upload is not a hung one. Distinct from the gRPC timeout, which
#: covers calls that answer in milliseconds.
UPLOAD_TIMEOUT = 900.0


class WebDavClient:
    """The `/dav` half of a Retrom server."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = UPLOAD_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WebDavClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    @staticmethod
    def _url(rel: str) -> str:
        return f"{DAV_PREFIX}/{str(rel).strip('/')}"

    def _request(self, method: str, rel: str, **kwargs) -> httpx.Response:
        url = self._url(rel)
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise RetromError(
                f"{method} {url} on Retrom's WebDAV service failed: {exc}"
            ) from exc

    def exists(self, rel: str) -> bool:
        """Whether `rel` resolves to anything under the DAV root.

        `PROPFIND` with `Depth: 0` rather than `HEAD`, because a directory
        answers `PROPFIND` with `207` and may not answer `HEAD` usefully at
        all -- and a directory is precisely what this is asked about.
        """
        resp = self._request("PROPFIND", rel, headers={"Depth": "0"})
        return resp.status_code in (200, 207)

    def mkcol(self, rel: str) -> None:
        """Create a collection (directory), tolerating one that is there.

        `405 Method Not Allowed` is what a WebDAV server answers for
        `MKCOL` on an existing collection; that is the desired end state,
        not a failure.
        """
        resp = self._request("MKCOL", rel)
        if resp.status_code in (200, 201, 405):
            return
        raise RetromError(
            f"could not create directory {self._url(rel)!r} in Retrom's "
            f"library: HTTP {resp.status_code}"
        )

    def makedirs(self, rel: str) -> None:
        """`mkcol` every level of `rel`, parents first.

        WebDAV's `MKCOL` creates exactly one collection and answers `409
        Conflict` when its parent is missing -- there is no `-p`. So a
        nested destination has to be walked from the top, which is also
        what makes an already-existing prefix free (each level answers
        `405` and is skipped).
        """
        parts = [part for part in str(rel).strip("/").split("/") if part]
        for depth in range(1, len(parts) + 1):
            self.mkcol("/".join(parts[:depth]))

    def put_file(self, rel: str, path: Path) -> None:
        """Stream `path` to `rel`.

        `Content-Length` is set explicitly from the file's size. httpx
        would otherwise send an iterator body as `Transfer-Encoding:
        chunked`, and a length is both cheaper for the server and one less
        thing for a proxy in front of Retrom to mishandle.
        """
        path = Path(path)
        size = path.stat().st_size

        def chunks():
            with path.open("rb") as fh:
                while chunk := fh.read(_CHUNK_BYTES):
                    yield chunk

        resp = self._request(
            "PUT",
            rel,
            content=chunks(),
            headers={
                "Content-Length": str(size),
                "Content-Type": "application/octet-stream",
            },
        )
        self._check_write(resp, rel, size)

    def put_bytes(self, rel: str, data: bytes, content_type: str) -> None:
        resp = self._request(
            "PUT", rel, content=data, headers={"Content-Type": content_type}
        )
        self._check_write(resp, rel, len(data))

    def _check_write(self, resp: httpx.Response, rel: str, size: int) -> None:
        # 201 for a new file, 204 for an overwrite, 200 from servers that
        # answer the PUT with a body.
        if resp.status_code in (200, 201, 204):
            return
        hint = ""
        if resp.status_code in (403, 405, 409):
            hint = (
                " -- the directory may not exist, or the Retrom process may "
                "not have write permission on it (check PUID/PGID and the "
                "ownership of the content directory)"
            )
        raise RetromError(
            f"writing {size} bytes to {self._url(rel)!r} failed: HTTP "
            f"{resp.status_code}{hint}"
        )


def _path_components(absolute: str) -> list[str]:
    """The components of a server-side absolute path, POSIX or Windows.

    Retrom canonicalises the paths it stores, so this is whatever the
    *server's* OS produced -- which is not necessarily the OS the Hub is
    running on. Splitting on both separators costs nothing and stops a
    Windows-hosted Retrom being unaddressable from a Linux sidecar.
    """
    return [
        part
        for part in absolute.replace("\\", "/").split("/")
        if part and part != "."
    ]


def dav_path_for(dav: WebDavClient, absolute: str) -> str:
    """The `/dav`-relative path of a server-side absolute path.

    The DAV root is the server's data directory, whose absolute path
    Retrom does not report over any API. So the relationship is
    established by probe: try the longest suffix of `absolute` first and
    walk down, taking the first one WebDAV says exists. Longest-first
    matters -- with a data dir of `/app/data` and a platform at
    `/app/data/library/dosbox`, both `library/dosbox` and a stray
    top-level `dosbox` could exist, and the longer is the one that is
    actually inside the library.

    `RETROM_DATA_DIR` short-circuits the probe when it is set: it names
    the *server's* data directory (the same variable Retrom itself reads),
    and an operator who sets it gets an exact answer instead of a search.
    """
    components = _path_components(absolute)
    if not components:
        raise RetromError(
            "Retrom reported an empty path for this platform, so there is "
            "nowhere to write a ROM"
        )

    configured = env.get("RETROM_DATA_DIR")
    if configured:
        root = _path_components(configured)
        if components[: len(root)] == root:
            return "/".join(components[len(root) :])
        raise RetromError(
            f"RETROM_DATA_DIR is set to {configured!r} but Retrom reports "
            f"this platform at {absolute!r}, which is not inside it. Retrom "
            f"serves WebDAV from its data directory only, so a content "
            f"directory outside it cannot be written to over the network."
        )

    for start in range(len(components)):
        candidate = "/".join(components[start:])
        if dav.exists(candidate):
            return candidate

    raise RetromError(
        f"Retrom's library directory for this platform ({absolute!r}) is not "
        f"reachable over its WebDAV service. Retrom exposes WebDAV rooted at "
        f"its data directory (RETROM_DATA_DIR, /app/data in the official "
        f"image) and has no upload API of any kind, so rom-hub can only file "
        f"a ROM into a content directory that lives inside it. Either move "
        f"or bind-mount the content directory under the data directory, or "
        f"set RETROM_DATA_DIR to the server's data directory if it is "
        f"already there under a path this probe could not find."
    )


def storage_type_for(client: RetromClient, platform_path: str) -> int:
    """How the content directory containing `platform_path` is laid out.

    This decides where the file goes, and getting it wrong is a silent
    failure rather than an error: `ResolvedPlatform::get_game_resolvers`
    skips a non-directory in a `MULTI_FILE_GAME` library and a
    non-file in a `SINGLE_FILE_GAME` one, so a misplaced upload lands on
    disk, scans cleanly, and simply never becomes a game.

    The longest matching content directory wins, for the same reason it
    does in `dav_path_for`: nested content directories are legal and the
    innermost is the one that governs.
    """
    platform_parts = _path_components(platform_path)
    best: tuple[int, int] | None = None
    for entry in client.content_directories():
        parts = _path_components(entry["path"])
        if platform_parts[: len(parts)] != parts:
            continue
        storage_type = entry.get("storage_type")
        if storage_type is None:
            # Retrom refuses such a directory outright, so a platform
            # inside one cannot have been scanned from it.
            continue
        if best is None or len(parts) > best[0]:
            best = (len(parts), storage_type)

    if best is None:
        raise RetromError(
            f"Retrom's platform directory {platform_path!r} is not inside any "
            f"content directory the server currently has configured, so the "
            f"layout a new ROM has to follow is unknown. This usually means "
            f"the platform is left over from a content directory that has "
            f"since been removed."
        )

    storage_type = best[1]
    if storage_type == CUSTOM_LAYOUT:
        raise RetromError(
            f"the content directory holding {platform_path!r} uses Retrom's "
            f"CUSTOM storage type, whose layout is defined by a user-supplied "
            f"template. rom-hub will not guess where a game belongs in a "
            f"custom library; use SINGLE_FILE_GAME or MULTI_FILE_GAME for a "
            f"content directory it should import into."
        )
    if storage_type not in (SINGLE_FILE_GAME, MULTI_FILE_GAME):
        raise RetromError(
            f"Retrom reports an unrecognised storage type {storage_type} for "
            f"the content directory holding {platform_path!r}"
        )
    return storage_type


def upload_file(
    client: RetromClient,
    dav: WebDavClient,
    path: Path,
    platform_id: int,
) -> str:
    """Put one ROM where a Retrom scan will find it. Returns the DAV path.

    For a `SINGLE_FILE_GAME` library that is
    `<platform>/<filename>`; for `MULTI_FILE_GAME` it is
    `<platform>/<stem>/<filename>`, with the containing directory created
    first, because there the *directory* is the game and a bare file at
    game depth is skipped by the scanner.

    Writes nothing to the database: the file exists and Retrom does not
    know about it until `UpdateLibrary` runs. See `backend.scan_platform`.
    """
    path = Path(path)
    platform_path = client.platform_path(platform_id)
    storage_type = storage_type_for(client, platform_path)
    platform_dav = dav_path_for(dav, platform_path)

    if storage_type == MULTI_FILE_GAME:
        game_dir = f"{platform_dav}/{PurePosixPath(path.name).stem}"
        dav.mkcol(game_dir)
        target = f"{game_dir}/{path.name}"
    else:
        target = f"{platform_dav}/{path.name}"

    dav.put_file(target, path)
    return target
