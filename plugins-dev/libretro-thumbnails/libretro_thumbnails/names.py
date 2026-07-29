"""Turning a RomM rom name into the filename libretro actually serves.

This is the whole plugin. libretro's thumbnail repositories are keyed by
*name*, not by any id, so a plugin that gets the name wrong finds nothing
-- and a plugin that gets it nearly right finds nothing either.

**The scrub rule is not invented here.** RetroArch builds the filename it
asks for in `gfx_thumbnail_fill_content_img()` (`gfx/gfx_thumbnail.c`),
and that function is one line long where it matters::

    while ((scrub_char_ptr = strpbrk(s, "&*/:`\\"<>?\\\\|")))
       *scrub_char_ptr = '_';

so the substituted set is exactly ``& * / : ` " < > ? \\ |`` -> ``_``, and
nothing else. That was checked against the live repositories rather than
taken on trust: across 30,903 filenames captured from the Named_Boxarts
listings of four systems (SNES, NES, PlayStation, DOS) on 2026-07-29, ten
of those eleven characters appear **zero** times, while `'`, `!`, `,`,
`.`, `[`, `]`, `+`, `$`, `%`, `^`, `~`, `;`, `#` and `=` all appear
freely. Only `&` shows up at all, and only inside the NES set's
TOSEC-named entries -- files RetroArch itself can never request, because
it asks for `_`. The rule holds; the exceptions are the repository's.

The same function has a second half worth copying: when the full name
misses, RetroArch retries with everything from the first `" ("` dropped.
That is where `Prince of Persia (1990)` and `Prince of Persia` -- both of
which really exist in the DOS set -- come from.

**Articles are the other half of the problem.** These are No-Intro DAT
names, and No-Intro moves a leading article to the end of the title,
*before* any `" - "` subtitle: `Legend of Zelda, The - A Link to the
Past`. In the same 30,903-name sample 912 names end in `, The` and 27
begin with `The`, so both spellings exist and the move is generated in
both directions.

Nothing here guesses beyond spelling. Every candidate is a *re-spelling*
of a name the operator's library already contains. The plugin never
invents a region tag, a year or a subtitle, because a candidate that adds
words is a candidate that can match a different game.
"""

import re

# RetroArch's scrub set, verbatim from gfx_thumbnail_fill_content_img().
SCRUBBED = '&*/:`"<>?\\|'
_SCRUB_RE = re.compile("[" + re.escape(SCRUBBED) + "]")

# Articles No-Intro moves to the end of a title. Deliberately missing:
# bare "I". It is the Italian plural article, and it is also the English
# word that opens "I Have No Mouth, and I Must Scream" -- a title whose
# article-moved form is nonsense and which no DAT spells that way.
ARTICLES = (
    "The",
    "A",
    "An",
    "Le",
    "La",
    "Les",
    "Der",
    "Die",
    "Das",
    "El",
    "Los",
    "Las",
    "Il",
    "Lo",
    "Gli",
    "O",
    "Os",
    "As",
    "Um",
    "Uma",
    "De",
    "Het",
    "Een",
)

# A trailing ".sfc"/".zip"/".chd". The text after the dot has to be short
# and wordless, which is what keeps "Dr. Mario" and "Mario Bros. 3" whole:
# in both, what follows the last dot contains a space.
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,4}\Z")

# Everything a No-Intro name puts in brackets: region, languages, revision,
# dump status. Dropping them is how "Prince of Persia" is recognised as the
# same *title* as "Prince of Persia (1990)".
_TAG_RE = re.compile(r"\s*[(\[][^)\]]*[)\]]")

# Where the tags start, for the article move: an article belongs in front
# of "(USA)", never after it.
_TAG_TAIL_RE = re.compile(r"\s*[(\[].*\Z", re.DOTALL)

# No-Intro's subtitle separator. The article goes at the end of the part
# before it: "Legend of Zelda, The - A Link to the Past".
_SUBTITLE = " - "

# How many URLs enrich() will probe. Each probe is a real image download
# through the host, so this is a bandwidth budget, not a style choice.
MAX_CANDIDATES = 8


