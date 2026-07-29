"""libretro-thumbnails `metadata`: box art for a rom already in RomM.

    RomRef -> libretro system -> candidate filenames -> a URL that answers 200

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

import html
import re
from urllib.parse import quote, unquote

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .names import candidates, match_key, scrub
from .systems import NeedsMapping, system_for  # noqa: F401  (re-exported)

BASE = "https://thumbnails.libretro.com/"

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

# Region tags, best first. Used only to choose between several releases of
# a title that has already been matched exactly -- never to match one.
_REGIONS = ("USA", "World", "Europe", "Japan")

# Tags that mark a release nobody wants as their cover.
_UNWANTED = re.compile(
    r"\((Beta|Proto|Prototype|Demo|Sample|Pirate|Unl|Hack|Aftermarket)[^)]*\)"
    r"|\[b[\d]*\]|\[h[^\]]*\]|\[p[\d]*\]",
    re.IGNORECASE,
)

_HREF_RE = re.compile(r'href="([^"]+?\.png)"', re.IGNORECASE)


class NoThumbnail(Exception):
    """No thumbnail could be identified for this rom, and the message says
    which spellings were tried."""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        system = system_for(rom.platform)
        kind = self._kind()

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

        for name in names:
            url = self._url(system, kind, name)
            if self._exists(url):
                return MetadataPatch(artwork_url=url)

        if override or not self._index_fallback():
            raise NoThumbnail(self._refusal(rom, system, kind, names))

        found = self._from_index(system, kind, [rom.name, rom.filename])
        if found is not None:
            return MetadataPatch(artwork_url=self._url(system, kind, found))

        raise NoThumbnail(self._refusal(rom, system, kind, names, indexed=True))

    # -- configuration ---------------------------------------------------

    def _kind(self) -> str:
        chosen = str(self.ctx.config.get("art_kind") or "boxart").strip().lower()
        if chosen not in KINDS:
            raise NoThumbnail(
                f"art_kind {chosen!r} is not one of {sorted(KINDS)}; libretro "
                f"serves those four sets and no others"
            )
        return chosen

    def _index_fallback(self) -> bool:
        return bool(self.ctx.config.get("index_fallback", True))

    # -- the network -----------------------------------------------------

    def _url(self, system: str, kind: str, name: str) -> str:
        return (
            BASE
            + quote(system, safe="")
            + "/"
            + KINDS[kind]
            + "/"
            + quote(f"{name}.png", safe="")
        )

    def _exists(self, url: str) -> bool:
        """True if libretro serves this file.

        A 404 is an answer, not a fault: it means "try the next spelling".
        Anything else is the service being unwell, and probing it seven
        more times would be both rude and useless, so it stops here.
        """
        response = self.ctx.http.get(url)
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise NoThumbnail(
            f"libretro's thumbnail server answered HTTP {response.status_code} "
            f"for {url!r}; nothing was proposed for this rom"
        )

    def _from_index(self, system: str, kind: str, labels) -> str | None:
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

        listing = self._index(system, kind)
        matches = [name for name in listing if match_key(name) in wanted]
        if not matches:
            return None
        # Several releases of one title. Prefer the region the library
        # already names, then USA/World/Europe/Japan, then the plainest.
        preferred = self._preferred_region(labels)
        return min(matches, key=lambda n: self._rank(n, preferred))

    def _index(self, system: str, kind: str) -> list[str]:
        url = BASE + quote(system, safe="") + "/" + KINDS[kind] + "/"
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise NoThumbnail(
                f"libretro's thumbnail server answered HTTP "
                f"{response.status_code} for the {system!r} {kind} listing"
            )
        return [
            unquote(html.unescape(match.group(1))).removesuffix(".png")
            for match in _HREF_RE.finditer(response.text)
        ]

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
    def _refusal(rom, system, kind, names, indexed=False) -> str:
        tried = ", ".join(repr(n) for n in names)
        extra = (
            " The directory listing was read too, and no entry matches this "
            "title once tags, punctuation and articles are ignored."
            if indexed
            else ""
        )
        return (
            f"libretro has no {kind} for rom {rom.rom_id} "
            f"({rom.name or rom.filename!r}) under {system!r}. Tried: {tried}."
            f"{extra} If the library's name differs from libretro's, pass the "
            f"exact one with --source-id."
        )
