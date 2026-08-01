"""Handing a validated stream target to something that can play it.

`stream` used to end at the host gate: `PluginProcess.resolve_stream()`
validated the plugin's answer and the CLI printed it. That is a contract,
not a capability -- an operator left holding a validated URL still had to
work out what to do with it.

This module is the host side, and it is deliberately the same shape as
`cores`, `firmware` and `emuassets`: the plugin *describes*, the host
*acts*, and what the host will act on is a closed set.

## What the host does

It resolves a validated `StreamTarget` into a **handover** -- the one
thing this target can be given to -- and then performs it if asked:

  * `browser`  -- a `url` target, opened in the operator's own browser.
                  This is real playback for the case that exists today:
                  an Archive.org `/details/` page runs Emularity in the
                  page, so opening it *is* playing the game.
  * `handoff`  -- a `handle` target. An identifier issued by some other
                  service. The Hub does not know which, will not guess,
                  and says so instead of inventing a URL around it.

`library_player_url` covers the second real case: a rom the operator's
library server already holds, which RomM serves an EmulatorJS player for
at `/rom/<id>/ejs`. That URL is built by the *host* from the operator's
own backend settings -- no plugin is involved and none could be, because
a plugin has never heard of the library's ids.

## What the host refuses to do

It does not build a transport. `romm-stream` is the streaming server and
nothing here becomes a second one: this module never allocates a display,
spawns an emulator, captures a framebuffer or moves a video byte.

It also does not start a `romm-stream` session, and that is a finding
rather than a decision. `romm-stream`'s session routes take either a
`platform` + `rom_name` naming a file under *its own* ROM directory, or a
`romm_rom_id` plus RomM credentials; `POST /api/stream/start`'s `url`
form is gated on a hardcoded origin allowlist. There is no route that
accepts an arbitrary resolved URL or an opaque handle, so a plugin's
target cannot be handed to it. What it *will* answer, read-only, is
whether a platform is playable there and on which tier -- see
`StreamServerClient`, which uses those two GETs and nothing else.

## Why the allowlist is checked again here

`broker.host.resolve_stream()` already checks a `url` target against the
plugin's `network` allowlist, at the boundary where the answer crosses
out of the subprocess. This module checks it again, immediately before
the URL is handed to a browser.

That is the `paths.dest_in_job_dir` pattern and it is here for the same
reason: the first check is the one that produces a legible error, the
second is the one that has to hold when the first has a gap. A
`StreamTarget` does not only ever arrive straight off the wire -- it can
be rebuilt from `--json` output, from a job record, from whatever queues
this later -- and the check that protects the operator is the one
standing immediately in front of the act.
"""

from __future__ import annotations

import webbrowser
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit

from rom_hub.netpolicy import PolicyViolation, check_url
from rom_hub.types import StreamTarget


class StreamError(Exception):
    """Resolving, routing or launching a stream target failed."""


#: A `url` target: something the operator's browser can open and play.
BROWSER = "browser"

#: A `handle` target: an identifier for a service the Hub does not know.
HANDOFF = "handoff"


@dataclass(frozen=True)
class Handover:
    """What the host decided a validated target can be handed to.

    Frozen because it is a decision, not a workspace: the URL inside it
    has been allowlist-checked, and a mutable field would let a caller
    swap it out between the check and the act.
    """

    route: Literal["browser", "handoff"]
    target: StreamTarget
    #: The URL to open, when there is one. `None` for a `handoff`.
    url: str | None
    #: One line an operator can act on, printed by the CLI.
    how: str
    #: Where this came from, for the JSON form. Set by the caller.
    source: str = ""

    @property
    def playable(self) -> bool:
        """True when the Hub itself can launch this."""
        return self.route == BROWSER and self.url is not None

    def as_dict(self) -> dict:
        """The machine-readable form `--json` prints.

        A launcher, a TV app or another Hub command is the intended
        consumer, so this is the target *plus* the host's decision about
        it -- a consumer that had to re-derive `route` from `kind` would
        be re-implementing the part that carries the security reasoning.
        """
        return {
            "route": self.route,
            "kind": self.target.kind,
            "target": self.target.target,
            "url": self.url,
            "title": self.target.title,
            "mime_type": self.target.mime_type,
            "extra": dict(self.target.extra),
            "how": self.how,
            "source": self.source,
        }


