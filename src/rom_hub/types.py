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

# The one prose field RomM actually *stores*, and the reason this model
# grew past "a title and a picture".
#
# `Body_update_rom_api_roms__id__put` declares `summary` alongside `name`,
# and unlike the eight `raw_*_metadata` fields -- which RomM 4.9.2 accepts
# with a 200 and then does not persist -- this one round-trips. Measured
# against a live RomM 4.9.2 on 2026-08-01:
#
#   PUT summary="A probe summary written by rom-hub."  -> 200
#   GET /api/roms/{id}                                 -> that exact string
#   PUT name=<unchanged>, no summary part              -> 200, summary kept
#
# So it is a real column, it is partial-update-safe in the same way `name`
# is, and it is where a source's release date, developer, publisher, genre
# and player count can reach an operator's library. Nothing else on that
# endpoint can carry them: `metadatum` (genres, companies, first_release_
# date, player_count) is populated by RomM's own providers and has no form
# field at all.
#
# Bounded because a plugin composes it. RomM's column is TEXT and would
# take far more; a summary is a paragraph, and a plugin that sends a
# megabyte of one is a plugin that has gone wrong.
MAX_SUMMARY_CHARS = 8 * 1024

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

    **What is deliberately not here.** RomM's update endpoint also takes
    `fs_name`, `url_cover` and `url_manual`, and none of the three is a
    field a plugin gets to set. `fs_name` renames the file on disk, which
    is not a metadata edit at all. `url_cover` and `url_manual` are worse
    than they look: measured against RomM 4.9.2, writing `url_cover` makes
    *RomM* go and fetch that URL server-side -- `path_cover_large` went
    from "" to a stored image seconds later -- so a plugin-named URL would
    be fetched by a process the Hub does not control, bypassing both the
    allowlist check and `MAX_ARTWORK_BYTES`. The Hub fetching the cover
    itself, which is what `artwork_url` already does, is strictly the
    safer arrangement and produces the same stored cover.

    Structured metadata is not here either, and not for want of trying.
    RomM keeps genres, companies, `first_release_date`, `player_count` and
    `average_rating` in a `metadatum` sub-object that its own providers
    populate; `PUT /api/roms/{id}` has no form field for any of it, and a
    part named `genres` is accepted with a 200 and discarded (measured).
    `summary` is the one prose field that survives, so a source's release
    date, developer and genre reach the library through that or not at
    all. Each plugin's README says which of its data cannot arrive.
    """

    name: str | None = Field(
        default=None, min_length=1, max_length=_MAX_ROM_NAME_CHARS
    )
    #: A description of the game. Absent means leave RomM's alone; see
    #: MAX_SUMMARY_CHARS for what was measured about this field.
    summary: str | None = Field(
        default=None, min_length=1, max_length=MAX_SUMMARY_CHARS
    )
    provider_ids: dict[str, int | str] = Field(default_factory=dict)
    raw_metadata: dict[str, dict | list] = Field(default_factory=dict)
    artwork_url: str | None = None
    artwork_base64: str | None = None
    artwork_filename: str = DEFAULT_ARTWORK_FILENAME

    @field_validator("summary", mode="before")
    @classmethod
    def _summary_is_prose(cls, v):
        """Trim, and refuse a summary that is only whitespace.

        A source with no description should leave the field absent, which
        means "leave RomM's alone". A plugin that instead forwards the
        empty string it found would write a blank over whatever an
        operator (or IGDB) had put there -- the exact erasure the absent/
        empty distinction exists to prevent, arriving through the one
        field where a source legitimately has nothing to say quite often.
        """
        if isinstance(v, str):
            trimmed = v.strip()
            if not trimmed:
                raise ValueError(
                    "summary is blank; leave it unset to keep RomM's "
                    "existing description rather than erasing it"
                )
            return trimmed
        return v

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
        if self.summary is not None:
            fields["summary"] = self.summary
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


# -- firmware ------------------------------------------------------------

# A firmware id is chosen by the plugin and typed by an operator
# (`rom-hub firmware install <plugin> <firmware_id>`). Compared, printed
# and logged; never a path component -- the files a firmware item installs
# are named by its FetchPlan, which validates them as filenames already.
_FIRMWARE_ID_CHARS = _CORE_ID_CHARS
_MAX_FIRMWARE_ID_CHARS = 64

# A BIOS shelf, not a package index. Bounded like a FetchPlan's file list
# and a core catalogue, and for the same reason: the host walks whatever
# it is given.
MAX_FIRMWARE_PER_PLUGIN = 256

# How many files one firmware item may unpack out of an archive. A console
# generation's boot ROMs, not a filesystem.
MAX_FIRMWARE_MEMBERS = 16

# Nobody's licence statement is a paragraph, and this string is printed in
# a column an operator reads while deciding what to install.
_MAX_LICENSE_CHARS = 200


class FirmwareArtifact(BaseModel):
    """One installable BIOS/firmware file set, as a plugin describes it.

    A description only, exactly like `CoreArtifact`:
    `FirmwareProvider.plan(firmware)` turns the operator's choice into a
    `FetchPlan`, and that is what the host acts on -- so the same allowlist
    and the same filename rules apply to a BIOS download as to a ROM
    download, by *reusing* the same type rather than resembling it.

    Two fields are required here that are optional on a `CoreArtifact`,
    and both are required because firmware is not a core.

    **`platform`.** Firmware is keyed by platform: a PlayStation BIOS is
    meaningless filed under Game Boy, and the backend upload needs an
    actual platform id. A core, by contrast, is a shared library that the
    operator points an emulator at; its `system` is a label. So this one
    is mandatory, and a plugin that cannot name the platform for an item
    must not offer that item.

    **`license`.** The entire value of a firmware source is knowing what
    you are allowed to have. Emulation firmware is the one artifact class
    where "where did this come from" is the first question and the answer
    is usually "a dumped console" -- so RPP makes the plugin *state* it,
    on every item, in a field the CLI prints. A required field cannot make
    a plugin honest; it can make silence impossible, which is the part a
    type system can do.

    `archive`/`members` exist because the open firmware that is actually
    published is published inside zips. SameBoy's boot ROMs, for one, ship
    only inside its emulator release. A plugin declares the members it
    wants and the *host* unpacks exactly those, by full-name equality,
    into destinations it chose itself -- see `rom_hub.firmware`.
    """

    firmware_id: str = Field(min_length=1, max_length=_MAX_FIRMWARE_ID_CHARS)
    name: str = Field(min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=64)
    license: str = Field(min_length=1, max_length=_MAX_LICENSE_CHARS)
    version: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1000)
    archive: Literal["zip"] | None = None
    members: list[str] = Field(default_factory=list, max_length=MAX_FIRMWARE_MEMBERS)

    @field_validator("firmware_id")
    @classmethod
    def _identifier_only(cls, v: str) -> str:
        bad = sorted(set(v) - _FIRMWARE_ID_CHARS)
        if bad:
            raise ValueError(
                f"firmware_id contains characters that are not permitted in "
                f"an identifier: {bad!r}"
            )
        return v

    @field_validator("members")
    @classmethod
    def _bare_names_only(cls, v: list[str]) -> list[str]:
        # Each of these becomes a file the host opens for writing, so it
        # gets the validator a FetchPlan filename gets -- the same one, not
        # a second copy of the rule.
        for name in v:
            bare_filename(name)
        folded = [name.casefold() for name in v]
        duplicated = sorted({n for n in folded if folded.count(n) > 1})
        if duplicated:
            raise ValueError(
                f"every member of a firmware archive needs a distinct name; "
                f"these are repeated: {duplicated!r}"
            )
        return v

    @model_validator(mode="after")
    def _members_belong_to_an_archive(self) -> "FirmwareArtifact":
        if self.archive is None and self.members:
            raise ValueError(
                "members were declared without an archive; a member only "
                "means something inside one"
            )
        if self.archive is not None and not self.members:
            raise ValueError(
                f"archive = {self.archive!r} was declared with no members; "
                f"name the files inside it that should be installed, so the "
                f"host never has to decide what a zip was for"
            )
        return self


# -- assets --------------------------------------------------------------

#: An asset id is chosen by the plugin and typed by an operator
#: (`rom-hub assets install <plugin> <asset_id>`). Compared, printed and
#: logged; never a path component -- the files an asset installs are named
#: by its FetchPlan, which validates them as filenames already.
#:
#: An asset id is a **path of bare filenames** within a source tree
#: (`udev/8BitDo_ Wired_Xbox.cfg`, `cht/Nintendo - Game Boy/Tetris.cht`),
#: so what it may contain is defined as exactly that: whatever a filename
#: may contain, plus the separator that joins them.
#:
#: Derived from `_ALLOWED_PUNCTUATION` rather than spelled out again,
#: because the two cannot be allowed to drift. A hand-written list here
#: had already drifted once: it omitted `[` and `]`, which meant every
#: GoodTools-named cheat file in libretro-database -- `Super Mario Land 4
#: (J) [!].cht` -- was refused off the wire, and the catalogue would have
#: been silently short of exactly the files people look for.
#:
#: Alphanumerics are tested with `str.isalnum` for the same reason
#: `bare_filename` does: it is unicode-aware, and these repositories carry
#: Japanese and accented titles that an ASCII allowlist would drop.
#:
#: The separator is safe to admit here precisely because an asset id is
#: never joined onto a path: `install_asset` builds destinations only from
#: `FetchPlan` filenames, each of which goes through `bare_filename` and
#: then `dest_in_job_dir`. A separator in this field reaches no filesystem
#: call. `..` is refused below all the same -- a value that cannot be a
#: traversal should not be able to look like one in a log.
_ASSET_ID_PUNCTUATION = _ALLOWED_PUNCTUATION | {"/"}
_MAX_ASSET_ID_CHARS = 200

#: A support-file catalogue, not a package index. Bounded like a core
#: catalogue and for the same reason -- the host walks whatever it is
#: given -- but larger, because these sources are naturally bigger: one
#: platform's cheat directory in libretro-database holds 2,265 files. A
#: plugin whose catalogue exceeds this is expected to say so and name the
#: config key that narrows it, rather than truncating silently.
MAX_ASSETS_PER_PLUGIN = 512

#: What kinds of support file RPP v1 knows, and therefore what the host is
#: able to choose an install directory for.
#:
#: This is the *host's* vocabulary, not a list of the plugins that happen
#: to exist. `shader` is here with no plugin behind it in this release:
#: the two libretro shader repositories were dropped on licensing (see
#: `docs/DESIGN.md`), but a shader is still a thing RetroArch has a
#: directory for, and a differently-licensed shader source should be able
#: to ship as a plugin without the host learning a new word first.
#:
#: Closed rather than free-text because each kind is a directory the host
#: picks. A plugin inventing `kind = "config"` would be asking the host to
#: invent a destination for it, and "somewhere sensible" is not a
#: destination anybody can audit.
KNOWN_ASSET_KINDS = ("shader", "overlay", "cheat", "controller")


class AssetArtifact(BaseModel):
    """One installable emulator support file, as a plugin describes it.

    A description only, exactly like `CoreArtifact` and `FirmwareArtifact`:
    `AssetProvider.plan(asset)` turns the operator's choice into a
    `FetchPlan`, and that is what the host acts on -- so the same allowlist
    and the same filename rules apply to a shader, a bezel, a cheat file or
    a controller profile as to a ROM download, by *reusing* the same type
    rather than resembling it.

    Two required fields carry the weight here.

    **`kind`.** This is what the host maps to an install directory, and it
    is the reason these four things are one capability instead of four.
    Shaders, overlays, cheats and controller profiles differ in exactly one
    respect that the Hub cares about -- which directory an emulator reads
    them from -- and that is a lookup, not four code paths. A closed
    vocabulary because the host must be able to choose a destination for
    every value; see `KNOWN_ASSET_KINDS`.

    **`license`.** Required, for the reason `FirmwareArtifact.license` is
    required. Every source behind this capability is a community
    repository of contributed files, which is precisely the situation
    where "may I have this, and on what terms?" has a real answer that
    varies -- and two of the five sources originally surveyed for this
    capability were dropped because that answer could not be established.
    A required field cannot make a plugin honest; it can make silence
    impossible, and it puts the answer in a column the CLI prints.

    `system` is optional and free text: the console or platform this asset
    is for, when the plugin knows it. Unlike `FirmwareArtifact.platform` it
    is never resolved against a library, because nothing here is ever filed
    in one -- see `rom_hub.emuassets`.
    """

    asset_id: str = Field(min_length=1, max_length=_MAX_ASSET_ID_CHARS)
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["shader", "overlay", "cheat", "controller"]
    license: str = Field(min_length=1, max_length=_MAX_LICENSE_CHARS)
    system: str | None = Field(default=None, max_length=100)
    version: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=1000)
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("asset_id")
    @classmethod
    def _identifier_only(cls, v: str) -> str:
        bad = sorted(
            {c for c in v if not (c.isalnum() or c in _ASSET_ID_PUNCTUATION)}
        )
        if bad:
            raise ValueError(
                f"asset_id contains characters that are not permitted in an "
                f"asset identifier: {bad!r}"
            )
        # Not reachable as a filesystem write -- see `_ASSET_ID_CHARS` --
        # but an id that cannot traverse should not read like one either,
        # and this is the field an operator copies off a listing and types
        # back into a command.
        if ".." in v:
            raise ValueError("asset_id must not contain '..'")
        if v.startswith("/") or v.endswith("/"):
            raise ValueError("asset_id must not start or end with '/'")
        return v


# -- census: enumerating a source completely -----------------------------
#
# The other six capabilities answer "what can you give me for this query?".
# `census` answers a different question -- "what is *all* of it, and how do
# you know?" -- and the types below exist to make the second answer
# checkable rather than merely large.
#
# The whole design turns on one identity, enforced by `rom_hub.census`:
#
#     declared_total == kept + sum(skipped.values())
#
# Every entry the source itself declares is either catalogued or skipped
# for a *named* reason. A number that does not balance is reported as a
# shortfall rather than rounded into a coverage percentage, because a
# coverage percentage with no denominator behind it is exactly the claim
# this capability exists to stop making.

#: How many units one `scope()` may describe. A unit is an item, a
#: directory, a shard -- something the source itself treats as a container
#: -- so this is generous for anything shaped like a real source and still
#: a bound on work an untrusted process can ask the host to do.
MAX_CENSUS_UNITS = 4096

#: How many records one `enumerate()` page may carry. A page has to cross
#: the JSON-RPC channel, which caps at `protocol.MAX_MESSAGE_CHARS` (8 MiB)
#: -- at a realistic ~250 characters per record that frame holds roughly
#: 33,000, so this sits comfortably under it. A plugin with more to say
#: returns a cursor and is called again.
MAX_CENSUS_RECORDS_PER_PAGE = 10000

#: What a unit *is*, in the host's vocabulary. Closed, because the host
#: prints these, filters on them, and decides default scope with them --
#: an invented kind would be an unreviewable exclusion.
#:
#: `roms`      one entry per game: the shape a library can actually import.
#: `pack`      archives-of-archives. `NoIntroROMsCollection` is 67 files
#:             and 44.8 GB; each file is a whole set. Catalogued as what it
#:             is rather than pretended to be 67 games.
#: `cdn-dump`  a console maker's distribution tree, mirrored whole.
#:             `nointro_wiiu_cdn_nov_2020_2` is 928 GB.
#: `media`     soundtracks, screenshots, videos, box scans -- real files,
#:             not ROMs.
#: `dat`       DAT/XML/checksum files: the No-Intro *catalogue*, not its
#:             contents.
#: `other`     enumerated and classified as nothing in particular. Never a
#:             silent bucket: it is printed with its own count.
KNOWN_CENSUS_KINDS = ("roms", "pack", "cdn-dump", "media", "dat", "other")

_MAX_UNIT_ID_CHARS = 400
_MAX_RECORD_ID_CHARS = 600
_MAX_CURSOR_CHARS = 400
_MAX_REASON_CHARS = 500


class CensusUnit(BaseModel):
    """One container inside a source, and what the source says is in it.

    `declared_total` is the load-bearing field and it is deliberately the
    source's own number, not the plugin's estimate. For an Archive.org item
    it is `files_count` off `advancedsearch.php`; for a directory listing it
    is however many entries the index itself declares. The point is that it
    is obtained *independently of the walk*, so "we enumerated 820" can be
    checked against something the walk did not produce. A plugin that
    genuinely cannot get one sends `None`, and the host reports that unit's
    completeness as unknown rather than assuming it.

    `include=False` is how a plugin proposes leaving a unit out, and
    `reason` is required when it does. Excluding a 928 GB CDN dump is a
    perfectly good decision; excluding it silently is the thing this field
    exists to prevent.
    """

    unit_id: str = Field(min_length=1, max_length=_MAX_UNIT_ID_CHARS)
    label: str = Field(min_length=1, max_length=500)
    kind: Literal["roms", "pack", "cdn-dump", "media", "dat", "other"] = "other"
    declared_total: int | None = Field(default=None, ge=0)
    platform: str | None = Field(default=None, max_length=100)
    size_bytes: int | None = Field(default=None, ge=0)
    include: bool = True
    reason: str = Field(default="", max_length=_MAX_REASON_CHARS)
    extra: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _exclusions_must_be_explained(self) -> "CensusUnit":
        if not self.include and not self.reason.strip():
            raise ValueError(
                "a unit with include=False must carry a reason: an "
                "exclusion nobody can read is indistinguishable from a "
                "source the plugin failed to find"
            )
        return self


class CensusRecord(BaseModel):
    """One catalogued thing: a ROM, a pack file, a DAT.

    `extra` is where digests go, under exactly the keys
    `rom_hub.grouping` reads -- `sha256`, `sha1`, `md5`, `crc32`. That is
    not a convention this type invents; it is the reason cross-unit
    deduplication works at all here. Archive.org publishes md5, sha1 and
    crc32 for every file it holds, so two items mirroring the same ROM
    under different names collapse on *evidence* rather than on a title
    parse that happens to agree.
    """

    record_id: str = Field(min_length=1, max_length=_MAX_RECORD_ID_CHARS)
    title: str = Field(min_length=1, max_length=500)
    platform: str | None = Field(default=None, max_length=100)
    size_bytes: int | None = Field(default=None, ge=0)
    url: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class CensusPage(BaseModel):
    """One bounded slice of a unit's enumeration.

    `skipped` is what makes the page auditable: a mapping of *reason* to
    count, covering every entry this page walked past without cataloguing.
    Archive.org's six bookkeeping files per item are a skip reason; so is
    "not a ROM extension". The host adds `len(records) + sum(skipped)` to
    the unit's walked total and compares that with `declared_total` at the
    end -- so a plugin that quietly drops rows produces a visible shortfall
    instead of a smaller, tidier, wrong catalogue.

    `cursor` is opaque to the host and is handed straight back on the next
    call. `None` means the unit is exhausted.
    """

    records: list[CensusRecord] = Field(
        default_factory=list, max_length=MAX_CENSUS_RECORDS_PER_PAGE
    )
    cursor: str | None = Field(default=None, max_length=_MAX_CURSOR_CHARS)
    declared_total: int | None = Field(default=None, ge=0)
    skipped: dict[str, int] = Field(default_factory=dict)

    @field_validator("skipped")
    @classmethod
    def _counts_are_counts(cls, v: dict[str, int]) -> dict[str, int]:
        for reason, count in v.items():
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError(
                    f"skip reason {reason!r} must carry a non-negative "
                    f"integer count, got {count!r}"
                )
            if not reason.strip():
                raise ValueError("a skip reason must not be blank")
            if len(reason) > _MAX_REASON_CHARS:
                raise ValueError(
                    f"skip reason is {len(reason)} characters, over the "
                    f"{_MAX_REASON_CHARS} limit"
                )
        return v

    @property
    def walked(self) -> int:
        """Entries this page accounted for: kept plus skipped."""
        return len(self.records) + sum(self.skipped.values())
