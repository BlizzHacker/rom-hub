"""itch.io `importer`: decide what a game page permits, and say so.

**THIS PLUGIN IS SEARCH-ONLY.** `plan()` has five exits and all five raise
ImportRefused. There is no success path, and adding one is not a matter of
finishing the code -- it needs a verb the broker does not offer.

This importer routes a game page to one of five outcomes, and **every one
of them is a refusal**. That is not a stub; it is the accurate answer for
this source, and the reason is worth stating once rather than discovering
it per title:

itch.io hands out download URLs only in response to a **POST** to
`/<game>/file/<upload_id>` carrying the game page's `csrf_token`. The reply
is a short-lived pre-signed object-store URL. ROM Hub's broker offers
`http.get` and nothing else -- deliberately, since a plugin has no sockets
-- and the host fetches `FetchPlan` URLs with GET too. Checked against the
live site: a GET to that endpoint answers `302` back to the game page,
`itch.io/game/download/<id>` is disallowed for every user-agent in
itch.io's robots.txt, and `api.itch.io/uploads/<id>/download` answers
`401`.

So the last outcome is *"this game is free and downloadable, here is
exactly which file and which RomM platform, and itch.io will not serve it
over GET"*. Everything before it is real work -- checkout detection, key
detection, payload selection, platform mapping, filename sanitisation --
and it all runs, because the alternative was returning a plan whose URL
answers `302` with an HTML page that the host would then hash, upload, and
file in RomM as a ROM. A visible refusal beats an import that succeeds with
the wrong bytes; that is the same rule `archive_org` applies to
`stream_only` items.
"""

import html
import re

from rom_hub_sdk import FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename
from .platforms import platform_for

# https://<developer>.itch.io/<game>, as `search` reports it.
_SOURCE_ID = re.compile(
    r"\A(?P<developer>[A-Za-z0-9][A-Za-z0-9-]*)/(?P<game>[A-Za-z0-9][A-Za-z0-9._-]*)\Z"
)

_UPLOAD_MARKER = '<div class="upload">'
# The file list is the last thing in its section; these end it. Without a
# bound, the final upload's block would run to the end of the document and
# pick up icons and sizes belonging to devlogs and comments below it.
_BLOCK_ENDS = ("<section", "</section>", "<footer")
_BLOCK_CHARS = 8000

_UPLOAD_NAME = re.compile(
    r'<strong[^>]*\btitle="([^"]*)"[^>]*\bclass="name"'
    r'|<strong[^>]*\bclass="name"[^>]*\btitle="([^"]*)"'
)
_UPLOAD_ID = re.compile(
    r'class="button download_btn"[^>]*\bdata-upload_id="(\d+)"'
    r'|\bdata-upload_id="(\d+)"[^>]*class="button download_btn"'
)
_FILE_SIZE = re.compile(r'<span class="file_size"><span>([^<]*)</span>')
_UPLOAD_PLATFORMS = re.compile(
    r'<span class="download_platforms">(.*?)</span>\s*</div>', re.DOTALL
)
_ICON_TITLE = re.compile(r'title="([^"]*)"')
_BUY_BTN = re.compile(r'class="[^"]*\bbuy_btn\b[^"]*"')
_BUY_MESSAGE = re.compile(r'<span class="buy_message">(.*?)</span>\s*</div>', re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")

# itch.io prints decimal units: "137 MB" is 137 * 10^6, not 2^20.
_SIZE_UNITS = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}
_SIZE_RE = re.compile(r"\A([\d.,]+)\s*([kmgt]?b)\Z", re.IGNORECASE)


class ImportRefused(Exception):
    """This game cannot be imported, and the message says why."""


def _text(fragment: str) -> str:
    return " ".join(html.unescape(_TAGS.sub(" ", fragment or "")).split())


def parse_size(raw: str) -> int | None:
    """`"137 MB"` -> bytes, or None when itch.io printed something else.

    Only ever a hint: a wrong guess here must not be able to fail a plan.
    """
    match = _SIZE_RE.match((raw or "").strip())
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    if value < 0:
        return None
    return int(value * _SIZE_UNITS[match.group(2).lower()])


class Upload:
    """One row of a game page's file list."""

    def __init__(
        self, name: str, upload_id: str, size_bytes: int | None, labels: list[str]
    ):
        self.name = name
        self.upload_id = upload_id
        self.size_bytes = size_bytes
        self.labels = labels

    @property
    def downloadable(self) -> bool:
        """True when itch.io rendered a Download button for this row.

        A row without one is a file the page is *describing*: what you get
        after paying, or after claiming a key.
        """
        return bool(self.upload_id)


def _blocks(page: str) -> list[str]:
    """The page's upload rows, each bounded so it cannot read the next."""
    parts = (page or "").split(_UPLOAD_MARKER)[1:]
    blocks = []
    for index, part in enumerate(parts):
        if index == len(parts) - 1:
            cuts = [part.find(end) for end in _BLOCK_ENDS]
            cut = min([c for c in cuts if c != -1], default=len(part))
            part = part[: min(cut, _BLOCK_CHARS)]
        blocks.append(part)
    return blocks


