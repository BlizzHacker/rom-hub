"""Which library platforms this dataset is actually about.

**This is the guard that carries the plugin.** Ludusavi's manifest is a PC
dataset — RomM issue #1908 introduces the tool in exactly those words, "a
save manager for a multitude of PC games and storefronts" — and its entries
say where a game writes save files *on a PC filesystem*. That statement is
true of a DOS game and meaningless for a cartridge dump, where the save
lives in the cartridge's own SRAM or in a file the emulator chooses and
names.

The failure this prevents is not hypothetical, and it is the ordinary case
rather than a corner one. `Sonic the Hedgehog` is in the manifest. So are
`Prince of Persia`, `Aladdin`, `Batman`, `Contra`, `Double Dragon`,
`Golden Axe`, `Rampage`, `Tetris`-adjacent titles and a long tail of other
names a console library is full of. Every one of them would match a
console ROM of the same name on the title alone, and every one of those
matches would attach a Windows or Steam path to a game that has never seen
one. A wrong save path is worse than a missing one, because nobody
re-checks a field that is already filled in.

So the platform is checked **first**, before the title is even normalised,
and a platform outside this set is refused by name rather than searched
and missed. The set is derived rather than invented: ludusavi's `when.os`
vocabulary is `windows`, `linux`, `mac` and `dos`, and these are the RomM
platform slugs those four correspond to. All five exist in RomM 4.9.2's
`GET /api/platforms/supported`.

Deliberately absent, and each for a stated reason rather than an oversight:

* every console, handheld and arcade platform — the save is the
  emulator's business, not the game's
* `scummvm` — a ScummVM game's saves go where ScummVM puts them, which is
  ScummVM's own save directory and not the path the DOS original used
* `browser`, `steam-vr`, `android`, `ios` — ludusavi does not back these
  up, and the manifest carries no `os` value for them
* `pc-booter` — an IBM PC boot floppy saves onto the floppy; there is no
  filesystem path outside the disk image for a backup tool to name

`platforms` in `manifest.toml` can widen this. It is an operator's
decision to make deliberately, and the README says what it costs.
"""

#: RomM platform slug -> the ludusavi `when.os` value it corresponds to.
#: The mapping is one-way and is here to be read, not consulted: the
#: plugin reports every `when` clause the manifest gives, unfiltered, so
#: an operator can see for themselves which OS a path belongs to.
PC_PLATFORMS: dict[str, str] = {
    "dos": "dos",
    "win": "windows",
    "win3x": "windows",
    "linux": "linux",
    "mac": "mac",
}


def is_pc_platform(slug: str | None, allowed) -> bool:
    return bool(slug) and slug.strip().lower() in allowed


def describe(allowed) -> str:
    """The permitted platforms, for a refusal message."""
    return ", ".join(sorted(allowed)) or "(none configured)"
