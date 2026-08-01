"""Merging a fan-out into something a person can read.

The Game Gear shelf of a real demo library returns *Batman Returns* eight
times, *Aladdin* four times, and *Desert Assault* and *Agassi* twice each.
Every one of those rows is a genuinely distinct ROM -- a different region,
a different revision, a different dump -- and the listing still reads as
broken. Console Living Room alone holds roughly ten thousand downloadable
Genesis ROMs; concatenating ten sources' worth of that is not a search
result, it is a wall.

So this module answers two different questions, and keeping them apart is
most of the design:

**Which results are the same game?** `(platform, normalised title)`. That
is a `GameGroup`: one row per game per platform, with a variant count.
`prince of persia` should show the game once for Genesis and once for
Game Boy, not once per region-and-revision on each.

**Which results are the same dump of that game?** Everything the naming
convention says about it -- region, revision, disc, dump flags -- plus,
where it exists, a hash. That is a `Variant`. Two sources offering the
identical ROM collapse into one variant listing two sources; two regions
stay two variants.

Nothing is discarded. A group's `results` still contain every row that
went into it, `variant_count` is printed next to every collapsed row, and
`--no-group` turns the whole thing off. This module reorganises a listing;
it never shortens the set of things you can reach.

### The evidence ladder

1. **A matching strong hash is proof.** sha256, sha1 or md5: two results
   carrying the same one are the same bytes, whatever they are called.
2. **A conflicting hash is disproof, and it outranks the name.** Two rows
   named identically whose hashes disagree are two different dumps that a
   catalogue named carelessly, and they stay two rows. CRC-32 counts here
   even though it does not count as proof -- the same asymmetry
   `plugins-dev/hasheous` already applies, and for the same reason: a
   32-bit digest is strong enough to *refuse* a merge and far too weak to
   assert one.
3. **Otherwise, the parsed name decides**, via `rom_hub.romnames`.

Which means the failure mode is always the same direction: an unparsed
name, an unknown tag, or a missing hash produces *more rows*, never a
merge that should not have happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .romnames import RomName, normalise_title, parse, variant_rank
from .types import SearchResult

#: Hashes strong enough to assert "these are the same bytes". A match on
#: any one of them merges two results outright, across sources and across
#: whatever the two catalogues chose to call the file.
STRONG_HASHES = ("sha256", "sha1", "md5")

#: Hashes strong enough only to *refuse* a merge. See the evidence ladder.
WEAK_HASHES = ("crc", "crc32")

#: `extra` keys a plugin may use for each. Spelled out rather than guessed
#: at, because the host reads plugin-authored dictionaries here and a typo
#: must degrade to "no hash known" rather than to a wrong comparison.
_HASH_KEYS = {
    "sha256": ("sha256",),
    "sha1": ("sha1",),
    "md5": ("md5",),
    "crc": ("crc", "crc32"),
}
_HASH_LENGTHS = {"sha256": 64, "sha1": 40, "md5": 32, "crc": 8}
_HEX = re.compile(r"^[0-9a-f]+$")


def _hashes(result: SearchResult) -> dict[str, str]:
    """Whatever digests this result carries, normalised, or nothing.

    Values arrive from an untrusted plugin, so each is checked for the
    right length and hex alphabet before it is allowed to decide anything.
    A malformed digest is treated as absent: the result then merges on its
    name like any other, which is the same outcome it would have had if
    the plugin had said nothing.
    """
    extra = result.extra
    if not extra:
        return {}
    out: dict[str, str] = {}
    for kind, keys in _HASH_KEYS.items():
        for key in keys:
            raw = extra.get(key)
            if not isinstance(raw, str):
                continue
            value = raw.strip().lower()
            if len(value) == _HASH_LENGTHS[kind] and _HEX.match(value):
                out[kind] = value
                break
    return out


def platform_key(platform: str | None) -> str | None:
    """The bucket a result's platform puts it in, or None for "not stated".

    Case and surrounding whitespace only. No alias table on purpose: a
    result whose platform a plugin did not state is **never** merged into
    a named platform, because "unknown" is not evidence of anything and
    guessing which console a ROM is for is how a library ends up wrong in
    a way nobody can see.
    """
    if not isinstance(platform, str):
        return None
    return platform.strip().lower() or None


# --- variant assembly ---------------------------------------------------


class _Sets:
    """Disjoint sets over one group's results, with a hash veto.

    Union-find because the two merge rules can fire in either order: a
    hash can join two rows the names would have kept apart, and a name can
    join two rows neither of which carries a hash. Both are "these belong
    together", and union-find is what makes that order-independent.

    The veto is the part worth reading. Every set carries the digests its
    members claim, and a union is refused outright if the two sets
    disagree about any digest kind they both know. That is what makes a
    conflicting CRC-32 able to *split* two identically-named rows without
    ever being allowed to *merge* two differently-named ones.
    """

    def __init__(self, hashes: list[dict[str, str]]):
        self._parent = list(range(len(hashes)))
        self._known = [dict(h) for h in hashes]

    def find(self, i: int) -> int:
        root = i
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[i] != root:  # path compression
            self._parent[i], i = root, self._parent[i]
        return root

    def compatible(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        left, right = self._known[ra], self._known[rb]
        for kind, value in left.items():
            other = right.get(kind)
            if other is not None and other != value:
                return False
        return True

    def union(self, a: int, b: int) -> bool:
        """Join the two sets unless their digests contradict each other."""
        if not self.compatible(a, b):
            return False
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return True
        self._parent[rb] = ra
        self._known[ra].update(self._known[rb])
        return True


@dataclass
class Variant:
    """One dump of one game, and every source offering it.

    `results` has more than one entry exactly when several sources carry
    the same ROM -- which is the cross-source case, and the one where
    collapsing costs the operator nothing: they still see every source in
    the `sources` column and can import from whichever they like.
    """

    name: RomName
    results: list[SearchResult] = field(default_factory=list)

    @property
    def primary(self) -> SearchResult:
        return self.results[0]

    @property
    def label(self) -> str:
        return self.name.label

    @property
    def sources(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for r in self.results:
            seen.setdefault(r.plugin or "?", None)
        return tuple(sorted(seen))

    @property
    def size_bytes(self) -> int | None:
        sizes = [r.size_bytes for r in self.results if r.size_bytes]
        return max(sizes) if sizes else None

    @property
    def stream_only(self) -> bool:
        """True only when *every* source says so.

        One source offering a downloadable copy is enough to make the
        variant importable, and the flag has to say the useful thing
        rather than the pessimistic one.
        """
        return all(
            (r.extra or {}).get("stream_only") == "true" for r in self.results
        )


@dataclass
class GameGroup:
    """One game on one platform, with its variants reachable underneath."""

    title_key: str
    platform: str | None
    variants: list[Variant] = field(default_factory=list)
    relevance: int = 0

    @property
    def title(self) -> str:
        """What to print on the collapsed row.

        A group with one variant prints that result's own title verbatim,
        tags and all, because collapsing has removed nothing and the full
        name is strictly more informative. A group with several prints the
        shared base title, because that is the thing they are all variants
        *of*.
        """
        if len(self.variants) == 1:
            return self.variants[0].primary.title
        return self.variants[0].name.title

    @property
    def variant_count(self) -> int:
        return len(self.variants)

    @property
    def result_count(self) -> int:
        return sum(len(v.results) for v in self.variants)

    @property
    def results(self) -> list[SearchResult]:
        return [r for v in self.variants for r in v.results]

    @property
    def sources(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for v in self.variants:
            for slug in v.sources:
                seen.setdefault(slug, None)
        return tuple(sorted(seen))

    @property
    def size_bytes(self) -> int | None:
        return self.variants[0].size_bytes

    @property
    def stream_only(self) -> bool:
        return all(v.stream_only for v in self.variants)


# --- relevance ----------------------------------------------------------


def relevance(title_key: str, query_key: str, query_tokens: tuple[str, ...]) -> int:
    """How well a group answers the query. Higher is better.

    Deliberately coarse -- four bands, not a float. A score fine enough to
    order two near-identical titles would also be fine enough to reorder
    them on an irrelevant difference, and the tie-breaks after it
    (platform, then title length, then title) are more explicable than any
    scoring curve.
    """
    if not query_key:
        return 0
    if title_key == query_key:
        return 4
    if title_key.startswith(query_key):
        return 3
    tokens = set(title_key.split())
    if query_tokens and all(t in tokens for t in query_tokens):
        return 2
    if query_tokens and all(t in title_key for t in query_tokens):
        return 1
    return 0


def _group_sort_key(group: GameGroup) -> tuple:
    # Relevance, then platform, then variant -- the order the merged
    # listing is specified to read in. "Not stated" sorts after every named
    # platform so an under-described source cannot head the page.
    return (
        -group.relevance,
        group.platform is None,
        group.platform or "",
        len(group.title_key),
        group.title_key,
    )


# --- the entry point ----------------------------------------------------


def group_results(results, query: str = "") -> list[GameGroup]:
    """Merge a flat fan-out into ordered `GameGroup`s.

    Linear in the number of results apart from the final sort, and the
    per-result work is a cached parse plus a handful of dictionary
    operations -- which matters, because the whole point of this module is
    the case where a fan-out returns thousands of rows.
    """
    query_key = normalise_title(query)
    query_tokens = tuple(query_key.split()) if query_key else ()

    # Parsed once and carried, not looked up again per stage: `parse` is
    # cached, but a cache big enough for one page is not big enough for a
    # ten-thousand-row fan-out, and re-parsing would double the only part
    # of this that is not a dictionary operation.
    buckets: dict[
        tuple[str | None, str], list[tuple[SearchResult, RomName]]
    ] = {}
    for result in results:
        name = parse(result.title)
        buckets.setdefault(
            (platform_key(result.platform), name.title_key), []
        ).append((result, name))

    groups: list[GameGroup] = []
    for (platform, title_key), members in buckets.items():
        groups.append(
            GameGroup(
                title_key=title_key,
                # The platform as some source actually spelled it, not the
                # lowercased key -- the key exists to compare with, and
                # printing it would be showing our own bookkeeping.
                platform=next(
                    (m.platform for m, _ in members if m.platform), None
                ),
                variants=_variants(members),
                relevance=relevance(title_key, query_key, query_tokens),
            )
        )

    groups.sort(key=_group_sort_key)
    return groups


def _variants(pairs: list[tuple[SearchResult, RomName]]) -> list[Variant]:
    """Split one game's results into distinct dumps of it."""
    members = [m for m, _ in pairs]
    names = [n for _, n in pairs]
    digests = [_hashes(m) for m in members]
    sets = _Sets(digests)

    # 1. A shared strong hash merges outright, whatever the names say.
    seen: dict[tuple[str, str], int] = {}
    for index, digest in enumerate(digests):
        for kind in STRONG_HASHES:
            value = digest.get(kind)
            if value is None:
                continue
            first = seen.setdefault((kind, value), index)
            if first != index:
                sets.union(first, index)

    # 2. Then the name, for everything a hash did not already decide. Each
    #    variant key keeps a list of representatives rather than a single
    #    one: when a member's digests contradict the representative it
    #    would otherwise join, it starts a set of its own instead of being
    #    forced in. That is the "conflicting hash splits identical names"
    #    rule, and it is why this is a list and not a dict lookup.
    reps: dict[tuple, list[int]] = {}
    for index, name in enumerate(names):
        candidates = reps.setdefault(name.variant_key, [])
        for candidate in candidates:
            if sets.union(candidate, index):
                break
        else:
            candidates.append(index)

    ordered: dict[int, Variant] = {}
    for index, member in enumerate(members):
        root = sets.find(index)
        variant = ordered.get(root)
        if variant is None:
            ordered[root] = Variant(name=names[root], results=[member])
        else:
            variant.results.append(member)

    variants = list(ordered.values())
    for variant in variants:
        # Stable and explicable: the source whose name parsed to this
        # variant's identity leads, then alphabetically by plugin so the
        # same search prints the same order twice running.
        variant.results.sort(key=lambda r: (r.plugin or "", r.source_id))
    variants.sort(key=lambda v: variant_rank(v.name))
    return variants


