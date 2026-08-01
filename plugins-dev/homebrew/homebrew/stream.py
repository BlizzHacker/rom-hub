"""Homebrew Hub `stream`: the entry's own page, which is a player.

    SearchResult -> a Hub entry -> StreamTarget(url=hh.gbdev.io/game/<slug>)

The Homebrew Hub describes itself, in its backend's own README, as "the
largest digital archive of Game Boy, GBC, GBA, and NES homebrew software,
**playable natively in your browser**". That is not a claim this plugin
makes on its behalf: the frontend (`gbdev/virens`) ships WebAssembly
builds of binjgb, binjnes and mGBA, and `pages/game/[slug].vue` mounts the
emulator on the entry page itself. So the page *is* the player, and the
whole job of this capability is to resolve a search result to it.

**The gate is `files[].playable`, and it is the frontend's own rule.**
`[slug].vue` walks `game.files` and points its emulator at the one flagged
`playable`; an entry with none renders a details page with no player on
it. 1,565 of the Hub's 1,571 entries have one -- counted over the whole
catalogue on 2026-08-01 -- and the six that do not refuse here by name
rather than being handed a URL that will show them nothing.

**Nothing is invented.** There is no media endpoint and no embed URL made
up to look like one; the ROM the player loads is served from
`hh3.gbdev.io/static/...` and is what `importer` already plans, so a
fabricated "stream URL" would be a second, worse spelling of a file the
operator can simply have. The target is the page, and the host checks it
against this plugin's allowlist like any other URL it is handed.

**Why this needed a new host in the manifest.** `hh.gbdev.io` was
deliberately absent while `SearchResult.url` was the only thing pointing
at it -- a displayed URL is never fetched, and an allowlist should say
where a plugin causes traffic. A `StreamTarget(kind="url")` is different:
the host validates it and hands it to something that will open it, so the
host has to be declared. It is, and only for this.

Everything here is derived from published source rather than from the
site, because `hh.gbdev.io/robots.txt` carries `User-agent: ClaudeBot /
Disallow: /`.
"""

import json

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget

from .hub import API, HubError, parse_page
from .platforms import platform_for


class StreamRefused(Exception):
    """This entry cannot be streamed, and the message says why."""


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        slug = (result.source_id or "").strip()
        if not slug:
            raise StreamRefused(
                "the search result carries no Homebrew Hub slug; expected "
                "something like 'dorotea'"
            )

        entry = self._entry(slug)
        playable = entry.playable_file
        if playable is None:
            raise StreamRefused(
                f"Homebrew Hub entry {slug!r} ({entry.title!r}) has no file "
                f"flagged playable, so its page renders no emulator at all. "
                f"Six of the Hub's 1,571 entries are like this -- usually a "
                f"source drop or a patch rather than a ROM. `rom-hub import` "
                f"still works for it."
            )

        extra = {
            "slug": entry.slug,
            "hub_platform": entry.platform or "",
            "rom": playable.filename,
            "developer": entry.developer,
        }
        # Not required to play -- the page runs whatever it is -- but a
        # caller choosing between this and a local player wants the
        # machine named in the library's own vocabulary.
        platform = platform_for(entry.platform or "")
        if platform:
            extra["platform"] = platform
        if entry.license:
            extra["license"] = entry.license

        return StreamTarget(
            kind="url",
            target=entry.site_url,
            mime_type="text/html",
            title=entry.title or None,
            extra=extra,
        )

    def _entry(self, slug: str):
        """The one entry with this slug, or a refusal.

        The Hub's lookup is a text search, so it answers with near misses
        -- `q=snake` returns eleven entries. Streaming the wrong game is a
        smaller mistake than importing it and it is still a mistake, so
        the slug has to match exactly.
        """
        response = self.ctx.http.get(API, params={"q": slug, "page": 1})
        if response.status_code != 200:
            raise HubError(
                f"the Homebrew Hub returned HTTP {response.status_code} "
                f"looking up {slug!r}"
            )
        try:
            payload = json.loads(response.text)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HubError(
                f"the Homebrew Hub's answer for {slug!r} was not JSON: {exc}"
            ) from exc
        entries, _ = parse_page(payload)
        entry = next((e for e in entries if e.slug == slug), None)
        if entry is None:
            raise StreamRefused(
                f"no Homebrew Hub entry has the slug {slug!r}. The Hub's "
                f"lookup is a text search, so it answers near misses; taking "
                f"one would point the player at another game."
            )
        return entry
