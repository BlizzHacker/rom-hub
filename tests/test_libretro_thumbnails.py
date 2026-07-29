"""The libretro-thumbnails `metadata` capability.

`FakeLibretro` is not a mock of what we hoped the service does -- it is
driven by **directory listings captured from the live service** on
2026-07-29 and checked in under `tests/fixtures/libretro/`:

* `hartung_named_boxarts.html` is one complete, unedited listing;
* `snes_named_boxarts_subset.html` and `dos_named_boxarts_subset.html`
  are rows copied verbatim from the SNES and DOS listings, with the live
  server's own header and footer. Only the *choice* of rows is ours.

So a URL answers 200 in these tests exactly when it answers 200 in
reality. No test opens a socket: the plugin's only network path is
`ctx.http`, and the artwork URL it returns is fetched by the host, not by
the plugin (see tests/test_broker_enrich.py).
"""

import html
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "libretro-thumbnails"
sys.path.insert(0, str(PLUGIN_ROOT))

from libretro_thumbnails.metadata import BASE, Metadata, NoThumbnail  # noqa: E402
from libretro_thumbnails.systems import NeedsMapping  # noqa: E402

from rom_hub.types import RomRef  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "libretro"

# The three real listings, keyed by the system directory they came from.
LISTINGS = {
    "Hartung - Game Master": "hartung_named_boxarts.html",
    "Nintendo - Super Nintendo Entertainment System": (
        "snes_named_boxarts_subset.html"
    ),
    "DOS": "dos_named_boxarts_subset.html",
}

_HREF = re.compile(r'href="([^"]+?\.png)"', re.IGNORECASE)


def _names(fixture: str) -> list[str]:
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    return [unquote(html.unescape(m.group(1))) for m in _HREF.finditer(text)]


class FakeLibretro:
    """Answers like thumbnails.libretro.com does, from the real listings."""

    def __init__(self, kind="Named_Boxarts", status_override=None):
        self.kind = kind
        self.status_override = status_override
        self.calls: list[str] = []
        self.files = {
            system: set(_names(fixture)) for system, fixture in LISTINGS.items()
        }

    def get(self, url, params=None):
        self.calls.append(url)
        if self.status_override is not None:
            return HttpResponse(status_code=self.status_override, text="")
        assert url.startswith(BASE), f"off-allowlist URL: {url}"
        rest = unquote(url[len(BASE) :])
        system, _, tail = rest.partition("/")
        kind, _, filename = tail.partition("/")
        if kind != self.kind or system not in self.files:
            return HttpResponse(status_code=404, text="")
        if filename == "":
            # A directory listing: hand back the captured HTML verbatim.
            return HttpResponse(
                status_code=200,
                text=(FIXTURES / LISTINGS[system]).read_text(encoding="utf-8"),
            )
        if filename in self.files[system]:
            # The real server sends PNG bytes; the plugin only reads the
            # status code, and ctx.http would hand it a lossy decode anyway.
            return HttpResponse(status_code=200, text="�PNG")
        return HttpResponse(status_code=404, text="")


def _provider(http=None, config=None):
    http = http or FakeLibretro()
    return Metadata(PluginContext(config=config or {}, http=http)), http


def _ref(**kwargs):
    base = {
        "rom_id": 1,
        "name": "Super Mario World (USA)",
        "filename": "Super Mario World (USA).sfc",
        "platform": "snes",
        "extra": {},
    }
    base.update(kwargs)
    return RomRef(**base)


SNES = BASE + "Nintendo%20-%20Super%20Nintendo%20Entertainment%20System/Named_Boxarts/"
DOS = BASE + "DOS/Named_Boxarts/"


# -- the happy path -----------------------------------------------------


def test_an_exactly_named_rom_resolves_on_the_first_probe():
    provider, http = _provider()
    patch = provider.enrich(_ref())
    assert patch.artwork_url == SNES + "Super%20Mario%20World%20%28USA%29.png"
    assert len(http.calls) == 1


def test_the_plugin_names_a_url_and_never_fetches_the_artwork_itself():
    """The host fetches it, after checking it against the allowlist."""
    from rom_hub.netpolicy import check_url

    provider, _ = _provider()
    patch = provider.enrich(_ref())
    check_url(patch.artwork_url, ["thumbnails.libretro.com"])


def test_an_ampersand_title_is_found_by_substituting_underscore():
    """`Pocky & Rocky (USA)` is served as `Pocky _ Rocky (USA).png`."""
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(name="Pocky & Rocky (USA)", filename="Pocky & Rocky (USA).sfc")
    )
    assert patch.artwork_url == SNES + "Pocky%20_%20Rocky%20%28USA%29.png"


