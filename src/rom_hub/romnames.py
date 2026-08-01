"""Reading a ROM's name the way No-Intro, GoodTools and TOSEC wrote it.

Every result the Hub merges arrives as a *string a human typed for a
different purpose*. `Batman Returns (USA) (Rev 1) [!].md` is not a title --
it is a title, a region, a revision, a dump-quality flag and a file
extension, concatenated by a naming convention that predates this project
by twenty years. Search cannot group anything until it can take that
string apart.

Two rules govern everything below.

**Parse to group, never to discard.** This project has already shipped a
filename validator strict enough to drop every GoodTools `[!]` name it saw
-- the "verified good dump" marker, i.e. exactly the files people want
most -- and nothing said so. Nothing here filters. A name that does not
match a single pattern still parses: it becomes a title with no tags, gets
its own group, and shows up as a row. The worst outcome of a parse failure
is a row that did not merge with its siblings.

**Conservative beats clever.** Wrongly merging two different games is a
far worse result than showing two rows, so every normalisation here is one
that can be justified without reference to any particular title:
case, Latin accents, punctuation, leading/trailing articles, `&` vs
"and". Notably absent: roman-numeral folding, subtitle stripping, and
trailing-number removal. `Sonic the Hedgehog` and `Sonic the Hedgehog 2`
are different games; no amount of duplicate suppression is worth risking
that.

The output of `parse()` feeds two different decisions, and they are
deliberately separate:

* `title_key` decides which **game** a result belongs to.
* `variant_key` decides which **dump of that game** it is.

Cross-source duplicates share both. Cross-variant siblings share the first
and differ in the second. See `rom_hub.grouping`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

# --- file extensions ---------------------------------------------------
#
# Stripped only when the suffix is a name we recognise. A blanket "drop
# whatever follows the last dot" would turn `Sonic 3.0` into `Sonic 3` and
# `Mario Bros.` into `Mario Bros` -- the first is a wrong merge waiting to
# happen and the second is a title. An allowlist cannot make that mistake:
# an unrecognised suffix simply stays part of the title, which costs a
# missed merge and never a wrong one.
_EXTENSIONS = frozenset(
    """
    zip 7z rar gz bz2 xz tar lzh lha
    bin rom img iso cue chd ccd sub mdf mds nrg gdi cdi
    md gen smd sms gg sg sc col int vec a26 a52 a78 lnx ws wsc ngp ngc vb min
    nes fds unf unif nsf sfc smc swc fig n64 z64 v64 ndd nds dsi 3ds cia cci
    gb gbc gba srl xci nsp gcm rvz wbfs wad wux cso pbp vpk xex xbe
    pce sgx tg16 cue32
    adf adz dms ipf st msa dim d64 d71 d81 g64 t64 tap prg crt nib po dsk woz
    atr xfd cas wav tzx cdt uef ssd dsd mgt sad trd scl fdi fdd hdf
    j64 jag lyx sv gam gamegear pc2 mgw
    """.split()
)

# --- regions -----------------------------------------------------------
#
# Both spellings the conventions actually use: No-Intro writes the country
# out (`(USA, Europe)`), GoodTools packs initials together (`(UE)`). They
# have to canonicalise to the same thing or the same ROM from two sources
# never merges.
_REGION_WORDS = {
    "usa": "USA",
    "us": "USA",
    "u.s.a.": "USA",
    "america": "USA",
    "north america": "USA",
    "europe": "Europe",
    "eur": "Europe",
    "pal": "Europe",
    "japan": "Japan",
    "jpn": "Japan",
    "jp": "Japan",
    "ntsc-j": "Japan",
    "world": "World",
    "australia": "Australia",
    "brazil": "Brazil",
    "canada": "Canada",
    "china": "China",
    "korea": "Korea",
    "taiwan": "Taiwan",
    "hong kong": "Hong Kong",
    "asia": "Asia",
    "germany": "Germany",
    "france": "France",
    "italy": "Italy",
    "spain": "Spain",
    "netherlands": "Netherlands",
    "sweden": "Sweden",
    "norway": "Norway",
    "denmark": "Denmark",
    "finland": "Finland",
    "russia": "Russia",
    "poland": "Poland",
    "portugal": "Portugal",
    "greece": "Greece",
    "israel": "Israel",
    "india": "India",
    "mexico": "Mexico",
    "belgium": "Belgium",
    "austria": "Austria",
    "switzerland": "Switzerland",
    "uk": "UK",
    "united kingdom": "UK",
    "scandinavia": "Scandinavia",
    "latin america": "Latin America",
    "unknown": "Unknown",
}

# GoodTools initials. Only consulted for a *parenthesised* tag: `[a]` is
# "alternate dump" and `(A)` is Australia, and telling them apart is
# exactly why the bracket style is kept through parsing.
_REGION_LETTERS = {
    "U": "USA",
    "E": "Europe",
    "J": "Japan",
    "W": "World",
    "A": "Australia",
    "B": "Brazil",
    "C": "China",
    "K": "Korea",
    "F": "France",
    "G": "Germany",
    "I": "Italy",
    "S": "Spain",
    "D": "Netherlands",
    "N": "Netherlands",
    "R": "Russia",
    "H": "Hong Kong",
}

# --- tag vocabularies --------------------------------------------------

_REVISION = re.compile(
    r"^(?:rev(?:ision)?\.?\s*|v(?:er(?:sion)?\.?)?\s*)([0-9][0-9a-z.]*|[a-z])$"
)
_DISC = re.compile(r"^(?:disc|disk|cd|side|tape|cart|part)\s*([0-9a-z]+)$")
_LANGUAGES = re.compile(r"^[A-Z][a-z](?:\s*,\s*[A-Z][a-z])+$")

# Parenthesised words that describe what the dump *is* rather than where it
# is from. An optional trailing index is kept: `(Beta 2)` and `(Beta 3)`
# are two different builds and must not collapse into one row.
_PAREN_FLAGS = {
    "beta": "beta",
    "proto": "proto",
    "prototype": "proto",
    "demo": "demo",
    "sample": "sample",
    "preview": "preview",
    "kiosk": "kiosk",
    "promo": "promo",
    "debug": "debug",
    "alt": "alternate",
    "alternate": "alternate",
    "unl": "unlicensed",
    "unlicensed": "unlicensed",
    "pirate": "pirate",
    "aftermarket": "aftermarket",
    "homebrew": "homebrew",
    "bios": "bios",
    "program": "program",
    "test program": "program",
    "virtual console": "rerelease",
    "switch online": "rerelease",
    "classic mini": "rerelease",
    "gamecube": "rerelease",
    "e-reader": "rerelease",
    "steam": "rerelease",
}

# GoodTools bracket codes. `!` is the one that matters most: it marks a
# dump verified against the canonical checksum, and it is the single most
# common reason a well-formed ROM name looks "weird" to a naive filter.
_BRACKET_FLAGS = {
    "!": "verified",
    "!p": "pending",
    "a": "alternate",
    "b": "baddump",
    "o": "overdump",
    "f": "fixed",
    "h": "hack",
    "p": "pirate",
    "t": "trained",
    "x": "badchecksum",
    "cr": "cracked",
}
_BRACKET_INDEXED = re.compile(r"^([a-z]{1,2})([0-9]+)$")
_TRANSLATION = re.compile(r"^t[+-]\s*(.*)$")

# One parenthesised or bracketed group at the very end of the string.
# Trailing-only on purpose: every convention this module parses writes its
# tags as a suffix, and a source that puts prose *after* the tags is a
# source this module should decline to interpret rather than guess at. The
# cost of declining is a row that keeps its whole title as its group key --
# more rows, never a wrong merge.
_TRAILING_TAG = re.compile(r"\s*(\(([^()]*)\)|\[([^\[\]]*)\])\s*$")

# Articles that the conventions move to the end (`Legend of Zelda, The`)
# and that catalogues elsewhere leave at the front (`The Legend of Zelda`).
# Folding both spellings to the same key is what lets those two rows meet.
_ARTICLES = ("the", "a", "an", "le", "la", "les", "der", "die", "das", "el", "los")

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

#: How many parsed names to remember. Search results repeat titles heavily
#: -- eight `Batman Returns` rows parse the same eight strings only once
#: per distinct string -- and a bounded cache keeps a 10,000-row Console
#: Living Room page from being 10,000 regex walks.
_CACHE_SIZE = 8192


def _fold_accents(text: str) -> str:
    """Drop Latin diacritics and nothing else.

    `Pokemon` and `Pokémon` are one game and have to key the same. A blanket
    "NFKD then drop every combining mark" would also strip the dakuten off
    Japanese kana -- turning `ガ` into `カ`, which is a different syllable
    and a genuinely wrong merge. So a mark is dropped only when the
    character it decorates is ASCII, which is precisely the Latin case.

    The survivors are recomposed with NFC before returning. Without that
    they would still be *separate* combining characters, and the
    punctuation pass downstream -- which sees a combining mark as
    punctuation, because it is not alphanumeric -- would delete the ones
    this function just went to the trouble of keeping.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    out: list[str] = []
    for ch in decomposed:
        if unicodedata.combining(ch):
            if out and out[-1].isascii():
                continue  # a Latin accent: drop it
            out.append(ch)  # someone else's script: leave it alone
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def normalise_title(text: str) -> str:
    """The key two titles must share to be considered the same game.

    Every step is a transformation that is true of *titles in general*
    rather than of any title in particular:

    * case and Latin accents -- `POKEMON`, `Pokémon`, `pokemon`;
    * `&` spelled as the word, because catalogues disagree
      (`Sonic & Knuckles` / `Sonic and Knuckles`);
    * punctuation to whitespace, so `Mario Bros.`, `Mario Bros`,
      `Mario-Bros` and `Mario: Bros` agree;
    * the leading or trailing article, so `The Legend of Zelda` and
      `Legend of Zelda, The` agree.

    What is deliberately *not* here is anything that could change which
    game is being named. Digits stay (`Sonic 2` is not `Sonic`), roman
    numerals are not folded into digits, and no subtitle is stripped.
    """
    text = _fold_accents(text or "").casefold()
    text = text.replace("&", " and ")
    text = _PUNCTUATION.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    if not text:
        return ""
    words = text.split(" ")
    # `Legend of Zelda, The` -> the comma is already gone, so the article is
    # simply the last word. Only moved when something would remain.
    if len(words) > 1 and words[-1] in _ARTICLES:
        words = words[:-1]
    if len(words) > 1 and words[0] in _ARTICLES:
        words = words[1:]
    return " ".join(words)


