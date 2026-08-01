"""Reading Archive.org's index at collection scale, and what that costs.

`search.py` used to send one `advancedsearch.php` request with `rows=limit`
and `page=1`. That is fine for a search box and useless for a collection:
the Console Living Room holds **24,746 items**, and nothing in the plugin
could see past the first page of them.

Getting the rest is not a matter of asking for more pages, so the three
findings that shape this module are written down here rather than
discovered again by the next person.

## 1. `page` is what imposes the ceiling, not `rows`

`advancedsearch.php` refuses to page deeper than 10,000 results::

    rows=100  page=101  -> {"error": "[DEEP_PAGING] Requested results would
                            exceed the deep paging limit for this service,
                            10000 results; ..."}
    rows=1000 page=11   -> the same
    rows=2000 page=6    -> the same

and the error text itself names the way out: *"You may request any number
of results at one time if you do NOT specify any page."* Measured against
the live service::

    q=collection:(consolelivingroom) AND emulator:("genesis")
      AND NOT collection:(stream_only)
    rows=11000, no page  ->  10,045 docs in ~2 seconds

So a bulk read omits `page` entirely and asks for the whole result set in
one response. `page` is still used for small asks, where it is the natural
resume point -- see `PAGE_CEILING`.

## 2. The scrape API is the documented answer and the wrong one

`/services/search/v1/scrape` is what that error message points at, and it
does work: cursor-paged, it returned all 24,746 items of the collection in
137 seconds over 25 pages of 1,000 -- with each successive page slower
than the last (3.5s early, 9.5s at the end). It has two disqualifying
properties.

**It does not apply a field filter.** Asked for
`collection:(consolelivingroom) AND emulator:("genesis")` on a fresh
connection it answered `total=24746` -- the whole collection -- and the
first page came back 36 genesis, 20 nes, 12 a2600, 5 gbcolor. The
`emulator:` clause is silently dropped. Every one of 33 per-emulator
queries returned the identical first 100 identifiers. A filter that is
accepted and ignored is worse than one that is rejected, because the
caller gets a plausible answer to a question it did not ask.

**Its result set is not reliably the one you asked for.** Consecutive
requests on one keep-alive connection returned each other's totals:
`collection:(nasa)` and `collection:(consolelivingroom)` both answered
`total=208822` in one run and both answered `total=24746` in another,
depending only on which query had been sent first.

Neither of those is a thing to build a plugin's idea of "the collection"
on, and both were reproduced from this workstation more than once. The
scrape endpoint is therefore **not used**. That is a finding about
Archive.org, not a preference, and it is why this module reaches
collection scale through `advancedsearch.php` alone.

## 3. Be a considerate client

Everything here is a GET against a free public service that rate-limits,
so: responses are cached for the life of the process (`Index` is
constructed once per capability call, and one `search()` that pages will
hit the cache rather than re-ask), failures back off rather than retry
immediately, and a request that fails twice **halves `rows` and tries
again** -- a caller that asked for 20,000 and can be given 5,000 is better
served than one given an exception.
"""

from __future__ import annotations

import json

ENDPOINT = "https://archive.org/advancedsearch.php"

#: Fields asked for on every index read. `emulator` is what platform
#: routing needs, `collection` is what tells import from stream, and
#: `notes` is one of the three places Archive.org keeps control
#: information -- see `controls.py`. All four are indexed, verified live.
FIELDS = (
    "identifier",
    "title",
    "collection",
    "item_size",
    "emulator",
    "emulator_ext",
    "notes",
)

#: Above this, a read stops using `page` and asks for the whole result set
#: in one request. Under it, `page` is kept: it is the natural resume
#: point, it lets a caller stop early, and it keeps an ordinary search box
#: asking for ordinary-sized things.
PAGE_CEILING = 1000

#: `advancedsearch.php` refuses to page past this. Not a number this
#: module chose -- see the DEEP_PAGING quote above.
DEEP_PAGING_LIMIT = 10000

