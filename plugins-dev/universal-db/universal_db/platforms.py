"""Universal-DB `systems` -> RomM platform slug, and what a title looks like.

The database publishes a `systems` **list** per entry, and unlike most
sources its vocabulary is closed and tiny: across all 400 live entries the
only values are `3DS` (325 entries), `DS` (67) and both together (8). That
makes an exact-match table cheap and a fallback indefensible.

**DS and 3DS never collapse into one another.** They are two consoles, two
RomM platforms and two incompatible executable formats, and the eight
entries that ship for both ship a *different file* for each -- `PKCount.cia`
and `PKCount.nds` are not two names for one thing. A library that quietly
merged them cannot be un-merged later, because nothing afterwards records
which rows were guessed. An entry that names both systems and is imported
without `--platform` is refused for exactly this reason.

An unknown system raises "needs mapping" naming itself. That is a visible
gap somebody can close in one line; a silently misfiled ROM is not.

Values checked against RomM's own platform-slug enum (`3ds`, `nds`).
"""

# Universal-DB system token -> RomM slug.
SYSTEM_PLATFORMS: dict[str, str] = {
    "3DS": "3ds",
    "DS": "nds",
}

# RomM slug -> Universal-DB system token, for filtering a search.
ROMM_SYSTEMS: dict[str, str] = {
    slug: system for system, slug in SYSTEM_PLATFORMS.items()
}

# What counts as a bootable title on each platform, **best first**.
#
# `.cia` before `.3dsx`: a CIA installs into the HOME menu as a title,
# which is what a ROM library is a library of; a 3DSX runs only from the
# Homebrew Launcher. `.nds` before `.dsi`: the DSi build is the narrower
# one, running only on DSi/3DS hardware, where the NDS build runs
# everywhere the DSi build does and on an original DS as well.
#
# This order also decides between archives, because an archive is chosen by
# what the database says is *inside* it.
TITLE_FORMATS: dict[str, tuple[str, ...]] = {
    "3ds": ("cia", "3dsx"),
    "nds": ("nds", "dsi"),
}

# Containers the host can already handle (`rom_hub.dedup` reads zip with
# the standard library, and `rom_hub.importer` has a documented path for
# the two it cannot hash). A large minority of homebrew ships this way --
# a 3DSX plus its romfs assets is not one file -- so refusing archives
# outright would drop 76 real entries including Apotris, Super Haxagon and
# CrossCraft. They are still only ever planned on the database's own
# evidence about their contents; see `payload.py`.
ARCHIVE_FORMATS: frozenset[str] = frozenset({"zip", "7z", "rar"})


def platform_for(system: str) -> str | None:
    """The RomM slug for a Universal-DB system, or None.

    None means "not in the table". Callers must turn it into a visible
    refusal naming the value; it never means "use a default".
    """
    if not isinstance(system, str):
        return None
    return SYSTEM_PLATFORMS.get(system.strip())


def system_for(romm_slug: str) -> str | None:
    """The Universal-DB system token for a RomM slug, or None.

    None means this source has nothing for that platform -- an empty
    result, not an error. Asking a 3DS/DS homebrew database for Dreamcast
    games is a reasonable question with a boring answer.
    """
    if not isinstance(romm_slug, str):
        return None
    return ROMM_SYSTEMS.get(romm_slug.strip().lower())


def format_rank(platform: str, extension: str) -> int | None:
    """How good a title format `extension` is on `platform`, 0 = best.

    None means it is not a title format there at all, which is the whole
    point: a `.nds` is not a 3DS title and a `.cia` is not a DS one, so
    this function is also what keeps the two platforms apart when an entry
    is published for both.
    """
    formats = TITLE_FORMATS.get((platform or "").strip().lower())
    if not formats:
        return None
    extension = (extension or "").strip().lower().lstrip(".")
    try:
        return formats.index(extension)
    except ValueError:
        return None
