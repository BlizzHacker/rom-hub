"""itch.io `stream`: the one thing this plugin can actually hand you.

    source_id -> the game page -> StreamTarget(url=<dev>.itch.io/<game>)

**This is the capability that answers the plugin's own complaint.** Its
`importer` refuses by design and always will -- itch.io issues a download
URL only for a POST carrying the page's csrf_token, `/game/download/` is
Disallowed in robots.txt, and this Hub's broker performs GET only. So
nothing here will ever put a file in a library. But a very large share of
itch.io's free catalogue is *browser* software, and for those the file was
never the point: the game runs on the page.

**The target is the game page and never the embed.** itch.io's robots.txt
carries `Disallow: /embed/` and `Disallow: /embed-upload/`, and the page's
own markup hands the iframe a `html-classic.itch.zone` URL. Neither is
returned. The page is what a person opens, it is the thing itch.io means
to be opened, and it is on a host this plugin already declares. Pulling
the inner URL out of the markup to look more direct would be reaching
around two robots directives to arrive somewhere worse.

**The gate is `html_embed_widget`, and it is itch.io's own marker.** A
game page with a browser build renders that widget, wrapping a
`game_frame` and a `load_iframe_btn`; a download-only page renders none of
it. Checked live 2026-08-01 across both shapes -- present exactly once on
each of two web games, absent on a download-only one. The browse cell's
`web_flag` says the same thing a page earlier, which is why `search`
already reports `browser` in `extra.platforms` and this only has to
confirm it.

**Confirming costs one request and is not skipped.** The listing is a
popularity-ordered slice that can be minutes stale and a developer can
remove a web build; streaming an operator to a page with no game on it is
a small failure and it is still a failure this can cheaply avoid.
"""

import re

from rom_hub_sdk import SearchResult, StreamProvider, StreamTarget

from .metadata import _GAME_URL, _SOURCE_ID, heading_title, product_name

#: itch.io's own wrapper for a browser build. Present on a page with one,
#: absent on a page without. Matched on the class rather than on the
#: element, because attribute order on itch.io is not stable -- the same
#: lesson `browse.py` learned from the listing markup.
_EMBED = re.compile(r'class="[^"]*\bhtml_embed_widget\b')

#: The tag the widget wraps. Not required -- the widget alone decides --
#: but its size is carried in `extra` so an operator can see the page
#: really has a player rather than taking this plugin's word for it.
#:
#: The whole tag is matched and the two attributes are then pulled out of
#: it separately, because **itch.io's attribute order is not stable**: the
#: live page emits `<div data-width="640" data-height="480"
#: class="game_frame game_pending">`, with the class last, and a pattern
#: anchored on `class=...data-height=` finds nothing on it. Same lesson
#: `browse.py` and `metadata.py` each learned from a different piece of
#: this site's markup, which is why it is spelled out a third time.
_FRAME_TAG = re.compile(r"<div\b[^>]*\bgame_frame\b[^>]*>")
_DATA_HEIGHT = re.compile(r'data-height="(\d+)"')
_DATA_WIDTH = re.compile(r'data-width="(\d+)"')


class NotIdentified(Exception):
    """No itch.io game id was supplied, and one cannot be guessed."""


class StreamRefused(Exception):
    """This game cannot be streamed, and the message says why."""


def has_web_build(page: str) -> bool:
    """Whether this game page renders a browser build."""
    return bool(_EMBED.search(page or ""))


class Stream(StreamProvider):
    def resolve(self, result: SearchResult) -> StreamTarget:
        developer, game = self._identify(result)
        page_url = f"https://{developer}.itch.io/{game}"
        page = self._page(page_url)

        if not has_web_build(page):
            raise StreamRefused(
                f"itch.io game {developer}/{game} has no browser build: its "
                f"page renders no `html_embed_widget`, so there is nothing on "
                f"it to play. itch.io only ever issues a download URL for a "
                f"POST carrying the page's csrf_token and `/game/download/` "
                f"is Disallowed in its robots.txt, so this plugin cannot "
                f"fetch the downloadable build either -- open the page and "
                f"take it from itch.io yourself."
            )

        extra = {
            "developer": developer,
            "game": game,
            # itch.io's own vocabulary, so a caller can see the answer
            # came from the page and not from this plugin's opinion.
            "web_build": "true",
        }
        frame = _FRAME_TAG.search(page)
        if frame:
            tag = frame.group(0)
            height = _DATA_HEIGHT.search(tag)
            width = _DATA_WIDTH.search(tag)
            if height:
                extra["frame_height"] = height.group(1)
            if width:
                extra["frame_width"] = width.group(1)

        title = product_name(page) or heading_title(page) or result.title

        return StreamTarget(
            kind="url",
            target=page_url,
            mime_type="text/html",
            title=title or None,
            extra=extra,
        )

    @staticmethod
    def _identify(result: SearchResult) -> tuple[str, str]:
        raw = (result.source_id or "").strip()
        if not raw:
            raise NotIdentified(
                "the search result carries no itch.io game id; expected "
                "'<developer>/<game>', as in '13-23/petal'"
            )
        match = _SOURCE_ID.match(raw) or _GAME_URL.match(raw)
        if not match:
            raise NotIdentified(
                f"{raw!r} is not an itch.io game id: expected "
                f"'<developer>/<game>', as in '13-23/petal', or the game's "
                f"page URL."
            )
        return match.group("developer"), match.group("game")

    def _page(self, url: str) -> str:
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise StreamRefused(
                f"itch.io returned HTTP {response.status_code} for {url!r}"
            )
        return response.text
