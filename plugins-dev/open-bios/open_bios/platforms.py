"""This plugin's system names -> a library platform slug.

**This table is the only thing standing between an install and a BIOS
filed under the wrong system**, so it is an exact-match lookup with no
fallback. A system that is not spelled out below raises "needs mapping"
and names itself, and the catalogue refuses to be built.

Firmware makes that stricter than it is for a ROM, not looser. A ROM in
the wrong place is visible -- it is in the library, under a heading that
looks wrong. A BIOS in the wrong place is *invisible*: the emulator that
needed it goes on saying it has no BIOS, and nothing anywhere says why.
The failure mode of a guess here is an operator concluding the firmware
they installed does not work.

Two temptations were considered and rejected:

* **Deriving the slug from the system name.** "Game Boy Color" ->
  `game-boy-color` is wrong (`gbc`), and "Game Boy Advance" ->
  `game-boy-advance` is wrong (`gba`). The slugs are IGDB's abbreviations,
  which are not a function of the name.
* **Falling back to the nearest Game Boy.** The three Game Boy generations
  take *different* boot ROMs and an emulator will not accept one for
  another, so "close enough" produces exactly the silent failure above.

The values were checked against a live RomM's own platform list rather
than against an enum in a document -- `platform_id()` resolves against the
platforms the server actually has, so a slug nobody's library uses would
fail later with a much less useful message.
"""

#: System, as this plugin's catalogue names it -> library platform slug.
SYSTEM_PLATFORMS: dict[str, str] = {
    "Game Boy": "gb",
    "Game Boy Color": "gbc",
    "Game Boy Advance": "gba",
}


class NeedsMapping(Exception):
    """A system this plugin has no platform slug for."""


def platform_for(system: str) -> str:
    """The library platform slug for `system`, or a refusal naming it.

    Called while building the catalogue, not while installing. A source
    added to `catalogue.py` without a row here therefore fails at
    `rom-hub firmware list`, loudly, rather than shipping an item that
    cannot be filed.
    """
    try:
        return SYSTEM_PLATFORMS[system]
    except KeyError:
        known = ", ".join(sorted(SYSTEM_PLATFORMS))
        raise NeedsMapping(
            f"system {system!r} needs mapping: it is not in this plugin's "
            f"system -> platform table, which knows {known}. Firmware is "
            f"keyed by platform and this plugin will not guess one -- a BIOS "
            f"filed under the wrong system is invisible, not visibly wrong."
        ) from None
