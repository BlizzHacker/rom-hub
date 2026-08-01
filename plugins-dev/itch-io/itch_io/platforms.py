"""itch.io platform label -> RomM platform slug.

itch.io does not publish a platform *code* anywhere a scraper can see. What
it publishes is the tooltip on the little icon in a game cell -- literally
`title="Download for Windows"` -- plus a `web_flag` span for browser builds.
Those human-readable strings are the source values this table keys on, with
the `Download for ` prefix stripped by `label_for`.

The set is closed and small (itch.io has five upload traits), so an exact
lookup with no fallback costs nothing and buys the same guarantee the
Archive.org plugin's table buys: **an unrecognised label is a visible
refusal, never a guess**. If itch.io adds a sixth trait, or renames one, the
importer stops and names the string it did not know -- which is a one-line
fix here -- instead of quietly filing a ROM under whichever platform
happened to sort first.

The values were checked against RomM's own platform-slug enum
(`backend/handler/metadata/base_handler.py`), because a slug RomM does not
know fails much later and much less usefully.
"""

#: RomM slug -> the itch.io *browse facet* that scopes a listing to it.
#:
#: This is the half that was missing. `--platform win` used to be applied
#: here, to cells that had already been fetched -- so a page of 36 mostly
#: Windows-less games yielded a handful and the walk moved on. itch.io
#: scopes a browse itself: `/games/free/platform-windows` is 36 Windows
#: games, every one of them. All five verified live 2026-08-01; note
#: `platform-osx` rather than `platform-mac`, which 301s.
BROWSE_FACETS: dict[str, str] = {
    "win": "platform-windows",
    "mac": "platform-osx",
    "linux": "platform-linux",
    "android": "platform-android",
    "browser": "platform-web",
}


# itch.io platform label (lowercased, `Download for ` stripped) -> RomM slug.
ITCH_PLATFORMS: dict[str, str] = {
    "windows": "win",
    "macos": "mac",
    # itch.io wrote "OS X" for years and still does on older listings.
    "os x": "mac",
    "linux": "linux",
    "android": "android",
    # The `web_flag` span, normalised by `label_for` to "web".
    "web": "browser",
}


def label_for(raw: str) -> str:
    """Normalise a raw itch.io platform string to a table key.

    `Download for Windows` and `Windows` are the same trait; so are
    `macOS` and `MACOS`. Normalising here rather than in the table keeps
    the table a plain data mapping.
    """
    if not isinstance(raw, str):
        return ""
    label = raw.strip()
    prefix = "download for "
    if label.lower().startswith(prefix):
        label = label[len(prefix) :]
    return " ".join(label.lower().split())


def facet_for(romm_slug: str) -> str | None:
    """The browse facet that scopes a listing to a RomM platform, or None.

    None means itch.io has no facet for it, which for a slug outside
    `ITCH_PLATFORMS` is the same thing as "this source has nothing there".
    """
    if not isinstance(romm_slug, str):
        return None
    return BROWSE_FACETS.get(romm_slug.strip().lower())


def platform_for(raw: str) -> str | None:
    """The RomM platform slug for an itch.io platform label, or None.

    None means "not in the table". Callers must turn that into a visible
    refusal naming the label; it never means "use a default".
    """
    return ITCH_PLATFORMS.get(label_for(raw))
