"""What an Archive.org item actually *is*, from what the search index says.

Seventy-one items match `identifier:nointro*` and they are not the same
kind of thing. Twenty-odd are flat directories of per-game archives.
`NoIntroROMsCollection` is sixty-two files and 44.8 GB in which each file
is an entire console's set. `nointro_wiiu_cdn_nov_2020_2` is 928 GB of
Nintendo's own distribution tree. Counting all three the same way is how a
catalogue ends up claiming a CDN mirror as forty thousand games.

The classifier runs on what one `advancedsearch.php` request already
returns -- `mediatype`, `files_count`, `item_size` -- so the whole scope is
decided before a single item is opened. That is deliberate: opening
seventy-one items to find out which ones are worth opening is the shape
this is trying to avoid.

**The signal is the average file.** A cartridge ROM is kilobytes to a few
megabytes; a set archive is hundreds of megabytes. `item_size /
files_count` separates them cleanly on the real corpus, and the two
thresholds below were chosen by looking at where the gap actually is
rather than by rounding a guess:

    NoIntro-commodore-64_202302   3,682 files   0.36 GB   ~0.1 MB/file
    nointro.md                    2,779 files   1.77 GB   ~0.7 MB/file
    NoIntroIBMPc                    322 files   7.60 GB  ~24   MB/file
    NoIntro_Atari                    12 files   0.26 GB  ~22   MB/file
    NoIntroPack2019Dec01MinusDS      79 files  43.64 GB  ~566  MB/file
    nointro-merged                   21 files  37.01 GB ~1,763 MB/file

`NoIntroIBMPc` is the case that stops this being a single threshold. Its
average file is 24 MB -- the same order as `NoIntro_Atari`, which is eight
`.7z` files each holding a whole machine -- but it is a genuine directory
of 318 individual PC games, because KryoFlux flux dumps of floppies really
are that big. What tells them apart is *how many*: a set has hundreds of
entries, a bundle of sets has a dozen. So a large average condemns an item
outright only past `PACK_MEAN_BYTES`; between `SMALL_PACK_MEAN_BYTES` and
that, the file count decides.

Nothing here is a curated per-item list, and that is on purpose. A hand
list would be right about these seventy-one items and silent about the
seventy-second, and the whole point of a census is that a new item shows
up in the report rather than in nobody's field of view.
"""

from __future__ import annotations

MIB = 1024 * 1024
GIB = 1024 * 1024 * 1024

#: Past this, an item is a bulk mirror rather than a set. The three items
#: over it are Nintendo's Wii U CDN (1,115 GB and 928 GB in two parts) and
#: Sony's PS Vita `PSVgameSD` tree (291 GB, plus a 167 GB supplement) --
#: which are exactly the things nobody means by "the No-Intro archive".
CDN_BYTES = 100 * GIB

#: An average file this large is a set archive whatever else is true.
PACK_MEAN_BYTES = 64 * MIB

#: And this large is a set archive *if* there are few enough of them to be
#: one bundle rather than a library of unusually big games.
SMALL_PACK_MEAN_BYTES = 8 * MIB
SMALL_UNIT_FILES = 64

#: Below this an item is not a set of anything: one `.7z` of DAT files, a
#: stray `.pkg`, an item holding nothing but Archive.org's own bookkeeping.
#: Counted against `files_count`, which includes the four to six
#: bookkeeping entries Archive.org adds to every item -- so this is
#: "roughly four real files", and `nointro.sg`, a legitimate SuperGrafx set
#: with five ROMs in it, clears it.
MIN_SET_FILES = 8

#: Archive.org's own word for it. `image` is deliberately absent: it is
#: what `nointro.gbamultiboot` is filed under, and that is a real ROM set.
MEDIA_MEDIATYPES = frozenset({"audio", "movies"})

#: Why each kind is not walked by default, in words an operator reads in
#: `rom-hub catalogue report`. A kind with no entry here is walked.
EXCLUSION_REASONS = {
    "cdn-dump": (
        "a console maker's distribution tree mirrored whole, not a ROM set "
        "-- hundreds of gigabytes of one platform's digital catalogue"
    ),
    "pack": (
        "archives-of-archives: each file is an entire machine's set, so the "
        "file count is a count of consoles and not of games"
    ),
    "media": "soundtracks, screenshots or video, not ROMs",
    "dat": "No-Intro's DAT files -- the catalogue itself, not its contents",
    "other": (
        "too few files to be a set: a loose archive, a DAT bundle, or an "
        "item holding nothing but Archive.org's own bookkeeping"
    ),
}


def classify(mediatype: str | None, files_count: int, item_size: int) -> str:
    """One of `rom_hub.types.KNOWN_CENSUS_KINDS`, from index fields alone.

    Order matters and is not arbitrary. `mediatype` first, because an item
    Archive.org files under `audio` is not a ROM set however its sizes
    read -- `NoIntroUnofficialVideoGame` is eighteen 700 MB soundtrack
    archives and would otherwise classify as a pack, which is true but
    much less useful than "this is music". Then size, because a 928 GB
    item needs no further argument.
    """
    if (mediatype or "").strip().lower() in MEDIA_MEDIATYPES:
        return "media"
    files_count = max(0, int(files_count or 0))
    item_size = max(0, int(item_size or 0))
    if item_size >= CDN_BYTES:
        return "cdn-dump"
    mean = (item_size / files_count) if files_count else 0
    if mean >= PACK_MEAN_BYTES:
        return "pack"
    if mean >= SMALL_PACK_MEAN_BYTES and files_count < SMALL_UNIT_FILES:
        return "pack"
    if files_count >= MIN_SET_FILES:
        return "roms"
    return "other"


def exclusion_reason(kind: str) -> str:
    """Why an item of this kind is not a ROM directory, in one sentence.

    Empty for `roms`. Never empty for anything else: `CensusUnit` refuses
    an exclusion with no reason attached, which is the point -- leaving a
    928 GB dump out is a good decision and leaving it out silently is not.
    """
    return EXCLUSION_REASONS.get(kind, "")
