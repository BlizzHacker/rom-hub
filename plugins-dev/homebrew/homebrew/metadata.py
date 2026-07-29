"""Homebrew Hub `metadata`: the submitter's own title and cover.

    RomRef -> a Hub entry -> MetadataPatch(name, artwork_url)

The plugin never fetches the cover. It names a URL and the **host**
fetches it, after checking that URL against this plugin's own `network`
allowlist -- the same rule a FetchPlan URL follows, for the same reason.

**Why this source may set `name` when libretro-thumbnails may not.** The
libretro plugin refuses to write a title because what it has is a
No-Intro DAT string: a filename from a different project, not a curated
name. The Homebrew Hub's title is the one the *author* submitted with the
game. For homebrew there is no publisher-of-record other than the person
who wrote it, so this is as close to an authoritative title as the
material has. It is still `set_name` config, defaulting on, because an
operator who has curated their library is entitled to keep their spelling.

**Only a file actually named `cover.*` becomes artwork.** Roughly half
the Hub's entries carry one; the rest have in-game screenshots, and
promoting a screenshot to box art would fill a library with pictures of
gameplay that somebody then has to undo one at a time. No cover means no
`artwork_url`, which `MetadataPatch` reads as "leave RomM's alone".

**Resolution is exact or it is a refusal.** The Hub's lookup is a text
search, so it answers with near misses. Attaching the wrong homebrew
game's title and cover to a rom is the failure this whole codebase is
built to avoid, so a query that does not land on exactly one entry raises
and names the candidates it saw.
"""

import json
import posixpath
import re

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .filenames import safe_filename
from .hub import API, parse_page
from .platforms import hub_platform_for

DEFAULT_ARTWORK_FILENAME = "cover.png"

# Where a Hub slug may arrive. `source_id` is what the CLI's --source-id
# fills in, and is the route that skips searching entirely.
SLUG_KEYS = ("homebrew_slug", "slug", "source_id")

# Matching two titles. Case, punctuation and runs of whitespace are noise
# -- "Super Snake Off" and "super snake-off" are the same game -- but the
# comparison stays an equality test on the scrubbed form. A prefix or
# substring test would make "Snake" match "Snake II".
_NOISE = re.compile(r"[^0-9a-z]+")


def match_key(label: str) -> str:
    """A title reduced to what two spellings of it have in common."""
    if not isinstance(label, str):
        return ""
    return _NOISE.sub("", label.lower())


class NoMatch(Exception):
    """No Homebrew Hub entry could be identified for this rom."""


class Ambiguous(Exception):
    """Several entries match, and choosing between them is not this
    plugin's call to make."""


class ApiFailed(Exception):
    """The Homebrew Hub answered, but not with an entry list."""


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        entry = self._resolve(rom)

        patch: dict = {}
        if self._set_name() and entry.title:
            patch["name"] = entry.title

        cover = entry.cover()
        if cover is not None:
            patch["artwork_url"] = entry.static_url(cover)
            patch["artwork_filename"] = safe_filename(
                posixpath.basename(cover), fallback=DEFAULT_ARTWORK_FILENAME
            )

        if not patch:
            # Nothing resolved. Returning an empty patch would have the
            # host report a successful enrich that changed nothing, which
            # reads as "there was nothing to add" rather than "this
            # plugin was configured not to add the only thing it had".
            raise NoMatch(
                f"Homebrew Hub entry {entry.slug!r} ({entry.title!r}) has no "
                f"cover image, and `set_name` is off, so there is nothing "
                f"left for this plugin to propose."
            )
        return MetadataPatch(**patch)

    # -- configuration ---------------------------------------------------

    def _set_name(self) -> bool:
        return bool(self.ctx.config.get("set_name", True))

    # -- resolution ------------------------------------------------------

    def _resolve(self, rom: RomRef):
        """The one Hub entry this rom is, or a refusal saying why not."""
        slug = self._slug(rom)
        if slug:
            entry = next((e for e in self._query({"q": slug}) if e.slug == slug), None)
            if entry is None:
                raise NoMatch(
                    f"no Homebrew Hub entry has the slug {slug!r}. The Hub's "
                    f"lookup is a text search, so it answers near misses; "
                    f"taking one would attach another game's title and cover."
                )
            return entry

        title = (rom.name or "").strip() or self._title_from_filename(rom.filename)
        if not title:
            raise NoMatch(
                f"rom {rom.rom_id} has neither a name nor a filename in the "
                f"library, so there is nothing to look up. Pass --source-id "
                f"with the Homebrew Hub slug instead."
            )

        params: dict[str, str | int] = {"q": title}
        hub_platform = self._hub_platform(rom)
        if hub_platform:
            params["platform"] = hub_platform

        wanted = match_key(title)
        matches = [e for e in self._query(params) if match_key(e.title) == wanted]
        if not matches:
            raise NoMatch(
                f"the Homebrew Hub has no entry titled {title!r}"
                f"{f' on {hub_platform}' if hub_platform else ''}. Matching is "
                f"exact once case and punctuation are ignored, deliberately: a "
                f"close-enough match would attach the wrong game's cover. If "
                f"the Hub spells it differently, pass --source-id with its slug."
            )
        if len(matches) > 1:
            names = ", ".join(sorted(e.slug for e in matches))
            raise Ambiguous(
                f"{len(matches)} Homebrew Hub entries are titled {title!r}: "
                f"{names}. Which one this rom is, is not a choice this plugin "
                f"will make for you -- pass --source-id with the slug you want."
            )
        return matches[0]

    def _slug(self, rom: RomRef) -> str:
        for key in SLUG_KEYS:
            value = (rom.extra.get(key) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _title_from_filename(filename: str) -> str:
        """A title guessed from a filename is still only used to *search*.

        Whatever comes out of here is matched exactly against the Hub's own
        title before anything is proposed, so a bad guess costs a miss, not
        a wrong cover.
        """
        stem = posixpath.basename((filename or "").replace("\\", "/"))
        stem = stem.rsplit(".", 1)[0] if "." in stem else stem
        return " ".join(stem.replace("_", " ").split())

    def _hub_platform(self, rom: RomRef) -> str:
        """The Hub's own platform token for this rom, if it has one.

        Narrowing the search is worth a lot here: the Hub's `q` is a text
        search over 1,500-plus entries and a platform filter is the
        difference between one candidate and several. An unmapped platform
        is not an error -- the Hub covers four systems, so a rom on a
        fifth simply has no entry, and that is a miss rather than a fault.
        """
        return hub_platform_for(rom.platform or "") or ""

    # -- the network -----------------------------------------------------

    def _query(self, params: dict):
        """One page of Hub results. One request per enrich, deliberately.

        The Hub serves ten entries a page and the exact-title match either
        lands on the first page or the query was too vague to page through
        usefully.
        """
        response = self.ctx.http.get(API, params={**params, "page": 1})
        if response.status_code != 200:
            raise ApiFailed(
                f"the Homebrew Hub returned HTTP {response.status_code} for "
                f"{params!r}"
            )
        try:
            payload = json.loads(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ApiFailed(
                f"the Homebrew Hub's answer for {params!r} was not JSON: {exc}"
            ) from exc
        entries, _ = parse_page(payload)
        return entries