def plan_handover(
    target: StreamTarget, allowlist: list[str], *, source: str = ""
) -> Handover:
    """Decide what this target can be handed to, re-checking any URL.

    `allowlist` is the resolving plugin's manifest `network` list. It is
    required rather than optional on purpose: a signature that let a
    caller omit it would make the un-checked call the convenient one.
    """
    if target.kind == "url":
        try:
            check_url(target.target, list(allowlist))
        except PolicyViolation as exc:
            raise StreamError(
                f"refusing to hand over {target.target!r}: {exc}"
            ) from exc
        return Handover(
            route=BROWSER,
            target=target,
            url=target.target,
            how="open this URL in a browser to play it",
            source=source,
        )

    return Handover(
        route=HANDOFF,
        target=target,
        url=None,
        how=(
            "this is an identifier, not a URL: give it to the service that "
            "issued it. The Hub will not guess a URL around a handle"
        ),
        source=source,
    )


def open_handover(
    handover: Handover, allowlist: list[str], opener=None
) -> str:
    """Actually launch a plugin-resolved `browser` handover.

    The allowlist check runs again here -- a third time for a target that
    came straight off the wire -- because this is the function that
    performs the act, and it is callable with a `Handover` that was built
    somewhere this module cannot see.

    There is a second door, `open_library_url`, and it does not take an
    allowlist. Two functions rather than one with an optional argument,
    because an `allowlist=None` default is exactly how the unchecked call
    becomes the convenient one. Each door is labelled with whose URL goes
    through it.
    """
    if handover.route != BROWSER or handover.url is None:
        raise StreamError(
            f"a {handover.route!r} target cannot be opened: {handover.how}"
        )
    try:
        check_url(handover.url, list(allowlist))
    except PolicyViolation as exc:
        raise StreamError(
            f"refusing to open {handover.url!r}: {exc}"
        ) from exc
    return _launch(handover.url, opener)


def open_library_url(url: str, opener=None) -> str:
    """Launch a URL the *host* built from the operator's own settings.

    No plugin allowlist, because no plugin was involved: this URL is the
    operator's backend base plus a rom id they typed. Enforcing a
    plugin's `network` declaration against the operator's own library
    server would refuse every LAN deployment -- `netpolicy` permits https
    only, and a self-hosted RomM on a home network is routinely plain
    http. What is still enforced is that this is an http(s) origin.
    """
    _origin(url, what="library player")
    return _launch(url, opener)


def _launch(url: str, opener) -> str:
    """The one place a browser is actually opened. Returns the URL.

    `opener` is resolved here rather than as a default argument, because a
    default is bound once at import: `opener=webbrowser.open` would freeze
    the real browser launcher into the signature, and a test that replaced
    `webbrowser.open` would find it had replaced nothing and opened a
    window on whoever ran the suite.
    """
    opener = webbrowser.open if opener is None else opener
    if not opener(url):
        raise StreamError(
            f"no browser could be launched for {url!r}; open it yourself, "
            f"or re-run without --open"
        )
    return url


# -- the library's own player ----------------------------------------------

#: The in-browser player path a library server serves for a rom it holds,
#: per backend, `{rom_id}`-templated.
#:
#: One entry, and it is here because it is *verified* rather than assumed:
#: it is the URL `romm-stream` itself drives when it autoplays a library
#: rom, so it is known to load an emulator with the game in it.
#:
#: The other backends are deliberately absent. Neither has a player path
#: this project has confirmed, and a guessed URL is worse than a refusal:
#: it opens, it 404s, and the operator has no way to tell whether the
#: guess or their library is at fault. An entry is added here when someone
#: has watched a game start behind it.
LIBRARY_PLAYERS: dict[str, str] = {
    "romm": "/rom/{rom_id}/ejs",
}


def library_player_path(backend_name: str) -> str:
    """The player path template for a backend, or refuse naming the ones that exist.

    Split out so a caller can refuse *before* reading connection settings.
    An operator whose backend has no player should be told that, not told
    that the backend is unconfigured -- the second message sends them off
    to set variables that would not have helped.
    """
    try:
        return LIBRARY_PLAYERS[backend_name]
    except KeyError:
        known = ", ".join(sorted(LIBRARY_PLAYERS)) or "(none)"
        raise StreamError(
            f"backend {backend_name!r} has no in-browser player this project "
            f"has verified, so there is no URL to hand you; backends that do: "
            f"{known}"
        ) from None


