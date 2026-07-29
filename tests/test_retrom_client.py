"""Retrom's RPCs and its WebDAV storage, against a fake that behaves like it.

The fake is a `httpx.MockTransport` that speaks real gRPC-Web framing and
real protobuf, and enforces the parts of Retrom's behaviour that this
backend depends on rather than rubber-stamping whatever it is sent:

* `MKCOL` answers `409` when the parent collection is missing and `405`
  when the collection already exists, which is what makes `makedirs` a
  walk rather than one call;
* `PUT` answers `409` into a directory that does not exist, which is what
  Retrom did when the cover directory had never been created;
* `UpdateGameMetadata` upserts a **diesel changeset** -- `optional` scalars
  that are absent are left alone, and `repeated` fields are always written
  because they are `Vec<String>` and cannot be absent. That last rule is
  the reason `update_game_metadata` reads before it writes, and a fake
  that did not reproduce it would let the read-modify-write be deleted
  without a single test failing.

No test here requires a live Retrom.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import httpx
import pytest

from rom_hub.backends.retrom import proto
from rom_hub.backends.retrom.client import (
    MULTI_FILE_GAME,
    SINGLE_FILE_GAME,
    RetromClient,
    RetromError,
)
from rom_hub.backends.retrom.grpcweb import GrpcWebChannel, iter_frames
from rom_hub.backends.retrom.upload import (
    WebDavClient,
    dav_path_for,
    storage_type_for,
    upload_file,
)

BASE = "http://retrom.example:5101"


def _ok(message: bytes) -> httpx.Response:
    trailer = b"grpc-status:0\r\n"
    body = (
        b"\x00"
        + len(message).to_bytes(4, "big")
        + message
        + b"\x80"
        + len(trailer).to_bytes(4, "big")
        + trailer
    )
    return httpx.Response(
        200, content=body, headers={"content-type": "application/grpc-web+proto"}
    )


def _unframe(body: bytes) -> bytes:
    for flags, payload in iter_frames(body):
        if not flags & 0x80:
            return payload
    return b""


class FakeRetrom:
    """A Retrom server, as far as this backend can tell."""

    def __init__(
        self,
        *,
        platforms=(("/app/data/library/dosbox", 2, "DOS"),),
        content_dirs=(("/app/data/library", SINGLE_FILE_GAME),),
        dav_dirs=("library", "library/dosbox", "public"),
    ):
        self.platforms = [
            {"id": pid, "path": path, "name": name}
            for path, pid, name in platforms
        ]
        self.content_dirs = [
            {"path": path, "storage_type": st} for path, st in content_dirs
        ]
        self.games: list[dict] = []
        self.metadata: dict[int, dict] = {}
        self.dav_dirs = set(dav_dirs)
        self.dav_files: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.scans = 0

    # -- library ----------------------------------------------------------

    def add_game(self, game_id: int, path: str, platform_id: int, size: int = 0):
        self.games.append(
            {"id": game_id, "path": path, "platform_id": platform_id, "size": size}
        )

    def scan(self) -> None:
        """What `UpdateLibrary` does: turn files on disk into game rows."""
        for platform in self.platforms:
            dav_root = platform["path"].lstrip("/").split("/", 2)[-1]
            for rel in sorted(self.dav_files):
                if not rel.startswith(dav_root + "/"):
                    continue
                absolute = "/app/data/" + rel
                if any(game["path"] == absolute for game in self.games):
                    continue
                self.add_game(
                    len(self.games) + 1,
                    absolute,
                    platform["id"],
                    len(self.dav_files[rel]),
                )

    # -- transport ---------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path.startswith("/dav/"):
            return self._dav(request, path[len("/dav/") :].strip("/"))
        if request.method == "POST":
            return self._grpc(path.lstrip("/"), _unframe(request.read()))
        if path.startswith("/rest/public/"):
            rel = "public/" + path[len("/rest/public/") :]
            if rel in self.dav_files:
                return httpx.Response(200, content=self.dav_files[rel])
            return httpx.Response(404)
        return httpx.Response(404)

    # -- WebDAV ------------------------------------------------------------

    def _dav(self, request: httpx.Request, rel: str) -> httpx.Response:
        parent = str(PurePosixPath(rel).parent)
        parent = "" if parent == "." else parent

        if request.method == "PROPFIND":
            if rel in self.dav_dirs or rel in self.dav_files or rel == "":
                return httpx.Response(207, text="<multistatus/>")
            return httpx.Response(404)

        if request.method == "MKCOL":
            if rel in self.dav_dirs:
                return httpx.Response(405)
            if parent and parent not in self.dav_dirs:
                return httpx.Response(409)
            self.dav_dirs.add(rel)
            return httpx.Response(201)

        if request.method == "PUT":
            if parent and parent not in self.dav_dirs:
                return httpx.Response(409)
            created = rel not in self.dav_files
            self.dav_files[rel] = request.read()
            return httpx.Response(201 if created else 204)

        if request.method == "GET" and rel in self.dav_files:
            return httpx.Response(200, content=self.dav_files[rel])
        return httpx.Response(404)

    # -- gRPC --------------------------------------------------------------

    def _grpc(self, method: str, request: bytes) -> httpx.Response:
        fields = proto.decode(request)

        if method == "retrom.ServerService/GetServerInfo":
            version = b"".join(
                proto.varint_field(i + 1, v) for i, v in enumerate((0, 8, 4))
            )
            return _ok(proto.bytes_field(1, proto.bytes_field(1, version)))

        if method == "retrom.ServerService/GetServerConfig":
            config = b"".join(
                proto.bytes_field(
                    2,
                    proto.string_field(1, entry["path"])
                    + (
                        proto.varint_field(2, entry["storage_type"])
                        if entry["storage_type"] is not None
                        else b""
                    ),
                )
                for entry in self.content_dirs
            )
            return _ok(proto.bytes_field(1, config))

        if method == "retrom.PlatformService/GetPlatforms":
            body = b"".join(
                proto.bytes_field(
                    1,
                    proto.varint_field(1, entry["id"])
                    + proto.string_field(2, entry["path"]),
                )
                for entry in self.platforms
            )
            if proto.as_bool(fields, 2):
                body += b"".join(
                    proto.bytes_field(
                        2,
                        proto.varint_field(1, entry["id"])
                        + proto.string_field(2, entry["name"]),
                    )
                    for entry in self.platforms
                    if entry["name"]
                )
            return _ok(body)

        if method == "retrom.GameService/GetGames":
            platform_ids = proto.as_packed_ints(fields, 1)
            ids = proto.as_packed_ints(fields, 2)
            chosen = [
                game
                for game in self.games
                if (not platform_ids or game["platform_id"] in platform_ids)
                and (not ids or game["id"] in ids)
            ]
            body = b"".join(
                proto.bytes_field(
                    1,
                    proto.varint_field(1, game["id"])
                    + proto.string_field(3, game["path"])
                    + proto.varint_field(4, game["platform_id"]),
                )
                for game in chosen
            )
            if proto.as_bool(fields, 4):
                body += b"".join(
                    proto.bytes_field(
                        3,
                        proto.varint_field(1, game["id"])
                        + proto.varint_field(3, game["size"])
                        + proto.string_field(4, game["path"])
                        + proto.varint_field(6, game["id"]),
                    )
                    for game in chosen
                )
            if proto.as_bool(fields, 3):
                body += b"".join(
                    self._metadata_message(game["id"], field=2)
                    for game in chosen
                    if game["id"] in self.metadata
                )
            return _ok(body)

        if method == "retrom.MetadataService/GetGameMetadata":
            body = b"".join(
                self._metadata_message(game_id, field=1)
                for game_id in proto.as_packed_ints(fields, 1)
                if game_id in self.metadata
            )
            return _ok(body)

        if method == "retrom.MetadataService/UpdateGameMetadata":
            for entry in proto.as_messages(fields, 1):
                self._apply_changeset(entry)
            return _ok(b"")

        if method == "retrom.LibraryService/UpdateLibrary":
            # The counter lives here, not in `scan`, so a test that
            # replaces `scan` to model a slow background job still sees
            # the trigger it actually made.
            self.scans += 1
            self.scan()
            return _ok(proto.string_field(1, f"job-{self.scans}"))

        return httpx.Response(404)

    def _metadata_message(self, game_id: int, field: int) -> bytes:
        row = self.metadata[game_id]
        body = proto.varint_field(1, game_id)
        for number, key in ((2, "name"), (3, "description"), (4, "cover_url")):
            if row.get(key):
                body += proto.string_field(number, row[key])
        if row.get("igdb_id") is not None:
            body += proto.varint_field(7, row["igdb_id"])
        for number, key in ((12, "screenshot_urls"), (13, "artwork_urls")):
            for value in row.get(key, []):
                body += proto.string_field(number, value)
        return proto.bytes_field(field, body)

    def _apply_changeset(self, entry: dict) -> None:
        """Diesel's `AsChangeset`, faithfully.

        Absent `Option` scalars are left alone. `repeated` fields are
        `Vec<String>`, are never absent, and are therefore written on
        every call -- including when the caller did not mention them.
        """
        game_id = proto.as_int(entry, 1)
        row = self.metadata.setdefault(
            game_id,
            {
                "name": "",
                "description": "",
                "cover_url": "",
                "igdb_id": None,
                "screenshot_urls": [],
                "artwork_urls": [],
            },
        )
        for number, key in ((2, "name"), (3, "description"), (4, "cover_url")):
            value = proto.as_str(entry, number)
            if value is not None:
                row[key] = value
        igdb_id = proto.as_int(entry, 7)
        if igdb_id is not None:
            row["igdb_id"] = igdb_id
        row["screenshot_urls"] = proto.as_strs(entry, 12)
        row["artwork_urls"] = proto.as_strs(entry, 13)


def _client(server: FakeRetrom) -> RetromClient:
    return RetromClient(
        BASE, channel=GrpcWebChannel(BASE, transport=server.transport())
    )


def _dav(server: FakeRetrom) -> WebDavClient:
    return WebDavClient(BASE, transport=server.transport())


# -- reachability ----------------------------------------------------------


def test_authenticate_calls_a_real_rpc_not_the_static_web_root():
    server = FakeRetrom()
    client = _client(server)
    client.authenticate()
    assert client.server_version() == "0.8.4"
    assert "POST /retrom.ServerService/GetServerInfo" in server.calls


def test_an_unreachable_server_says_which_url_and_which_port():
    def handler(request):
        raise httpx.ConnectError("refused")

    client = RetromClient(
        BASE, channel=GrpcWebChannel(BASE, transport=httpx.MockTransport(handler))
    )
    with pytest.raises(RetromError) as exc:
        client.authenticate()
    assert BASE in str(exc.value)
    assert "5101" in str(exc.value)


# -- platforms -------------------------------------------------------------


def test_a_platform_resolves_by_its_library_directory_name():
    client = _client(FakeRetrom())
    assert client.platform_id("dosbox") == 2
    assert client.platform_id("DOSBox") == 2


def test_a_platform_also_resolves_by_its_metadata_name():
    assert _client(FakeRetrom()).platform_id("dos") == 2


def test_the_directory_name_wins_over_another_platforms_metadata_name():
    """The directory is what the operator made; the metadata name is
    whatever IGDB decided to call something else."""
    server = FakeRetrom(
        platforms=(
            ("/app/data/library/dos", 1, "Arcade"),
            ("/app/data/library/arcade", 2, "DOS"),
        )
    )
    assert _client(server).platform_id("dos") == 1


def test_an_unknown_platform_raises_and_says_retrom_cannot_create_one():
    with pytest.raises(RetromError) as exc:
        _client(FakeRetrom()).platform_id("gamecube")
    message = str(exc.value)
    assert "gamecube" in message
    assert "no API to create one" in message
    assert "dosbox" in message  # what it does know about


def test_platform_listings_are_fetched_once_per_run():
    server = FakeRetrom()
    client = _client(server)
    client.platform_id("dosbox")
    client.platform_id("dosbox")
    assert server.calls.count("POST /retrom.PlatformService/GetPlatforms") == 1


def test_a_scan_invalidates_the_platform_cache():
    """A scan can create a platform, so the listing taken before is stale."""
    server = FakeRetrom()
    client = _client(server)
    client.list_platforms()
    client.update_library()
    client.list_platforms()
    assert server.calls.count("POST /retrom.PlatformService/GetPlatforms") == 2


# -- listings --------------------------------------------------------------


def test_a_listing_carries_the_two_keys_dedup_reads():
    server = FakeRetrom()
    server.add_game(1, "/app/data/library/dosbox/rubik.zip", 2, size=15000)
    [game] = _client(server).list_games(2)
    assert game["id"] == 1
    assert game["fs_name"] == "rubik.zip"
    assert game["fs_size_bytes"] == 15000


def test_a_listing_carries_no_hashes_because_retrom_stores_none():
    """`find_duplicate` must find nothing rather than match on absence."""
    from rom_hub.dedup import FileHashes, find_by_filename, find_duplicate

    server = FakeRetrom()
    server.add_game(1, "/app/data/library/dosbox/rubik.zip", 2)
    listing = _client(server).list_games(2)

    hashes = FileHashes(crc32="0" * 8, md5="0" * 32, sha1="0" * 40)
    assert find_duplicate(hashes, listing) is None
    assert find_by_filename("rubik.zip", listing)["id"] == 1


def test_a_listing_is_scoped_to_the_platform_asked_for():
    server = FakeRetrom(
        platforms=(
            ("/app/data/library/dosbox", 2, "DOS"),
            ("/app/data/library/snes", 3, "SNES"),
        )
    )
    server.add_game(1, "/app/data/library/dosbox/a.zip", 2)
    server.add_game(2, "/app/data/library/snes/b.zip", 3)
    assert [g["id"] for g in _client(server).list_games(3)] == [2]


def test_get_game_fills_in_the_platform_slug_a_plugin_would_recognise():
    server = FakeRetrom()
    server.add_game(7, "/app/data/library/dosbox/rubik.zip", 2, size=15000)
    server.metadata[7] = {"name": "Rubik", "screenshot_urls": [], "artwork_urls": []}
    rom = _client(server).get_game(7)
    assert rom["name"] == "Rubik"
    assert rom["fs_name"] == "rubik.zip"
    assert rom["platform_slug"] == "dosbox"
    assert rom["fs_size_bytes"] == 15000


def test_get_game_raises_for_an_id_retrom_does_not_have():
    with pytest.raises(RetromError):
        _client(FakeRetrom()).get_game(404)


# -- metadata --------------------------------------------------------------


def _seeded(server: FakeRetrom) -> RetromClient:
    server.add_game(1, "/app/data/library/dosbox/rubik.zip", 2)
    server.metadata[1] = {
        "name": "Rubik",
        "description": "a cube",
        "cover_url": "",
        "igdb_id": 4242,
        "screenshot_urls": ["https://example.invalid/shot.png"],
        "artwork_urls": ["https://example.invalid/art.png"],
    }
    return _client(server)


def test_a_partial_patch_leaves_the_scalars_it_did_not_mention_alone():
    server = FakeRetrom()
    after = _seeded(server).update_game_metadata(1, {"name": "Rubik 2"})
    assert after["name"] == "Rubik 2"
    assert after["description"] == "a cube"
    assert after["igdb_id"] == 4242


def test_a_partial_patch_does_not_erase_the_repeated_url_lists():
    """The read-modify-write in `update_game_metadata` exists for this.

    Retrom's changeset always writes `repeated` fields, so without the
    read every enrich would wipe a user's IGDB-synced screenshots.
    """
    server = FakeRetrom()
    after = _seeded(server).update_game_metadata(1, {"name": "Rubik 2"})
    assert after["screenshot_urls"] == ["https://example.invalid/shot.png"]
    assert after["artwork_urls"] == ["https://example.invalid/art.png"]


def test_the_fake_really_would_clobber_without_the_read():
    """Guards the test above: if the fake were permissive, that test would
    pass with the read-modify-write deleted."""
    server = FakeRetrom()
    client = _seeded(server)
    from rom_hub.backends.retrom.client import UPDATE_GAME_METADATA

    naive = proto.varint_field(1, 1) + proto.string_field(2, "Rubik 3")
    client._call(UPDATE_GAME_METADATA, proto.bytes_field(1, naive))
    assert client.game_metadata(1)["screenshot_urls"] == []


def test_igdb_id_is_written_as_an_integer():
    server = FakeRetrom()
    after = _seeded(server).update_game_metadata(1, {"igdb_id": "99"})
    assert after["igdb_id"] == 99


def test_a_non_integer_igdb_id_is_refused():
    server = FakeRetrom()
    with pytest.raises(RetromError) as exc:
        _seeded(server).update_game_metadata(1, {"igdb_id": "abc"})
    assert "int64" in str(exc.value)


def test_a_field_retrom_has_no_column_for_is_refused_not_dropped():
    """`rom-hub enrich` reports what it wrote, so it has to be true."""
    server = FakeRetrom()
    with pytest.raises(RetromError) as exc:
        _seeded(server).update_game_metadata(1, {"name": "x", "moby_id": "7"})
    assert "moby_id" in str(exc.value)
    assert server.metadata[1]["name"] == "Rubik"  # nothing was written


# -- WebDAV path resolution ------------------------------------------------


def test_the_dav_path_is_found_by_probing_the_longest_suffix_first():
    server = FakeRetrom(dav_dirs=("library", "library/dosbox", "dosbox"))
    with _dav(server) as dav:
        assert dav_path_for(dav, "/app/data/library/dosbox") == "library/dosbox"


def test_retrom_data_dir_short_circuits_the_probe(monkeypatch):
    monkeypatch.setenv("RETROM_DATA_DIR", "/app/data")
    server = FakeRetrom(dav_dirs=())
    with _dav(server) as dav:
        assert dav_path_for(dav, "/app/data/library/dosbox") == "library/dosbox"
    assert not server.calls  # no probing at all


def test_a_platform_outside_the_configured_data_dir_is_refused(monkeypatch):
    monkeypatch.setenv("RETROM_DATA_DIR", "/app/data")
    with _dav(FakeRetrom()) as dav:
        with pytest.raises(RetromError) as exc:
            dav_path_for(dav, "/lib1/dosbox")
    assert "not inside it" in str(exc.value)


def test_a_library_outside_the_dav_root_says_exactly_what_to_change():
    """The stock compose file mounts libraries at /lib1, and data at
    /app/data -- so this is the normal deployment, not an exotic one."""
    server = FakeRetrom(dav_dirs=("library",))
    with _dav(server) as dav:
        with pytest.raises(RetromError) as exc:
            dav_path_for(dav, "/lib1/dosbox")
    message = str(exc.value)
    assert "no upload API" in message
    assert "RETROM_DATA_DIR" in message


def test_a_windows_hosted_retrom_path_still_splits():
    server = FakeRetrom(dav_dirs=("library", "library/dosbox"))
    with _dav(server) as dav:
        assert dav_path_for(dav, r"C:\app\data\library\dosbox") == "library/dosbox"


# -- storage type ----------------------------------------------------------


def test_the_storage_type_comes_from_the_containing_content_directory():
    server = FakeRetrom(content_dirs=(("/app/data/library", MULTI_FILE_GAME),))
    client = _client(server)
    assert (
        storage_type_for(client, "/app/data/library/dosbox") == MULTI_FILE_GAME
    )


def test_the_innermost_content_directory_wins():
    server = FakeRetrom(
        content_dirs=(
            ("/app/data", SINGLE_FILE_GAME),
            ("/app/data/library", MULTI_FILE_GAME),
        )
    )
    assert (
        storage_type_for(_client(server), "/app/data/library/dosbox")
        == MULTI_FILE_GAME
    )


def test_a_custom_layout_is_refused_rather_than_guessed_at():
    server = FakeRetrom(content_dirs=(("/app/data/library", 2),))
    with pytest.raises(RetromError) as exc:
        storage_type_for(_client(server), "/app/data/library/dosbox")
    assert "CUSTOM" in str(exc.value)


def test_a_platform_in_no_configured_content_directory_is_refused():
    server = FakeRetrom(content_dirs=(("/other", SINGLE_FILE_GAME),))
    with pytest.raises(RetromError) as exc:
        storage_type_for(_client(server), "/app/data/library/dosbox")
    assert "not inside any content directory" in str(exc.value)


def test_a_content_directory_with_no_storage_type_is_ignored():
    """Retrom refuses such a directory outright, so nothing scanned from it."""
    server = FakeRetrom(content_dirs=(("/app/data/library", None),))
    with pytest.raises(RetromError):
        storage_type_for(_client(server), "/app/data/library/dosbox")


# -- upload ----------------------------------------------------------------


def test_a_single_file_library_takes_the_rom_beside_its_siblings(tmp_path):
    server = FakeRetrom()
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"payload")
    with _dav(server) as dav:
        target = upload_file(_client(server), dav, rom, 2)
    assert target == "library/dosbox/rubik.zip"
    assert server.dav_files["library/dosbox/rubik.zip"] == b"payload"


def test_a_multi_file_library_gets_a_directory_because_the_directory_is_the_game(
    tmp_path,
):
    """`get_game_resolvers` skips a bare file at game depth in a
    MULTI_FILE_GAME library -- the upload would scan cleanly and never
    become a game."""
    server = FakeRetrom(content_dirs=(("/app/data/library", MULTI_FILE_GAME),))
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"payload")
    with _dav(server) as dav:
        target = upload_file(_client(server), dav, rom, 2)
    assert target == "library/dosbox/rubik/rubik.zip"
    assert "library/dosbox/rubik" in server.dav_dirs


def test_an_upload_streams_with_an_explicit_length(tmp_path):
    """Not chunked: a length is cheaper for the server and one less thing
    for a proxy in front of Retrom to mishandle."""
    server = FakeRetrom()
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"x" * 3_000_000)

    lengths = {}
    original = server.__call__

    def watching(request: httpx.Request) -> httpx.Response:
        if request.method == "PUT":
            lengths["content-length"] = request.headers.get("content-length")
            lengths["transfer-encoding"] = request.headers.get("transfer-encoding")
        return original(request)

    with WebDavClient(BASE, transport=httpx.MockTransport(watching)) as dav:
        upload_file(_client(server), dav, rom, 2)

    assert lengths["content-length"] == "3000000"
    assert lengths["transfer-encoding"] is None


def test_an_upload_into_a_missing_directory_says_what_to_check(tmp_path):
    server = FakeRetrom(dav_dirs=("library",))
    rom = tmp_path / "rubik.zip"
    rom.write_bytes(b"payload")
    with _dav(server) as dav:
        with pytest.raises(RetromError):
            upload_file(_client(server), dav, rom, 2)


def test_makedirs_walks_from_the_top_because_mkcol_has_no_dash_p():
    server = FakeRetrom(dav_dirs=("public",))
    with _dav(server) as dav:
        dav.makedirs("public/rom-hub/covers")
    assert "public/rom-hub" in server.dav_dirs
    assert "public/rom-hub/covers" in server.dav_dirs


# -- scan ------------------------------------------------------------------


def test_update_library_returns_the_job_ids_it_was_given():
    assert _client(FakeRetrom()).update_library() == ["job-1"]
