"""More than one plugin directory, and what a directory is allowed to be.

`catalog.py` reads *a* catalog. This reads **the operator's list of them**,
merges the result, and says out loud what it could not reach.

Why it exists. With one catalog baked into the repository, only whoever
ships the repository can publish a plugin. That is the opposite of what a
plugin ecosystem is for: somebody who keeps their plugins on their own
Gitea (say `git.moveweight.com`) had no way to be found at all, and no way
for an operator to point the Hub at them.

    bundled catalog/plugins.json  ─┐
    https://.../plugins.json      ─┼─▶  merge, first source wins  ─▶ browse
    /srv/local/plugins.json       ─┘        collisions reported       install

**Three properties this module is responsible for.**

*The catalog still grants nothing.* Everything in `catalog.py`'s header is
now load-bearing rather than merely true: a remote catalog is written by
somebody the operator has no relationship with, and if a `network` list in
it could widen an installed plugin's reach, adding a source would be
handing that person every plugin on the host. It cannot. An installed
plugin's allowlist is read from its own `manifest.toml` at install time and
enforced by the broker, which never imports this module.
`test_catalog_cannot_widen_permissions` pins it for the bundled file and
`test_a_remote_catalog_cannot_widen_permissions` pins it for a fetched one.

*A fetched catalog is untrusted input and is parsed like it.* Same
default-deny posture as `manifest.py`: `parse_catalog` rejects an unknown
field rather than ignoring it, so a catalog cannot smuggle a key past this
build in the hope that a later one grows a meaning for it. The response is
size-bounded before it is parsed at all.

*A partial answer says it is partial.* `search` already reports "N of M
sources responded" rather than presenting a short list as a complete one.
A directory has the same failure mode and the worse consequence -- a
plugin missing from `browse` looks like a plugin that does not exist -- so
`MergedCatalog` carries a `SourceStatus` per source and the CLI prints
"N of M catalogs reachable" whenever N < M.

**The trust class of a catalog URL, stated because it is genuinely a
different one from `ctx.http`.**

`ctx.http` is *plugin-supplied*: a sandboxed subprocess of somebody else's
code names a URL, and the host may only fetch it if that plugin's manifest
declared the host and the operator approved that declaration at install
time. The allowlist is the whole point, and `netpolicy.check_url` is what
makes the declaration real rather than advisory.

A catalog URL is *operator-supplied*: it is typed into `rom-hub catalog
add`, a command that does nothing else, with no plugin anywhere in the
loop. There is no manifest to consult and no allowlist that would mean
anything -- the operator naming the host **is** the authorisation, in the
same way that `rom-hub plugin install https://...` is. Gating it against
some other allowlist would be theatre.

So the *policy* does not carry over, but the *machinery* does, and this is
where it does make sense:

- **https only**, via `netpolicy.ALLOWED_SCHEMES`. Not a preference: a
  catalog fetched over http can be rewritten in flight by anyone on the
  path, and every install URL a reader would then trust comes from it.
- **The host must be a hostname**, via the same `netpolicy.url_allowed`
  that guards the brokered path, so the thing being connected to is the
  thing the operator read. Userinfo is refused outright here rather than
  merely stripped: `https://github.com@evil.example/c.json` is a phishing
  shape, and an operator who has to be told their catalog is not where
  they thought has already been fooled.
- **Every redirect hop is re-checked** against the one host the operator
  named, by fetching through `importer.HttpDownloader` -- the same class
  the import path and the data-asset path use, for the same reason. A 302
  to a host the operator never typed is not the source they added.
- **The body is bounded** (`MAX_CATALOG_BYTES`) before it is parsed, so an
  endless response cannot become an endless allocation.

What deliberately does *not* apply: the response is never executed, never
handed to a plugin, and never consulted for a permission. The worst a
hostile catalog can do is lie about where a plugin lives -- which is why
`plugin install` prints, loudly, when the slug it resolved came from a
source the project does not vouch for.

**Collision rule: first source wins, and the collision is always shown.**
Chosen over last-wins and over refuse-and-disambiguate, and the reasoning
is in `merge_catalogs`.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import env
from .catalog import CatalogEntry, CatalogError, parse_catalog
from .netpolicy import url_allowed
from .paths import UnsafeDestination, dest_in_job_dir

#: The directory this repository ships. Always present, always first, and
#: not removable -- see `read_sources`.
BUNDLED_CATALOG = Path(__file__).resolve().parents[2] / "catalog" / "plugins.json"

#: The name reserved for it. A configured source may not take this name,
#: because "which one said that" has to have exactly one answer.
BUNDLED_NAME = "bundled"

#: Where the operator's list lives. Beside `state.json` rather than under
#: `var/`, because it is configuration somebody typed and would expect to
#: survive clearing a cache -- unlike the fetched copies, which live under
#: `var/catalogs/` with the job queue and the downloads.
SOURCES_FILENAME = "catalog-sources.json"

CACHE_DIR_NAME = "catalogs"

#: A directory of a few hundred plugins is well under this. The cap exists
#: so a source that answers with an endless stream costs a bounded amount
#: of disk and no unbounded allocation; the bundled file is ~85 KiB.
MAX_CATALOG_BYTES = 8 * 1024 * 1024

#: How long a fetched catalog is served without asking again. Six hours:
#: a plugin directory changes on the order of days, and `browse` is a
#: command people run several times while deciding what to install. Set
#: `ROM_HUB_CATALOG_TTL` (seconds) to change it; `0` refetches every time.
DEFAULT_TTL_SECONDS = 6 * 60 * 60

#: A catalog fetch is a small JSON file, not a ROM.
FETCH_TIMEOUT = 30.0

_SOURCES_VERSION = 1

# Deliberately narrow, and for the same reason `registry._REF_RE` is: this
# string becomes a filename component in the cache directory and a column
# in `browse`, and a name that needs quoting is a name that will eventually
# be mis-parsed by something.
_NAME_HELP = (
    "a source name is letters, digits, '.', '_' or '-', starting with a "
    "letter or digit"
)


class CatalogSourceError(CatalogError):
    """A catalog source is malformed, unreachable, or unsafe to add.

    A subclass of `CatalogError` so `cli.main`'s existing handler already
    turns it into a one-line refusal rather than a traceback.
    """


def _valid_name(name: str) -> bool:
    if not name or not name[0].isalnum():
        return False
    return all(c.isalnum() or c in "._-" for c in name)


@dataclass(frozen=True)
class CatalogSource:
    """One place a directory of plugins can be read from."""

    name: str
    #: An https URL or a local filesystem path.
    location: str
    #: True only for the one this repository ships. What "the project
    #: vouches for this" means, and the only thing that suppresses the
    #: third-party notice at install time.
    bundled: bool = False

    @property
    def remote(self) -> bool:
        return self.location.lower().startswith("https://")

    @property
    def host(self) -> str:
        """The host a remote source is fetched from. `""` for a local one."""
        return urlsplit(self.location).hostname or "" if self.remote else ""

    def describe(self) -> str:
        kind = "bundled with rom-hub" if self.bundled else (
            "remote" if self.remote else "local file"
        )
        return f"{self.name} ({kind}): {self.location}"


def bundled_source() -> CatalogSource:
    return CatalogSource(name=BUNDLED_NAME, location=str(BUNDLED_CATALOG), bundled=True)


@dataclass(frozen=True)
class SourcedEntry:
    """A catalog entry and the source it was read from.

    The pairing is the feature: with more than one directory in play, "who
    said this" is part of what a reader is deciding on, so it travels with
    the entry rather than being reconstructed at the print site.
    """

    entry: CatalogEntry
    source: CatalogSource

    @property
    def slug(self) -> str:
        return self.entry.slug


@dataclass(frozen=True)
class SourceStatus:
    """What happened when one source was read. The honesty record.

    Mirrors `dispatcher.PluginStatus` on purpose -- same shape, same rule:
    a caller may never present a merged listing without being able to say
    how much of it is missing.
    """

    source: CatalogSource
    ok: bool
    count: int = 0
    error: str | None = None
    #: True when the entries came from a cached copy rather than a fetch.
    from_cache: bool = False
    #: Age of that copy in seconds, when it is older than the TTL and was
    #: served anyway because the source could not be reached. `None`
    #: otherwise. This is what makes staleness *visible* rather than a
    #: silent success.
    stale_seconds: float | None = None

    @property
    def stale(self) -> bool:
        return self.stale_seconds is not None

    def summary(self) -> str:
        if not self.ok:
            return f"unreachable: {self.error}"
        if self.stale:
            return (
                f"{self.count} plugin(s) from a cached copy "
                f"{human_age(self.stale_seconds)} old -- could not refresh: "
                f"{self.error}"
            )
        if self.from_cache:
            return f"{self.count} plugin(s) (cached)"
        return f"{self.count} plugin(s)"


@dataclass(frozen=True)
class Collision:
    """One slug claimed by more than one source."""

    slug: str
    winner: CatalogSource
    losers: tuple[CatalogSource, ...]

    def summary(self) -> str:
        names = ", ".join(s.name for s in self.losers)
        return (
            f"{self.slug}: served by {self.winner.name}; also claimed by "
            f"{names} and ignored"
        )


@dataclass
class MergedCatalog:
    """Every source's entries, merged, with what it cost to say so."""

    entries: list[SourcedEntry] = field(default_factory=list)
    statuses: list[SourceStatus] = field(default_factory=list)
    collisions: list[Collision] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.statuses)

    @property
    def reachable(self) -> int:
        return sum(1 for s in self.statuses if s.ok)

    @property
    def complete(self) -> bool:
        return self.reachable == self.total

    @property
    def stale(self) -> list[SourceStatus]:
        return [s for s in self.statuses if s.stale]

    @property
    def failures(self) -> list[SourceStatus]:
        return [s for s in self.statuses if not s.ok]

    def find(self, slug: str) -> SourcedEntry | None:
        return next((e for e in self.entries if e.slug == slug), None)

    def plain(self) -> list[CatalogEntry]:
        """Just the entries, for the code that predates sources."""
        return [e.entry for e in self.entries]

    def coverage(self) -> str:
        """The one line a caller must print when anything is missing.

        A stale source counts as reachable, because it did answer -- but
        it is named in the same breath rather than left to a later line.
        "3 of 3 reachable" on its own would read as "this is current",
        which is the one thing a day-old copy is not.
        """
        line = f"{self.reachable} of {self.total} catalog(s) reachable"
        stale = self.stale
        if stale:
            names = ", ".join(s.source.name for s in stale)
            line += f"; {len(stale)} serving a stale cached copy ({names})"
        return line


