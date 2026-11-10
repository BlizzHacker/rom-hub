"""MiNERVA Archive search over its public JSON API.

Myrient shut down 31 March 2026; MiNERVA is the community continuation of
its whole catalogue, distributed as collection torrents. The search API
needs no key:

    GET /v1/api/rom/search?query=&offset=&tokenised=&group=&console=&region=
    -> [{"id", "full_path", "magnet", "so_id"}, ...]

Two properties of this source shape the provider:

**A magnet is per collection group, not per game.** One torrent covers a
whole dump ("TOSEC - Nintendo" holds thousands of zips). Search therefore
keeps each matching API file row as its own result, with its row ID and exact
archive path visible. The result's source ID identifies that file row; its
extra field torrent_magnet carries the collection magnet for the torrent
capability. Picking a row still hands off the whole collection torrent. It
does not select or download only that file.

**The API publishes no size and no seeder count.** `size_bytes` is left
None (unknown) rather than 0-as-tiny; the host's scoring treats an unknown
size as "no evidence either way", not "too small to be real".
"""

import hashlib

from rom_hub_sdk import SearchProvider, SearchResult

from .platforms import consoles_for_platform, platform_for_console

ENDPOINT = "https://minerva-archive.org/v1/api/rom/search"
DETAIL = "https://minerva-archive.org/rom?id="
#: A full page is the API's "there is more" signal; it returns >100 when a
#: next page exists, so 100 is the page size to advance `offset` by.
PAGE = 100


def _title_of(full_path: str) -> str:
    """The filename at the end of a MiNERVA path, as the result's title.

    `full_path` is like `./TOSEC/Nintendo/Super Famicom/Games/Chrono
    Trigger (1995)(Square)(US).zip`. The scorer and the operator both want
    the game name, not the archive path, so the last component is the title.
    """
    path = (full_path or "").replace("\\", "/").strip()
    return path.rstrip("/").rsplit("/", 1)[-1] or path


def _info_hash(magnet: str) -> str:
    """Return the btih for display as the collection identity.

    Multiple file rows can share a torrent hash. This helper does not define
    search-result identity and must never be used to collapse those rows.
    """
    if not magnet.startswith("magnet:"):
        return ""
    for part in magnet.split("?", 1)[-1].split("&"):
        if part.startswith("xt=urn:btih:"):
            return part[len("xt=urn:btih:"):].lower()
    return ""