def library_player_url(backend_name: str, base_url: str, rom_id: int) -> str:
    """The in-browser player URL for a rom the library already holds.

    Built entirely from operator settings -- the backend's own base URL
    and an id the operator typed -- so nothing a plugin returned reaches
    it and the plugin allowlist has nothing to say about it. What does
    apply is that the *base* has to be a real http(s) origin, because the
    result is handed to a browser.

    Deliberately does not contact the backend first. A pre-flight GET
    would cost a round trip and an auth token to pre-check a URL the
    operator is about to open in a browser that carries its own session;
    a rom id that does not exist is a 404 in the player, which is a
    visible failure in the right place.
    """
    template = library_player_path(backend_name)
    if rom_id < 1:
        raise StreamError(f"a rom id is a positive integer (got {rom_id!r})")
    return _origin(base_url, what="library server") + template.format(rom_id=rom_id)


def library_handover(backend_name: str, base_url: str, rom_id: int) -> Handover:
    """A `browser` handover for a rom the library already holds.

    The same shape the plugin path produces, so the CLI has one printer
    and `--json` has one schema, but reached without a plugin at all.
    """
    url = library_player_url(backend_name, base_url, rom_id)
    return Handover(
        route=BROWSER,
        target=StreamTarget(
            kind="url",
            target=url,
            mime_type="text/html",
            extra={"backend": backend_name, "rom_id": str(rom_id)},
        ),
        url=url,
        how="open this URL in a browser to play it",
        source=f"{backend_name} library",
    )