def test_a_leading_article_is_moved_to_no_intros_position():
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(
            name="The Legend of Zelda - A Link to the Past (USA)",
            filename="The Legend of Zelda - A Link to the Past (USA).sfc",
        )
    )
    assert patch.artwork_url == (
        SNES + "Legend%20of%20Zelda%2C%20The%20-%20A%20Link%20to%20the%20Past"
        "%20%28USA%29.png"
    )


def test_both_hard_rules_at_once():
    """`The Ren & Stimpy Show - Veediots!` needs the ampersand substituted
    *and* the article moved in front of the subtitle."""
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(name="The Ren & Stimpy Show - Veediots! (USA)", filename="")
    )
    assert patch.artwork_url.endswith(
        "Ren%20_%20Stimpy%20Show%2C%20The%20-%20Veediots%21%20%28USA%29.png"
    )


def test_retroarchs_own_shortening_finds_the_untagged_file():
    """DOS carries both `Prince of Persia (1990).png` and
    `Prince of Persia.png`; a library naming a year libretro spells
    differently still lands."""
    provider, http = _provider()
    patch = provider.enrich(
        _ref(name="Prince of Persia (1989)", filename="", platform="dos")
    )
    assert patch.artwork_url == DOS + "Prince%20of%20Persia.png"
    assert len(http.calls) == 2, "the tagged spelling is tried first"


def test_the_filename_is_used_when_the_name_is_useless():
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(name="rom_00417", filename="Chrono Trigger (USA).sfc")
    )
    assert patch.artwork_url == SNES + "Chrono%20Trigger%20%28USA%29.png"


# -- artwork only -------------------------------------------------------


def test_the_patch_carries_artwork_and_nothing_else():
    """A No-Intro filename is not a curated title. Overwriting RomM's name
    with one is exactly the faithful-write damage MetadataPatch's
    absent-means-leave-alone rule exists to prevent."""
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert patch.name is None
    assert patch.form_fields() == {}
    assert patch.artwork_base64 is None
    assert not patch.is_empty()


def test_libretro_id_is_never_set_because_there_is_no_such_id():
    """These repositories are keyed by name. The only value on offer is the
    filename, and RomM's provider-id validator rejects it -- which is the
    argument, not a workaround for it."""
    from pydantic import ValidationError

    provider, _ = _provider()
    assert provider.enrich(_ref()).provider_ids == {}
    with pytest.raises(ValidationError):
        from rom_hub.types import MetadataPatch

        MetadataPatch(provider_ids={"libretro_id": "Super Mario World (USA)"})


# -- the index fallback -------------------------------------------------


def test_a_library_without_the_year_tag_is_matched_through_the_listing():
    """No spelling of `Prince of Persia 2 - The Shadow and The Flame` that
    the ladder can build carries `(1993)`, so probing must miss and the
    directory listing must find it."""
    provider, http = _provider()
    patch = provider.enrich(
        _ref(
            name="Prince of Persia 2 - The Shadow and The Flame",
            filename="",
            platform="dos",
        )
    )
    assert patch.artwork_url == (
        DOS + "Prince%20of%20Persia%202%20-%20The%20Shadow%20and%20The%20Flame"
        "%20%281993%29.png"
    )
    assert http.calls[-1] == DOS, "the listing is the last resort, not the first"


def test_the_listing_match_is_an_equality_not_a_prefix():
    """`Prince of Persia 3` does not exist; matching it to
    `Prince of Persia 2 ...` would be worse than finding nothing."""
    provider, _ = _provider()
    with pytest.raises(NoThumbnail, match="no entry matches this title"):
        provider.enrich(
            _ref(name="Prince of Persia 3", filename="", platform="dos")
        )


def test_the_listing_prefers_the_release_the_library_already_names():
    """`Bubble Boy` exists for Europe and for Germany in the real Hartung
    listing. A library that says Germany gets Germany."""
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(name="Bubble Boy (Germany)", filename="", platform="hartung")
    )
    assert patch.artwork_url.endswith("Bubble%20Boy%20%28Germany%29.png")


def test_the_listing_falls_back_to_region_order_when_the_library_is_silent():
    """The real listing has `Go Bang (Germany)` and `Go Bang! (Europe)`.
    They are one title -- the key ignores the `!` -- and with no region in
    the library's own name, Europe outranks Germany."""
    provider, _ = _provider()
    patch = provider.enrich(_ref(name="Go Bang", filename="", platform="hartung"))
    assert patch.artwork_url.endswith("Go%20Bang%21%20%28Europe%29.png")