class Search(SearchProvider):
    def search(
        self, query: str, platform: str | None, limit: int
    ) -> list[SearchResult]:
        query = (query or "").strip().lower()
        if not query:
            return []

        cfg = self.ctx.config
        group = str(cfg.get("group") or "")
        console = str(cfg.get("console") or "")
        region = str(cfg.get("region") or "")
        tokenised = bool(cfg.get("tokenised"))
        try:
            max_pages = max(1, int(cfg.get("max_pages") or 1))
        except (TypeError, ValueError):
            max_pages = 1

        # Which console filters to query, in priority order:
        #
        # 1. An explicit `console` config is the operator's own narrowing
        #    and wins outright -- it is a MiNERVA filter-bar value they
        #    typed, and silently ANDing a second filter onto it would
        #    search for something they did not ask for.
        # 2. Otherwise, the host's platform argument is translated through
        #    this plugin's own mapping. One slug can name several MiNERVA
        #    consoles (Redump, No-Intro and TOSEC spell "Sega Mega Drive"
        #    three ways), so this is one request *per console name*,
        #    merged below. That is the fix for the first-100-rows-only
        #    failure: a platform-specific search now pages within the
        #    system it asked for instead of paging through every system.
        # 3. No platform, or an unmapped one, searches unfiltered --
        #    unfiltered is "all rows exist and the host matches on
        #    title"; empty would be "no rows exist", which is a lie.
        if console:
            console_filters: list[str] = [console]
        else:
            console_filters = list(consoles_for_platform(platform))

        # Each row is kept with the console filter that produced it: the
        # live API's row objects carry no `console` field (only id,
        # full_path, magnet and so_id -- measured 2026-11-09), so the
        # request is the only place that knowledge exists. Unfiltered
        # searches tag rows with "".
        rows: list[tuple[dict, str]] = []

        def page_all(console_name: str) -> None:
            offset = 0
            for _ in range(max_pages):
                params: dict = {"query": query, "offset": offset}
                if console_name:
                    params["console"] = console_name
                if tokenised:
                    params["tokenised"] = "true"
                if group:
                    params["group"] = group
                if region:
                    params["region"] = region
                if not self._fetch_page(params, rows, console_name):
                    break
                offset += PAGE

        if console_filters:
            for console_name in console_filters:
                page_all(console_name)
        else:
            # Empty filters are omitted, not sent as "", so the API's own
            # defaults apply and the query string stays clean.
            page_all("")

        return self._to_results(rows, limit)

    def _fetch_page(
        self, params: dict, rows: list[tuple[dict, str]], console_name: str
    ) -> bool:
        """Fetch one page into `rows`; True if another page may exist."""
        response = self.ctx.http.get(ENDPOINT, params=params)
        if response.status_code != 200:
            # A non-200 is a hard failure of this page; surface what we
            # have rather than raise and lose earlier pages.
            return False
        page = response.json()
        if not isinstance(page, list) or not page:
            return False
        rows.extend((r, console_name) for r in page if isinstance(r, dict))
        return len(page) >= PAGE

    def _to_results(self, rows: list[dict], limit: int) -> list[SearchResult]:
        if limit <= 0:
            return []

        out: list[SearchResult] = []
        seen_rows: set[tuple[str, str, str, str]] = set()
        for row, console_filter in rows:
            magnet = str(row.get("magnet") or "")
            full_path = str(row.get("full_path") or "")
            if not magnet.startswith("magnet:") or not full_path:
                continue

            rom_id = row.get("id")
            so_id = row.get("so_id")
            # MiNERVA's id is the source row identifier. Include the path too
            # so revised rows with a reused id remain distinct.
            row_key = (
                str(rom_id or ""),
                str(so_id or ""),
                full_path,
                magnet,
            )
            if row_key in seen_rows:
                continue
            seen_rows.add(row_key)

            title = _title_of(full_path)
            if not title:
                continue
            hash_suffix = hashlib.sha256(full_path.encode("utf-8")).hexdigest()[:12]
            row_identity = str(rom_id or so_id or hash_suffix)
            # source_id identifies a file row; the collection magnet is
            # carried separately for Torrent.resolve().
            torrent_suffix = hashlib.sha256(magnet.encode("utf-8")).hexdigest()[:12]
            source_id = f"minerva-row:{row_identity}:{hash_suffix}:{torrent_suffix}"
            # The console this row was found under, mapped back to a host
            # platform slug. The API's rows carry no `console` field of
            # their own, so the filter that produced the row is the
            # evidence; if a future API revision does publish one, that
            # wins. Rows found unfiltered, or under a console this plugin
            # does not map, stay None and the host matches them on title
            # exactly as it did when every row was None.
            row_console = str(row.get("console") or console_filter or "")
            out.append(SearchResult(
                source_id=source_id,
                title=title,
                platform=platform_for_console(row_console),
                size_bytes=None,          # not published -- unknown, never 0
                url=f"{DETAIL}{rom_id}" if rom_id is not None else None,
                extra={
                    "id": str(rom_id or ""),
                    "group": str(row.get("group") or ""),
                    "region": str(row.get("region") or ""),
                    "console": str(row.get("console") or ""),
                    "so_id": str(so_id or ""),
                    "path": full_path,
                    "torrent_hash": _info_hash(magnet),
                    "torrent_magnet": magnet,
                },
            ))
            if len(out) >= limit:
                break
        return out