def _origin(base_url: str, *, what: str) -> str:
    """Validate an operator-configured base URL and strip its trailing slash.

    Operator configuration, never plugin input -- which is why this is not
    `netpolicy.check_url`. That module enforces a *plugin's* declared
    allowlist and permits https only; a library server or a `romm-stream`
    box on the operator's own LAN is routinely plain http, and refusing it
    would be enforcing a plugin rule against the operator's own settings.
    What is still checked is that this is a URL at all: a bare host or a
    path would otherwise be concatenated into something meaningless and
    handed to a browser or an HTTP client.
    """
    parts = urlsplit((base_url or "").strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise StreamError(
            f"{what} URL must be http:// or https:// with a host "
            f"(got {base_url!r})"
        )
    return f"{parts.scheme.lower()}://{parts.netloc}{parts.path}".rstrip("/")


# -- what a romm-stream server says it can play -----------------------------

#: The two `romm-stream` endpoints this module will call, and the whole
#: list. Both are GETs that read routing tables; neither allocates a
#: display, starts an emulator or creates a session.
#:
#: The session routes (`POST /api/stream/start`, `POST /api/rtc/offer`,
#: `GET /api/rtc/signal`) are **not** here and cannot be, because none of
#: them can be handed a plugin-resolved target: they take a `platform`
#: plus a `rom_name` that must resolve inside the stream server's own ROM
#: directory, or a `romm_rom_id` plus RomM credentials. Calling one of
#: them with a URL from a plugin is not something the Hub is declining to
#: do -- it is not something the server offers.
PLAY_ROUTE_PATH = "/api/play/route"
PLAY_STREAMABLE_PATH = "/api/play/streamable"

#: Where the operator's `romm-stream` lives, if they run one.
STREAM_SERVER_ENV = "ROM_HUB_STREAM_SERVER"

#: Short: this is a LAN service answering out of an in-memory table, and
#: a stream server that is down must not hold up a command whose real
#: answer -- the resolved target -- is already in hand.
STREAM_SERVER_TIMEOUT = 5.0


@dataclass(frozen=True)
class PlayRoute:
    """What a `romm-stream` server says about one platform."""

    platform: str
    #: "local" (EmulatorJS in the client) or "stream" (server-side), or
    #: None when the server says it cannot play this platform at all.
    tier: str | None
    #: The server's own reason, when there is no tier.
    why: str = ""
    #: Set when the server could not be reached or did not answer usefully.
    #: A stream server being down is not a stream *resolution* failing.
    unreachable: str = ""

    @property
    def known(self) -> bool:
        return not self.unreachable

    def describe(self) -> str:
        if self.unreachable:
            return f"stream server unreachable: {self.unreachable}"
        if self.tier == "local":
            return (
                f"stream server plays {self.platform!r} on the 'local' tier "
                f"(EmulatorJS in the client's own browser)"
            )
        if self.tier == "stream":
            return (
                f"stream server plays {self.platform!r} on the 'stream' tier "
                f"(server-side emulation, streamed)"
            )
        if self.tier:
            return f"stream server tier for {self.platform!r}: {self.tier}"
        return (
            f"stream server cannot play {self.platform!r}"
            + (f": {self.why}" if self.why else "")
        )


class StreamServerClient:
    """Read-only client for a `romm-stream` server's play-routing.

    Exists to answer one question the Hub cannot answer alone -- "could
    the operator's own stream server play this?" -- using only endpoints
    that server already exposes. It has no method that starts anything,
    and `ALLOWED_PATHS` is asserted by a test so that adding one is a
    visible change rather than a quiet one.
    """

    ALLOWED_PATHS = frozenset({PLAY_ROUTE_PATH, PLAY_STREAMABLE_PATH})

    def __init__(self, base_url: str, *, timeout: float = STREAM_SERVER_TIMEOUT,
                 transport=None):
        # Imported here rather than at module scope: `plan_handover` and
        # `library_player_url` are the common path and neither needs an
        # HTTP client.
        import httpx

        self.base_url = _origin(base_url, what="stream server")
        self._client = httpx.Client(
            base_url=self.base_url, timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "StreamServerClient":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def _get(self, path: str, params: dict | None = None):
        if path not in self.ALLOWED_PATHS:
            raise StreamError(
                f"{path!r} is not one of the read-only romm-stream endpoints "
                f"this client uses ({', '.join(sorted(self.ALLOWED_PATHS))})"
            )
        return self._client.get(path, params=params or {})

    def route(self, platform: str) -> PlayRoute:
        """Ask whether this platform is playable, and on which tier.

        Never raises for a server that is down or confused: the caller
        already holds the answer the operator asked for, and a failing
        extra must not turn a successful resolve into an error. That is
        `backends.degrade`'s reasoning, applied to a second service.
        """
        platform = (platform or "").strip()
        if not platform:
            return PlayRoute(platform="", tier=None, unreachable="no platform to ask about")
        try:
            resp = self._get(PLAY_ROUTE_PATH, {"platform": platform})
            body = resp.json()
        except StreamError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported, never propagated
            return PlayRoute(
                platform=platform,
                tier=None,
                unreachable=f"{type(exc).__name__}: {exc}",
            )
        if not isinstance(body, dict):
            return PlayRoute(
                platform=platform,
                tier=None,
                unreachable=f"unexpected reply of type {type(body).__name__}",
            )
        tier = body.get("tier")
        return PlayRoute(
            platform=platform,
            tier=tier if isinstance(tier, str) else None,
            why=str(body.get("why") or ""),
        )

    def streamable(self) -> list[str]:
        """Platform slugs the server says it can serve. Empty if unreachable."""
        try:
            body = self._get(PLAY_STREAMABLE_PATH).json()
        except StreamError:
            raise
        except Exception:  # noqa: BLE001
            return []
        if not isinstance(body, dict):
            return []
        slugs = body.get("streamable")
        if not isinstance(slugs, list):
            return []
        return [s for s in slugs if isinstance(s, str)]


@dataclass(frozen=True)
class StreamOutcome:
    """Everything `rom-hub stream` learned, for printing or serialising."""

    handover: Handover
    #: Present only when the operator configured a stream server.
    route: PlayRoute | None = None
    #: Set when `--open` actually launched something.
    opened: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = self.handover.as_dict()
        if self.route is not None:
            data["stream_server"] = {
                "platform": self.route.platform,
                "tier": self.route.tier,
                "why": self.route.why,
                "unreachable": self.route.unreachable,
            }
        if self.opened:
            data["opened"] = self.opened
        if self.notes:
            data["notes"] = list(self.notes)
        return data
