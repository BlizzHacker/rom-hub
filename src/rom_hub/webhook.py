"""What a GG Requestz game request means, and what the Hub does about it.

GG Requestz is a request front end: somebody asks for a game, an operator
approves it, and it POSTs a webhook to whatever `REQUEST_WEBHOOK_URL`
names. That side is merged upstream and is not going to change, so this
module is written to *its* contract rather than to a contract of our own
choosing -- including the parts of that contract that are unhelpful (no
signature, no auth header, a five-second deadline). See
`rom_hub.webhook_server` for the half that owns the socket.

Three decisions live here, and each one is a refusal to guess.

**A request is keyed on `request_id`, and a repeat is a no-op.** GG
Requestz re-dispatches when an already-fulfilled request is re-opened and
again on re-approval, so "the same request arrives twice" is the normal
case rather than the exceptional one. The claim is a single
`INSERT ... ON CONFLICT DO NOTHING` against a sqlite primary key, which
makes the second arrival a no-op even when it lands while the first is
still importing. `forget()` is the deliberate undo, because a request
that ended `NO_MATCH` is one an operator may well want to retry after
installing another plugin -- and without it their only option would be a
new request id.

**A near-miss is not a match.** The rule this whole module exists to
obey: a wrong ROM in somebody's library is worse than an unfulfilled
request. So a title has to match *exactly* under `romnames.
normalise_title` -- the same normaliser the search listing already groups
by, not a second opinion invented here -- or the request is recorded as
`NO_MATCH` and nothing is imported. An `igdb_id` decides when a source
states one, because an id is an identifier and a title is a string two
different games can share. Two candidate platforms with nothing to choose
between them is also a refusal, not a coin toss.

**A platform name is translated, never invented.** GG Requestz sends IGDB
platform *names* (`"Super Nintendo"`); every search plugin takes a RomM
*slug* (`snes`). `PLATFORM_NAMES` below is that translation, and its
targets are checked against `rom_hub.playability`'s own slug vocabulary by
`tests/test_webhook.py` so a typo here cannot become a search filter no
plugin recognises. A name with no entry is reported to the operator and
the search runs unfiltered -- narrowing on a guess would silently exclude
the right answer.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .grouping import group_results, platform_key
from .jobs import JobState
from .playability import EJS_CORES, EJS_NIGHTLY_CORES, NO_EQUIVALENT
from .romnames import normalise_title
from .types import SearchResult

#: How many platforms one request may narrow the search to. Each one is a
#: separate fan-out across every installed plugin, so an IGDB entry listing
#: fifteen platforms would otherwise mean fifteen full searches for one
#: request. The ones that do not fit are reported, never silently dropped.
MAX_PLATFORMS = 4

#: How many titles a refusal names when it found something but not the
#: right thing. Enough to show the operator what the sources actually
#: offered; not so many that the log column becomes a search listing.
NEAR_MISSES_SHOWN = 3

#: Request types this receiver will act on unless an operator widens it.
#: `update` and `fix` are complaints about a rom that is *already* in the
#: library -- the Hub cannot patch one, and importing a second copy is not
#: what was asked for. See `fulfil`.
DEFAULT_FULFIL_TYPES = ("game",)

#: Every request type GG Requestz can send, so a typo in the operator's
#: configuration is refused rather than quietly matching nothing.
KNOWN_REQUEST_TYPES = ("game", "update", "fix")

#: Shortest URL token the receiver will run with. The URL is the *entire*
#: gate -- GG Requestz sends no signature and no auth header -- so a
#: six-character one is not a configuration choice, it is an open door. 24
#: characters is `secrets.token_urlsafe(16)` rounded down.
TOKEN_MIN_LENGTH = 24


class WebhookPayloadError(Exception):
    """A request body could not be read as a GG Requestz event."""


class WebhookConfigError(Exception):
    """The receiver is configured in a way it will not start with.

    Its own type rather than a `SystemExit` or a bare `ValueError`, so
    `cli.main` can turn it into the one stderr line every other refusal in
    this project gets. A receiver misconfigured by one character is the
    commonest way this feature will go wrong.
    """


class FulfilmentRefused(Exception):
    """The chosen result cannot be imported, and the reason is already a
    sentence. `fulfil` records it verbatim rather than wrapped in a type
    name, because there is nothing to add to it."""


class WeakToken(WebhookConfigError):
    """The configured URL secret is too short to be the only gate there is."""


def check_token(token: str) -> str:
    """The token, stripped, or `WeakToken` if it cannot do the job.

    One function so that the CLI's configuration reader and the server's
    constructor cannot disagree about what an acceptable token is. Both
    call it: the CLI so the refusal arrives before a backend connection is
    attempted, the server so a caller that bypassed the CLI still cannot
    bind an endpoint behind a four-character secret.
    """
    token = (token or "").strip()
    if len(token) < TOKEN_MIN_LENGTH:
        raise WeakToken(
            f"the webhook token is {len(token)} character(s); it must be at "
            f"least {TOKEN_MIN_LENGTH}, because the URL is the only thing "
            f"standing between this endpoint and anyone who can reach it. "
            f'Generate one with: python -c "import secrets; '
            f'print(secrets.token_urlsafe(32))"'
        )
    return token


# --- the payload --------------------------------------------------------


class RequestEvent(BaseModel):
    """The `data` object of a GG Requestz `game_request` webhook.

    Deliberately lenient about everything it does not need and strict
    about the two things it cannot work without. The sender is merged
    upstream and may grow fields; a receiver that 400s on an unrecognised
    key would break on somebody else's release.

    `igdb_id` is typed as a string because that is what the documented
    payload sends, and coerced from a number because nothing in that
    contract stops the sender switching. It is an opaque key here -- it is
    only ever compared with what a search result states, never parsed.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    request_id: str = Field(min_length=1)
    game_title: str = Field(min_length=1)
    igdb_id: str | None = None
    platforms: list[str] = Field(default_factory=list)
    request_type: str = "game"
    user_id: str | None = None

    @field_validator("igdb_id", "user_id", mode="before")
    @classmethod
    def _blank_is_absent(cls, v):
        # `"igdb_id": ""` and `"igdb_id": null` mean the same thing to the
        # sender, and must mean the same thing here -- otherwise matching
        # would look for a source stating an id of the empty string.
        if v is None:
            return None
        if isinstance(v, bool):
            # `True` would otherwise become the id "True".
            raise ValueError("expected a string or a number, not a boolean")
        if isinstance(v, (int, float)):
            return str(v if isinstance(v, int) else int(v))
        text = str(v).strip()
        return text or None

    @field_validator("platforms", mode="before")
    @classmethod
    def _platforms_is_a_list(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            # A bare string is not "one platform": accepting it would mean
            # guessing that the sender changed its mind about the shape,
            # and `"Super Nintendo"` would silently become a list of one.
            raise ValueError("platforms must be an array, not a string")
        return v


def parse_event(raw: bytes) -> RequestEvent:
    """Read a webhook body into a `RequestEvent`.

    Every failure is a `WebhookPayloadError` with a sentence fit for a
    400 response body, because the sender's log is the only place an
    operator will see it.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WebhookPayloadError(f"body is not valid UTF-8: {exc}") from exc
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise WebhookPayloadError(f"body is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise WebhookPayloadError(
            f"body is a JSON {type(document).__name__}, not an object"
        )
    data = document.get("data")
    if not isinstance(data, dict):
        raise WebhookPayloadError(
            "the payload has no `data` object; a GG Requestz game_request "
            "carries request_id, game_title, igdb_id, platforms and "
            "request_type inside `data`"
        )
    try:
        return RequestEvent.model_validate(data)
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(str(part) for part in error["loc"]) or "(payload)"
            for error in exc.errors()
        )
        raise WebhookPayloadError(
            f"the payload's `data` object is not a game request: {fields} "
            f"({exc.error_count()} problem(s))"
        ) from exc


# --- platform names -----------------------------------------------------

#: Every platform slug the Hub knows about, taken from
#: `rom_hub.playability` rather than restated. That module's three tables
#: are RomM's own platform vocabulary -- the cores map, the nightly cores
#: map, and the "no core exists" list -- so it is the authority on which
#: slugs are real, and a second list here would be a second thing to keep
#: in step with RomM.
KNOWN_SLUGS: frozenset[str] = frozenset(EJS_CORES) | frozenset(
    EJS_NIGHTLY_CORES
) | frozenset(NO_EQUIVALENT)

#: RomM slug -> the names IGDB and GG Requestz spell it with.
#:
#: Written this way round because that is how it is *maintained*: a slug
#: gains a spelling far more often than a spelling changes slug. The
#: lookup table is inverted once, below, through the same normaliser the
#: search listing groups titles with -- so `"Sega Mega Drive/Genesis"`,
#: `"sega mega drive genesis"` and `"SEGA MEGA DRIVE / GENESIS"` are one
#: key and only one entry is needed for all three.
#:
#: Not exhaustive, and honest about it: a name with no entry is reported
#: rather than guessed at, and adding one is adding a string here.
PLATFORM_NAMES: dict[str, tuple[str, ...]] = {
    "3do": ("3DO Interactive Multiplayer", "3DO", "Panasonic 3DO"),
    "3ds": ("Nintendo 3DS", "3DS"),
    "acpc": ("Amstrad CPC", "Amstrad CPC 464", "Amstrad CPC 6128"),
    "amiga": ("Amiga", "Commodore Amiga"),
    "amiga-cd32": ("Amiga CD32", "Commodore CD32"),
    "amstrad-gx4000": ("Amstrad GX4000",),
    "android": ("Android",),
    "appleii": ("Apple II", "Apple ][", "Apple 2"),
    "arcade": ("Arcade",),
    "atari-st": ("Atari ST/STE", "Atari ST", "Atari STE"),
    "atari2600": ("Atari 2600", "Atari VCS"),
    "atari5200": ("Atari 5200",),
    "atari7800": ("Atari 7800",),
    "atari8bit": ("Atari 8-bit", "Atari 800", "Atari XE"),
    "browser": ("Web browser", "Browser"),
    "c-plus-4": ("Commodore Plus/4", "Commodore 16", "Plus/4"),
    "c128": ("Commodore 128", "Commodore C128"),
    "c64": ("Commodore C64/128/MAX", "Commodore 64", "C64"),
    "colecovision": ("ColecoVision",),
    "cpet": ("Commodore PET", "PET"),
    "dc": ("Dreamcast", "Sega Dreamcast"),
    "doom": ("Doom",),
    "dos": ("DOS", "MS-DOS", "PC DOS"),
    "fairchild-channel-f": ("Fairchild Channel F", "Channel F"),
    "famicom": ("Family Computer", "Famicom"),
    "fds": ("Family Computer Disk System", "Famicom Disk System"),
    "game-dot-com": ("Game.com", "Tiger Game.com"),
    "gamegear": ("Sega Game Gear", "Game Gear"),
    "gb": ("Game Boy", "Nintendo Game Boy"),
    "gba": ("Game Boy Advance", "Nintendo Game Boy Advance", "GBA"),
    "gbc": ("Game Boy Color", "Nintendo Game Boy Color", "GBC"),
    "genesis": (
        "Sega Mega Drive/Genesis",
        "Sega Genesis",
        "Sega Mega Drive",
        "Genesis",
        "Mega Drive",
    ),
    "intellivision": ("Intellivision", "Mattel Intellivision"),
    "jaguar": ("Atari Jaguar", "Jaguar"),
    "linux": ("Linux",),
    "lynx": ("Atari Lynx", "Lynx"),
    "mac": ("Mac", "macOS", "Mac OS", "Apple Macintosh"),
    "msx": ("MSX", "MSX2"),
    "n64": ("Nintendo 64", "N64"),
    "nds": ("Nintendo DS", "NDS"),
    "neo-geo-pocket": ("Neo Geo Pocket", "SNK Neo Geo Pocket"),
    "neo-geo-pocket-color": ("Neo Geo Pocket Color", "SNK Neo Geo Pocket Color"),
    "neogeoaes": ("Neo Geo AES", "Neo Geo", "SNK Neo Geo AES"),
    "neogeomvs": ("Neo Geo MVS", "SNK Neo Geo MVS"),
    "nes": ("Nintendo Entertainment System", "NES"),
    "new-nintendo-3ds": ("New Nintendo 3DS",),
    "ngc": ("Nintendo GameCube", "GameCube"),
    "nuon": ("Nuon",),
    "odyssey-2": ("Odyssey 2", "Magnavox Odyssey 2", "Philips Videopac G7000"),
    "pc-fx": ("PC-FX", "NEC PC-FX"),
    "philips-cd-i": ("Philips CD-i", "CD-i", "CDi"),
    "pokemon-mini": ("Pokemon mini", "Nintendo Pokemon mini"),
    "ps2": ("PlayStation 2", "PS2"),
    "ps3": ("PlayStation 3", "PS3"),
    "psp": ("PlayStation Portable", "PSP"),
    "psx": ("PlayStation", "Sony PlayStation", "PlayStation 1", "PS1", "PSX"),
    "saturn": ("Sega Saturn", "Saturn"),
    "scummvm": ("ScummVM",),
    "sega32": ("Sega 32X", "Sega Mega Drive 32X", "32X"),
    "segacd": ("Sega CD", "Mega-CD", "Sega Mega-CD"),
    "sfam": ("Super Famicom", "Nintendo Super Famicom"),
    "sg1000": ("SG-1000", "Sega SG-1000"),
    "sharp-x68000": ("Sharp X68000", "X68000"),
    "sms": ("Sega Master System/Mark III", "Sega Master System", "Master System"),
    "snes": (
        "Super Nintendo",
        "Super Nintendo Entertainment System",
        "SNES",
        "Super NES",
    ),
    "supergrafx": ("PC Engine SuperGrafx", "SuperGrafx"),
    "supervision": ("Watara/QuickShot Supervision", "Supervision"),
    "tg16": ("TurboGrafx-16/PC Engine", "TurboGrafx-16", "PC Engine", "TurboGrafx"),
    "trs-80-color-computer": ("TRS-80 Color Computer", "TRS-80 CoCo"),
    "turbografx-cd": (
        "Turbografx-16/PC Engine CD",
        "PC Engine CD",
        "TurboGrafx-CD",
    ),
    "vectrex": ("Vectrex",),
    "vic-20": ("Commodore VIC-20", "VIC-20"),
    "virtualboy": ("Virtual Boy", "Nintendo Virtual Boy"),
    "wii": ("Wii", "Nintendo Wii"),
    "win": ("PC (Microsoft Windows)", "Windows", "Microsoft Windows"),
    "wonderswan": ("WonderSwan", "Bandai WonderSwan"),
    "wonderswan-color": ("WonderSwan Color", "Bandai WonderSwan Color"),
    "xbox": ("Xbox", "Microsoft Xbox"),
    "xbox360": ("Xbox 360", "Microsoft Xbox 360"),
    "zx81": ("Sinclair ZX81", "ZX81"),
    "zxs": ("Sinclair ZX Spectrum", "ZX Spectrum", "Spectrum"),
}

#: normalised platform name -> slug. Inverted once at import.
PLATFORM_ALIASES: dict[str, str] = {
    normalise_title(name): slug
    for slug, names in PLATFORM_NAMES.items()
    for name in names
}


def platform_slugs(names) -> tuple[list[str], list[str]]:
    """`(slugs to search, names that could not be used)`.

    Two lists rather than one, because the second is not an error the
    caller should swallow: an operator whose requests all come back
    `NO_MATCH` because IGDB started spelling a platform differently needs
    to see the name that did not translate. Names beyond `MAX_PLATFORMS`
    are reported the same way -- unused is unused, whatever the reason.
    """
    slugs: list[str] = []
    unusable: list[str] = []
    for name in names or []:
        text = str(name).strip()
        if not text:
            continue
        # An operator (or a future GG Requestz) may send the slug itself.
        # Checked first so a name that is *also* a slug never depends on
        # the alias table being complete.
        direct = text.lower()
        slug = direct if direct in KNOWN_SLUGS else PLATFORM_ALIASES.get(
            normalise_title(text)
        )
        if slug is None:
            unusable.append(text)
        elif slug in slugs:
            continue
        elif len(slugs) >= MAX_PLATFORMS:
            unusable.append(text)
        else:
            slugs.append(slug)
    return slugs, unusable


# --- the log ------------------------------------------------------------


class RequestState(str, Enum):
    RECEIVED = "RECEIVED"
    SEARCHING = "SEARCHING"
    IMPORTING = "IMPORTING"
    FULFILLED = "FULFILLED"
    NO_MATCH = "NO_MATCH"
    FAILED = "FAILED"
    IGNORED = "IGNORED"


#: States that mean a worker was part-way through this request. If the
#: receiver restarts while a row sits in one, nothing is coming back to
#: finish it -- and unlike `rom_hub.jobs`, returning it to the pending
#: pool would achieve nothing, because a re-POST of the same request id is
#: a no-op by design. So it is failed with an actionable sentence instead.
_IN_FLIGHT = (RequestState.RECEIVED, RequestState.SEARCHING, RequestState.IMPORTING)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    game_title TEXT NOT NULL,
    igdb_id TEXT,
    platforms TEXT NOT NULL,
    request_type TEXT NOT NULL,
    user_id TEXT,
    state TEXT NOT NULL,
    detail TEXT,
    plugin TEXT,
    source_id TEXT,
    job_id INTEGER
)
"""


@dataclass
class RequestRow:
    """One request, as the log recorded it."""

    request_id: str
    received_at: str
    game_title: str
    igdb_id: str | None
    platforms: list[str]
    request_type: str
    user_id: str | None
    state: RequestState
    detail: str | None = None
    plugin: str | None = None
    source_id: str | None = None
    job_id: int | None = None

    def as_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "received_at": self.received_at,
            "game_title": self.game_title,
            "igdb_id": self.igdb_id,
            "platforms": list(self.platforms),
            "request_type": self.request_type,
            "user_id": self.user_id,
            "state": self.state.value,
            "detail": self.detail,
            "plugin": self.plugin,
            "source_id": self.source_id,
            "job_id": self.job_id,
        }


