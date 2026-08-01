"""Demozoo `metadata`: the scene's own title, and the screenshot it kept.

    RomRef -> a production -> MetadataPatch(name, artwork_url)

The plugin never fetches the picture. It names a URL and the **host**
fetches it, after checking that URL against this plugin's own `network`
allowlist -- the same rule a FetchPlan URL follows, for the same reason.
That is why `media.demozoo.org` is in the manifest as of 0.2.0 and was
not before: nothing used to need it.

**What Demozoo has and what can be delivered, kept apart.** A production
record carries a release date, an author (often several groups), a
platform list, a type list, tags, the party it was released at, the
competition it placed in and a full credit list -- code, graphics, music,
by nick. Almost none of that reaches RomM: 4.9.2's update endpoint takes
a name, a set of provider ids and a cover, and it accepts all eight
`raw_*_metadata` fields with HTTP 200 while **storing none of them**
(verified against a live 4.9.2). Putting a party name in a field that
belongs to IGDB would also be a lie in somebody else's column. So the
party, the credits and the placing stay in `SearchResult.extra`, where an
operator can read them, and this capability proposes the two things a
library keeps.

**`name` is worth writing because a scene filename is not a title.** A
Demozoo import lands as `sr.zip` or `fc-sr.lha`; the production is called
*Second Reality*. Demozoo is a curated database with a public edit
history, so its title is as authoritative as this material has.

**The screenshot is the production, not a box.** The demoscene has no box
art -- there was never a box -- and Demozoo's screenshots are frames
captured from the production itself, which is the closest thing to a
cover that exists for a demo. `standard_url` rather than `original_url`:
the originals run to a machine's full framebuffer at its native
resolution, the standard rendering is the one Demozoo itself shows, and
`MetadataPatch` caps artwork at 8 MB. First screenshot only; the rest are
later frames of the same production.

**Resolution is exact, and Demozoo's own API makes that easy for once.**
`?title=` is an exact, case-insensitive match rather than a substring
search -- `title=crackt` returns zero -- which is a limitation everywhere
else in this plugin and precisely the right behaviour here. A rom is
matched to one production or to none.
"""

import json

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .filenames import safe_filename
from .platforms import demozoo_ids_for_slug
from .productions import is_importable, parse_production

ENDPOINT = "https://demozoo.org/api/v1/productions/"

#: The one host Demozoo serves its own screenshots from. Every
#: `original_url`, `standard_url` and `thumbnail_url` in the API is on it
#: (checked across the sampled detail records), it answers 200 with zero
#: redirects, and it is in the manifest for this capability alone.
MEDIA_HOST = "https://media.demozoo.org/"

DEFAULT_ARTWORK_FILENAME = "cover.png"

#: Where a Demozoo production id may arrive. `source_id` is what the CLI's
#: `--source-id` fills in, and is the route that skips matching entirely.
ID_KEYS = ("demozoo_id", "production_id", "source_id")


class NoMatch(Exception):
    """No Demozoo production could be identified for this rom."""


class Ambiguous(Exception):
    """Several productions carry this title, and choosing between them is
    not this plugin's call to make."""


class ApiFailed(Exception):
    """Demozoo answered, but not with a production."""


def _strip_extension(name: str) -> str:
    """`sr.zip` -> `sr`; `Mr. Driller` unchanged.

    RomM derives a rom's name from the file it was uploaded as, so the
    library's `name` for a scene import is very often a filename. Only a
    two-to-five character alphanumeric last segment counts, which leaves
    a title with a real dot in it alone.
    """
    stem, dot, extension = (name or "").rpartition(".")
    if not dot or not stem:
        return name or ""
    if not (2 <= len(extension) <= 5) or not extension.isalnum():
        return name
    return stem.strip()


