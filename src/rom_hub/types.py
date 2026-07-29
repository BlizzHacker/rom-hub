"""RPP v1 wire types.

These validate data coming back from untrusted plugin subprocesses, so
constraints here are load-bearing rather than cosmetic.
"""

import base64
import binascii
import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

# Windows refuses these whatever the extension, and several of them are
# devices rather than files: bytes written to NUL are silently discarded,
# so the ROM would hash as empty and the upload would then fail with a
# misleading "cannot upload empty file".
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# An allowlist of permitted characters, not a denylist of bad forms. The
# previous version of this validator enumerated the bad forms it could
# think of -- separators, "." and ".." -- and was breached by one it had
# not: "C:evil.zip" carries no separator, so posixpath.basename left it
# alone, but on Windows `job_dir / "C:evil.zip"` discards the job
# directory entirely and resolves against C:'s current directory. A
# denylist of path syntax cannot be finished; a list of what a ROM
# filename may contain can be. `str.isalnum` is unicode-aware, so real
# non-ASCII names still pass.
_ALLOWED_PUNCTUATION = frozenset(" .-_()[]+,'!&~@#=")

# Long enough for any real ROM name, short enough to stay clear of
# NAME_MAX (255) and of Windows' 260-character full-path ceiling once the
# job directory has been prepended.
_MAX_FILENAME_CHARS = 200

# The host downloads every entry in a plan, so an unbounded list is an
# unbounded amount of work asked for by an untrusted process. The only
# bound before this was indirect -- protocol.MAX_MESSAGE_CHARS caps the
# reply frame at 8 MiB, or roughly 10^5 entries. Comfortably above any
# real multi-disc set, and an explicit statement of the default-deny
# posture the rest of this codebase takes.
MAX_FILES_PER_PLAN = 256


def bare_filename(v: str) -> str:
    """Validate a plugin-supplied name the host will open for writing.

    Extracted from `FetchFile.filename` because `importer` is no longer the
    only capability that hands the host a filename: `metadata` names the
    artwork file it wants fetched. One implementation, so a name refused on
    the import path cannot be accepted on the metadata one.

    Every rule below is applied on every platform. A name refused on Linux
    must be refused on Windows and vice versa: if this validator's answer
    depended on which OS the Hub happens to run on, a plugin could pick a
    name that is inert on the developer's machine and an escape on the
    operator's.
    """
    if len(v) > _MAX_FILENAME_CHARS:
        raise ValueError(f"filename must be at most {_MAX_FILENAME_CHARS} characters")

    bad = sorted({c for c in v if not (c.isalnum() or c in _ALLOWED_PUNCTUATION)})
    if bad:
        raise ValueError(
            f"filename contains characters that are not permitted in a "
            f"ROM filename: {bad!r}"
        )

    # Redundant given the character allowlist -- ":", "/" and "\" are all
    # excluded by it already -- but stated separately so that the invariant
    # survives any future widening of that allowlist.
    if PureWindowsPath(v).parts != (v,) or PurePosixPath(v).parts != (v,):
        raise ValueError(
            "filename must be a single bare name: no drive, anchor, "
            "UNC prefix or path separator, under either Windows or "
            "POSIX path rules"
        )

    # "." and ".." and anything else that is only dots and spaces: "..."
    # resolves to a *directory*, which makes dest.exists() true and seeds
    # the resume logic with a bogus offset.
    if not v.strip(". "):
        raise ValueError("filename must not be made only of dots and spaces")

    # Windows silently strips a trailing dot or space, so "g.zip." and
    # "g.zip " both open the same file as "g.zip" -- two plan entries that
    # look distinct would collide on disk.
    if v != v.rstrip(". "):
        raise ValueError("filename must not end in a dot or a space")

    if v.split(".")[0].upper() in _RESERVED_STEMS:
        raise ValueError(
            f"filename must not be a Windows reserved device name (got {v!r})"
        )
    return v


class SearchResult(BaseModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    platform: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    url: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)
    # Set by the host after the plugin returns; plugins cannot forge it.
    plugin: str = ""


class FetchFile(BaseModel):
    url: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("filename")
    @classmethod
    def _bare_name_only(cls, v: str) -> str:
        # The host writes this to disk. A plugin must not be able to point
        # that write anywhere but the job's own download directory.
        return bare_filename(v)


