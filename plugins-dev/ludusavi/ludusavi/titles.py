"""Turning a library rom's name into a key that can only match one game.

This is the whole risk of the plugin. Ludusavi's manifest is a **PC**
dataset -- 52,886 titles keyed by the name a storefront uses -- and a ROM
library is full of names that look like those and mean something else.
`Sonic the Hedgehog` is in the manifest; it is the 2006 PC release, and
its save path is not where a Mega Drive dump keeps anything. A wrong save
path is worse than no save path, because nobody checks a field that is
already filled in.

So the matching here is deliberately narrow, and every widening was
considered and rejected. Four rules do the work.

**Exact equality on a normalised key. Never a prefix, never a substring,
never a distance.** `Sonic the Hedgehog` must not reach `Sonic the
Hedgehog 2`, and no edit-distance threshold exists that separates those
two from `Quake` and `Quake II`.

**Ambiguity is a refusal, not a tie-break.** 373 normalised keys in the
manifest are shared by two or more titles, covering 1,069 entries --
`Accounting` and `Accounting+`, `Adam's Venture Origins` and `Adam's
Venture: Origins`, `Klaus` and `-KLAUS-`. When a key resolves to more
than one game the plugin names them and writes nothing.

**Short keys are refused outright.** 235 normalised keys are shorter than
four characters: `1`, `21`, `3d`, `age`, `air`, `arc`, `aka`. A rom called
`Arc` has no business acquiring the save paths of a PC game called `ARC`,
and the shorter the key the more certain that is.

**Normalisation is unicode-aware**, using `str.isalnum` rather than an
ASCII class -- the same choice `rom_hub.types.bare_filename` makes and for
a sharper reason here: an ASCII-only rule folds every Japanese, Chinese,
Korean, Cyrillic and Greek title in the manifest to the *empty string*.
Measured: 280 titles collapse into one bucket that way, so any rom whose
name is also non-Latin would match all 280 at once. With `isalnum` those
280 keep their own keys and the bucket disappears.

What is *not* done, and is a real cost: no article shuffling beyond the
No-Intro `, The` move, no roman-numeral folding, no subtitle stripping, no
year inference. Each of those would find more games and each can pick the
wrong one. The plugin misses rather than guesses, and `--source-id` is
there for when the operator knows the answer.
"""

import re

#: Below this, a key is too generic to be evidence of anything. Measured
#: against the manifest: 235 of its normalised keys are shorter, and they
#: are `1`, `21`, `3d`, `age`, `air`, `arc`, `aka` and friends.
MIN_KEY_CHARS = 4

#: Bracketed groups in a ROM name are dump flags -- `[!]`, `[a1]`, `[h1C]`,
#: `[b]`, `[o]`, `[t]`. All of them are decoration and none is part of a
#: title, so the whole group goes.
_BRACKET_RE = re.compile(r"\[[^\]]*\]")

#: What a *parenthesised* group may contain and still be dropped. An
#: allowlist, and that is the point: `(USA)` and `(Rev A)` are decoration,
#: but `Dungeons & Dragons (Chronicles of Mystara)` is a title and
#: dropping the parenthesis there would aim it at a different game. So
#: only recognised decoration is removed and everything else stays in the
#: key -- which costs a match and cannot cause a wrong one.
_DECORATIONS = (
    # Regions and the shorthands ROM sets use for them.
    "usa|europe|japan|world|asia|australia|brazil|canada|china|france"
    "|germany|italy|korea|netherlands|russia|spain|sweden|taiwan|uk|us|eu|jp"
    "|ntsc|ntsc-u|ntsc-j|pal|unknown|international"
    # Language tags, as they appear alone or comma-joined.
    "|en|fr|de|es|it|ja|nl|pt|sv|da|no|fi|ko|zh|ru|pl|cs|hu|tr|el"
    # Revisions and versions.
    r"|rev\s*[0-9a-z]+|v[0-9][0-9a-z.]*|version\s*[0-9][0-9a-z.]*|alt|alt\s*[0-9]+"
    # Media.
    r"|dis[ck]\s*[0-9]+(?:\s*of\s*[0-9]+)?|side\s*[ab]|cd|cd-rom|floppy|dvd"
    # Release status.
    "|proto|prototype|beta|alpha|demo|sample|kiosk|promo|preview|unl"
    "|unlicensed|pirate|hack|aftermarket|virtual\\s*console"
    # Distribution, for the PC platforms this plugin actually enriches.
    "|gog|gog\\.com|steam|talkie|cd\\s*version|floppy\\s*version|enhanced"
    # A bare year.
    "|(?:19|20)[0-9]{2}"
    # GoodTools' bare "verified good" marker.
    r"|!"
)
_PAREN_RE = re.compile(
    rf"\(\s*(?:{_DECORATIONS})(?:\s*[,+]\s*(?:{_DECORATIONS}))*\s*\)",
    re.IGNORECASE,
)

