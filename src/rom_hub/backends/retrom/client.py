"""Retrom's gRPC services, as the Hub's flat dict-shaped vocabulary.

Every RPC used here is cited against `packages/codegen/protos/retrom/`
with its field numbers, because a field number is the only thing on the
wire and a wrong one is a silent misread rather than an error.

## Retrom has no authentication

There is nothing to log in to. `packages/service/src/lib.rs` builds the
REST, gRPC and WebDAV routers with CORS, compression, tracing and cache
layers and **no auth layer at all**; no RPC takes a credential, no handler
inspects metadata for one, and the project's own README still lists
"(Multi-)User authentication" as an unchecked roadmap item. So
`authenticate()` cannot check a password -- it verifies reachability
instead, which is the only failure it is in a position to detect early.

An operator who wants Retrom protected puts it behind a reverse proxy;
`RETROM_URL` may carry `user:pass@` for that case and httpx will use it.

## The listing shape

`list_games` speaks the Hub's dict vocabulary -- `id`, `fs_name` -- not
Retrom's. That is what `rom_hub.dedup` reads, and it reads defensively:
a key it does not find is "no match", never an error.

**Retrom stores no checksums.** `GameFile` is `id`, `byte_size`, `path`,
`game_id` and timestamps -- there is no crc/md5/sha1 column anywhere in
`packages/codegen/protos/retrom/models/game-files.proto`. So
`find_duplicate` can never match against a Retrom listing and every dedup
decision falls to `find_by_filename`. That is sound rather than merely
tolerable: Retrom derives a game *from* its path, so within one platform
directory a filename **is** the identity of the game, and two files of the
same name cannot coexist. What it costs is the cross-name case -- the same
ROM already present under a different filename is re-uploaded rather than
skipped. No false positive is possible, which is the direction that
matters: a wrong skip loses a ROM the operator asked for.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import httpx

from rom_hub.backends.base import BackendError

from . import proto
from .grpcweb import GrpcError, GrpcWebChannel

# -- method names ----------------------------------------------------------
#
# `<proto package>.<Service>/<Method>`, which is the path tonic routes on.

GET_SERVER_INFO = "retrom.ServerService/GetServerInfo"
GET_SERVER_CONFIG = "retrom.ServerService/GetServerConfig"
GET_PLATFORMS = "retrom.PlatformService/GetPlatforms"
GET_GAMES = "retrom.GameService/GetGames"
GET_GAME_METADATA = "retrom.MetadataService/GetGameMetadata"
UPDATE_GAME_METADATA = "retrom.MetadataService/UpdateGameMetadata"
UPDATE_LIBRARY = "retrom.LibraryService/UpdateLibrary"

# -- storage types ---------------------------------------------------------
#
# retrom/server/config.proto :: StorageType. These decide where a file has
# to be written for Retrom to see it as a game at all, so they are not a
# detail -- see `upload`.

SINGLE_FILE_GAME = 0
MULTI_FILE_GAME = 1
CUSTOM_LAYOUT = 2

# -- what a metadata patch may write ---------------------------------------
#
# `GameMetadata` (models/metadata.proto) has name, description, cover_url,
# background_url, icon_url and **one** provider id: `igdb_id`. The Hub's
# `MetadataPatch` can carry eleven provider ids and eight raw-metadata
# blobs, and Retrom has a column for exactly one of them.
#
# Unwritable fields are refused, not dropped. `rom-hub enrich` reports what
# it wrote; quietly discarding `moby_id` would make that report false, and
# the operator would have no way to find out except by reading the library
# afterwards.
WRITABLE_FIELDS = frozenset({"name", "igdb_id"})


class RetromError(BackendError):
    """Any Retrom failure: transport, gRPC status, or a refusal from here."""


class RetromClient:
    """One Retrom server over gRPC-Web, with listings cached per run."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        channel: GrpcWebChannel | None = None,
    ):
        self._channel = channel or GrpcWebChannel(
            base_url, timeout=timeout, transport=transport
        )
        self._platforms: list[dict] | None = None
        self._content_dirs: list[dict] | None = None

    @property
    def base_url(self) -> str:
        return self._channel.base_url

    def close(self) -> None:
        self._channel.close()

    def __enter__(self) -> "RetromClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- plumbing ----------------------------------------------------------

    def _call(self, method: str, request: bytes) -> dict[int, list]:
        try:
            body = self._channel.unary(method, request)
        except GrpcError as exc:
            raise RetromError(str(exc)) from exc
        try:
            return proto.decode(body)
        except proto.ProtoError as exc:
            raise RetromError(
                f"{method} answered something that is not a protobuf "
                f"message: {exc}"
            ) from exc

    # -- reachability ------------------------------------------------------

    def server_version(self) -> str:
        """`ServerService/GetServerInfo` -> "major.minor.patch".

        services/server-service.proto; `GetServerInfoResponse.server_info`
        is field 1, `ServerInfo.version` field 1, and `Version` is
        major/minor/patch at 1/2/3 (server/server-info.proto).
        """
        response = self._call(GET_SERVER_INFO, b"")
        info = proto.as_message(response, 1) or {}
        version = proto.as_message(info, 1) or {}
        return ".".join(
            str(proto.as_int(version, field, 0)) for field in (1, 2, 3)
        )

    def authenticate(self) -> None:
        """Prove the server is a reachable Retrom. See the module docstring.

        Deliberately a real RPC rather than a `GET /`: Retrom answers `303`
        on `/` from a static-file router that would still be there if the
        gRPC services had failed to start, and it is the gRPC path that
        every later call depends on.
        """
        try:
            self.server_version()
        except RetromError as exc:
            raise RetromError(
                f"could not reach Retrom's gRPC-Web API at "
                f"{self.base_url!r}: {exc}. Check RETROM_URL points at the "
                f"service port (Retrom's default is 5101) and that nothing "
                f"in front of it strips the application/grpc-web+proto "
                f"content type."
            ) from exc

    # -- server configuration ---------------------------------------------

    def content_directories(self) -> list[dict]:
        """The library roots and how each is laid out, cached for the run.

        `GetServerConfigResponse.config` is field 1;
        `ServerConfig.content_directories` field 2; `ContentDirectory` is
        `path` 1 and `storage_type` 2 (server/config.proto).

        `storage_type` is `optional`, and a content directory without one
        is not merely defaulted by Retrom -- `ContentResolver::
        from_content_dir` refuses it outright ("Content directory has no
        storage type") and the whole directory is skipped by every scan.
        So `None` here means the same thing it means to Retrom: this
        directory is not part of the library.
        """
        if self._content_dirs is None:
            response = self._call(GET_SERVER_CONFIG, b"")
            config = proto.as_message(response, 1) or {}
            dirs = []
            for entry in proto.as_messages(config, 2):
                path = proto.as_str(entry, 1)
                if not path:
                    continue
                dirs.append(
                    {"path": path, "storage_type": proto.as_int(entry, 2)}
                )
            self._content_dirs = dirs
        return list(self._content_dirs)

    # -- platforms ---------------------------------------------------------

    def list_platforms(self) -> list[dict]:
        """Every platform, with its directory and metadata name.

        `GetPlatformsRequest.with_metadata` is field 2 (bool);
        `GetPlatformsResponse` is `platforms` 1 and `metadata` 2.
        `Platform` is `id` 1 and `path` 2; `PlatformMetadata` is
        `platform_id` 1 and `name` 2 (models/platforms.proto,
        models/metadata.proto).

        A platform's `path` is the *directory* Retrom found it in --
        Retrom has no notion of a platform slug, so the directory's own
        name is the only durable identifier it has.
        """
        if self._platforms is not None:
            return list(self._platforms)

        response = self._call(GET_PLATFORMS, proto.bool_field(2, True))
        names: dict[int, str] = {}
        for entry in proto.as_messages(response, 2):
            platform_id = proto.as_int(entry, 1)
            name = proto.as_str(entry, 2)
            if platform_id is not None and name:
                names[platform_id] = name

        platforms = []
        for entry in proto.as_messages(response, 1):
            platform_id = proto.as_int(entry, 1)
            path = proto.as_str(entry, 2, "")
            if platform_id is None:
                continue
            platforms.append(
                {
                    "id": platform_id,
                    "path": path,
                    "dir_name": PurePosixPath(path).name if path else "",
                    "name": names.get(platform_id, ""),
                }
            )
        self._platforms = platforms
        return list(platforms)

    def platform_id(self, platform: str) -> int:
        """Resolve a platform name to Retrom's integer id, or raise.

        Matched, case-insensitively, against the library directory's own
        name first and the IGDB metadata name second. The directory wins
        because it is what the operator actually created and what Retrom
        derives the platform from; the metadata name is a nicety that only
        exists once IGDB has been configured and a scan has run.

        Never creates anything. Retrom has no CreatePlatform RPC at all --
        `PlatformService` is Get/Update/Delete -- and a platform only comes
        into being when a scan finds a directory. Filing a ROM under a
        platform the operator did not create is exactly the failure
        `LibraryBackend.platform_id` refuses to risk.
        """
        wanted = platform.strip().lower()
        platforms = self.list_platforms()
        for key in ("dir_name", "name"):
            for entry in platforms:
                value = entry.get(key) or ""
                if value.lower() == wanted:
                    return entry["id"]

        known = ", ".join(
            sorted(entry["dir_name"] for entry in platforms if entry["dir_name"])
        )
        raise RetromError(
            f"no Retrom platform matches {platform!r}. Retrom derives a "
            f"platform from a directory inside a configured content "
            f"directory, and has no API to create one, so the directory has "
            f"to exist and have been scanned first. Platforms it knows "
            f"about: {known or '(none -- has the library been scanned?)'}"
        )

    def platform_path(self, platform_id: int) -> str:
        """The library directory `platform_id` lives in, as Retrom sees it."""
        for entry in self.list_platforms():
            if entry["id"] == platform_id:
                if not entry["path"]:
                    raise RetromError(
                        f"Retrom platform {platform_id} has no path recorded, "
                        f"so there is nowhere to write a ROM for it"
                    )
                return entry["path"]
        raise RetromError(f"Retrom has no platform with id {platform_id}")

    def invalidate_platforms(self) -> None:
        """Drop the cached platform listing.

        A scan can create a platform, so the listing taken before one is
        stale after it.
        """
        self._platforms = None

    # -- games -------------------------------------------------------------

    def list_games(self, platform_id: int) -> list[dict]:
        """Every game on `platform_id`, in the Hub's listing vocabulary.

        `GetGamesRequest` is `platform_ids` 1 (repeated int32),
        `with_metadata` 3 and `with_files` 4; `GetGamesResponse` is
        `games` 1, `metadata` 2, `game_files` 3 (services/game-service.proto).
        `Game` is `id` 1, `path` 3, `platform_id` 4; `GameFile` is `id` 1,
        `byte_size` 3, `path` 4, `game_id` 6.

        Not paged, because the RPC is not: `GetGames` takes no limit or
        offset and answers with the whole platform in one message. There
        is no page to miss, which is the failure mode paging exists to
        prevent.
        """
        request = (
            proto.packed_varints(1, [platform_id])
            + proto.bool_field(3, True)
            + proto.bool_field(4, True)
        )
        response = self._call(GET_GAMES, request)

        sizes: dict[int, int] = {}
        for entry in proto.as_messages(response, 3):
            game_id = proto.as_int(entry, 6)
            byte_size = proto.as_int(entry, 3)
            if game_id is None or byte_size is None:
                continue
            sizes[game_id] = sizes.get(game_id, 0) + byte_size

        names: dict[int, str] = {}
        for entry in proto.as_messages(response, 2):
            game_id = proto.as_int(entry, 1)
            name = proto.as_str(entry, 2)
            if game_id is not None and name:
                names[game_id] = name

        games = []
        for entry in proto.as_messages(response, 1):
            game_id = proto.as_int(entry, 1)
            if game_id is None:
                continue
            path = proto.as_str(entry, 3, "") or ""
            games.append(
                {
                    # `id` and `fs_name` are the two keys rom_hub.dedup and
                    # rom_hub.importer read. Everything else is context.
                    "id": game_id,
                    "fs_name": PurePosixPath(path).name,
                    "name": names.get(game_id, ""),
                    "path": path,
                    "platform_id": proto.as_int(entry, 4),
                    "fs_size_bytes": sizes.get(game_id),
                }
            )
        return games

    def get_game(self, game_id: int) -> dict:
        """One game, in the same vocabulary as a listing entry.

        `GetGamesRequest.ids` is field 2. `platform_slug` is filled in from
        the platform's directory name, which is the identifier a metadata
        plugin would recognise -- Retrom stores no slug of its own.
        """
        request = (
            proto.packed_varints(2, [game_id])
            + proto.bool_field(3, True)
            + proto.bool_field(4, True)
        )
        response = self._call(GET_GAMES, request)

        entries = proto.as_messages(response, 1)
        if not entries:
            raise RetromError(f"Retrom has no game with id {game_id}")
        entry = entries[0]

        size = None
        for file_entry in proto.as_messages(response, 3):
            if proto.as_int(file_entry, 6) == game_id:
                size = (size or 0) + (proto.as_int(file_entry, 3) or 0)

        name = ""
        for meta in proto.as_messages(response, 2):
            if proto.as_int(meta, 1) == game_id:
                name = proto.as_str(meta, 2, "") or ""

        path = proto.as_str(entry, 3, "") or ""
        platform_id = proto.as_int(entry, 4)
        platform_slug = ""
        if platform_id is not None:
            for platform in self.list_platforms():
                if platform["id"] == platform_id:
                    platform_slug = platform["dir_name"]
                    break

        return {
            "id": game_id,
            "name": name,
            "fs_name": PurePosixPath(path).name,
            "path": path,
            "platform_id": platform_id,
            "platform_slug": platform_slug,
            "fs_size_bytes": size,
        }

    # -- metadata ----------------------------------------------------------

    def game_metadata(self, game_id: int) -> dict:
        """The stored metadata row for one game, or `{}`.

        `GetGameMetadataRequest.game_ids` is field 1;
        `GetGameMetadataResponse.metadata` is field 1
        (services/metadata-service.proto).
        """
        response = self._call(
            GET_GAME_METADATA, proto.packed_varints(1, [game_id])
        )
        for entry in proto.as_messages(response, 1):
            if proto.as_int(entry, 1) != game_id:
                continue
            return {
                "game_id": game_id,
                "name": proto.as_str(entry, 2, "") or "",
                "description": proto.as_str(entry, 3, "") or "",
                "cover_url": proto.as_str(entry, 4, "") or "",
                "background_url": proto.as_str(entry, 5, "") or "",
                "icon_url": proto.as_str(entry, 6, "") or "",
                "igdb_id": proto.as_int(entry, 7),
                "links": proto.as_strs(entry, 10),
                "video_urls": proto.as_strs(entry, 11),
                "screenshot_urls": proto.as_strs(entry, 12),
                "artwork_urls": proto.as_strs(entry, 13),
            }
        return {}

    def update_game_metadata(
        self, game_id: int, fields: dict[str, str], cover_url: str | None = None
    ) -> dict:
        """Apply a partial patch to one game's metadata row.

        ## Why this reads before it writes

        `UpdateGameMetadata` upserts `UpdatedGameMetadata` as a diesel
        changeset (`packages/grpc-service/src/metadata/mod.rs`:
        `insert_into(...).on_conflict(game_id).do_update().set(&row)`).
        A diesel changeset skips `Option` fields that are `None`, so every
        *scalar* here is genuinely absent-means-leave-alone -- but
        `links`, `video_urls`, `screenshot_urls` and `artwork_urls` are
        proto3 `repeated`, which is `Vec<String>` and never `None`. They
        are therefore in the changeset on every call, and a patch that did
        not mention them would set all four to empty -- wiping a user's
        IGDB-synced screenshots as a side effect of writing a name.

        So the four lists are read back and resent unchanged. That is what
        makes this a partial update rather than an approximation of one,
        which `LibraryBackend.update_rom` requires.

        There is a race in the read-modify-write, and it is the right
        trade: losing a screenshot list added in the microseconds between
        the read and the write is strictly better than losing it on every
        single enrich.
        """
        unknown = sorted(set(fields) - WRITABLE_FIELDS)
        if unknown:
            raise RetromError(
                f"refusing to update game {game_id}: Retrom's GameMetadata "
                f"has no field for {unknown}. It stores name, description, "
                f"cover/background/icon urls and one provider id "
                f"(igdb_id) -- see models/metadata.proto. The write was "
                f"refused rather than partly applied, because a report of "
                f"what was written has to be true."
            )

        message = proto.varint_field(1, game_id)
        if "name" in fields:
            message += proto.string_field(2, str(fields["name"]))
        if "igdb_id" in fields:
            raw = str(fields["igdb_id"]).strip()
            try:
                message += proto.varint_field(7, int(raw))
            except ValueError:
                raise RetromError(
                    f"refusing to update game {game_id}: Retrom's igdb_id is "
                    f"an int64 and {raw!r} is not an integer"
                ) from None
        if cover_url is not None:
            message += proto.string_field(4, cover_url)

        existing = self.game_metadata(game_id)
        for field, key in (
            (10, "links"),
            (11, "video_urls"),
            (12, "screenshot_urls"),
            (13, "artwork_urls"),
        ):
            for value in existing.get(key, []):
                message += proto.string_field(field, value)

        self._call(UPDATE_GAME_METADATA, proto.bytes_field(1, message))
        return self.game_metadata(game_id)

    # -- library scan ------------------------------------------------------

    def update_library(self) -> list[str]:
        """`LibraryService/UpdateLibrary` -- rescan the content directories.

        `UpdateLibraryRequest` is empty; `UpdateLibraryResponse.job_ids` is
        field 1, repeated string (services/library-service.proto).

        This is what turns a file on disk into a row in the database. It
        is not a refresh and it is not optional: see `backend`.
        """
        response = self._call(UPDATE_LIBRARY, b"")
        self.invalidate_platforms()
        return proto.as_strs(response, 1)
