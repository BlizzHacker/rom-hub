"""Homebrew Hub `platform` -> RomM platform slug, and back again.

The Hub publishes a `platform` field per entry, and unlike most sources its
vocabulary is small and closed: every one of the 1,571 entries live at the
time of writing is `GB`, `GBC`, `GBA` or `NES` (913 / 447 / 188 / 23, which
sums to the total). That makes an exact-match table cheap and a fallback
indefensible.

**No fallback, and specifically no "GBC is a Game Boy" softening.** The
temptation here is real: the Hub's Game Boy database holds both, `gb` and
`gbc` ROMs both run on the same emulators, and collapsing them would never
visibly break anything. It would still be wrong -- RomM keeps them as
separate platforms because they *are* separate platforms, and a library
that quietly merged them cannot be un-merged later without knowing which
entries were guessed.

The reverse direction is what `--platform` uses. It is a plain inversion,
so it stays correct by construction when a row is added: the Hub's filter
is case-sensitive (`platform=GBC` matches, `platform=gbc` returns zero),
which is exactly the kind of detail that should live in one table rather
than in a caller.

Values were checked against RomM's platform-slug enum
(`backend/handler/metadata/base_handler.py`).
"""

# Homebrew Hub platform -> RomM slug.
HUB_PLATFORMS: dict[str, str] = {
    "GB": "gb",
    "GBC": "gbc",
    "GBA": "gba",
    "NES": "nes",
}

# RomM slug -> Homebrew Hub platform, for the server-side filter.
ROMM_PLATFORMS: dict[str, str] = {slug: hub for hub, slug in HUB_PLATFORMS.items()}


def platform_for(hub_platform: str) -> str | None:
    """The RomM slug for a Homebrew Hub platform, or None.

    None means "not in the table". Callers must turn it into a visible
    refusal naming the value; it never means "use a default".
    """
    if not isinstance(hub_platform, str):
        return None
    return HUB_PLATFORMS.get(hub_platform.strip())


def hub_platform_for(romm_slug: str) -> str | None:
    """The Homebrew Hub platform for a RomM slug, or None.

    None means this source has nothing for that platform -- which is an
    empty result, not an error: asking a Game Boy homebrew archive for
    Dreamcast games is a reasonable question with a boring answer.
    """
    if not isinstance(romm_slug, str):
        return None
    return ROMM_PLATFORMS.get(romm_slug.strip().lower())