#: No-Intro moves a leading article to the end of the title, before any
#: " - " subtitle. Ludusavi's titles are storefront names and keep natural
#: order, so the move is undone rather than applied.
_TRAILING_ARTICLE_RE = re.compile(r",\s*(The|A|An)\b", re.IGNORECASE)

_AMPERSAND_RE = re.compile(r"\s*&\s*")


def normalise(text: str) -> str:
    """A title reduced to lowercase alphanumeric words separated by spaces.

    `str.isalnum` rather than `[0-9a-z]`, because the ASCII rule empties
    every non-Latin title in the manifest and makes them all equal to each
    other. Casefold rather than lower, for the same class of reason.
    """
    folded = _AMPERSAND_RE.sub(" and ", text or "").casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in folded)
        .split()
    )


def strip_decorations(label: str) -> str:
    """Drop the ROM-set decoration from a name, leaving the title.

    `Prince of Persia (1990) [!].zip` -> `Prince of Persia`. Bracketed
    groups go unconditionally; parenthesised ones go only when their
    contents are recognised decoration, so a parenthesis that is part of a
    title survives and simply fails to match.
    """
    without = _PAREN_RE.sub(" ", _BRACKET_RE.sub(" ", label or ""))
    return " ".join(without.split())


def unshuffle_article(label: str) -> str:
    """`Legend of Zelda, The` -> `The Legend of Zelda`, or the input.

    Applied before any " - " subtitle, which is where No-Intro puts the
    article. Returns the label unchanged when there is nothing to move, so
    a caller can compare the two and skip a duplicate candidate.
    """
    head, separator, tail = label.partition(" - ")
    match = _TRAILING_ARTICLE_RE.search(head)
    if match is None or match.end() != len(head.rstrip()):
        return label
    moved = f"{match.group(1)} {head[: match.start()].rstrip()}"
    return moved + separator + tail if separator else moved


def drop_extension(filename: str) -> str:
    """`Fallout.exe` -> `Fallout`. Only a short, alphanumeric suffix.

    A dot in a PC game's name is common (`S.T.A.L.K.E.R.`, `F.E.A.R.`) and
    chopping at the last one would leave `S.T.A.L.K.E.R` or `F.E.A.R`, so
    the suffix has to look like a file extension before it is treated as
    one.
    """
    stem, dot, suffix = (filename or "").rpartition(".")
    if not dot or not stem:
        return filename or ""
    if 1 <= len(suffix) <= 5 and suffix.isalnum():
        return stem
    return filename


def candidates(labels) -> list[str]:
    """Normalised keys to try for one rom, best first, deduplicated.

    Each is a *re-spelling* of something the library already says about
    this rom. Nothing here adds a word, a year or a subtitle, because a
    candidate that adds words is a candidate that can match another game.
    """
    keys: list[str] = []
    for label in labels:
        if not label or not label.strip():
            continue
        for spelling in _spellings(label):
            key = normalise(spelling)
            if len(key) >= MIN_KEY_CHARS and key not in keys:
                keys.append(key)
    return keys


def _spellings(label: str) -> list[str]:
    stem = drop_extension(label)
    forms = [stem]
    stripped = strip_decorations(stem)
    if stripped and stripped != stem:
        forms.append(stripped)
    for form in list(forms):
        moved = unshuffle_article(form)
        if moved != form:
            forms.append(moved)
    return forms