class FetchPlan(BaseModel):
    files: list[FetchFile] = Field(min_length=1, max_length=MAX_FILES_PER_PLAN)
    platform: str = Field(min_length=1)
    collection: str | None = None

    @field_validator("files")
    @classmethod
    def _filenames_must_be_distinct(cls, v: list[FetchFile]) -> list[FetchFile]:
        # Two entries writing to one path do not merely overwrite. The
        # second download finds the first file already on disk, takes its
        # size as a resume offset, sends `Range: bytes=<n>-`, and a server
        # that honours Range (Archive.org does) answers 206 -- so the
        # second file's tail is *appended* to the first file's body. The
        # result hashes cleanly, uploads once per entry, and reports DONE.
        #
        # Refuse the plan rather than renaming one of them: a plugin that
        # names two files identically has a bug, and quietly fixing it up
        # hides the bug while still importing something nobody asked for.
        # Compared case-insensitively because Windows opens "g.zip" and
        # "G.zip" as the same file, and this must not depend on the OS.
        names = [f.filename.casefold() for f in v]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        if duplicated:
            raise ValueError(
                f"every file in a FetchPlan needs a distinct filename; "
                f"these are repeated: {duplicated!r}"
            )
        return v


# -- metadata -----------------------------------------------------------
#
# `metadata` is the second capability whose return value the host acts on
# with its own privileges: `enrich()` describes edits and artwork, and the
# host performs the RomM write and the artwork fetch. So the same rule as
# FetchPlan applies -- nothing here is trusted, the URL is allowlist-gated
# on the host side, and the artwork filename goes through `bare_filename`.

# RomM's `Body_update_rom_api_roms__id__put` provider-id form fields, as
# verified against RomM 4.9.2's OpenAPI. An allowlist because the request
# is built by iterating whatever the plugin returned: without it, a plugin
# could name any form field the endpoint happens to accept -- including
# ones that are not metadata at all.
PROVIDER_ID_FIELDS = frozenset(
    {
        "igdb_id",
        "sgdb_id",
        "moby_id",
        "ss_id",
        "ra_id",
        "launchbox_id",
        "hasheous_id",
        "tgdb_id",
        "flashpoint_id",
        "hltb_id",
        "libretro_id",
    }
)

# The `raw_*_metadata` JSON-string form fields of the same endpoint.
RAW_METADATA_FIELDS = frozenset(
    {
        "raw_igdb_metadata",
        "raw_ss_metadata",
        "raw_launchbox_metadata",
        "raw_hasheous_metadata",
        "raw_flashpoint_metadata",
        "raw_hltb_metadata",
        "raw_moby_metadata",
        "raw_manual_metadata",
    }
)

# A provider id is an identifier, not free text: it is echoed straight into
# a form field, and RomM parses most of them as integers.
_PROVIDER_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_MAX_PROVIDER_ID_CHARS = 64

_MAX_ROM_NAME_CHARS = 500

# Each raw blob is serialised into one form field. protocol.MAX_MESSAGE_CHARS
# (8 MiB) already bounds the whole reply, but a per-field bound keeps one
# enormous blob from being the entire budget.
MAX_RAW_METADATA_CHARS = 256 * 1024

# Box art. Generous for a cover, far under the fetcher's own limit, and
# bounded because the host buffers it in memory to post it to RomM.
MAX_ARTWORK_BYTES = 8 * 1024 * 1024

DEFAULT_ARTWORK_FILENAME = "cover.png"


class RomRef(BaseModel):
    """What the host tells a plugin about a rom it is asked to enrich.

    Built host-side from `GET /api/roms/{id}`. Deliberately thin: a plugin
    needs enough to identify the game and nothing more, and every field
    here is one a plugin could have learned from a search result anyway.
    """

    rom_id: int = Field(ge=1)
    name: str = ""
    filename: str = ""
    platform: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    extra: dict[str, str] = Field(default_factory=dict)


