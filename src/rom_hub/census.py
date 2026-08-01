"""A catalogue that can state what it is missing.

`rom-hub` has been reporting the wrong kind of number. "15,165 archives
reachable" is a fact about the plugin's configuration, not about the
source: it is the size of a list somebody typed, and no amount of it adds
up to "the No-Intro archive is catalogued". The owner's objection is
exact -- *complete, not random numbers reachable* -- and the difference
between the two claims is a **denominator you can defend**.

So this module builds a catalogue whose central assertion is arithmetic,
per unit, and checkable by anyone who can read the source:

    declared_total  ==  kept  +  sum(skipped.values())

`declared_total` comes from the source and not from the walk -- for an
Archive.org item it is `files_count` off `advancedsearch.php`, obtained by
a different request than the one that lists the files. So "we enumerated
820" is checked against a number the enumeration could not have
influenced. `kept` is what landed in the catalogue. `skipped` is a mapping
of *reason* to count covering everything walked past. When the three
balance, the unit is complete and the report says so with its working
shown. When they do not, the shortfall is printed as a shortfall -- never
folded into a percentage, never rounded away.

## What is stored, and why not `[[data_assets]]`

`rom_hub.assets` already fetches, verifies and caches large files a plugin
declares, and it is the wrong mechanism for this. A data asset is a file
that **exists upstream** with a **known sha256 written into the manifest**
before install. A catalogue has neither property: it does not exist until
the Hub builds it, its digest is a function of when it was built, and it
must be *appended to* across many processes as a long walk resumes. A
manifest digest for a file that changes every time it is rebuilt would be
a lie in a reviewable place, which is worse than no digest at all.

What it shares with data assets is *where it lives*:
`<ROM_HUB_HOME>/var/catalogues/<slug>.sqlite3`, beside `var/plugin-data`
and the job queue -- runtime state that grows, kept out of the repo and
off the system drive by default.

SQLite for the same reason `rom_hub.jobs` uses it: a walk of seventy-one
Archive.org items takes minutes and **will** be interrupted. Every page is
committed as it arrives, so a killed build resumes at the unit and cursor
it reached rather than starting again. One writer at a time -- there is no
WAL here either, and two Hub processes against one home is not a supported
shape.

## Deduplication is not reimplemented here

`rom_hub.grouping` already answers "which of these are the same game?" and
"which are the same dump?", with an evidence ladder -- a matching strong
hash is proof, a conflicting hash is disproof and outranks the name,
otherwise the parsed name decides. That is exactly the question a
cross-item catalogue asks, so `distinct()` below **calls it** rather than
counting distinct keys itself. The cost is a few seconds over thirty
thousand rows; the benefit is that there is one deduplicator in this
codebase and its failure direction is documented in one place.

This matters more here than it looks. Archive.org publishes md5, sha1 and
crc32 for every file it holds, and the census stores them under the keys
`grouping` reads -- so `NoIntro_VirtualBoy` and `NoIntroVirtualBoy`, two
items with thirty-one identically-hashed archives between them, collapse
on *proof*. Where two items package the same ROM differently -- a `.7z`
against a `.zip` -- the container hashes genuinely differ and the name
parse decides instead, which is the right answer for the right reason.

## Classification, and why an exclusion is loud

The seventy-one items matching `identifier:nointro*` are not the same kind
of thing. Twenty-odd are flat directories of per-game archives.
`NoIntroROMsCollection` is sixty-two files and 44.8 GB, each file a whole
set -- an archive of archives. `nointro_wiiu_cdn_nov_2020_2` is 928 GB of
a console maker's distribution tree. Merging those into one number would
claim a CDN mirror as forty thousand games.

So a unit carries a `kind`, the operator chooses which kinds to walk, and
**every unit not walked is named in the report with the reason it was
not**. Leaving out a 928 GB dump is a good decision. Leaving it out
silently is the thing being complained about.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .grouping import group_results
from .romnames import parse
from .types import CensusPage, CensusUnit, SearchResult

#: Under `var/`, beside `plugin-data` and `jobs.db`. See the module note.
CATALOGUE_DIR_NAME = "catalogues"

#: Kinds walked when the operator does not say otherwise. `roms` alone:
#: every other kind is a real thing the catalogue records the existence of
#: and does not pretend is a library of games.
DEFAULT_KINDS = ("roms",)

#: A stop on one unit's paging, so a plugin whose cursor never advances
#: cannot spin forever. Generous: a unit is one Archive.org item and no
#: observed item needs more than one page.
MAX_PAGES_PER_UNIT = 500

#: How a unit's walk ended.
PENDING = "pending"
PARTIAL = "partial"
DONE = "done"
EXCLUDED = "excluded"
FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS units (
    unit_id        TEXT PRIMARY KEY,
    label          TEXT NOT NULL,
    kind           TEXT NOT NULL,
    declared_total INTEGER,
    platform       TEXT,
    size_bytes     INTEGER,
    included       INTEGER NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    state          TEXT NOT NULL,
    cursor         TEXT,
    kept           INTEGER NOT NULL DEFAULT 0,
    skipped_json   TEXT NOT NULL DEFAULT '{}',
    error          TEXT,
    walked_at      REAL
);
CREATE TABLE IF NOT EXISTS records (
    unit_id      TEXT NOT NULL,
    record_id    TEXT NOT NULL,
    title        TEXT NOT NULL,
    platform     TEXT,
    size_bytes   INTEGER,
    url          TEXT,
    extra_json   TEXT NOT NULL DEFAULT '{}',
    platform_key TEXT,
    title_key    TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (unit_id, record_id)
);
CREATE INDEX IF NOT EXISTS records_title_key ON records (title_key);
CREATE INDEX IF NOT EXISTS records_platform ON records (platform_key);
"""


