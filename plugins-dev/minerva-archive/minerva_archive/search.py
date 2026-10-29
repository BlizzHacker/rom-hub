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

        rows: list[dict] = []
        offset = 0
        for _ in range(max_pages):
            params: dict = {"query": query, "offset": offset}
            # Empty filters are omitted, not sent as "", so the API's own
            # defaults apply and the query string stays clean.
            if tokenised:
                params["tokenised"] = "true"
            if group:
                params["group"] = group
            if console:
                params["console"] = console
            if region:
                params["region"] = region
            response = self.ctx.http.get(ENDPOINT, params=params)
            if response.status_code != 200:
                # A non-200 is a hard failure of this page; surface what we
                # have rather than raise and lose earlier pages.
                break
            page = response.json()
            if not isinstance(page, list) or not page:
                break
            rows.extend(r for r in page if isinstance(r, dict))
            if len(page) < PAGE:
                break
            offset += PAGE

        return self._to_results(rows, limit)

    def _to_results(self, rows: list[dict], limit: int) -> list[SearchResult]:
        if limit <= 0:
            return []

        out: list[SearchResult] = []
        seen_rows: set[tuple[str, str, str, str]] = set()
        for row in rows:
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
            out.append(SearchResult(
                source_id=source_id,
                title=title,
                # MiNERVA names no RomM platform in search results; the path's
                # vendor segment is not a reliable slug. Left None so the host
                # matches on title, exactly as it does for other sources.
                platform=None,
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
