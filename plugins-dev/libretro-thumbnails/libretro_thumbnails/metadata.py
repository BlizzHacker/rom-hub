"""libretro-thumbnails `metadata`: artwork for a rom already in RomM.

    RomRef -> libretro system -> candidate filenames
           -> Named_Boxarts, then Named_Titles, then Named_Snaps
           -> the first URL that answers 200

**All three sets, not just the box.** Every libretro system directory
carries `Named_Boxarts`, `Named_Titles`, `Named_Snaps` and `Named_Logos`
side by side, for the same games, and this plugin asked for exactly one
of them. A rom with a title screen and no box -- which is most of arcade,
and a great deal of every computer platform, because an arcade board
never had a box and a lot of 8-bit releases were a cassette in a bag --
got no artwork and a refusal that listed every spelling it had tried in a
directory that was never going to have any of them.

The order is the whole of the policy: box art first and nothing displaces
it, then a title screen, then an in-game shot. `art_kinds` changes the
chain; `art_kind` (singular) still means "only this one", because an
operator who named one was being specific.

RomM takes **one** image per rom -- `PUT /api/roms/{id}` has a single
`artwork` part -- so this is a fallback chain and not a gallery. RomM's
own `merged_screenshots` comes from its configured metadata providers and
there is no form field that reaches it; see README.md, "What cannot reach
RomM".

The plugin never fetches the artwork. It names a URL and the **host**
fetches it, after checking that URL against this plugin's own `network`
allowlist -- the same rule a FetchPlan URL follows, for the same reason.

Three decisions here are the careful half of choices that could have gone
the other way.

**A candidate is probed before it is proposed.** `MetadataPatch` has no
way to say "try this, and never mind if it 404s": the host fetches the
URL and a failed fetch fails the whole enrich with an HTTP error the
operator then has to interpret. So the plugin asks first, and an
exhausted candidate list becomes a refusal that *names every spelling it
tried*. The cost is that a hit is downloaded twice -- once here to
confirm it, once by the host to keep it. That is the price of a clear
failure, and it is bounded by `names.MAX_CANDIDATES`.

**Only artwork is proposed. Never a name.** The filename libretro serves
is a No-Intro DAT string, not a curated title, and writing it into RomM
would overwrite the operator's own naming with a spelling chosen by a
completely different project. `MetadataPatch` leaves absent fields alone
precisely so a plugin can do one thing; this plugin does one thing.

**`libretro_id` is left unset, because there is no such id.** These
repositories are keyed by *name*: there is no numeric or opaque
identifier anywhere in the service to record. The RPP field exists, and
putting something in it would look like an improvement, but the only
value available is the thumbnail's filename -- which RomM's provider-id
validator rejects anyway, since a provider id may not contain spaces or
parentheses. An invented id is worse than an absent one.
"""

import json
import posixpath
import re
from urllib.parse import quote, unquote, urlsplit

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .names import candidates, match_key, scrub
from .systems import NeedsMapping, system_for  # noqa: F401  (re-exported)

# Where the images live. thumbnails.libretro.com served these from 2019 until
# November 2026, when its index went empty and every image URL started to
# answer 404. The same sets are the libretro-thumbnails GitHub organisation's
# repositories -- one per system, which is what the server was a mirror of --
# so the plugin reads them there. `HEAD` is the repository's default branch:
# most are `master`, twelve are `main`, and neither is worth pinning per repo.
RAW = "https://raw.githubusercontent.com/libretro-thumbnails/"
API = "https://api.github.com/repos/libretro-thumbnails/"

# A git symlink is served by raw.githubusercontent.com as its *target's
# name*, with a 200: about one file in six in these repositories is one (599
# of 3,716 SNES box arts, 2026-11-22), linking a re-release to the original
# release's image. Proposing that URL hands RomM 38 bytes of text, which it
# refuses ("detected text/plain"). So a probe whose body is a bare `.png`
# name is followed, up to this many hops.
MAX_SYMLINK_HOPS = 3

# Kept for anything that imported the old name; it is the image base now.
BASE = RAW

# A system's repository is its name with spaces as underscores, except where
# the GitHub organisation named the repository differently from the server's
# directory. Read from GET /orgs/libretro-thumbnails/repos on 2026-11-22.
_REPO_NAMES = {
    "Atari - 8-bit": "Atari_-_8-bit_Family",
    "NEC - PC-8001 - PC-8801": "NEC_-_PC-88",
}

# Directories the server had that GitHub has no repository for. Refused by
# name, because the nearest repository (`Philips_-_Videopac`) is a different
# machine's library and its covers would be confidently wrong.
_NO_REPO = {"Philips - Videopac+"}