# --- paging -------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """One screen of merged groups, and enough to describe the rest.

    Paging happens **after** merging, which is the whole difference from
    what came before: a per-plugin limit of 25 across ten sources is 250
    rows in fan-out order, and no amount of slicing that makes it a page.
    """

    groups: list[GameGroup]
    offset: int
    limit: int
    total_groups: int
    total_results: int

    @property
    def first(self) -> int:
        """1-based index of the first group shown, or 0 when none is."""
        return self.offset + 1 if self.groups else 0

    @property
    def last(self) -> int:
        return self.offset + len(self.groups)

    @property
    def has_more(self) -> bool:
        return self.last < self.total_groups


def paginate(groups: list[GameGroup], limit: int, offset: int = 0) -> Page:
    """The `limit` groups starting at `offset`, plus the totals.

    A negative offset or a non-positive limit is corrected rather than
    refused: these come from a command line, and the useful reading of
    `--limit 0` is "you asked for nothing" while the useful reading of
    `--offset -5` is "the start".
    """
    offset = max(0, offset)
    limit = max(0, limit)
    total_results = sum(g.result_count for g in groups)
    window = groups[offset : offset + limit] if limit else []
    return Page(
        groups=window,
        offset=offset,
        limit=limit,
        total_groups=len(groups),
        total_results=total_results,
    )
