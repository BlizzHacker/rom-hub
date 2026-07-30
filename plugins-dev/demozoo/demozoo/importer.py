"""Demozoo `importer`: one production id into a FetchPlan.

    GET https://demozoo.org/api/v1/productions/<id>/?format=json
        -> types      is this a file that runs?     productions.py
        -> platforms  which RomM platform?          platforms.py
        -> download_links  where does it come from? links.py

The plugin decides *what* should be fetched and nothing else. It opens no
socket -- `ctx.http` is an RPC back to the host -- and the host
re-validates every URL in the returned plan against this plugin's own
manifest allowlist before fetching any of it, including every redirect
hop. That last part is not incidental here: the scene.org download this
plugin plans is a deliberate two-host chain, and `links.py` explains why.

The listing endpoint does not carry `download_links`, so this is a second
round trip per import and there is no way around it. It is one request per
production, made at the moment somebody asked for that production.

Three refusals happen before any URL is built, and the order is
deliberate -- each one is answered without naming a file, so that a
refusal can never double as instructions for fetching the thing by hand:

1. **Is it a file that runs?** A `Video` entry's download link is
   frequently a YouTube page. Importing that would put a link to a web
   page in a ROM library labelled as a game.
2. **Which platform?** Never guessed. An unmapped platform, a platform
   that is not a library platform at all, and a platform that is
   ambiguous each get their own sentence, because only the first is fixed
   by adding a row.
3. **Is there a download this plugin may fetch?** Most of Demozoo's
   archives are ruled out -- two by their own robots.txt, one by plain
   http, and the generic `BaseUrl` class by being able to point anywhere.

Everything Demozoo indexes is released free by its authors for
distribution; that is what the demoscene is. This plugin does not treat
that as a reason to relax anything above.
"""

from urllib.parse import quote

from rom_hub_sdk import FetchFile, FetchPlan, ImportProvider, SearchResult

from .filenames import safe_filename
from .links import resolve
from .productions import parse_production, require_importable

DETAIL = "https://demozoo.org/api/v1/productions/"

#: Everything imported from here lands in one collection by default, so an
#: operator can see at a glance what came from Demozoo and what did not.
DEFAULT_COLLECTION = "Demozoo"


class ImportRefused(Exception):
    """This production cannot be imported, and the message says why.

    Raised for every refusal -- not a runnable type, unmapped platform, no
    usable download, unusable record -- because they all reach an operator
    the same way: as the `error` column of a FAILED job.
    """


class Importer(ImportProvider):
    def plan(self, result: SearchResult) -> FetchPlan:
        production_id = self._production_id(result)
        raw = self._detail(production_id)
        production = parse_production(raw)
        if production is None:
            raise ImportRefused(
                f"Demozoo's record for production {production_id} is missing "
                f"an id or a title, so there is nothing to file"
            )

        # 1 and 2. Refuses with the sentence that fits the actual reason.
        _, slug = require_importable(production)

        # 3. Where the bytes come from -- and whether we may go there.
        download = resolve(raw.get("download_links"))

        return FetchPlan(
            files=[
                FetchFile(
                    url=download.url,
                    # The path, if any, belongs in the URL. `filename` is
                    # what the host opens for writing, and `FetchFile`
                    # rejects anything but a bare name for that reason.
                    filename=safe_filename(download.raw_name),
                    # Neither the listing nor the detail record carries a
                    # size. The host learns it from the response.
                    size_bytes=None,
                )
            ],
            platform=slug,
            collection=self.ctx.config.get("collection") or DEFAULT_COLLECTION,
        )

    # -- the network -----------------------------------------------------

    def _production_id(self, result: SearchResult) -> int:
        raw = (result.source_id or "").strip()
        if not raw.isdigit():
            raise ImportRefused(
                f"{raw!r} is not a Demozoo production id; a search result "
                f"from this plugin carries the numeric id from "
                f"/api/v1/productions/"
            )
        return int(raw)

    def _detail(self, production_id: int) -> dict:
        url = f"{DETAIL}{quote(str(production_id))}/"
        response = self.ctx.http.get(url, params={"format": "json"})
        if response.status_code == 404:
            raise ImportRefused(
                f"Demozoo has no production {production_id} (its API answered "
                f"404). Productions are occasionally merged or deleted, so a "
                f"search result from an earlier session can go stale."
            )
        if response.status_code != 200:
            raise ImportRefused(
                f"Demozoo answered HTTP {response.status_code} for production "
                f"{production_id}"
            )
        try:
            body = response.json()
        except ValueError as exc:
            raise ImportRefused(
                f"Demozoo's record for production {production_id} was not "
                f"JSON: {exc}"
            ) from exc
        if not isinstance(body, dict):
            raise ImportRefused(
                f"Demozoo's record for production {production_id} was not an "
                f"object"
            )
        return body