def human_age(seconds: float | None) -> str:
    if seconds is None:
        return "unknown age"
    if seconds < 90:
        return f"{int(seconds)}s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{int(minutes)}m"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


# -- the operator's list -------------------------------------------------


def sources_path(root: Path) -> Path:
    return Path(root) / SOURCES_FILENAME


def cache_dir(root: Path) -> Path:
    return Path(root) / "var" / CACHE_DIR_NAME


def check_location(location: str, *, must_exist: bool = True) -> str:
    """Return `location` unchanged, or refuse it with a reason.

    Shaped like `registry._checked_source`, and for the same reason: this
    is the one place a string the operator typed becomes something the Hub
    will connect to or open, so the refusals belong here rather than at
    each use.

    `must_exist` is False when reading the list back. A local catalog that
    has since been deleted -- an unmounted share, a file somebody tidied
    away -- must degrade as *one unreachable source*, exactly like a
    remote host that is down. Refusing it here would instead make the
    whole source list unreadable, so one missing file would take out the
    bundled directory too. What is checked either way is the *shape*:
    scheme, and the host actually being the host.
    """
    if not isinstance(location, str) or not location.strip():
        raise CatalogSourceError("a catalog source location is empty")
    location = location.strip()

    lowered = location.lower()
    if lowered.startswith("http://"):
        raise CatalogSourceError(
            f"refusing catalog source {location!r}: a catalog fetched over "
            f"http can be rewritten in flight by anyone on the path, and "
            f"every install URL you would then read comes from it. Use https."
        )
    if "://" in location or lowered.startswith("https:"):
        parts = urlsplit(location)
        if parts.scheme.lower() not in {"https"}:
            raise CatalogSourceError(
                f"refusing catalog source {location!r}: only https URLs and "
                f"local paths are catalog sources "
                f"(scheme {parts.scheme!r} is not one)"
            )
        # Refused rather than tolerated. urlsplit strips userinfo from
        # .hostname, so the fetch would go to the *real* host and the
        # allowlist would be that host -- safe, and completely useless to
        # an operator who read `github.com` and got `evil.example`.
        if "@" in (parts.netloc or ""):
            raise CatalogSourceError(
                f"refusing catalog source {location!r}: the part before '@' "
                f"is not the host that would be contacted "
                f"({parts.hostname or '?'} is), so the URL says one thing "
                f"and does another"
            )
        host = parts.hostname or ""
        # The same check the brokered path makes, against an allowlist of
        # exactly the host named: https scheme, a syntactically real
        # hostname, no port/userinfo confusion. See the module header for
        # why this is validation rather than authorisation.
        if not host or not url_allowed(location, [host]):
            raise CatalogSourceError(
                f"refusing catalog source {location!r}: it is not an https "
                f"URL with a plain hostname"
            )
        return location

    if not must_exist or Path(location).exists():
        return location
    raise CatalogSourceError(
        f"refusing catalog source {location!r}: it is neither an https URL "
        f"nor a path that exists"
    )