def test_the_fallback_can_be_switched_off():
    """It costs a whole directory listing, which for a big system is
    megabytes. An operator running a batch may not want that per rom."""
    provider, http = _provider(config={"index_fallback": False})
    with pytest.raises(NoThumbnail) as exc:
        provider.enrich(
            _ref(
                name="Prince of Persia 2 - The Shadow and The Flame",
                filename="",
                platform="dos",
            )
        )
    assert "no entry matches" not in str(exc.value)
    assert not any(call.endswith("Named_Boxarts/") for call in http.calls)


# -- refusals -----------------------------------------------------------


def test_an_unmapped_platform_raises_needs_mapping_and_names_itself():
    provider, http = _provider()
    with pytest.raises(NeedsMapping, match="'switch' needs mapping"):
        provider.enrich(_ref(platform="switch"))
    assert http.calls == [], "a refusal must not cost a request either"


def test_a_rom_with_no_platform_is_refused():
    provider, _ = _provider()
    with pytest.raises(NeedsMapping, match="no platform"):
        provider.enrich(_ref(platform=None))


def test_a_miss_lists_every_spelling_it_tried():
    provider, _ = _provider()
    with pytest.raises(NoThumbnail) as exc:
        provider.enrich(_ref(name="Not A Real Game & Friends", filename=""))
    message = str(exc.value)
    assert "Not A Real Game _ Friends" in message
    assert "--source-id" in message
    assert "Nintendo - Super Nintendo Entertainment System" in message


def test_a_rom_with_neither_name_nor_filename_is_refused():
    provider, http = _provider()
    with pytest.raises(NoThumbnail, match="keyed by name alone"):
        provider.enrich(_ref(name="", filename=""))
    assert http.calls == []


def test_a_server_error_stops_the_probing_instead_of_hammering():
    provider, http = _provider(FakeLibretro(status_override=503))
    with pytest.raises(NoThumbnail, match="503"):
        provider.enrich(_ref())
    assert len(http.calls) == 1


def test_an_unknown_art_kind_is_refused_before_any_request():
    provider, http = _provider(config={"art_kind": "screenshot"})
    with pytest.raises(NoThumbnail, match="not one of"):
        provider.enrich(_ref())
    assert http.calls == []


# -- the operator's override -------------------------------------------


def test_source_id_names_the_file_exactly():
    provider, http = _provider()
    patch = provider.enrich(
        _ref(name="whatever", extra={"source_id": "Star Fox (USA)"})
    )
    assert patch.artwork_url == SNES + "Star%20Fox%20%28USA%29.png"
    assert len(http.calls) == 1


def test_source_id_may_carry_the_png_suffix():
    provider, _ = _provider()
    patch = provider.enrich(
        _ref(name="whatever", extra={"source_id": "Star Fox (USA).png"})
    )
    assert patch.artwork_url.endswith("Star%20Fox%20%28USA%29.png")


def test_a_wrong_source_id_is_a_refusal_not_a_404_from_the_host():
    """The host would fetch whatever it is given. Probing turns the
    operator's typo into a message about the operator's typo."""
    provider, http = _provider()
    with pytest.raises(NoThumbnail, match="Star Fux"):
        provider.enrich(_ref(extra={"source_id": "Star Fux (USA)"}))
    assert not any(call.endswith("Named_Boxarts/") for call in http.calls), (
        "an explicit name must not be second-guessed against the listing"
    )


# -- the other three sets ----------------------------------------------


def test_art_kind_selects_a_different_libretro_set():
    provider, _ = _provider(
        FakeLibretro(kind="Named_Titles"), config={"art_kind": "title"}
    )
    patch = provider.enrich(_ref())
    assert "/Named_Titles/" in patch.artwork_url


@pytest.mark.parametrize("kind", ["boxart", "title", "snap", "logo"])
def test_every_kind_stays_inside_the_allowlist(kind):
    from rom_hub.netpolicy import check_url

    directory = {
        "boxart": "Named_Boxarts",
        "title": "Named_Titles",
        "snap": "Named_Snaps",
        "logo": "Named_Logos",
    }[kind]
    provider, _ = _provider(FakeLibretro(kind=directory), config={"art_kind": kind})
    check_url(provider.enrich(_ref()).artwork_url, ["thumbnails.libretro.com"])