class MetadataPatch(BaseModel):
    """Edits a plugin proposes for one rom. The HOST applies them.

    **Only what is set is sent.** Every field defaults to "absent", and
    `form_fields()` emits nothing for an absent one, because RomM's update
    endpoint takes the whole record: a `name=None` faithfully forwarded as
    an empty form field is how a plugin returning a partial patch would
    silently erase a user's curated library. Absent must mean "leave it
    alone", never "clear it".

    Artwork arrives either as `artwork_url` -- which the host fetches,
    after `check_url` against this plugin's own allowlist, exactly like a
    FetchPlan URL -- or as `artwork_base64`, bytes the plugin already has.
    Never both: two sources for one file is an ambiguity the host would
    have to resolve by guessing.
    """

    name: str | None = Field(
        default=None, min_length=1, max_length=_MAX_ROM_NAME_CHARS
    )
    provider_ids: dict[str, int | str] = Field(default_factory=dict)
    raw_metadata: dict[str, dict | list] = Field(default_factory=dict)
    artwork_url: str | None = None
    artwork_base64: str | None = None
    artwork_filename: str = DEFAULT_ARTWORK_FILENAME

    @field_validator("provider_ids", mode="before")
    @classmethod
    def _no_boolean_ids(cls, v):
        """Runs *before* coercion, which is the only place it can run.

        bool is an int in Python, so pydantic's lax mode quietly turns
        `igdb_id=True` into `igdb_id=1` -- a real, wrong id posted to a
        real library. By the time an "after" validator sees the value the
        evidence that it was ever a bool is gone.
        """
        if isinstance(v, dict):
            for key, value in v.items():
                if isinstance(value, bool):
                    raise ValueError(f"provider id {key!r} must be an id, not a bool")
        return v

    @field_validator("provider_ids")
    @classmethod
    def _known_provider_ids(cls, v: dict) -> dict:
        for key, value in v.items():
            if key not in PROVIDER_ID_FIELDS:
                raise ValueError(
                    f"unknown provider id field {key!r}; RPP v1 permits "
                    f"{sorted(PROVIDER_ID_FIELDS)}"
                )
            if isinstance(value, str):
                if not value or len(value) > _MAX_PROVIDER_ID_CHARS:
                    raise ValueError(
                        f"provider id {key!r} must be 1..{_MAX_PROVIDER_ID_CHARS} "
                        f"characters"
                    )
                bad = sorted(set(value) - _PROVIDER_ID_CHARS)
                if bad:
                    raise ValueError(
                        f"provider id {key!r} contains characters that are not "
                        f"permitted in an identifier: {bad!r}"
                    )
        return v

    @field_validator("raw_metadata")
    @classmethod
    def _known_raw_metadata(cls, v: dict) -> dict:
        for key, value in v.items():
            if key not in RAW_METADATA_FIELDS:
                raise ValueError(
                    f"unknown raw metadata field {key!r}; RPP v1 permits "
                    f"{sorted(RAW_METADATA_FIELDS)}"
                )
            try:
                encoded = json.dumps(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"raw metadata {key!r} is not JSON-serialisable: {exc}"
                ) from exc
            if len(encoded) > MAX_RAW_METADATA_CHARS:
                raise ValueError(
                    f"raw metadata {key!r} is {len(encoded)} characters, over "
                    f"the {MAX_RAW_METADATA_CHARS}-character limit"
                )
        return v

    @field_validator("artwork_base64", mode="before")
    @classmethod
    def _accept_raw_bytes(cls, v):
        """A plugin holding image bytes may hand them over directly.

        JSON has no byte string, so the wire form is base64 either way;
        encoding here means a plugin author never has to think about that.
        """
        if isinstance(v, (bytes, bytearray)):
            return base64.b64encode(bytes(v)).decode("ascii")
        return v

    @field_validator("artwork_base64")
    @classmethod
    def _decodable_and_bounded(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            data = base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"artwork_base64 is not valid base64: {exc}") from exc
        if not data:
            raise ValueError("artwork_base64 decoded to no bytes at all")
        if len(data) > MAX_ARTWORK_BYTES:
            raise ValueError(
                f"artwork is {len(data)} bytes, over the "
                f"{MAX_ARTWORK_BYTES}-byte limit"
            )
        return v

    @field_validator("artwork_filename")
    @classmethod
    def _bare_name_only(cls, v: str) -> str:
        # The host writes this to disk before posting it. Same check as
        # FetchFile's, by construction rather than by resemblance.
        return bare_filename(v)

    @model_validator(mode="after")
    def _one_artwork_source(self) -> "MetadataPatch":
        if self.artwork_url is not None and self.artwork_base64 is not None:
            raise ValueError(
                "a MetadataPatch may carry artwork_url or artwork_base64, not "
                "both -- the host would have to guess which one is the cover"
            )
        return self

    def has_artwork(self) -> bool:
        """True when this patch carries a cover, by either route.

        Asked before the cover is fetched, so a backend that cannot take
        one refuses without the host downloading it first.
        """
        return self.artwork_url is not None or self.artwork_base64 is not None

    def artwork_data(self) -> bytes | None:
        """The inline artwork bytes, if the plugin supplied any."""
        if self.artwork_base64 is None:
            return None
        return base64.b64decode(self.artwork_base64, validate=True)

    def form_fields(self) -> dict[str, str]:
        """The RomM form fields this patch sets, and no others.

        An unset field is *absent* from the returned mapping rather than
        present-and-empty. That distinction is the whole point: RomM's
        update endpoint writes what it is given, so a blank `name` part
        does not mean "unchanged", it means "erase the name".
        """
        fields: dict[str, str] = {}
        if self.name is not None:
            fields["name"] = self.name
        for key, value in self.provider_ids.items():
            fields[key] = str(value)
        for key, value in self.raw_metadata.items():
            fields[key] = json.dumps(value)
        return fields

    def is_empty(self) -> bool:
        """True when there is nothing to send, so the host can skip the write."""
        return (
            not self.form_fields()
            and self.artwork_url is None
            and self.artwork_base64 is None
        )