def strip_extension(name: str) -> str:
    """`name` without a trailing extension this module recognises."""
    head, dot, tail = name.rpartition(".")
    if not dot or not head:
        return name
    if tail.lower() in _EXTENSIONS:
        return head
    return name


def _canonical_regions(body: str, bracketed: bool) -> tuple[str, ...] | None:
    """The regions `body` names, or None if it does not name any.

    Handles both `USA, Europe` and the GoodTools `UE`. Returns a sorted
    tuple so `(U) (E)` and `(USA, Europe)` produce the same value -- which
    is the whole point, since those are the same ROM from two catalogues.
    """
    if bracketed:
        # `[a]` is an alternate dump, not Australia.
        return None
    parts = [p.strip() for p in body.split(",")]
    named = [_REGION_WORDS.get(p.lower()) for p in parts if p]
    if named and all(named):
        return tuple(sorted(set(named)))  # type: ignore[arg-type]
    # GoodTools initials: the whole tag is region letters and nothing else.
    if 1 <= len(body) <= 4 and body.isalpha() and body.isupper():
        letters = [_REGION_LETTERS.get(c) for c in body]
        if all(letters):
            return tuple(sorted(set(letters)))  # type: ignore[arg-type]
    return None


def _tag_token(body: str, bracketed: bool) -> str:
    """One tag reduced to a comparable token.

    Unrecognised tags become `tag:<text>` rather than being dropped. That
    is the discard rule in code: a tag this module has never seen still
    distinguishes one variant from another, so the two stay separate rows
    instead of being merged on the strength of a pattern that did not
    match.
    """
    text = _SPACES.sub(" ", body.strip())
    lowered = text.lower()

    if not bracketed:
        match = _REVISION.match(lowered)
        if match:
            return f"rev:{match.group(1)}"
        match = _DISC.match(lowered)
        if match:
            return f"disc:{match.group(1)}"
        if _LANGUAGES.match(text):
            codes = sorted(p.strip().lower() for p in text.split(","))
            return "lang:" + ",".join(codes)
        # `Beta`, `Beta 2`, `Demo 1` -- the index is part of the identity.
        stem, _, index = lowered.rpartition(" ")
        if stem and index.isdigit() and stem in _PAREN_FLAGS:
            return f"flag:{_PAREN_FLAGS[stem]}:{index}"
        if lowered in _PAREN_FLAGS:
            return f"flag:{_PAREN_FLAGS[lowered]}"
        return f"tag:{lowered}"

    if lowered in _BRACKET_FLAGS:
        return f"flag:{_BRACKET_FLAGS[lowered]}"
    match = _TRANSLATION.match(lowered)
    if match:
        return f"flag:translation:{match.group(1)}"
    match = _BRACKET_INDEXED.match(lowered)
    if match and match.group(1) in _BRACKET_FLAGS:
        return f"flag:{_BRACKET_FLAGS[match.group(1)]}:{match.group(2)}"
    return f"tag:{lowered}"


