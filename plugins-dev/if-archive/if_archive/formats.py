"""Story-file extension -> the interpreter that runs it -> a RomM platform.

**This table is the only thing standing between an import and a game filed
under the wrong runtime**, so it is an exact-match lookup on the extension
and there is no fallback. An extension that is not spelled out below is not
a story file as far as this plugin is concerned; an extension that is here
but has no RomM platform raises "needs mapping" **naming the format**, and
the import stops. A visible gap is cheap to close. A game filed under the
wrong system is not, because nothing about the library afterwards says
anything went wrong.

**Z-machine and Glulx are not the same platform, and RomM already knows
that.** RomM 4.9.2 answers `GET /api/platforms/supported` with 458 slugs,
and four of them are interpreter runtimes rather than machines::

    z-machine   glulx   tads   hugo

That is the whole mapped set, checked against a live RomM 4.9.2 rather
than assumed. RomM issue #2140 asks for a single new `interactive-fiction`
platform; it turns out RomM has four finer-grained ones already, so this
plugin uses them and collapses nothing. A Z-code `.z5` and a Glulx `.ulx`
need different interpreters, and filing both under one slug would hand the
operator a library where half the entries will not start.

**The extension decides, not the directory.** The archive's own filing is
a good signal but it is not the format: `The Cruel Count's Castle.gblorb`
sits in `games/zcode/` and is a *Glulx* game, and `zenspeak.blb` sits
there too and is not a game at all. A `.gblorb` is a Glulx blorb wherever
it is filed, so the extension wins and the directory is not consulted.

**`.blb` is refused on purpose.** A bare Blorb is a container: the chunk
inside says `ZCOD` or `GLUL`, and nothing outside the file does. The
archive files them under both runtimes -- 10 in `glulx/`, 1 in `zcode/` --
and reading the directory as the answer would be exactly the guess this
module exists to avoid. Worse, two of the eleven are not games: `zcode/
zenspeak.blb` is "the sound and music resources for Zen Speaks!" and
`glulx/glkebook.blb` is an eBook reader. So `.blb` is *known* -- it shows
up in search, with no platform -- and refuses at import naming what it is
and why. Eleven files, and none of them can be misfiled.

The formats that have no RomM platform at all are listed for the same
reason: an operator who points `directories` at `adrift` should get
"ADRIFT has no RomM platform" rather than silence.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    """One story-file extension, and where a game in it belongs."""

    #: Lowercase, no leading dot.
    extension: str
    #: The interpreter family, in the words the IF community uses.
    runtime: str
    #: The RomM platform slug, or None when RomM has no platform for this
    #: runtime. None is never "use a default" -- callers must turn it into
    #: a refusal that names the format.
    platform: str | None
    #: Why there is no platform, for the refusal message. Empty when there
    #: is one.
    unmapped_reason: str = ""


_FORMATS: tuple[Format, ...] = (
    # -- Z-machine -------------------------------------------------------
    # Infocom's virtual machine and its Inform descendants. Versions 1-8
    # are all the same runtime; `.zblorb`/`.zlb` are the same story wrapped
    # in a Blorb with its graphics and sound. RomM issue #2140 names z3,
    # z4, z5, z8, zlb and zblorb; z1, z2, z6 and z7 are here because the
    # machine has them and the archive serves `.z6`.
    *(Format(f"z{n}", "Z-machine", "z-machine") for n in range(1, 9)),
    Format("zblorb", "Z-machine", "z-machine"),
    Format("zlb", "Z-machine", "z-machine"),
    # -- Glulx -----------------------------------------------------------
    # Andrew Plotkin's 32-bit successor to the Z-machine. A different
    # virtual machine with a different interpreter; `.gblorb`/`.glb` are
    # the Blorb-wrapped form.
    Format("ulx", "Glulx", "glulx"),
    Format("gblorb", "Glulx", "glulx"),
    Format("glb", "Glulx", "glulx"),
    # -- TADS ------------------------------------------------------------
    # `.gam` is TADS 2 and `.t3` is TADS 3. Two virtual machines, but RomM
    # has one `tads` slug and the TADS interpreters ship together, so this
    # is RomM's granularity rather than a collapse of our own.
    Format("gam", "TADS 2", "tads"),
    Format("t3", "TADS 3", "tads"),
    # -- Hugo ------------------------------------------------------------
    Format("hex", "Hugo", "hugo"),
    # -- known, and deliberately unmapped --------------------------------
    Format(
        "blb",
        "Blorb",
        None,
        "a bare .blb is a Blorb container and the chunk inside it decides "
        "whether it holds a Z-machine or a Glulx story; the file extension "
        "does not say, and the directory it is filed under is not the same "
        "claim. Two of the eleven .blb files in the archive's game "
        "directories are not games at all (zcode/zenspeak.blb is a sound "
        "and music resource file, glulx/glkebook.blb is an eBook reader). "
        "Fetch it by hand if you know which runtime it is for",
    ),
    Format(
        "taf",
        "ADRIFT",
        None,
        "ADRIFT games run under the ADRIFT runner, and RomM 4.9.2 has no "
        "platform slug for it -- its supported list carries z-machine, "
        "glulx, tads and hugo and nothing else for interactive fiction. "
        "RomM issue #2140 asks for ADRIFT 4 support; until RomM has a "
        "platform, an import here would have to file it under one of the "
        "four it does have, which would be wrong",
    ),
    Format(
        "a3c",
        "Alan 3",
        None,
        "Alan games run under the Alan interpreter and RomM 4.9.2 has no "
        "platform slug for it",
    ),
    Format(
        "acd",
        "Alan 2",
        None,
        "Alan games run under the Alan interpreter and RomM 4.9.2 has no "
        "platform slug for it",
    ),
    Format(
        "aas",
        "AAS",
        None,
        "AAS games run under the AAS interpreter and RomM 4.9.2 has no "
        "platform slug for it",
    ),
    Format(
        "quest",
        "Quest",
        None,
        "Quest games run under Quest/QuestKit and RomM 4.9.2 has no "
        "platform slug for it",
    ),
    Format(
        "asl",
        "Quest",
        None,
        "Quest games run under Quest/QuestKit and RomM 4.9.2 has no "
        "platform slug for it",
    ),
    Format(
        "acx",
        "Archetype",
        None,
        "Archetype games run under the Archetype interpreter and RomM "
        "4.9.2 has no platform slug for it",
    ),
    Format(
        "tag",
        "TAG",
        None,
        "TAG games run under the TAG interpreter and RomM 4.9.2 has no "
        "platform slug for it",
    ),
)

#: extension (lowercase, no dot) -> Format.
FORMATS: dict[str, Format] = {f.extension: f for f in _FORMATS}

#: Every RomM platform slug this plugin will ever name, for `--platform`.
PLATFORMS: frozenset[str] = frozenset(
    f.platform for f in _FORMATS if f.platform is not None
)


def extension_of(filename: str) -> str:
    """The lowercase extension of a bare filename, without its dot.

    Only the last one: `Enhanced.tar.Z` is `z`, which is not a story format
    and is therefore not offered. Splitting on the *first* dot would make
    it `tar`, which is not a story format either -- but `905notes.txt`
    would become `txt` under both rules and `A.Mind.Forever.z5` would
    become `mind` under the first, which is why it is the last one.
    """
    _, _, extension = filename.rpartition(".")
    return extension.lower() if extension and extension != filename else ""


def format_for(filename: str) -> Format | None:
    """The `Format` for a filename, or None when it is not a story file.

    None means "this plugin has nothing to say about this file" -- an
    index carries `.txt` notes, `.pdf` maps, `.zip` bundles and source
    code alongside the games. It never means "guess".
    """
    if not isinstance(filename, str):
        return None
    return FORMATS.get(extension_of(filename))
