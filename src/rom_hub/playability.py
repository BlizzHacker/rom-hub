"""Which platforms the web player can actually run.

A ROM filed under a platform with no emulator core is *imported but dead*:
it appears in the library, it has a cover, it has metadata, and it does
nothing at all when somebody clicks it. Nothing about the library
afterwards says why -- which makes it exactly the class of failure the
rest of this project refuses to ship. `platforms.py` in a plugin will not
guess which machine a file is for; this module is the same rule pointed
one step further downstream, at whether the machine it named can be
played.

**Where this comes from.** RomM's frontend keeps two tables in
`frontend/src/utils/index.ts`::

    _EJS_CORES_MAP            platform slug -> EmulatorJS cores
    _EJS_NIGHTLY_CORES_MAP    the same, for cores only in the nightly build

and `getSupportedEJSCores()` merges the second into the first **only when
`EJS_NETPLAY_ENABLED` is set**. `isEJSEmulationSupported()` then refuses
any platform whose merged list is empty. So the base map is the honest
answer to "will this play", and the nightly map is a conditional bonus.
The copy below was read from RomM **4.9.2** and is exact: 78 base slugs,
23 nightly slugs of which 3 (`3ds`, `new-nintendo-3ds`, `intellivision`)
are new platforms rather than extra cores for a platform already in the
base map.

**The Xbox client ships the same player**, so this governs there too.
There is no second table to keep in step and no platform that is playable
on one and not the other.

**This must be refreshed when RomM adds cores.** It is a copy of somebody
else's data and it will go stale; that is a property of the arrangement,
not a defect in it. The alternative -- asking the server at runtime -- is
not available: RomM publishes no endpoint for the core map, `/api/config`
carries `EJS_NETPLAY_ENABLED` but not the mapping, and the map lives in
compiled frontend JavaScript. So it is vendored, dated, and pinned by
`tests/test_playability.py`, which fails if these tables stop matching the
`_EJS_CORES_MAP` fixture captured from RomM's own source. When RomM ships
new cores: re-read `frontend/src/utils/index.ts`, update the fixture, and
update these two tables together.

**Staleness fails safe in one direction only.** A platform RomM has since
learned to play still shows here as catalogue-only -- a warning somebody
did not need, which costs an operator one flag. A platform RomM has
*dropped* would show as playable, which is the wrong way round, and is why
the version this was read from is stated rather than implied.

Nothing here refuses an import. A library is not only a player: cataloguing
an Apple II disk, a Dreamcast rip or a ScummVM package is a legitimate
thing to want, and a host that made that impossible would be substituting
its judgement for the operator's. What it does is make sure the operator
knows *before* the ROM lands, rather than discovering it at the point of
clicking play.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The RomM release these tables were read from. Stated because a stale
#: copy that says which release it is stale relative to can be checked;
#: one that does not, cannot.
ROMM_VERSION = "4.9.2"

#: How to name the thing that does or does not have a core, in text an
#: operator reads.
#:
#: It lives here because this module is the only one entitled to say it.
#: `catalog.py` renders the directory and is held to naming no library
#: product at all -- backend-specific knowledge belongs in the package
#: that owns it, and `test_the_display_name_comes_from_the_backend_not_
#: from_this_module` enforces that -- so it asks for these two strings
#: rather than spelling either out.
#:
#: **And this is the honest scope of the whole module.** EmulatorJS is the
#: player RomM ships and the player the Xbox client ships, which is why
#: one table answers for both. A backend that ships a *different* player
#: is not covered: against one of those, a verdict here is still right for
#: anyone who also browses the same library through RomM, and an
#: over-warning for anyone who does not. That is the same direction a
#: stale copy fails in, and it is the reason this produces a warning
#: rather than a refusal -- being wrong must never cost an operator an
#: import they wanted.
PLAYER = "EmulatorJS"

#: The product and release the map was read from, as one phrase, so a
#: caller that may not name a library product can still be specific
#: about which one it checked against.
PLAYER_SOURCE = f"RomM {ROMM_VERSION}"

#: RomM's `_EJS_CORES_MAP`, verbatim. Platform slug -> the EmulatorJS
#: cores that run it. A slug absent from this table has no core in a
#: default RomM, and a ROM filed under it will not start.
EJS_CORES: dict[str, tuple[str, ...]] = {
    "3do": ("opera",),
    "acpc": ("cap32", "crocods"),
    "amiga": ("puae",),
    "amiga-cd32": ("puae",),
    "arcade": ("mame2003", "mame2003_plus", "fbneo", "fbalpha2012_cps1", "fbalpha2012_cps2"),
    "atari-2600-plus": ("stella2014",),
    "atari-lynx-mkii": ("handy",),
    "atari2600": ("stella2014",),
    "atari5200": ("a5200",),
    "atari7800": ("prosystem",),
    "c-plus-4": ("vice_xplus4",),
    "c128": ("vice_x128",),
    "c64": ("vice_x64sc", "vice_x64"),
    "colecovision": ("gearcoleco",),
    # Not a typo -- RomM spells it with three m's, and a "corrected"
    # spelling here would simply never match what the server sends.
    "commmodore-128": ("vice_x128",),
    "commodore-64c": ("vice_x64sc", "vice_x64"),
    "cpet": ("vice_xpet",),
    "doom": ("prboom",),
    "dos": ("dosbox_pure",),
    "famicom": ("fceumm", "nestopia"),
    "fds": ("fceumm", "nestopia"),
    "game-boy-adavance-sp": ("mgba",),
    "game-boy-light": ("gambatte", "mgba"),
    "game-boy-micro": ("mgba",),
    "game-boy-pocket": ("gambatte", "mgba"),
    "game-televisison": ("fceumm",),
    "gamegear": ("genesis_plus_gx",),
    "gb": ("gambatte", "mgba"),
    "gba": ("mgba",),
    "gbc": ("gambatte", "mgba"),
    "genesis": ("genesis_plus_gx",),
    "ique-player": ("mupen64plus_next",),
    "jaguar": ("virtualjaguar",),
    "lynx": ("handy",),
    "master-system-girl": ("genesis_plus_gx",),
    "master-system-super-compact": ("genesis_plus_gx",),
    "mega-pc": ("genesis_plus_gx",),
    "n64": ("mupen64plus_next", "parallel_n64"),
    "nds": ("melonds", "desmume", "desmume2015"),
    "neo-geo-pocket": ("mednafen_ngp",),
    "neo-geo-pocket-color": ("mednafen_ngp",),
    "neogeoaes": ("fbneo",),
    "neogeomvs": ("fbneo",),
    "nes": ("fceumm", "nestopia"),
    "new-style-nes": ("fceumm",),
    "new-style-super-nes-model-sns-101": ("snes9x",),
    "nintendo-ds-lite": ("melonds", "desmume", "desmume2015"),
    "nintendo-dsi": ("melonds", "desmume", "desmume2015"),
    "nintendo-dsi-xl": ("melonds", "desmume", "desmume2015"),
    "pc-fx": ("mednafen_pcfx",),
    "philips-cd-i": ("same_cdi",),
    "psp": ("ppsspp",),
    "psx": ("pcsx_rearmed", "mednafen_psx_hw"),
    "saturn": ("yabause",),
    "sega-game-box-9": ("genesis_plus_gx",),
    "sega-mark-iii": ("genesis_plus_gx",),
    "sega-master-system-ii": ("genesis_plus_gx", "smsplus"),
    "sega-mega-drive-2-slash-genesis": ("genesis_plus_gx",),
    "sega-mega-jet": ("genesis_plus_gx",),
    "sega-nomad": ("genesis_plus_gx",),
    "sega32": ("picodrive",),
    "segacd": ("genesis_plus_gx", "picodrive"),
    "sfam": ("snes9x",),
    "sms": ("genesis_plus_gx",),
    "snes": ("snes9x",),
    "super-famicom-jr-model-shvc-101": ("snes9x",),
    "super-famicom-shvc-001": ("snes9x",),
    "super-nintendo-original-european-version": ("snes9x",),
    "supergrafx": ("mednafen_pce",),
    "swancrystal": ("mednafen_wswan",),
    "tera-drive": ("genesis_plus_gx",),
    "tg16": ("mednafen_pce",),
    "turbografx-cd": ("mednafen_pce",),
    "vic-20": ("vice_xvic",),
    "virtualboy": ("beetle_vb",),
    "wonderswan": ("mednafen_wswan",),
    "wonderswan-color": ("mednafen_wswan",),
    "zxs": ("fuse",),
}

#: RomM's `_EJS_NIGHTLY_CORES_MAP`, verbatim.
#:
#: **Deliberately not merged into the table above.** RomM merges these
#: only when `EJS_NETPLAY_ENABLED` is on, and treating them as
#: unconditionally playable is the mistake this separation exists to
#: prevent: it would tell an operator a Nintendo 3DS import is fine on a
#: server where clicking play does nothing. Most rows here are extra cores
#: for a platform the base map already covers; three are platforms that
#: exist *only* here.
EJS_NIGHTLY_CORES: dict[str, tuple[str, ...]] = {
    "3ds": ("azahar",),
    "gamegear": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "genesis": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "intellivision": ("freeintv",),
    "master-system-girl": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "master-system-super-compact": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "mega-pc": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "new-nintendo-3ds": ("azahar",),
    "new-style-super-nes-model-sns-101": ("snes9x", "bsnes"),
    "sega-game-box-9": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "sega-mark-iii": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "sega-master-system-ii": ("genesis_plus_gx", "genesis_plus_gx_wide", "smsplus"),
    "sega-mega-drive-2-slash-genesis": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "sega-mega-jet": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "sega-nomad": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "segacd": ("genesis_plus_gx", "genesis_plus_gx_wide", "picodrive"),
    "sfam": ("snes9x", "bsnes"),
    "sms": ("genesis_plus_gx", "genesis_plus_gx_wide"),
    "snes": ("snes9x", "bsnes"),
    "super-famicom-jr-model-shvc-101": ("snes9x", "bsnes"),
    "super-famicom-shvc-001": ("snes9x", "bsnes"),
    "super-nintendo-original-european-version": ("snes9x", "bsnes"),
    "tera-drive": ("genesis_plus_gx", "genesis_plus_gx_wide"),
}

#: Playable on any RomM. The name is the promise: no configuration
#: required, no flag to turn on.
PLAYABLE: frozenset[str] = frozenset(EJS_CORES)

#: Playable *only* where netplay is enabled, because that is the switch
#: that pulls the nightly cores in. Deliberately excludes every slug the
#: base map already covers -- those are playable either way, and listing
#: them here would make "needs netplay" mean two different things.
NETPLAY_ONLY: frozenset[str] = frozenset(EJS_NIGHTLY_CORES) - PLAYABLE

#: Three verdicts, and they are not a spectrum. `catalogue-only` is not a
#: worse `playable`; it is a different, legitimate use of a library.
PLAYS = "playable"
NEEDS_NETPLAY = "netplay-only"
CATALOGUE_ONLY = "catalogue-only"


@dataclass(frozen=True)
class Verdict:
    """What happens when somebody clicks play on a ROM filed here."""

    platform: str
    verdict: str
    #: The cores that would run it. Empty for `catalogue-only`.
    cores: tuple[str, ...]

    @property
    def plays(self) -> bool:
        """True only for the unconditional case.

        `netplay-only` answers False on purpose: the question this
        property is asked is "can I promise this will play", and the
        honest answer on a server whose configuration is unknown is no.
        """
        return self.verdict == PLAYS

    def __str__(self) -> str:
        if self.verdict == PLAYS:
            return f"{self.platform!r} plays ({', '.join(self.cores)})"
        if self.verdict == NEEDS_NETPLAY:
            return (
                f"{self.platform!r} plays only where netplay is enabled "
                f"({', '.join(self.cores)} ship in RomM's nightly cores)"
            )
        return f"{self.platform!r} has no emulator core -- catalogue only"


def _normalise(platform: str) -> str:
    """RomM lowercases the slug before its own lookup; so does this."""
    return platform.strip().lower() if isinstance(platform, str) else ""


def cores_for(platform: str, *, netplay: bool = False) -> tuple[str, ...]:
    """The cores that would run `platform`, mirroring `getSupportedEJSCores`.

    `netplay` mirrors RomM's `EJS_NETPLAY_ENABLED`: with it set, the
    nightly map is merged over the base one -- *merged*, so a nightly row
    replaces rather than extends the base row for the same slug, which is
    what RomM's object spread does.
    """
    slug = _normalise(platform)
    if netplay and slug in EJS_NIGHTLY_CORES:
        return EJS_NIGHTLY_CORES[slug]
    return EJS_CORES.get(slug, ())


def verdict_for(platform: str) -> Verdict:
    """Whether a ROM filed under `platform` can be played, and by what.

    Asked without reference to any particular server's netplay setting,
    because the caller is usually deciding what to *tell* an operator
    rather than what to do -- and "plays only if you turned netplay on" is
    the more useful sentence than a yes or a no that depends on a config
    value the Hub cannot read.
    """
    slug = _normalise(platform)
    if slug in EJS_CORES:
        return Verdict(slug, PLAYS, EJS_CORES[slug])
    if slug in EJS_NIGHTLY_CORES:
        return Verdict(slug, NEEDS_NETPLAY, EJS_NIGHTLY_CORES[slug])
    return Verdict(slug, CATALOGUE_ONLY, ())


def is_playable(platform: str, *, netplay: bool = False) -> bool:
    """True if `platform` has a core. `netplay` includes the nightly ones."""
    return bool(cores_for(platform, netplay=netplay))


def import_warning(platform: str) -> str:
    """The sentence an operator gets before a dead ROM lands, or "".

    Empty for a platform that plays -- callers can treat this as "warn if
    truthy" without a second predicate, which is what keeps the check from
    being forgotten at one of its call sites.

    It names the platform, says what will happen, and says both of the
    things a reader might reasonably want to do about it: nothing, because
    they wanted the catalogue entry, or stop being told, because they
    always want the catalogue entry.
    """
    result = verdict_for(platform)
    if result.verdict == PLAYS:
        return ""
    if result.verdict == NEEDS_NETPLAY:
        return (
            f"platform {result.platform!r} has no emulator core unless netplay "
            f"is enabled on the library server: RomM ships "
            f"{', '.join(result.cores)} in its nightly cores only, and reads "
            f"them just when EJS_NETPLAY_ENABLED is set. With it unset this "
            f"ROM will import and then do nothing when played. The import is "
            f"going ahead; pass --allow-unplayable to stop saying so."
        )
    return (
        f"platform {result.platform!r} cannot be played in the library's web "
        f"player: RomM {ROMM_VERSION} has no EmulatorJS core for it, and the "
        f"Xbox client ships the same player. This ROM will import, appear in "
        f"the library and do nothing when played. That is a fine thing to want "
        f"-- a catalogue is not only a player -- so the import is going ahead; "
        f"pass --allow-unplayable to stop saying so."
    )