class CensusError(Exception):
    """A catalogue could not be built, opened or read."""


def catalogue_root(root: Path) -> Path:
    return Path(root) / "var" / CATALOGUE_DIR_NAME


def catalogue_path(root: Path, slug: str) -> Path:
    """Where one plugin's catalogue lives.

    The slug is manifest-validated before it ever reaches here, and it is
    still checked: this is a filesystem path built from a plugin-chosen
    string, and the codebase's rule is that such a join has a containment
    check on it rather than an argument about why it cannot be abused.
    """
    from .paths import UnsafeDestination, dest_in_job_dir

    directory = catalogue_root(root)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        return dest_in_job_dir(directory, f"{slug}.sqlite3")
    except UnsafeDestination as exc:
        raise CensusError(str(exc)) from exc


# -- what the report is made of ------------------------------------------


@dataclass(frozen=True)
class UnitCoverage:
    """One unit's row in the completeness report."""

    unit_id: str
    label: str
    kind: str
    platform: str | None
    declared_total: int | None
    kept: int
    skipped: dict[str, int]
    included: bool
    reason: str
    state: str
    error: str | None = None

    @property
    def walked(self) -> int:
        """Entries accounted for: catalogued plus skipped-with-a-reason."""
        return self.kept + sum(self.skipped.values())

    @property
    def shortfall(self) -> int | None:
        """Declared entries this walk never accounted for, or None.

        None means the source declared no total, which is reported as
        "unknown" and never as zero -- an unmeasured unit and a complete
        one must not print the same thing.
        """
        if self.declared_total is None:
            return None
        return self.declared_total - self.walked

    @property
    def complete(self) -> bool:
        return self.state == DONE and self.shortfall == 0