def parse_uploads(page: str) -> list[Upload]:
    uploads: list[Upload] = []
    for block in _blocks(page):
        name_match = _UPLOAD_NAME.search(block)
        name = html.unescape(
            next((g for g in (name_match.groups() if name_match else ()) if g), "")
        ).strip()
        if not name:
            continue
        id_match = _UPLOAD_ID.search(block)
        upload_id = next((g for g in (id_match.groups() if id_match else ()) if g), "")
        size_match = _FILE_SIZE.search(block)
        platforms = _UPLOAD_PLATFORMS.search(block)
        uploads.append(
            Upload(
                name=name,
                upload_id=upload_id,
                size_bytes=parse_size(size_match.group(1)) if size_match else None,
                labels=(
                    [html.unescape(t) for t in _ICON_TITLE.findall(platforms.group(1))]
                    if platforms
                    else []
                ),
            )
        )
    return uploads


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        developer, game = self._identify(result.source_id)
        page_url = f"https://{developer}.itch.io/{game}"
        page = self._page(page_url, result.source_id)

        uploads = parse_uploads(page)
        downloadable = [u for u in uploads if u.downloadable]

        # 1. Is anything on offer without paying? Asked first, and answered
        #    without naming a file, so the refusal cannot double as
        #    instructions for getting the file some other way.
        if not downloadable and _BUY_BTN.search(page):
            message = _text(m.group(1)) if (m := _BUY_MESSAGE.search(page)) else ""
            raise ImportRefused(
                f"itch.io game {result.source_id!r} is behind a checkout"
                f"{f' ({message})' if message else ''}: its files are released "
                f"through {page_url}/purchase, which needs a payment or a "
                f"name-your-own-price confirmation. Nothing was imported."
            )

        # 2. Files listed, no button, no checkout: a download key or a jam
        #    claim gates them, and a plugin holds neither.
        if uploads and not downloadable:
            raise ImportRefused(
                f"itch.io game {result.source_id!r} lists {len(uploads)} file(s) "
                f"but offers no download button, which is how itch.io renders "
                f"content that needs a download key or a claim on an account. It "
                f"cannot be imported."
            )

        # 3. Nothing downloadable at all -- almost always a browser build.
        if not uploads:
            raise ImportRefused(
                f"itch.io game {result.source_id!r} publishes no downloadable "
                f"files (a play-in-browser title, or a page that lists none). "
                f"There is nothing to import."
            )

        # 4. Which file, and which platform? Never guessed -- see platforms.py.
        payload = self._payload(downloadable)
        platform = self._platform(result, payload)
        filename = safe_filename(payload.name)

        # 5. The wall. See this module's docstring.
        raise ImportRefused(
            f"itch.io game {result.source_id!r} is free and downloadable "
            f"({payload.name!r} -> {filename!r}, RomM platform {platform!r}), but "
            f"itch.io issues a download URL only for a POST carrying the game "
            f"page's csrf_token, and this Hub's broker performs GET only "
            f"(itch.io's robots.txt also disallows /game/download/). Fetch it "
            f"from {page_url} by hand."
        )

    @staticmethod
    def _identify(source_id: str | None) -> tuple[str, str]:
        match = _SOURCE_ID.match((source_id or "").strip())
        if not match:
            raise ImportRefused(
                f"{source_id!r} is not an itch.io game id: expected "
                f"'<developer>/<game>', as in "
                f"'csbrannan/disco-elysium-game-boy-edition'"
            )
        return match.group("developer"), match.group("game")

    def _page(self, url: str, source_id: str) -> str:
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise ImportRefused(
                f"itch.io returned HTTP {response.status_code} for {url!r} "
                f"(game {source_id!r})"
            )
        return response.text

    @staticmethod
    def _payload(uploads: list[Upload]) -> Upload:
        """The largest downloadable file; ties broken by name.

        The same rule the Archive.org plugin uses: among several builds the
        biggest is the game and the rest are soundtracks, source drops or
        manuals. A row with no printed size sorts below every sized one so a
        stub cannot outrank the real build, and the name tie-break keeps the
        choice stable across calls.
        """
        return max(
            uploads,
            key=lambda u: (u.size_bytes if u.size_bytes is not None else -1, u.name),
        )

    @staticmethod
    def _platform(result: SearchResult, payload: Upload) -> str:
        # An operator's --platform reaches the plugin on the SearchResult and
        # is authoritative -- it is how the multi-platform case below gets
        # settled without this plugin choosing.
        override = (result.platform or "").strip()
        if override:
            return override

        mapped = [(label, platform_for(label)) for label in payload.labels]
        unmapped = [label for label, slug in mapped if slug is None]
        if unmapped:
            raise ImportRefused(
                f"itch.io platform label {unmapped[0]!r} (upload {payload.name!r}) "
                f"needs mapping: it is not in this plugin's label -> RomM platform "
                f"table, and guessing would file the game under the wrong system. "
                f"Add it to itch_io/platforms.py."
            )
        slugs = sorted({slug for _, slug in mapped if slug})
        if not slugs:
            raise ImportRefused(
                f"itch.io names no platform for upload {payload.name!r}, so there "
                f"is nothing to map to a RomM platform. Pass --platform to say "
                f"where it should be filed."
            )
        if len(slugs) > 1:
            raise ImportRefused(
                f"upload {payload.name!r} is published for {', '.join(slugs)}; "
                f"which of them it should be filed under is a choice this plugin "
                f"will not make for you. Pass --platform."
            )
        return slugs[0]