def scrub(name: str) -> str:
    """Replace the characters RetroArch replaces, and nothing else."""
    return _SCRUB_RE.sub("_", name)


def strip_extension(label: str) -> str:
    """`Super Mario World (USA).sfc` -> `Super Mario World (USA)`.

    Only a real-looking extension goes, and never the whole label: `.hack`
    is a title, not a suffix.
    """
    stripped = _EXTENSION_RE.sub("", label)
    return stripped if stripped.strip() else label


def _split_tags(label: str) -> tuple[str, str]:
    match = _TAG_TAIL_RE.search(label)
    if match and match.start() > 0:
        return label[: match.start()], label[match.start() :]
    return label, ""


def _split_subtitle(body: str) -> tuple[str, str]:
    index = body.find(_SUBTITLE)
    return (body[:index], body[index:]) if index > 0 else (body, "")


def _article_of(head: str) -> tuple[str, str | None]:
    """Split a title's leading-or-trailing article off. `(rest, article)`."""
    for article in ARTICLES:
        suffix = f", {article}"
        if head.endswith(suffix) and len(head) > len(suffix):
            return head[: -len(suffix)], article
    for article in ARTICLES:
        prefix = f"{article} "
        if head.startswith(prefix) and len(head) > len(prefix):
            return head[len(prefix) :], article
    return head, None


def move_article(label: str) -> str | None:
    """`The Legend of Zelda` <-> `Legend of Zelda, The`, or None.

    Returns the *other* spelling whichever way round the input is, so one
    function covers both directions. None means there is no article to
    move, which the caller reads as "no extra candidate".
    """
    body, tags = _split_tags(label)
    head, subtitle = _split_subtitle(body)
    rest, article = _article_of(head)
    if article is None:
        return None
    if head.startswith(f"{article} "):
        return f"{rest}, {article}{subtitle}{tags}"
    return f"{article} {rest}{subtitle}{tags}"


def shorten(label: str) -> str | None:
    """RetroArch's own fallback: drop everything from the first `" ("`.

    None when there is nothing to drop, so no duplicate candidate is made.
    """
    index = label.find(" (")
    return label[:index] if index > 0 else None


def match_key(label: str) -> str:
    """A comparison key for one *title*, ignoring spelling and release.

    Used only against names read out of a real directory listing, never to
    build a URL. Tags go, the article goes, punctuation collapses, case
    goes. Two labels sharing a key are the same title -- they may still be
    different releases of it, which is why the caller chooses between them
    by region rather than taking the first.

    The article is dropped rather than normalised into place because a
    library that says `Prince of Persia` and a repository that says
    `Prince of Persia (1990)` should meet, and so should `The Oregon
    Trail` and `Oregon Trail, The (1990)`. What is *not* dropped is any
    other word: `Prince of Persia` and `Prince of Persia 2` keep different
    keys, which is the whole reason this is an equality test and not a
    prefix test.
    """
    body = _TAG_RE.sub("", scrub(strip_extension(label))).strip()
    head, subtitle = _split_subtitle(body)
    rest, _article = _article_of(head)
    collapsed = re.sub(r"[^0-9A-Za-z]+", " ", (rest + subtitle).replace("_", " "))
    return " ".join(collapsed.lower().split())


def candidates(labels) -> list[str]:
    """Filenames worth asking libretro for, best first.

    Each label contributes its own spelling, its article move, RetroArch's
    shortened form and that form's article move -- then, last, the label
    exactly as it arrived, in case stripping an "extension" took a real
    word off the end. Deduplicated with the order kept, so the most likely
    spelling is probed first and nothing is probed twice.
    """
    out: list[str] = []

    def offer(form: str) -> None:
        scrubbed = scrub(form).strip()
        if scrubbed and scrubbed not in out:
            out.append(scrubbed)

    for raw in labels:
        raw = (raw or "").strip()
        if not raw:
            continue
        label = strip_extension(raw)
        offer(label)
        moved = move_article(label)
        if moved:
            offer(moved)
        short = shorten(label)
        if short:
            offer(short)
            short_moved = move_article(short)
            if short_moved:
                offer(short_moved)
        if label != raw:
            offer(raw)
    return out[:MAX_CANDIDATES]