@dataclass(frozen=True)
class RomName:
    """One result's name, taken apart.

    `raw` is always the string that came in. Nothing here replaces it: the
    CLI still prints real titles, and `title`/`title_key`/`variant_key`
    exist only to decide what sits next to what.
    """

    raw: str
    title: str
    title_key: str
    tags: tuple[str, ...]
    regions: tuple[str, ...]
    tokens: tuple[str, ...]

    @property
    def variant_key(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """What makes this dump different from its siblings.

        Regions are separated from the rest so that `(U) (E)` and
        `(USA, Europe)` compare equal -- the same ROM described by two
        catalogues has to reach the same key or cross-source dedup never
        fires.
        """
        return (self.regions, self.tokens)

    @property
    def flags(self) -> frozenset[str]:
        """The semantic flags on this dump (`verified`, `beta`, ...).

        Used for ordering only. Nothing is ever hidden because of a flag:
        a bad dump sorts last and stays reachable.
        """
        return frozenset(
            t.split(":")[1] for t in self.tokens if t.startswith("flag:")
        )

    @property
    def label(self) -> str:
        """How to name this variant to a human.

        The tags verbatim, in the order the source wrote them -- because
        the operator is going to copy a title out of this listing and paste
        it somewhere, and a prettified reconstruction is not the name of
        anything.
        """
        return " ".join(self.tags) if self.tags else "(no variant tags)"


@lru_cache(maxsize=_CACHE_SIZE)
def parse(raw: str) -> RomName:
    """Take a result title apart into game, tags and variant identity.

    Total: every string parses. One that matches nothing becomes its own
    title with no tags, which groups with exactly the results that share
    that title and nothing else.
    """
    original = raw or ""
    working = strip_extension(original.strip())

    tags: list[str] = []
    styles: list[bool] = []  # True where the tag was bracketed
    while True:
        match = _TRAILING_TAG.search(working)
        if not match:
            break
        remainder = working[: match.start()].rstrip()
        if not remainder:
            # The whole string is one tag -- `(Homebrew)` as a title. Taking
            # it would leave nothing to group on, so it stays the title.
            break
        bracketed = match.group(3) is not None
        body = match.group(3) if bracketed else match.group(2)
        tags.insert(0, match.group(1))
        styles.insert(0, bracketed)
        working = remainder
        del body

    title = working.strip() or original.strip()
    title_key = normalise_title(title) or normalise_title(original)

    regions: set[str] = set()
    tokens: list[str] = []
    for tag, bracketed in zip(tags, styles):
        body = tag[1:-1]
        named = _canonical_regions(body, bracketed)
        if named is not None:
            regions.update(named)
            continue
        tokens.append(_tag_token(body, bracketed))

    return RomName(
        raw=original,
        title=title,
        title_key=title_key,
        tags=tuple(tags),
        regions=tuple(sorted(regions)),
        tokens=tuple(sorted(tokens)),
    )


# --- display ordering ---------------------------------------------------
#
# Which variant a person most likely wants at the top of an expanded group.
# Ordering only: every variant is listed, and this decides nothing about
# what exists.

_STATUS_RANK = (
    # (flag, rank). Lowest wins. Absent from this table -> 10, i.e. a
    # plain release, which is the common case and sits in the middle.
    ("verified", 0),
    ("fixed", 20),
    ("translation", 25),
    ("alternate", 30),
    ("trained", 35),
    ("hack", 40),
    ("preview", 45),
    ("demo", 50),
    ("sample", 52),
    ("beta", 55),
    ("proto", 60),
    ("kiosk", 62),
    ("promo", 63),
    ("debug", 64),
    ("pirate", 70),
    ("cracked", 72),
    ("overdump", 80),
    ("badchecksum", 85),
    ("baddump", 90),
)

# A display preference, not a claim about which dump is "correct". Regions
# nobody named sort last because "we do not know" is less useful than any
# answer, not because it is worse.
_REGION_PREFERENCE = ("World", "USA", "Europe", "Japan")


def variant_rank(name: RomName) -> tuple:
    """Sort key for one variant inside a group. Never a filter."""
    flags = name.flags
    status = min(
        (rank for flag, rank in _STATUS_RANK if flag in flags), default=10
    )
    if not flags:
        status = min(status, 10)
    region_rank = len(_REGION_PREFERENCE)
    for index, region in enumerate(_REGION_PREFERENCE):
        if region in name.regions:
            region_rank = index
            break
    revisions = [t for t in name.tokens if t.startswith("rev:")]
    # A revised release leads, newest first: Rev 2 supersedes Rev 1
    # supersedes the original press, for an operator who has not said
    # otherwise. Ordering only -- the original is still listed.
    revision = (0, _invert(max(revisions))) if revisions else (1, ())
    return (
        status,
        bool(not name.regions),
        region_rank,
        tuple(sorted(name.regions)),
        revision,
        name.raw,
    )


def _invert(text: str) -> tuple:
    """A sort key that orders `text` descending among strings."""
    return tuple(-ord(c) for c in text)