# The four sets every system directory carries. `boxart` is the default
# because it is the one RomM shows as a cover; the others are here because
# many systems -- arcade and computer platforms especially -- have a title
# screen where they have no box.
KINDS = {
    "boxart": "Named_Boxarts",
    "title": "Named_Titles",
    "snap": "Named_Snaps",
    "logo": "Named_Logos",
}

# The chain, best first, when the operator has not named one.
#
# Box art is what RomM shows as a cover, so it goes first and nothing
# displaces it. The other two are what exists when no box does -- an
# arcade board never had one, and a great many computer releases were
# cassettes in a bag -- and until this was a list rather than a single
# name, those roms got a refusal that carefully listed every spelling it
# had tried in a directory that was never going to have them.
#
# `logo` is not in the default. It is a wordmark on transparency, which
# is a fine thing and is not a picture of a game; a library falling back
# to it would look like it had covers when it had lettering.
DEFAULT_KINDS = ("boxart", "title", "snap")

# Region tags, best first. Used only to choose between several releases of
# a title that has already been matched exactly -- never to match one.
_REGIONS = ("USA", "World", "Europe", "Japan")

# Tags that mark a release nobody wants as their cover.
_UNWANTED = re.compile(
    r"\((Beta|Proto|Prototype|Demo|Sample|Pirate|Unl|Hack|Aftermarket)[^)]*\)"
    r"|\[b[\d]*\]|\[h[^\]]*\]|\[p[\d]*\]",
    re.IGNORECASE,
)


class NoThumbnail(Exception):
    """No thumbnail could be identified for this rom, and the message says
    which spellings were tried."""