@dataclass
class CompletenessReport:
    """What the catalogue holds, against what the source says exists.

    Everything here is a count of *entries the source declared*, so the
    numbers add up in public. `accounted` is the claim; `shortfall` is the
    admission; `excluded` is the list of things deliberately not walked,
    each with the reason attached.
    """

    slug: str
    units: list[UnitCoverage] = field(default_factory=list)
    built_at: float | None = None
    #: Distinct games and distinct dumps, as `rom_hub.grouping` counts
    #: them. Filled in by `Catalogue.report(distinct=True)`; left None
    #: otherwise, because grouping thirty thousand rows is not free and a
    #: progress line should not pay for it.
    games: int | None = None
    variants: int | None = None

    @property
    def walked_units(self) -> list[UnitCoverage]:
        return [u for u in self.units if u.included]

    @property
    def excluded_units(self) -> list[UnitCoverage]:
        return [u for u in self.units if not u.included]

    @property
    def failed_units(self) -> list[UnitCoverage]:
        return [u for u in self.units if u.state == FAILED]

    @property
    def declared(self) -> int:
        """Entries the source declares across every unit it has, walked or
        not. The denominator of the strongest claim available."""
        return sum(u.declared_total or 0 for u in self.units)

    @property
    def declared_in_scope(self) -> int:
        return sum(u.declared_total or 0 for u in self.walked_units)

    @property
    def declared_excluded(self) -> int:
        return sum(u.declared_total or 0 for u in self.excluded_units)

    @property
    def kept(self) -> int:
        return sum(u.kept for u in self.units)

    @property
    def skipped(self) -> dict[str, int]:
        """Every skip reason across the whole walk, with its total."""
        totals: dict[str, int] = {}
        for unit in self.units:
            for reason, count in unit.skipped.items():
                totals[reason] = totals.get(reason, 0) + count
        return dict(sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])))

    @property
    def accounted(self) -> int:
        return sum(u.walked for u in self.walked_units)

    @property
    def unreachable(self) -> int:
        """Declared entries in units the Hub could not read at all.

        Kept apart from `shortfall`, because the two are different findings
        and conflating them was the first thing this report got wrong. A
        unit that 404s or times out is *unreachable*: the source says it
        holds 825 files and the Hub could not see them. A unit that was
        read successfully and still does not balance is a *shortfall*: the
        Hub walked it and lost rows, which is a bug in the plugin.

        Both keep the catalogue from being complete. Only one of them is
        fixable by trying again.
        """
        return sum(
            u.shortfall
            for u in self.walked_units
            if u.state == FAILED and u.shortfall is not None
        )

    @property
    def shortfall(self) -> int:
        """Entries a *successful* walk neither catalogued nor explained.

        Zero is the only value that supports a completeness claim, and on a
        healthy build it is the number that should never move: a unit that
        was read at all should account for every entry the source declared.
        """
        return sum(
            u.shortfall
            for u in self.walked_units
            if u.state != FAILED and u.shortfall is not None
        )

    @property
    def unmeasured(self) -> list[UnitCoverage]:
        """In-scope units whose source declared no total at all."""
        return [u for u in self.walked_units if u.declared_total is None]

    @property
    def complete(self) -> bool:
        """Whether every in-scope unit balances.

        Deliberately strict: one failed unit, one unmeasured unit or one
        entry unaccounted for and the answer is no. A catalogue that is
        *nearly* complete should say a number, not a yes.
        """
        return (
            bool(self.walked_units)
            and not self.failed_units
            and not self.unmeasured
            and self.shortfall == 0
            and all(u.state == DONE for u in self.walked_units)
        )

    @property
    def age_seconds(self) -> float | None:
        return None if self.built_at is None else max(0.0, time.time() - self.built_at)

    def headline(self) -> str:
        """The one line this whole module exists to be able to print."""
        if not self.units:
            return f"{self.slug}: no catalogue has been built yet"
        scope = (
            f"{self.accounted:,} of {self.declared_in_scope:,} declared entries "
            f"across {len(self.walked_units)} units"
        )
        if self.complete:
            head = f"{self.slug}: complete -- {scope}"
        else:
            head = f"{self.slug}: {scope}"
            if self.failed_units:
                # Named first and named as unreachable, because that is the
                # honest word for it: the source says those entries exist
                # and the Hub could not read them. Presenting them as
                # "missing" would suggest they are gone, and folding them
                # into a coverage percentage would hide them entirely.
                head += (
                    f", {self.unreachable:,} unreachable in "
                    f"{len(self.failed_units)} units that could not be read"
                )
            if self.shortfall:
                head += f", {self.shortfall:,} unaccounted for"
            if self.unmeasured:
                head += f", {len(self.unmeasured)} units with no declared total"
        if self.excluded_units:
            head += (
                f"; {len(self.excluded_units)} units excluded "
                f"({self.declared_excluded:,} entries)"
            )
        return head