class Metadata(MetadataProvider):
    def enrich(self, rom: RomRef) -> MetadataPatch:
        production, detail = self._resolve(rom)

        patch: dict = {}
        if self._set_name() and production.title:
            patch["name"] = production.title

        artwork = self._screenshot(detail)
        if artwork is not None:
            patch["artwork_url"] = artwork
            patch["artwork_filename"] = safe_filename(
                artwork.rsplit("/", 1)[-1], fallback=DEFAULT_ARTWORK_FILENAME
            )

        if not patch:
            raise NoMatch(
                f"Demozoo production {production.id} ({production.title!r}) "
                f"has no screenshot, and `set_name` is off, so there is "
                f"nothing left for this plugin to propose."
            )
        return MetadataPatch(**patch)

    # -- what to propose -------------------------------------------------

    @staticmethod
    def _screenshot(detail: dict) -> str | None:
        """The first screenshot's standard rendering, or None.

        `standard_url` and not `original_url`: an original is the
        production's own framebuffer at its native size, the standard one
        is what Demozoo itself renders, and `MetadataPatch` caps artwork
        at 8 MB. Anything not on `media.demozoo.org` is ignored rather
        than named -- the host would refuse it, and a plugin that names a
        URL it knows will be refused turns a shrug into an error.
        """
        shots = detail.get("screenshots")
        if not isinstance(shots, list):
            return None
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            for key in ("standard_url", "original_url"):
                url = shot.get(key)
                if isinstance(url, str) and url.startswith(MEDIA_HOST):
                    return url
        return None

    def _set_name(self) -> bool:
        return bool(self.ctx.config.get("set_name", True))

    # -- resolution ------------------------------------------------------

    def _resolve(self, rom: RomRef):
        """(production, detail record), or a refusal saying why not."""
        production_id = self._id(rom)
        if production_id is not None:
            detail = self._detail(production_id)
            production = parse_production(detail)
            if production is None:
                raise NoMatch(
                    f"Demozoo production {production_id} could not be read as "
                    f"a production record"
                )
            return production, detail

        for title in self._candidate_titles(rom):
            matches = self._by_title(title, rom)
            if len(matches) == 1:
                production = matches[0]
                return production, self._detail(production.id)
            if len(matches) > 1:
                ids = ", ".join(str(p.id) for p in matches)
                raise Ambiguous(
                    f"{len(matches)} Demozoo productions are titled {title!r}: "
                    f"{ids}. The scene reuses titles constantly -- there are "
                    f"seven productions called 'Second Reality' -- so which "
                    f"one this rom is, is not a choice this plugin will make "
                    f"for you. Pass --source-id with the id you want."
                )

        raise NoMatch(
            f"Demozoo has no production titled "
            f"{(rom.name or rom.filename or '')!r}"
            f"{f' on {rom.platform}' if rom.platform else ''}. Demozoo's "
            f"`title=` is an exact match rather than a substring search "
            f"(`title=crackt` returns zero), which is a limitation "
            f"everywhere else in this plugin and the right behaviour here. "
            f"Pass --source-id with the production id if the scene spells "
            f"it differently."
        )

    def _by_title(self, title: str, rom: RomRef) -> list:
        """Importable productions with exactly this title, narrowed by
        platform where the rom names one Demozoo indexes.

        RomM's `amiga` is three Demozoo platforms, so a platform-narrowed
        lookup can cost three requests. Worth it: without the narrowing,
        a title shared between a C64 demo and a Windows one is ambiguous
        and refuses, and with it the answer is usually singular.
        """
        streams: list[dict] = [{"format": "json", "title": title}]
        ids = demozoo_ids_for_slug(rom.platform or "")
        if ids:
            streams = [dict(streams[0], platform=pid) for pid in ids]

        found: dict[int, object] = {}
        for params in streams:
            for raw in self._page(params).get("results") or []:
                production = parse_production(raw)
                if production is None or not is_importable(production):
                    continue
                found[production.id] = production
        return list(found.values())

    @classmethod
    def _candidate_titles(cls, rom: RomRef) -> list[str]:
        """Spellings to try, best first, de-duplicated.

        The library's name as given, then the same with a trailing
        extension removed, then the filename's stem. Each is matched
        *exactly* by Demozoo, so a wrong guess costs a request and a miss
        rather than another group's screenshot.
        """
        raw = (rom.name or "").strip()
        stem = (rom.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        candidates = [raw, _strip_extension(raw), _strip_extension(stem)]
        seen: list[str] = []
        for candidate in candidates:
            candidate = " ".join((candidate or "").split())
            if candidate and candidate not in seen:
                seen.append(candidate)
        return seen

    @staticmethod
    def _id(rom: RomRef) -> int | None:
        for key in ID_KEYS:
            value = (rom.extra.get(key) or "").strip()
            if value.isdigit() and int(value) > 0:
                return int(value)
        return None

    # -- the network -----------------------------------------------------

    def _detail(self, production_id: int) -> dict:
        return self._get(f"{ENDPOINT}{production_id}/", {"format": "json"})

    def _page(self, params: dict) -> dict:
        return self._get(ENDPOINT, params)

    def _get(self, url: str, params: dict) -> dict:
        response = self.ctx.http.get(url, params=params)
        if response.status_code != 200:
            raise ApiFailed(
                f"Demozoo answered HTTP {response.status_code} for {url!r} "
                f"with {params!r}"
            )
        try:
            body = json.loads(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            # Maintenance pages and rate limiters both arrive as 200+HTML.
            raise ApiFailed(f"Demozoo's response was not JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ApiFailed("Demozoo's response was not a JSON object")
        return body