# -- stream -------------------------------------------------------------

# Long enough for a signed URL with a token in it, short enough that a
# plugin cannot make the host's log its dumping ground.
_MAX_STREAM_TARGET_CHARS = 4096

# Schemes that name something a program will go and *retrieve*. A target
# using one of these is a URL whatever the plugin calls it, and it has to
# go through the allowlist -- otherwise `kind="handle"` is a way to hand
# the host an unchecked URL and hope something downstream fetches it.
_FETCHABLE_SCHEMES = frozenset(
    {"http", "https", "ftp", "ftps", "file", "data", "blob", "javascript", "ws", "wss"}
)


class StreamTarget(BaseModel):
    """Where a `stream` plugin says an item can be played.

    Deliberately opaque and deliberately thin. Streaming itself is
    `romm-stream`'s job, not the Hub's: this capability's whole contract is
    "resolve an item to something a player can be pointed at", so the host
    validates the answer and hands it over rather than building any
    transport of its own.

    `kind` is the discriminator that decides how the answer is treated:

      * `url`    -- the host checks it against the plugin's `network`
                    allowlist, exactly as it checks a FetchPlan URL, because
                    something will eventually fetch it.
      * `handle` -- an identifier for another service (`ia:rubik_202308`,
                    a room id, a session token). Never fetched by the host.

    A `handle` may therefore not *be* a URL. Without that rule the
    discriminator would be the hole: declare `kind="handle"`, put
    `https://evil.example/x` in `target`, and the allowlist check is
    skipped on a string that any consumer would treat as a URL anyway.
    """

    kind: Literal["url", "handle"]
    target: str = Field(min_length=1, max_length=_MAX_STREAM_TARGET_CHARS)
    mime_type: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=_MAX_ROM_NAME_CHARS)
    extra: dict[str, str] = Field(default_factory=dict)

    @field_validator("target")
    @classmethod
    def _no_control_characters(cls, v: str) -> str:
        # This string is printed, logged, and handed to another service. A
        # CR or LF in it is a header-splitting or log-forging primitive
        # depending on who consumes it, and no legitimate target has one.
        bad = sorted({c for c in v if ord(c) < 0x20 or ord(c) == 0x7F})
        if bad:
            raise ValueError(
                f"stream target must not contain control characters: "
                f"{[hex(ord(c)) for c in bad]}"
            )
        return v

    @model_validator(mode="after")
    def _a_handle_is_not_a_url(self) -> "StreamTarget":
        if self.kind != "handle":
            return self
        scheme = urlsplit(self.target).scheme.lower()
        if scheme in _FETCHABLE_SCHEMES:
            raise ValueError(
                f"a stream target of kind 'handle' must not be a {scheme!r} URL "
                f"-- declare kind='url' so the host can check it against the "
                f"plugin's network allowlist"
            )
        return self


# -- cores ---------------------------------------------------------------

# A core id is chosen by the plugin and typed by an operator
# (`rom-hub cores install <plugin> <core_id>`). It is compared, printed
# and logged; it is never a path component -- the files a core installs
# are named by the FetchPlan, which validates them as filenames already.
_CORE_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_MAX_CORE_ID_CHARS = 64

# An emulator catalogue, not a package index. Bounded like a FetchPlan's
# file list, and for the same reason: the host walks whatever it is given.
MAX_CORES_PER_PLUGIN = 256


class CoreArtifact(BaseModel):
    """One installable emulator core, as a plugin describes it.

    A description only. `CoreProvider.plan(core)` turns the operator's
    choice into a `FetchPlan`, and that is what the host acts on -- so the
    same allowlist and the same filename rules apply to a core download as
    to a ROM download, by reusing the same type rather than resembling it.
    """

    core_id: str = Field(min_length=1, max_length=_MAX_CORE_ID_CHARS)
    name: str = Field(min_length=1, max_length=200)
    version: str | None = Field(default=None, max_length=64)
    system: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("core_id")
    @classmethod
    def _identifier_only(cls, v: str) -> str:
        bad = sorted(set(v) - _CORE_ID_CHARS)
        if bad:
            raise ValueError(
                f"core_id contains characters that are not permitted in an "
                f"identifier: {bad!r}"
            )
        return v