# -- the store -----------------------------------------------------------


class Catalogue:
    """One plugin's catalogue on disk.

    Opened in autocommit mode, exactly as `rom_hub.jobs` is, so a page
    written during a long walk is durable the moment it is written rather
    than at the end of an implicit transaction that a kill would discard.
    Resumability is the whole point; a batch that is only safe if the
    process survives would defeat it.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path), isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "Catalogue":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- scope ----------------------------------------------------------

    def begin(self, slug: str, units: list[CensusUnit], *, kinds=None) -> None:
        """Record the scope, preserving progress on units that have not moved.

        A rebuilt scope is the normal case -- an operator re-runs the build
        to pick up new items -- and re-walking everything each time would
        make a refresh cost as much as a first build. So a unit whose
        identity, declared total and kind are unchanged keeps its records
        and its state; anything else is reset, because a unit whose
        declared total moved is a unit whose contents moved.
        """
        kinds = tuple(kinds if kinds is not None else DEFAULT_KINDS)
        self._set_meta("slug", slug)
        self._set_meta("kinds", ",".join(kinds))
        self._set_meta("scoped_at", repr(time.time()))

        seen = set()
        for unit in units:
            seen.add(unit.unit_id)
            included = unit.include and unit.kind in kinds
            reason = unit.reason
            if unit.include and not included:
                # The operator's kind filter, not the plugin's proposal.
                # Recorded in the same field so the report has one place to
                # read an exclusion's reason from, and worded so the two
                # are distinguishable when it is printed.
                reason = (
                    f"kind {unit.kind!r} is outside the requested scope "
                    f"({', '.join(kinds)})"
                )
            row = self._db.execute(
                "SELECT declared_total, kind, state FROM units WHERE unit_id = ?",
                (unit.unit_id,),
            ).fetchone()
            unchanged = (
                row is not None
                and row["declared_total"] == unit.declared_total
                and row["kind"] == unit.kind
            )
            if unchanged and included and row["state"] in (DONE, PARTIAL):
                self._db.execute(
                    "UPDATE units SET label = ?, platform = ?, size_bytes = ?, "
                    "included = 1, reason = ? WHERE unit_id = ?",
                    (unit.label, unit.platform, unit.size_bytes, reason, unit.unit_id),
                )
                continue
            self._db.execute("DELETE FROM records WHERE unit_id = ?", (unit.unit_id,))
            self._db.execute(
                "INSERT INTO units (unit_id, label, kind, declared_total, platform, "
                "size_bytes, included, reason, state, cursor, kept, skipped_json, "
                "error, walked_at) VALUES (?,?,?,?,?,?,?,?,?,NULL,0,'{}',NULL,NULL) "
                "ON CONFLICT(unit_id) DO UPDATE SET label=excluded.label, "
                "kind=excluded.kind, declared_total=excluded.declared_total, "
                "platform=excluded.platform, size_bytes=excluded.size_bytes, "
                "included=excluded.included, reason=excluded.reason, "
                "state=excluded.state, cursor=NULL, kept=0, skipped_json='{}', "
                "error=NULL, walked_at=NULL",
                (
                    unit.unit_id,
                    unit.label,
                    unit.kind,
                    unit.declared_total,
                    unit.platform,
                    unit.size_bytes,
                    1 if included else 0,
                    reason,
                    PENDING if included else EXCLUDED,
                ),
            )
        # A unit that has vanished from the source keeps neither its rows
        # nor its place in the denominator: a catalogue that counted items
        # the source no longer has would overstate its own coverage.
        if seen:
            placeholders = ",".join("?" * len(seen))
            self._db.execute(
                f"DELETE FROM records WHERE unit_id NOT IN ({placeholders})",
                tuple(seen),
            )
            self._db.execute(
                f"DELETE FROM units WHERE unit_id NOT IN ({placeholders})",
                tuple(seen),
            )

    def pending(self) -> list[tuple[CensusUnit, str | None]]:
        """In-scope units still to walk, with the cursor each resumes from."""
        rows = self._db.execute(
            "SELECT * FROM units WHERE included = 1 AND state IN (?, ?) "
            "ORDER BY unit_id",
            (PENDING, PARTIAL),
        ).fetchall()
        return [(_unit_from_row(row), row["cursor"]) for row in rows]

    # -- walking --------------------------------------------------------

    def add_page(self, unit_id: str, page: CensusPage) -> int:
        """Persist one page and advance the unit. Returns rows newly stored.

        `INSERT OR IGNORE` rather than `INSERT`: a resumed walk may re-read
        a page whose cursor was stored after the rows were, and a duplicate
        record is a re-read rather than a new one. The unit's `kept` is
        recomputed from the table for that reason -- incrementing it by the
        page length would count a re-read twice and turn a resumed build
        into an over-complete one.
        """
        rows = []
        for record in page.records:
            name = parse(record.title)
            rows.append(
                (
                    unit_id,
                    record.record_id,
                    record.title,
                    record.platform,
                    record.size_bytes,
                    record.url,
                    json.dumps(record.extra, sort_keys=True),
                    (record.platform or "").strip().lower() or None,
                    name.title_key,
                )
            )
        if rows:
            self._db.executemany(
                "INSERT OR IGNORE INTO records (unit_id, record_id, title, "
                "platform, size_bytes, url, extra_json, platform_key, title_key) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
        kept = self._db.execute(
            "SELECT COUNT(*) FROM records WHERE unit_id = ?", (unit_id,)
        ).fetchone()[0]

        current = self._db.execute(
            "SELECT skipped_json, declared_total FROM units WHERE unit_id = ?",
            (unit_id,),
        ).fetchone()
        skipped = _loads_counts(current["skipped_json"] if current else "{}")
        for reason, count in page.skipped.items():
            skipped[reason] = skipped.get(reason, 0) + count

        declared = current["declared_total"] if current else None
        if page.declared_total is not None:
            # The plugin may refine the total once it has the unit open --
            # an index page that prints its own count, say. Taken as an
            # improvement on the scope figure, never as a way to make a
            # shortfall disappear: it is recorded and the report prints
            # what it is comparing against.
            declared = page.declared_total

        self._db.execute(
            "UPDATE units SET kept = ?, skipped_json = ?, cursor = ?, "
            "declared_total = ?, state = ?, walked_at = ? WHERE unit_id = ?",
            (
                kept,
                json.dumps(skipped, sort_keys=True),
                page.cursor,
                declared,
                PARTIAL if page.cursor is not None else DONE,
                time.time(),
                unit_id,
            ),
        )
        return len(rows)

    def fail_unit(self, unit_id: str, error: str) -> None:
        """Mark a unit as failed, keeping whatever it had already produced.

        Kept rather than rolled back: half of an item is a real half, and
        the report says which unit failed and why. Discarding it would make
        the next build re-fetch work that succeeded.
        """
        self._db.execute(
            "UPDATE units SET state = ?, error = ?, walked_at = ? WHERE unit_id = ?",
            (FAILED, error[:2000], time.time(), unit_id),
        )

    # -- reading --------------------------------------------------------

    def report(self, *, distinct: bool = False) -> CompletenessReport:
        rows = self._db.execute("SELECT * FROM units ORDER BY unit_id").fetchall()
        built = self._db.execute(
            "SELECT MAX(walked_at) FROM units WHERE walked_at IS NOT NULL"
        ).fetchone()[0]
        report = CompletenessReport(
            slug=self._get_meta("slug") or "",
            units=[_coverage_from_row(row) for row in rows],
            built_at=built,
        )
        if distinct:
            groups = group_results(self.results())
            report.games = len(groups)
            report.variants = sum(g.variant_count for g in groups)
        return report

    def results(
        self,
        query: str = "",
        platform: str | None = None,
        limit: int | None = None,
    ) -> list[SearchResult]:
        """Catalogued rows as `SearchResult`s, for grouping or for search.

        Matching is every term as a substring of the title, case-folded --
        the same shape the plugin's own live search uses, done in SQL over
        an index instead of over a directory listing fetched per query.
        Ranking is left to `rom_hub.grouping`, which the caller runs.
        """
        sql = [
            "SELECT r.* FROM records r JOIN units u ON u.unit_id = r.unit_id "
            "WHERE u.included = 1"
        ]
        params: list = []
        for term in (query or "").lower().split():
            sql.append("AND LOWER(r.title) LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(term)}%")
        if platform:
            sql.append("AND r.platform_key = ?")
            params.append(platform.strip().lower())
        sql.append("ORDER BY LENGTH(r.title), r.title, r.unit_id")
        if limit is not None:
            sql.append("LIMIT ?")
            params.append(max(0, int(limit)))
        cursor = self._db.execute(" ".join(sql), tuple(params))
        return [_result_from_row(row) for row in cursor]

    def count(self) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM records r JOIN units u ON u.unit_id = r.unit_id "
            "WHERE u.included = 1"
        ).fetchone()[0]

    # -- meta -----------------------------------------------------------

    def _set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def _get_meta(self, key: str) -> str | None:
        row = self._db.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None


# -- the driver ----------------------------------------------------------


#: How many times the subprocess may be replaced during one build. A unit
#: that kills the plugin costs one restart, and a source of seventy-one
#: units cannot need more restarts than it has units -- so this is a stop
#: on a plugin that dies every single time, not a working budget.
MAX_RESTARTS = 100


class _Worker:
    """The plugin subprocess, replaced when a unit kills it.

    This class exists because of a failure observed on the first real
    build, and it is worth writing down because the naive driver looks
    correct until it happens.

    `PluginProcess` enforces its 30-second call budget by **killing the
    subprocess** -- that is the only portable way to interrupt a blocking
    read on a pipe. So a single slow unit does not merely fail: it leaves
    the process dead, and every following unit fails with "plugin process
    is not running". Measured, one rate-limited 28-file item took out
    seventeen units behind it, including every one of the big sets. The
    catalogue then reported 15,477 of 29,955 -- an honest number, and
    honest about entirely the wrong thing, because those seventeen units
    were perfectly reachable.

    The fix is not a longer timeout. It is to treat a dead process as an
    ordinary consequence of a failed unit and start another one. The
    catalogue is already resumable per unit, so the restart costs one
    subprocess launch and nothing else.
    """

    def __init__(self, open_process, max_restarts: int = MAX_RESTARTS):
        self._open = open_process
        self._proc = None
        self.max_restarts = max_restarts
        self.restarts = 0
        self.exhausted = False

    def get(self):
        """A live process, starting one if the last was killed."""
        if self._proc is None:
            if self.exhausted:
                raise CensusError(
                    f"the plugin subprocess has been replaced "
                    f"{self.restarts} times, the ceiling; it is dying on "
                    f"every unit rather than on an unlucky one"
                )
            self._proc = self._open()
        return self._proc

    def recycle(self) -> None:
        """Discard the current process; the next unit gets a fresh one.

        Called after *any* unit failure rather than only after a detected
        death. Distinguishing "the plugin raised" from "the process was
        killed" would mean parsing an error message, and the cost of being
        wrong is the cascade above. A subprocess launch is milliseconds and
        a unit failure is rare, so recycling unconditionally is the cheap
        side of that trade.
        """
        self.close()
        self.restarts += 1
        if self.restarts >= self.max_restarts:
            self.exhausted = True

    def close(self) -> None:
        proc, self._proc = self._proc, None
        closer = getattr(proc, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:  # noqa: BLE001 - teardown must not mask a result
                pass


def build(
    open_process,
    catalogue: Catalogue,
    *,
    slug: str,
    kinds=None,
    progress=None,
    max_pages_per_unit: int = MAX_PAGES_PER_UNIT,
    max_restarts: int = MAX_RESTARTS,
) -> CompletenessReport:
    """Walk every in-scope unit of one source into `catalogue`.

    `open_process` is a zero-argument callable returning a **started**
    plugin process -- not a process, because one build may need several.
    See `_Worker` for what went wrong when this took a single process.

    A full enumeration cannot fit in one call: each has a 30-second budget.
    That is why `CensusProvider` is shaped as "cursor in, page out" -- the
    way to spend an unbounded amount of time is to make a bounded number of
    calls many times.

    Every page is committed before the next is asked for. A build killed at
    any point leaves a catalogue that resumes; nothing is held in memory
    waiting for a final flush.
    """
    worker = _Worker(open_process, max_restarts=max_restarts)
    try:
        units = worker.get().census_scope()
        catalogue.begin(slug, units, kinds=kinds)
        _say(progress, f"{slug}: scope is {len(units)} units")

        for unit, cursor in catalogue.pending():
            pages = 0
            try:
                while True:
                    page = worker.get().census_page(unit, cursor)
                    stored = catalogue.add_page(unit.unit_id, page)
                    pages += 1
                    _say(
                        progress,
                        f"{slug}: {unit.unit_id} +{stored} "
                        f"({'more' if page.cursor else 'done'})",
                    )
                    if page.cursor is None:
                        break
                    if page.cursor == cursor:
                        # A cursor that does not move is a plugin bug, and
                        # the honest response is to stop this unit and say
                        # so rather than loop until somebody notices.
                        raise CensusError(
                            f"cursor {page.cursor!r} did not advance; the "
                            f"unit cannot be finished without re-reading "
                            f"the same page"
                        )
                    cursor = page.cursor
                    if pages >= max_pages_per_unit:
                        raise CensusError(
                            f"stopped after {pages} pages, the per-unit "
                            f"ceiling; the unit is recorded as partial "
                            f"rather than complete"
                        )
            except Exception as exc:  # noqa: BLE001 - one unit, isolated
                # Isolated like a search fan-out: one item that 404s, times
                # out or kills the plugin costs its own rows and nothing
                # else, and the report names it. A build that abandoned
                # seventy units because the seventy-first was unavailable
                # would be useless against a service that rate-limits.
                catalogue.fail_unit(unit.unit_id, f"{type(exc).__name__}: {exc}")
                _say(progress, f"{slug}: {unit.unit_id} FAILED: {exc}")
                worker.recycle()
    finally:
        worker.close()

    return catalogue.report(distinct=True)


# -- helpers -------------------------------------------------------------


def _say(progress, message: str) -> None:
    if progress is not None:
        progress(message)


def _like_escape(term: str) -> str:
    """Neutralise LIKE's wildcards in a user-supplied search term.

    Without this a query containing `%` matches everything, which is not a
    security problem here -- the value is bound, not interpolated -- but is
    a correctness one: `100%` should find *100%* and not the whole
    catalogue.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _loads_counts(raw: str) -> dict[str, int]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(k): int(v)
        for k, v in value.items()
        if isinstance(v, int) and not isinstance(v, bool) and v >= 0
    }


def _unit_from_row(row) -> CensusUnit:
    return CensusUnit(
        unit_id=row["unit_id"],
        label=row["label"],
        kind=row["kind"],
        declared_total=row["declared_total"],
        platform=row["platform"],
        size_bytes=row["size_bytes"],
        include=bool(row["included"]),
        reason=row["reason"] or "",
    )


def _coverage_from_row(row) -> UnitCoverage:
    return UnitCoverage(
        unit_id=row["unit_id"],
        label=row["label"],
        kind=row["kind"],
        platform=row["platform"],
        declared_total=row["declared_total"],
        kept=row["kept"],
        skipped=_loads_counts(row["skipped_json"]),
        included=bool(row["included"]),
        reason=row["reason"] or "",
        state=row["state"],
        error=row["error"],
    )


def _result_from_row(row) -> SearchResult:
    try:
        extra = json.loads(row["extra_json"] or "{}")
    except (TypeError, ValueError):
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    return SearchResult(
        source_id=row["record_id"],
        title=row["title"],
        platform=row["platform"],
        size_bytes=row["size_bytes"],
        url=row["url"],
        extra={str(k): str(v) for k, v in extra.items()},
    )