def check_name(name: str) -> str:
    if not isinstance(name, str) or not _valid_name(name.strip()):
        raise CatalogSourceError(
            f"refusing catalog source name {name!r}: {_NAME_HELP}"
        )
    name = name.strip()
    if name == BUNDLED_NAME:
        raise CatalogSourceError(
            f"{BUNDLED_NAME!r} is the name of the directory that ships with "
            f"rom-hub; pick another so 'which source said that' has one answer"
        )
    return name


def _read_file(root: Path) -> list[CatalogSource]:
    path = sources_path(root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogSourceError(f"cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("version") != _SOURCES_VERSION:
        raise CatalogSourceError(
            f"{path}: unsupported catalog source list version "
            f"{raw.get('version') if isinstance(raw, dict) else '?'!r}; this "
            f"build writes version {_SOURCES_VERSION}"
        )
    items = raw.get("sources")
    if not isinstance(items, list):
        raise CatalogSourceError(f"{path}: 'sources' must be a list")

    out: list[CatalogSource] = []
    for item in items:
        if not isinstance(item, dict):
            raise CatalogSourceError(f"{path}: each source must be an object")
        unknown = sorted(set(item) - {"name", "location"})
        if unknown:
            raise CatalogSourceError(
                f"{path}: source has unknown key(s) {unknown}; permitted: "
                f"name, location"
            )
        out.append(
            CatalogSource(
                name=check_name(str(item.get("name", ""))),
                location=check_location(
                    str(item.get("location", "")), must_exist=False
                ),
            )
        )
    return out


def _write_file(root: Path, sources: list[CatalogSource]) -> None:
    path = sources_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": _SOURCES_VERSION,
                "sources": [
                    {"name": s.name, "location": s.location} for s in sources
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def read_sources(root: Path) -> list[CatalogSource]:
    """The ordered source list: the bundled directory, then the operator's.

    The bundled one is **always first and cannot be removed**, and both
    halves of that are the collision rule doing its job. First means no
    third-party catalog can shadow a plugin this project ships -- see
    `merge_catalogs`. Not removable means the operator always has one
    source they can reason about, and `browse` on a fresh install behaves
    exactly as it did before this feature existed.
    """
    return [bundled_source(), *_read_file(root)]


def add_source(root: Path, name: str, location: str) -> CatalogSource:
    name = check_name(name)
    location = check_location(location)
    existing = _read_file(root)
    if any(s.name == name for s in existing):
        raise CatalogSourceError(
            f"a catalog source named {name!r} is already configured; remove "
            f"it first ('rom-hub catalog remove {name}')"
        )
    source = CatalogSource(name=name, location=location)
    _write_file(root, [*existing, source])
    return source


def remove_source(root: Path, name: str) -> CatalogSource:
    if name == BUNDLED_NAME:
        raise CatalogSourceError(
            f"{BUNDLED_NAME!r} is the directory that ships with rom-hub and "
            f"cannot be removed"
        )
    existing = _read_file(root)
    gone = next((s for s in existing if s.name == name), None)
    if gone is None:
        configured = ", ".join(s.name for s in existing) or "(none)"
        raise CatalogSourceError(
            f"no catalog source named {name!r} is configured "
            f"(configured: {configured})"
        )
    _write_file(root, [s for s in existing if s.name != name])
    return gone


# -- reading one source --------------------------------------------------


def ttl_seconds() -> float:
    """How long a fetched catalog is served without asking again.

    Read at call time, like every other setting, so a shell can flip it.
    A value that is not a number is a typo, and the safe reading of a typo
    is the default rather than "never cache" or "cache forever".
    """
    raw = env.get("ROM_HUB_CATALOG_TTL").strip()
    if not raw:
        return float(DEFAULT_TTL_SECONDS)
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_TTL_SECONDS)
    return max(0.0, value)


def cache_key(location: str) -> str:
    """A filename for this source's cached copy.

    A hash rather than the source name, because the cache is keyed on
    *where the bytes came from*: renaming a source must not serve it
    another source's cached copy, and repointing a name at a new URL must
    not serve the old one's.
    """
    return hashlib.sha256(location.encode("utf-8")).hexdigest()[:32]


def _cache_paths(root: Path, source: CatalogSource) -> tuple[Path, Path]:
    directory = cache_dir(root)
    key = cache_key(source.location)
    try:
        body = dest_in_job_dir(directory, f"{key}.json")
        meta = dest_in_job_dir(directory, f"{key}.meta.json")
    except UnsafeDestination as exc:  # pragma: no cover - key is a hex digest
        raise CatalogSourceError(str(exc)) from exc
    return body, meta


def _read_cache(body: Path, meta: Path) -> tuple[dict, float] | None:
    """`(raw catalog, fetched_at)` from the cache, or None if unusable."""
    try:
        raw = json.loads(body.read_text(encoding="utf-8"))
        stamp = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not isinstance(stamp, dict):
        return None
    try:
        fetched_at = float(stamp.get("fetched_at", 0))
    except (TypeError, ValueError):
        return None
    return raw, fetched_at


def _write_cache(body: Path, meta: Path, text: str, now: float) -> None:
    body.parent.mkdir(parents=True, exist_ok=True)
    body.write_text(text, encoding="utf-8")
    meta.write_text(json.dumps({"fetched_at": now}), encoding="utf-8")


def _fetch_text(source: CatalogSource, root: Path, *, transport=None) -> str:
    """Fetch a remote catalog's bytes, re-checking every redirect hop.

    Imported here rather than at module scope for the reason `assets.py`
    gives: `importer` pulls in the job queue, the dedup hasher and the
    socket.io scanner, and `rom-hub catalog list` needs none of them.

    The class is reused rather than copied because the redirect handling
    is the point: httpx follows nothing, each hop is re-checked against
    the one host the operator named, and a hop that leaves it ends the
    download. A catalog that 302s to somewhere else is not the catalog
    that was added.
    """
    from .importer import DownloadError, HttpDownloader

    directory = cache_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        incoming = dest_in_job_dir(
            directory, f"{cache_key(source.location)}.incoming"
        )
    except UnsafeDestination as exc:  # pragma: no cover - key is a hex digest
        raise CatalogSourceError(str(exc)) from exc
    # Never resume onto a previous attempt's bytes: HttpDownloader would
    # send a Range header and splice a fresh catalog onto a stale prefix.
    if incoming.exists():
        incoming.unlink()

    downloader = HttpDownloader(
        allowlist=[source.host],
        timeout=FETCH_TIMEOUT,
        transport=transport,
        max_bytes=MAX_CATALOG_BYTES,
    )
    try:
        downloader.download(source.location, incoming)
        return incoming.read_text(encoding="utf-8")
    except DownloadError as exc:
        # No source name in front of it: `SourceStatus` already carries the
        # source, and every caller prints the two together, so adding one
        # here produced "offline: offline: downloading ... failed".
        raise CatalogSourceError(str(exc)) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise CatalogSourceError(
            f"{source.name}: the catalog fetched from {source.location!r} "
            f"could not be read as UTF-8 text: {exc}"
        ) from exc
    finally:
        downloader.close()
        if incoming.exists():
            incoming.unlink()


def load_source(
    source: CatalogSource,
    root: Path,
    *,
    transport=None,
    ttl: float | None = None,
    now: float | None = None,
) -> tuple[list[CatalogEntry], SourceStatus]:
    """Read one source. Never raises for a source that is simply down.

    A local source is read straight through: there is nothing to cache and
    a file that is not there is not a network condition.

    A remote source is served from cache while the TTL holds, refetched
    when it does not, and -- if that refetch fails -- served from the
    *stale* cache with the age and the error attached. Degrading to a
    known-old answer is better than degrading to nothing, but only if the
    caller is told, which is what `SourceStatus.stale_seconds` is for.
    """
    now = time.time() if now is None else now
    ttl = ttl_seconds() if ttl is None else ttl

    if not source.remote:
        try:
            raw = json.loads(Path(source.location).read_text(encoding="utf-8"))
        except OSError as exc:
            return [], SourceStatus(source, False, error=f"cannot read: {exc}")
        except json.JSONDecodeError as exc:
            return [], SourceStatus(source, False, error=f"not valid JSON: {exc}")
        try:
            entries = parse_catalog(raw, origin=source.name)
        except CatalogError as exc:
            return [], SourceStatus(source, False, error=str(exc))
        return entries, SourceStatus(source, True, len(entries))

    body, meta = _cache_paths(root, source)
    cached = _read_cache(body, meta)
    age = None if cached is None else max(0.0, now - cached[1])
    if cached is not None and ttl > 0 and age is not None and age < ttl:
        try:
            entries = parse_catalog(cached[0], origin=source.name)
        except CatalogError as exc:
            # A cached copy that no longer parses is not a reason to serve
            # nothing: fall through and refetch.
            entries = None
            cache_error: str | None = str(exc)
        else:
            return entries, SourceStatus(source, True, len(entries), from_cache=True)
    else:
        cache_error = None

    try:
        text = _fetch_text(source, root, transport=transport)
        raw = json.loads(text)
        entries = parse_catalog(raw, origin=source.name)
    except (CatalogError, json.JSONDecodeError) as exc:
        reason = f"{type(exc).__name__}: {exc}" if not isinstance(
            exc, CatalogError
        ) else str(exc)
        if cached is not None and cache_error is None:
            try:
                stale_entries = parse_catalog(cached[0], origin=source.name)
            except CatalogError:
                stale_entries = None
            if stale_entries is not None:
                return stale_entries, SourceStatus(
                    source,
                    True,
                    len(stale_entries),
                    error=reason,
                    from_cache=True,
                    stale_seconds=age,
                )
        return [], SourceStatus(source, False, error=reason)

    _write_cache(body, meta, text, now)
    return entries, SourceStatus(source, True, len(entries))


# -- merging -------------------------------------------------------------


def merge_catalogs(
    loaded: list[tuple[list[CatalogEntry], SourceStatus]],
) -> MergedCatalog:
    """Merge in source order. **First source wins**, and says so.

    Three rules were available and this is not the obvious one, so:

    *Last wins* was rejected outright. The bundled directory is first, so
    last-wins would mean that adding any third-party source lets it
    silently repoint `archive-org` -- a slug an operator has typed a
    hundred times -- at its own repository. That is a supply-chain swap
    with a one-command setup, and no amount of reporting makes it a
    reasonable default.

    *Refuse and require disambiguation* was rejected because it hands
    every third-party catalog a veto: claim the popular slugs and the
    operator's whole directory stops working until they intervene. A
    directory that a stranger can break is not a directory.

    *First wins* has neither failure. Precedence follows the order the
    operator wrote, the bundled entries are unshadowable, and a source
    added later can add plugins but never replace one. The cost is that a
    third-party catalog cannot offer a *better* build of a bundled plugin
    under the same slug -- which is the right cost, because "better" is
    exactly the claim an attacker would make.

    The collision is never silent: it is returned in `MergedCatalog.
    collisions`, printed by `plugin browse`, and printed again by `plugin
    install` when the slug being installed is one of them.
    """
    merged = MergedCatalog()
    owner: dict[str, CatalogSource] = {}
    shadowed: dict[str, list[CatalogSource]] = {}

    for entries, status in loaded:
        merged.statuses.append(status)
        for entry in entries:
            if entry.slug in owner:
                shadowed.setdefault(entry.slug, []).append(status.source)
                continue
            owner[entry.slug] = status.source
            merged.entries.append(SourcedEntry(entry, status.source))

    merged.collisions = [
        Collision(slug=slug, winner=owner[slug], losers=tuple(losers))
        for slug, losers in sorted(shadowed.items())
    ]
    return merged


def load_all(
    root: Path,
    *,
    sources: list[CatalogSource] | None = None,
    transport=None,
    ttl: float | None = None,
    now: float | None = None,
) -> MergedCatalog:
    """Every configured source, merged, with a status for each."""
    chosen = read_sources(root) if sources is None else list(sources)
    return merge_catalogs(
        [
            load_source(s, root, transport=transport, ttl=ttl, now=now)
            for s in chosen
        ]
    )
