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

_HREF_RE = re.compile(r'href="([^"]+?\.png)"', re.IGNORECASE)


class NoThumbnail(Exception):
    """No thumbnail could be identified for this rom, and the message says
    which spellings were tried."""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        system = system_for(rom.platform)
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
                url = self._url(system, kind, name)
                if self._exists(url):
                    return MetadataPatch(artwork_url=url)

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
                return MetadataPatch(artwork_url=self._url(system, kind, found))

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
        url = BASE + quote(system, safe="") + "/" + KINDS[kind] + "/"
        try:
            response = self.ctx.http.get(url)
        except RuntimeError:
            # The broker's own refusals -- the size ceiling above, a
            # timeout, an allowlist block -- arrive as RuntimeError
            # carrying the host's message.
            return None
        if response.status_code == 404:
            # This system has no directory for this set at all, which is
            # ordinary -- not every system carries all four. An answer,
            # not a fault, exactly as a 404 on a single file is: the
            # caller moves to the next kind in the chain. Raising here is
            # what made the chain abort on the first system that happened
            # to lack `Named_Titles`.
            return []
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
                f"largest directory indexes are over it), so the title match "
                f"never ran for {'that set' if len(unreadable) == 1 else 'those sets'}."
            )
        return (
            f"libretro has no image for rom {rom.rom_id} "
            f"({rom.name or rom.filename!r}) under {system!r} in {sets}. "
            f"Tried: {tried}.{extra} If the library's name differs from "
            f"libretro's, pass the exact one with --source-id."
        )