def _row(row: sqlite3.Row) -> RequestRow:
    raw = row["platforms"] or ""
    return RequestRow(
        request_id=row["request_id"],
        received_at=row["received_at"],
        game_title=row["game_title"],
        igdb_id=row["igdb_id"],
        platforms=[part for part in raw.split("\n") if part],
        request_type=row["request_type"],
        user_id=row["user_id"],
        state=RequestState(row["state"]),
        detail=row["detail"],
        plugin=row["plugin"],
        source_id=row["source_id"],
        job_id=row["job_id"],
    )


class RequestLog:
    """Which requests have been seen, and what came of each.

    sqlite for the same reason `rom_hub.jobs` is sqlite: the state has to
    survive a restart, or the idempotency guarantee lasts exactly as long
    as the process does and every restart re-imports whatever GG Requestz
    re-sends.

    **This is the one place in the project where two threads share a
    connection.** The acceptor thread claims a request id before answering
    202; the worker thread updates the same row minutes later. So the
    connection is opened with `check_same_thread=False` and every
    statement is taken under a lock -- not decoration, and not something
    to remove because "sqlite serialises writes anyway": Python's sqlite3
    module raises `ProgrammingError` on cross-thread use regardless of
    what the C library would have done.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self._db_path), isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "RequestLog":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def claim(self, event: RequestEvent, now: str | None = None) -> bool:
        """Record `event` as newly seen. False if this id was already here.

        The whole duplicate defence, in one statement. `ON CONFLICT DO
        NOTHING` means a second arrival cannot overwrite what the first
        one concluded -- which matters, because GG Requestz re-sends the
        *original* payload on a re-approval and a plain INSERT OR REPLACE
        would reset a `FULFILLED` row to `RECEIVED` and import again.
        """
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO requests (request_id, received_at, game_title, "
                "igdb_id, platforms, request_type, user_id, state) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(request_id) DO NOTHING",
                (
                    event.request_id,
                    now or _now(),
                    event.game_title,
                    event.igdb_id,
                    "\n".join(event.platforms),
                    event.request_type,
                    event.user_id,
                    RequestState.RECEIVED.value,
                ),
            )
            return cur.rowcount == 1

    def begin(
        self,
        request_id: str,
        state: RequestState,
        plugin: str | None = None,
        source_id: str | None = None,
    ) -> None:
        """Move a request to an in-flight state, optionally naming the
        source that was chosen.

        The source is written *before* the import rather than after it,
        for the reason `jobs.set_notes` gives: the choice is true from the
        moment it is made, and a request that fails half way through an
        import should still say what it was importing.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET state = ? WHERE request_id = ?",
                (state.value, request_id),
            )
            if plugin is not None or source_id is not None:
                self._conn.execute(
                    "UPDATE requests SET plugin = ?, source_id = ? "
                    "WHERE request_id = ?",
                    (plugin, source_id, request_id),
                )

    def finish(
        self,
        request_id: str,
        state: RequestState,
        detail: str,
        job_id: int | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE requests SET state = ?, detail = ? WHERE request_id = ?",
                (state.value, detail, request_id),
            )
            if job_id is not None:
                self._conn.execute(
                    "UPDATE requests SET job_id = ? WHERE request_id = ?",
                    (job_id, request_id),
                )

    def get(self, request_id: str) -> RequestRow | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM requests WHERE request_id = ?", (request_id,)
            ).fetchone()
        return _row(row) if row is not None else None

    def list(self, state: RequestState | None = None) -> list[RequestRow]:
        with self._lock:
            if state is None:
                rows = self._conn.execute(
                    "SELECT * FROM requests ORDER BY received_at, request_id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM requests WHERE state = ? "
                    "ORDER BY received_at, request_id",
                    (RequestState(state).value,),
                ).fetchall()
        return [_row(row) for row in rows]

    def forget(self, request_id: str) -> bool:
        """Drop a request so a re-approval is acted on. False if unknown.

        The deliberate undo of the duplicate guard, and the reason it
        exists: a request recorded `NO_MATCH` because no plugin covered
        the platform becomes fulfillable the moment one is installed, and
        without this the operator would have to invent a new request.
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM requests WHERE request_id = ?", (request_id,)
            )
            return cur.rowcount == 1

    def mark_interrupted(self) -> list[RequestRow]:
        """Fail every row a stopped worker left mid-flight, and return them."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT request_id FROM requests WHERE state IN "
                f"({','.join('?' for _ in _IN_FLIGHT)})",
                [s.value for s in _IN_FLIGHT],
            ).fetchall()
            ids = [row["request_id"] for row in rows]
            for request_id in ids:
                self._conn.execute(
                    "UPDATE requests SET state = ?, detail = ? WHERE request_id = ?",
                    (
                        RequestState.FAILED.value,
                        "the receiver stopped while this request was in flight, "
                        "so nothing finished it. Run 'rom-hub webhook forget "
                        f"{request_id}' and re-approve the request to try again.",
                        request_id,
                    ),
                )
        return [r for r in (self.get(i) for i in ids) if r is not None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- choosing what to import --------------------------------------------


@dataclass(frozen=True)
class Choice:
    """The one result to import, or the reason there is not one."""

    result: SearchResult | None = None
    how: str = ""
    refusal: str = ""


def _igdb_of(result: SearchResult) -> str | None:
    """The igdb id a search result states, if it states one.

    Plugins put whatever they like in `extra`, so the key is matched
    case-insensitively and under both spellings anything in this project
    has used. **No search plugin shipped in this repository emits one
    today** -- they index Archive.org items, homebrew releases and itch.io
    pages, none of which carry IGDB ids -- so in practice matching falls
    to the title. This is here because the sender always sends the id, it
    is the stronger key when it is available, and the alternative is
    ignoring it and then having to add it back with a migration.
    """
    extra = result.extra or {}
    for key, value in extra.items():
        if str(key).strip().lower() in ("igdb_id", "igdb", "igdb-id"):
            text = str(value).strip()
            if text:
                return text
    return None


def _stream_only(result: SearchResult) -> bool:
    """Whether this row is a play-in-the-browser handle, not a download.

    The same predicate `grouping.Variant.stream_only` applies, at the row
    level. Importing one cannot work: the plugin has no file to plan.
    """
    return (result.extra or {}).get("stream_only") == "true"


def _identity(result: SearchResult) -> tuple[str, str]:
    return (result.plugin or "", result.source_id)


def _importable(group, preferred: set[tuple[str, str]]) -> SearchResult | None:
    """The best downloadable row in `group`, preferring `preferred`.

    `group.variants` is already ranked best-dump-first by
    `grouping.variant_rank`, and each variant's results are already
    ordered -- so this is a filter over an existing ordering rather than a
    ranking of its own. Nothing here re-decides which dump is better.
    """
    rows = [
        result
        for variant in group.variants
        for result in variant.results
        if not _stream_only(result)
    ]
    if not rows:
        return None
    if preferred:
        for result in rows:
            if _identity(result) in preferred:
                return result
    return rows[0]


def choose(event: RequestEvent, results, requested: list[str]) -> Choice:
    """Which single result answers this request, or why none does.

    `requested` is the resolved platform slugs, used only to break a tie:
    it never *adds* a candidate, so a request whose platform did not
    translate is no more likely to import the wrong thing than one whose
    platform did.
    """
    groups = group_results(results, event.game_title)
    if not groups:
        return Choice(refusal="no source returned anything for this title")

    preferred: set[tuple[str, str]] = set()
    if event.igdb_id:
        preferred = {
            _identity(result)
            for group in groups
            for result in group.results
            if _igdb_of(result) == event.igdb_id
        }

    if preferred:
        how = f"igdb_id {event.igdb_id}"
        candidates = [
            group
            for group in groups
            if any(_identity(r) in preferred for r in group.results)
        ]
    else:
        how = "an exact title match"
        key = normalise_title(event.game_title)
        candidates = [group for group in groups if group.title_key == key]

    if not candidates:
        offered = ", ".join(
            repr(group.title) for group in groups[:NEAR_MISSES_SHOWN]
        )
        return Choice(
            refusal=(
                f"nothing matched {event.game_title!r} exactly, so nothing was "
                f"imported. {len(groups)} game(s) came back, including {offered}"
            )
        )

    if len(candidates) > 1 and requested:
        wanted = set(requested)
        narrowed = [
            group for group in candidates if platform_key(group.platform) in wanted
        ]
        if narrowed:
            candidates = narrowed

    if len(candidates) > 1:
        platforms = ", ".join(
            sorted(group.platform or "(not stated)" for group in candidates)
        )
        return Choice(
            refusal=(
                f"{event.game_title!r} matched on {len(candidates)} platforms "
                f"({platforms}) and the request did not say which, so nothing "
                f"was imported"
            )
        )

    group = candidates[0]
    chosen = _importable(group, preferred)
    if chosen is None:
        return Choice(
            refusal=(
                f"the only copies of {event.game_title!r} offered are "
                f"stream-only, which cannot be imported -- try "
                f"'rom-hub stream' instead"
            )
        )
    return Choice(result=chosen, how=how)


# --- fulfilment ---------------------------------------------------------


@dataclass
class Fulfilment:
    """What became of one request. Also what the log row now says."""

    state: RequestState
    detail: str
    job_id: int | None = None
    notes: list[str] = field(default_factory=list)


#: Import outcomes that mean the request was answered. `SKIPPED_DUPLICATE`
#: is one of them: the requester wanted the game in the library and it is
#: in the library. Reporting that as a failure would have an operator
#: chasing a problem that does not exist.
_ANSWERED = (JobState.DONE, JobState.SKIPPED_DUPLICATE)


def fulfil(
    event: RequestEvent,
    *,
    log: RequestLog,
    search,
    importer,
    fulfil_types=DEFAULT_FULFIL_TYPES,
) -> Fulfilment:
    """Search for `event`'s game and import it, recording every step.

    `search(query, platform)` returns a `dispatcher.SearchOutcome`;
    `importer(result)` returns an `importer.ImportResult`. Both are
    injected rather than built here, and that is the seam that makes this
    testable at all: the CLI supplies the real fan-out and the real
    pipeline, a test supplies fakes, and neither this function nor the
    tests need a plugin subprocess or a library server to decide whether
    the *decision* is right.

    Never raises. A request that could not be fulfilled is a recorded
    outcome, because the caller is a worker thread serving an HTTP
    endpoint and an exception there would take the receiver down with it.
    """
    if event.request_type not in tuple(fulfil_types):
        return _record(
            log,
            event,
            RequestState.IGNORED,
            f"this is a {event.request_type!r} request, not one of "
            f"{', '.join(fulfil_types)}. An {event.request_type!r} asks about a "
            f"rom that is already in the library, which the Hub cannot patch; "
            f"set ROM_HUB_WEBHOOK_TYPES to act on it anyway",
        )

    slugs, unusable = platform_slugs(event.platforms)
    notes: list[str] = []
    if unusable:
        notes.append(
            f"searched without a platform filter for {', '.join(unusable)}: no "
            f"Hub platform slug is known by that name"
            if not slugs
            else f"ignored the platform(s) {', '.join(unusable)}: no Hub "
            f"platform slug is known by those names"
        )

    log.begin(event.request_id, RequestState.SEARCHING)
    try:
        results, responded, total = _search_every_platform(
            search, event.game_title, slugs
        )
    except Exception as exc:  # noqa: BLE001 - a worker thread must not die
        return _record(
            log,
            event,
            RequestState.FAILED,
            f"the search failed: {type(exc).__name__}: {exc}",
            notes,
        )

    if total and responded < total:
        # The project's rule, applied here: a partial answer must say it is
        # partial. "Found nothing" and "found nothing, and two thirds of the
        # sources were down" are different events and lead an operator to
        # different actions.
        notes.append(f"{responded} of {total} sources responded")

    decision = choose(event, results, slugs)
    if decision.result is None:
        return _record(log, event, RequestState.NO_MATCH, decision.refusal, notes)

    chosen = decision.result
    log.begin(
        event.request_id,
        RequestState.IMPORTING,
        plugin=chosen.plugin or None,
        source_id=chosen.source_id,
    )
    try:
        outcome = importer(chosen)
    except FulfilmentRefused as exc:
        # Already a sentence written for an operator. Wrapping it in a type
        # name would be the mistake `importer._ImportFailure` exists to
        # avoid: "unexpected FulfilmentRefused during import" tells nobody
        # anything the message did not already say better.
        return _record(log, event, RequestState.FAILED, str(exc), notes)
    except Exception as exc:  # noqa: BLE001 - a worker thread must not die
        return _record(
            log,
            event,
            RequestState.FAILED,
            f"importing {chosen.source_id!r} from {chosen.plugin or '?'} raised "
            f"{type(exc).__name__}: {exc}",
            notes,
        )

    answered = outcome.state in _ANSWERED
    detail = (
        f"{chosen.title!r} from {chosen.plugin or '?'} "
        f"({chosen.source_id}, matched by {decision.how}): {outcome.message}"
    )
    for warning in getattr(outcome, "warnings", ()) or ():
        notes.append(warning)
    return _record(
        log,
        event,
        RequestState.FULFILLED if answered else RequestState.FAILED,
        detail,
        notes,
        job_id=getattr(outcome, "job_id", None),
    )


def _search_every_platform(search, query, slugs):
    """Fan out once per requested platform, or once with no filter at all.

    One call per slug rather than one unfiltered call filtered afterwards,
    because `platform` is a real argument to a plugin's `search()`: the
    archive-org plugin turns it into a different query and refuses
    outright for a platform it files nothing under. Filtering our end
    would throw away rows that never had a platform stated and keep rows
    the source would not have offered.
    """
    results: list[SearchResult] = []
    responded = 0
    total = 0
    for platform in slugs or [None]:
        outcome = search(query, platform)
        results.extend(outcome.results)
        responded += outcome.responded
        total += outcome.total
    return results, responded, total


def _record(
    log: RequestLog,
    event: RequestEvent,
    state: RequestState,
    detail: str,
    notes=None,
    job_id: int | None = None,
) -> Fulfilment:
    notes = list(notes or [])
    full = "; ".join([detail, *notes])
    log.finish(event.request_id, state, full, job_id=job_id)
    return Fulfilment(state=state, detail=full, job_id=job_id, notes=notes)