def repo_for(system: str) -> str:
    """The libretro-thumbnails repository that holds this system's sets."""
    if system in _NO_REPO:
        raise NoThumbnail(
            f"libretro publishes no thumbnail repository for {system!r}; "
            f"its old server had the directory, GitHub does not"
        )
    return _REPO_NAMES.get(system, system.replace(" ", "_"))


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        system = system_for(rom.platform)
        repo_for(system)  # refuse an unpublished system before any request
        self._trees: dict[str, dict | None] = {}
        kinds = self._kinds()

        override = (rom.extra.get("source_id") or "").strip()
        if override:
            # The operator has named the file. Probe it anyway -- a typo
            # should be a refusal here, not an HTTP error from the host.
            names = [scrub(override.removesuffix(".png"))]
        else:
            names = candidates([rom.name, rom.filename])

        if not names:
            raise NoThumbnail(
                f"rom {rom.rom_id} has neither a name nor a filename in RomM, "
                f"and libretro's thumbnails are keyed by name alone"
            )

        # Every spelling of the *preferred* kind before any spelling of the
        # next. The other order -- every kind for one spelling, then the
        # next spelling -- would hand back a title screen for a game whose
        # box art is filed under a name this rom also has, which is exactly
        # the wrong way round: the fallback exists for games with **no**
        # box art, not for names probed in an unlucky order.
        for kind in kinds:
            for name in names:
                resolved = self._resolve(self._url(system, kind, name))
                if resolved:
                    return MetadataPatch(artwork_url=resolved)

        if override or not self._index_fallback():
            raise NoThumbnail(self._refusal(rom, system, kinds, names))

        unreadable: list[str] = []
        for kind in kinds:
            listing = self._index(system, kind)
            if listing is None:
                unreadable.append(KINDS[kind])
                continue
            found = self._from_index(system, kind, [rom.name, rom.filename], listing)
            if found is not None:
                resolved = self._resolve(self._url(system, kind, found))
                if resolved:
                    return MetadataPatch(artwork_url=resolved)

        raise NoThumbnail(
            self._refusal(rom, system, kinds, names, indexed=True, unreadable=unreadable)
        )

    # -- configuration ---------------------------------------------------

    def _kinds(self) -> tuple[str, ...]:
        """The image sets to try, best first.

        **This is a chain now, and it used to be one name.** libretro's
        system directories carry four sets side by side and this plugin
        asked for exactly one of them, so a game with a title screen and
        no box -- which is most of arcade, and a great deal of every
        computer platform -- got no artwork at all and a refusal listing
        the spellings it had tried for a set that was never going to have
        it. The default is `boxart, title, snap`: the cover RomM shows
        first, then the two that exist where no box does.

        `art_kind` (singular) is still honoured and still means "only
        this one". An operator who set it did say something specific --
        `snap` for a library they want in-game shots for -- and quietly
        appending two fallbacks to a deliberate choice would be the same
        mistake in the other direction.
        """
        single = str(self.ctx.config.get("art_kind") or "").strip().lower()
        if single:
            return (self._known(single, "art_kind"),)

        raw = self.ctx.config.get("art_kinds")
        if raw is None:
            raw = DEFAULT_KINDS
        if isinstance(raw, str):
            raw = [raw]
        chosen: list[str] = []
        for item in raw:
            name = self._known(str(item).strip().lower(), "art_kinds")
            if name not in chosen:
                chosen.append(name)
        if not chosen:
            raise NoThumbnail(
                "art_kinds is empty, so there is no image set to look in. "
                f"Name at least one of {sorted(KINDS)}"
            )
        return tuple(chosen)

    @staticmethod
    def _known(name: str, setting: str) -> str:
        if name not in KINDS:
            raise NoThumbnail(
                f"{setting} names {name!r}, which is not one of "
                f"{sorted(KINDS)}; libretro serves those four sets and no "
                f"others"
            )
        return name

    def _index_fallback(self) -> bool:
        return bool(self.ctx.config.get("index_fallback", True))

    # -- the network -----------------------------------------------------

    def _url(self, system: str, kind: str, name: str) -> str:
        return (
            RAW
            + quote(repo_for(system), safe="")
            + "/HEAD/"
            + KINDS[kind]
            + "/"
            + quote(f"{name}.png", safe="")
        )

    def _resolve(self, url: str) -> str | None:
        """The URL that serves this image's bytes, or None on a 404.

        Follows git symlinks (see MAX_SYMLINK_HOPS): the body of a symlink
        is one line naming a `.png` relative to the link's own directory.
        """
        for _ in range(MAX_SYMLINK_HOPS + 1):
            body = self._fetch(url)
            if body is None:
                return None
            target = _symlink_target(body)
            if target is None:
                return url
            url = _sibling(url, target)
        raise NoThumbnail(
            f"libretro's thumbnail {url!r} is a chain of more than "
            f"{MAX_SYMLINK_HOPS} symlinks; nothing was proposed for this rom"
        )

    def _fetch(self, url: str) -> str | None:
        """The body if libretro serves this file, None on a 404.

        A 404 is an answer, not a fault: it means "try the next spelling".
        Anything else is the service being unwell, and probing it seven
        more times would be both rude and useless, so it stops here.
        """
        response = self.ctx.http.get(url)
        if response.status_code == 200:
            return response.text or ""
        if response.status_code == 404:
            return None
        raise NoThumbnail(
            f"GitHub answered HTTP {response.status_code} for libretro's "
            f"thumbnail {url!r}; nothing was proposed for this rom"
        )

    def _from_index(self, system: str, kind: str, labels, listing) -> str | None:
        """Second chance: read the directory and match on the title alone.

        Probing only finds a file whose *spelling* the library already
        has. This finds `Prince of Persia (1990)` for a library that says
        `Prince of Persia`, by comparing titles with tags, punctuation,
        case and articles removed -- an equality test, never a prefix one,
        so `Sonic the Hedgehog` cannot pick up `Sonic the Hedgehog 2`.
        """
        wanted = {match_key(label) for label in labels if (label or "").strip()}
        wanted.discard("")
        if not wanted:
            return None

        matches = [name for name in listing if match_key(name) in wanted]
        if not matches:
            return None
        # Several releases of one title. Prefer the region the library
        # already names, then USA/World/Europe/Japan, then the plainest.
        preferred = self._preferred_region(labels)
        return min(matches, key=lambda n: self._rank(n, preferred))

    def _index(self, system: str, kind: str) -> list[str] | None:
        """The directory listing, `[]` if there is none, `None` if unreadable.

        Three outcomes rather than two, and the third was found by running
        this against a real library rather than by reading the code.

        `ctx.http` refuses a response over 4 MiB, and libretro's NES
        `Named_Titles` listing is **4,297,395 bytes** -- 16,172 entries.
        `Named_Boxarts` for the same system is 13,418 entries and fits, so
        while this plugin read one directory the ceiling was never
        reached; the moment the chain reached for a second set, every
        single NES enrich died on a `ResponseTooLarge` raised out of a
        *fallback*, after the probe ladder had already missed.

        A listing this plugin cannot read is not a failure of the enrich.
        It means "no answer available from this set" -- the same thing a
        404 means -- so it is `None`, the chain moves on, and the refusal
        at the end names it so nobody debugs a match that never had a
        chance to happen. Any other HTTP status still raises: a 503 is the
        service being unwell and probing it seven more times would be both
        rude and useless.
        """
        tree = self._root_tree(system)
        if tree is None:
            return None
        entry = next(
            (
                item
                for item in tree.get("tree", [])
                if item.get("path") == KINDS[kind] and item.get("type") == "tree"
            ),
            None,
        )
        if entry is None:
            # This system has no directory for this set at all, which is
            # ordinary -- not every system carries all four. An answer,
            # not a fault, exactly as a 404 on a single file is: the
            # caller moves to the next kind in the chain. Raising here is
            # what made the chain abort on the first system that happened
            # to lack `Named_Titles`.
            return []
        listing = self._tree(repo_for(system), str(entry.get("sha") or ""), system, kind)
        if listing is None:
            return None
        return [
            item["path"].removesuffix(".png")
            for item in listing.get("tree", [])
            if item.get("type") == "blob" and str(item.get("path", "")).endswith(".png")
        ]

    def _root_tree(self, system: str) -> dict | None:
        """The repository's top level, read once per enrich."""
        if system not in self._trees:
            tree = self._tree(repo_for(system), "HEAD", system, "root")
            self._trees[system] = {"tree": []} if tree == {} else tree
        return self._trees[system]

    def _tree(self, repo: str, ref: str, system: str, what: str) -> dict | None:
        """One git tree from the GitHub API: `{}` for 404, `None` if unreadable.

        A rate limit (403/429) is "unreadable", not a fault: the probe
        ladder has already run, and an anonymous Hub gets 60 API requests
        an hour -- set ROM_HUB_GITHUB_TOKEN on the Hub to lift that.
        """
        url = API + quote(repo, safe="") + "/git/trees/" + quote(ref, safe="")
        try:
            response = self.ctx.http.get(url)
        except RuntimeError:
            # The broker's own refusals -- the size ceiling above, a
            # timeout, an allowlist block -- arrive as RuntimeError
            # carrying the host's message.
            return None
        if response.status_code == 404:
            return {}
        if response.status_code in (403, 429):
            return None
        if response.status_code != 200:
            raise NoThumbnail(
                f"GitHub answered HTTP {response.status_code} for the "
                f"{system!r} {what} listing of libretro's thumbnails"
            )
        try:
            return json.loads(response.text)
        except ValueError:
            return None

    # -- choosing between releases ---------------------------------------

    @staticmethod
    def _preferred_region(labels) -> str | None:
        for label in labels:
            for region in _REGIONS:
                if f"({region}" in (label or ""):
                    return region
        return None

    @staticmethod
    def _rank(name: str, preferred: str | None) -> tuple:
        unwanted = 1 if _UNWANTED.search(name) else 0
        if preferred and f"({preferred}" in name:
            region = -1
        else:
            region = next(
                (i for i, r in enumerate(_REGIONS) if f"({r}" in name), len(_REGIONS)
            )
        return (unwanted, region, len(name), name)

    # -- refusals --------------------------------------------------------

    @staticmethod
    def _refusal(rom, system, kinds, names, indexed=False, unreadable=()) -> str:
        tried = ", ".join(repr(n) for n in names)
        sets = ", ".join(KINDS[kind] for kind in kinds)
        extra = ""
        if indexed:
            extra = (
                " Those directories were listed too, and no entry matches "
                "this title once tags, punctuation and articles are ignored."
            )
        if unreadable:
            # Named, because "no match" and "the fallback could not run" are
            # different problems and only one of them is about the name.
            # libretro's NES Named_Titles listing is 4,297,395 bytes and
            # ctx.http refuses anything over 4 MiB.
            extra += (
                f" The listing for {', '.join(unreadable)} could not be read "
                f"(the Hub caps a plugin's response at 4 MiB and libretro's "
                f"largest directory indexes are over it, or GitHub's API rate "
                f"limit was reached), so the title match "
                f"never ran for {'that set' if len(unreadable) == 1 else 'those sets'}."
            )
        return (
            f"libretro has no image for rom {rom.rom_id} "
            f"({rom.name or rom.filename!r}) under {system!r} in {sets}. "
            f"Tried: {tried}.{extra} If the library's name differs from "
            f"libretro's, pass the exact one with --source-id."
        )


def _symlink_target(body: str) -> str | None:
    """The target named by a git symlink's body, or None for image bytes."""
    text = body.strip()
    if not text or len(text) > 512 or "\n" in text or "\ufffd" in text:
        return None
    return text if text.lower().endswith(".png") else None


def _sibling(url: str, target: str) -> str:
    """Resolve a symlink target against the link's directory, inside the repo."""
    parts = urlsplit(url)
    directory = posixpath.dirname(unquote(parts.path))
    path = posixpath.normpath(posixpath.join(directory, target))
    # /libretro-thumbnails/<repo>/HEAD/... : never climb out of the repository.
    prefix = "/".join(unquote(parts.path).split("/")[:4])
    if not path.startswith(prefix + "/"):
        raise NoThumbnail(f"libretro's thumbnail symlink {target!r} points outside its repository")
    return f"{parts.scheme}://{parts.netloc}" + quote(path, safe="/")