#: The largest single bulk request this will make. Above the whole Console
#: Living Room (24,746) with room to spare, and low enough that a config
#: typo cannot ask Archive.org for a million rows.
MAX_ROWS = 50000

#: How many times one request is attempted before its `rows` is halved.
ATTEMPTS_PER_SIZE = 2

#: How far `rows` may be halved before giving up. 20,000 -> 625.
HALVINGS = 5


class IndexUnavailable(Exception):
    """Archive.org's index could not be read, and the message says why.

    Reaches an operator the same way every other refusal in this plugin
    does: as the `error` column of a FAILED job.
    """


def escape(term: str) -> str:
    r"""Make one term safe to sit inside a Lucene quoted phrase.

    Quoting is what neutralises Lucene's operators: `-`, `&`, `:` and the
    rest are literal text inside `"..."`, which is why real titles like
    `r-type` and `sonic & knuckles` need no special handling. Only the two
    characters that can *end* the phrase early have to be escaped -- the
    quote itself, and the backslash that would otherwise consume the
    escape. Backslash first, or escaping the quote would then
    double-escape.
    """
    return term.replace("\\", "\\\\").replace('"', '\\"')


def build_query(
    query: str | None,
    collections: list[str],
    *,
    emulators: list[str] | None = None,
    downloadable_only: bool = False,
) -> str:
    '''The advancedsearch `q`, with the user's terms confined to the title.

    This used to be `({query}) AND collection:({scope})`, which put a bare
    term into Archive.org's *default* field -- effectively the whole
    record, description and subject tags and uploader notes included --
    and then let relevance ranking sort it out. It did not sort it out.
    Searching `sonic` returned `Die Hard (2004)(Die Chefrocker)`;
    `oregon trail` returned `Great Hierophant's .WOZ Archive`;
    `prince of persia` returned `Total Replay`. Those items match
    somewhere in their metadata, which is not a claim anybody searching a
    ROM library is making.

    So each term is required to appear **in the title**, and all of them
    must:

        title:("prince" AND "of" AND "persia") AND collection:(softwarelibrary)

    Two deliberate choices about how far to narrow:

    **Terms, not a phrase.** `title:("prince of persia")` also fixes the
    junk, but it demands adjacency and word order -- verified live, it
    returns *zero* results for `hedgehog sonic` and `persia prince`, while
    the AND-of-terms form answers both with the Sonic and Prince of Persia
    titles. A search that silently returns nothing because the words were
    typed in a different order is a worse bug than the one being fixed.

    **Title only, not title-or-identifier.** Checked live: adding
    `identifier:(...)` changed essentially nothing, because an
    Archive.org identifier already echoes the title. It is complexity
    that buys no recall.

    An empty query drops the clause entirely rather than emitting
    `title:()`, which is a syntax error, or `title:("")`, which matches
    nothing -- browsing a collection has to stay possible, and browsing
    is the whole point of a collection-scale read.

    `emulators` narrows to Archive.org's own machine ids and
    `downloadable_only` drops the `stream_only` half of the collection.
    Both are clauses Archive.org's *advancedsearch* honours; neither
    survives the scrape API, which is one of the reasons this module does
    not use it.
    '''
    clauses = []

    terms = [t for t in (query or "").split() if t]
    if terms:
        inner = " AND ".join(f'"{escape(t)}"' for t in terms)
        clauses.append(f"title:({inner})")

    scope = " OR ".join(collections)
    clauses.append(f"collection:({scope})")

    if emulators:
        wanted = " OR ".join(f'"{escape(e)}"' for e in emulators)
        clauses.append(f"emulator:({wanted})")

    if downloadable_only:
        # Archive.org's own marker for "playable in a browser, not
        # downloadable". Excluding it here means a bulk import never plans
        # a fetch that `importer.py` would refuse one call later.
        clauses.append("NOT collection:(stream_only)")

    return " AND ".join(clauses)


class Index:
    """One capability call's view of `advancedsearch.php`.

    Holds the response cache, so a capability that reads the index twice
    for the same query pays for it once. Deliberately not shared between
    calls: a plugin process is short-lived, and a cache that outlived a
    call would start answering with yesterday's collection.
    """

    def __init__(self, http, *, max_rows: int = MAX_ROWS):
        self._http = http
        self._max_rows = max(1, min(int(max_rows), MAX_ROWS))
        self._cache: dict[tuple, list[dict]] = {}

    def fetch(self, q: str, limit: int, *, page: int | None = None) -> list[dict]:
        """Up to `limit` documents for `q`.

        `page` is honoured only while the whole ask stays inside
        Archive.org's deep-paging limit. Past that the request is sent
        **without** `page`, because that is the only form the service
        answers -- see this module's docstring.
        """
        limit = max(1, min(int(limit), self._max_rows))
        paged = page is not None and (page * limit) <= DEEP_PAGING_LIMIT
        if page is not None and not paged:
            raise IndexUnavailable(
                f"Archive.org will not page past {DEEP_PAGING_LIMIT} results "
                f"(asked for page {page} of {limit}), and a bulk read cannot "
                f"resume from a page number beyond it. Ask for the results in "
                f"one request instead: this plugin does that automatically for "
                f"a limit over {PAGE_CEILING}."
            )

        key = (q, limit, page if paged else None)
        if key in self._cache:
            return self._cache[key]

        rows = limit
        for _ in range(HALVINGS + 1):
            docs = self._attempt(q, rows, page if paged else None)
            if docs is not None:
                self._cache[key] = docs
                return docs
            if rows <= 1:
                break
            rows = max(1, rows // 2)

        raise IndexUnavailable(
            f"Archive.org's search endpoint did not answer for {q!r}, over "
            f"{HALVINGS + 1} attempts ending at rows={rows}. It rate-limits, "
            f"and both a rate-limited response and a maintenance page arrive "
            f"as something that is not JSON; try again, or narrow the query."
        )

    # -- one attempt ----------------------------------------------------

    def _attempt(self, q: str, rows: int, page: int | None) -> list[dict] | None:
        """`rows` documents, or None for "ask again, smaller".

        None rather than an exception because the caller's response to a
        failure is to retry, and a bare return value keeps the retry loop
        readable. A *permanent* problem -- a query the service rejects --
        raises instead, since halving `rows` will never fix it.
        """
        params = {"q": q, "fl[]": list(FIELDS), "rows": rows, "output": "json"}
        if page is not None:
            params["page"] = page

        for _ in range(ATTEMPTS_PER_SIZE):
            response = self._http.get(ENDPOINT, params=params)
            if response.status_code != 200:
                continue
            try:
                body = response.json()
            except (ValueError, json.JSONDecodeError):
                # Rate limiting and maintenance both arrive as 200 + HTML.
                continue
            if not isinstance(body, dict):
                continue

            error = body.get("error")
            if isinstance(error, str) and error:
                # A rejected query, not a busy service. Halving `rows`
                # would only make the same complaint arrive more slowly.
                raise IndexUnavailable(
                    f"Archive.org refused the query {q!r}: {error.strip()}"
                )

            docs = (body.get("response") or {}).get("docs")
            if isinstance(docs, list):
                return [d for d in docs if isinstance(d, dict)]
        return None

    def total(self, q: str) -> int | None:
        """`numFound` for `q`, or None if the service would not say.

        `rows=0` is the cheap form: Archive.org counts without returning
        any document. Used to tell an operator how much of a collection
        they are about to ask for.
        """
        response = self._http.get(
            ENDPOINT, params={"q": q, "rows": 0, "output": "json"}
        )
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError):
            return None
        if not isinstance(body, dict):
            return None
        found = (body.get("response") or {}).get("numFound")
        return found if isinstance(found, int) else None
