"""The RetroAchievements `metadata` capability.

**Where the fixture comes from, and what it is not.** Unlike the other
plugin suites in this repo, `get_game_list_console_1.json` is *not* a
capture made by us from the live API. Two reasons, both worth stating
rather than papering over:

1. the endpoint requires a web API key and none was available;
2. `retroachievements.org` and `api-docs.retroachievements.org` both
   publish a `robots.txt` that disallows `ClaudeBot` outright, so this
   suite's author did not crawl them.

The fixture is instead RetroAchievements' own published response for
`API_GetGameList.php`, taken from the two places the project publishes it
under an open licence on GitHub: the sample response in
`RetroAchievements/api-docs` (`docs/v1/get-game-list.md`) and the mock in
`RetroAchievements/api-js` (`src/console/getGameList.test.ts`). Both
entries are kept exactly as published -- including the wart that makes
them worth having, which is that `ID` comes back as a JSON **string** on
this endpoint. RA's own client corrects for it
(`serializeProperties(..., { shouldCastToNumbers: ["ID", "ConsoleID"] })`),
and so must this plugin, because `ra_id` posted as `"4247"` is not the
same value as `4247`.

So: these are the maintainers' shapes, not ours, and the live path is
**unverified**. Everything below runs fully offline.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "retroachievements"
sys.path.insert(0, str(PLUGIN_ROOT))

from retroachievements.consoles import NeedsMapping  # noqa: E402
from retroachievements.metadata import (  # noqa: E402
    API,
    GAME_API,
    IMAGE_BASE,
    ApiFailed,
    Metadata,
    NoMatch,
    NotConfigured,
)

from rom_hub.types import RomRef  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "retroachievements"
GAME_LIST = json.loads((FIXTURES / "get_game_list_console_1.json").read_text())

# `API_GetGame.php`'s shape, on the same terms as the game-list fixture:
# RetroAchievements' own published mock, from `api-js`'s
# `src/game/getGame.test.ts`, kept exactly as published. Two warts come
# with it and both are the point of having it -- every value is a JSON
# *string*, including `ID` and `Flags`, and `Publisher` carries a trailing
# space ("Activision ") that a summary must not reproduce.
GAME_DETAILS = json.loads((FIXTURES / "get_game_4247.json").read_text())

# The two hashes RA publishes for "Elemental Master" (console 1, id 4247).
ELEMENTAL_MASTER = "32e1a15161ef1f070b023738353bde51"
GLEY_LANCER = "8bd4a97783cda077c342173df0a9b51e"

KEY = "abc123DEFghi456"


class FakeRA:
    """Answers like retroachievements.org/API does, from the real shape.

    Routed by URL since the plugin makes two calls: the game list, and
    `API_GetGame.php` for the four catalogue fields the list does not
    carry. `details` is what the second one answers; `None` makes it a
    404, which is the case where the enrich has to keep going on what the
    first call gave.
    """

    def __init__(
        self,
        payload=None,
        status_code=200,
        text=None,
        raises=None,
        details=GAME_DETAILS,
        details_status=200,
    ):
        self.payload = GAME_LIST if payload is None else payload
        self.status_code = status_code
        self.text = text
        self.raises = raises
        self.details = details
        self.details_status = details_status
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        if self.raises is not None:
            raise self.raises
        if url == GAME_API:
            if self.details is None:
                return HttpResponse(status_code=404, text="")
            return HttpResponse(
                status_code=self.details_status, text=json.dumps(self.details)
            )
        body = self.text if self.text is not None else json.dumps(self.payload)
        return HttpResponse(status_code=self.status_code, text=body)

    @property
    def list_calls(self) -> list[tuple[str, dict]]:
        """Only the game-list requests, for the tests that count them."""
        return [call for call in self.calls if call[0] == API]


def _provider(http=None, config=None):
    http = http or FakeRA()
    merged = {"api_key": KEY}
    merged.update(config or {})
    return Metadata(PluginContext(config=merged, http=http)), http


def _ref(**kwargs):
    base = {
        "rom_id": 7,
        "name": "Elemental Master (USA)",
        "filename": "Elemental Master (USA).md",
        "platform": "genesis",
        "extra": {"source_id": ELEMENTAL_MASTER},
    }
    base.update(kwargs)
    return RomRef(**base)


# -- the happy path -----------------------------------------------------


def test_a_hash_hit_sets_ra_id():
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert patch.provider_ids == {"ra_id": 4247}


def test_the_id_is_an_int_even_though_ra_sends_a_string():
    """`"4247"` and `4247` are different values in a column RomM parses as
    an integer, and this endpoint really does send the string."""
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert isinstance(patch.provider_ids["ra_id"], int)
    assert patch.form_fields()["ra_id"] == "4247"


def test_an_entry_whose_id_is_already_a_number_works_too():
    provider, _ = _provider()
    patch = provider.enrich(_ref(extra={"source_id": GLEY_LANCER}))
    assert patch.provider_ids == {"ra_id": 3684}


def test_the_title_is_taken_from_the_matched_game():
    provider, _ = _provider()
    assert provider.enrich(_ref()).name == "Elemental Master"


def test_the_hash_comparison_ignores_case():
    provider, _ = _provider()
    patch = provider.enrich(_ref(extra={"source_id": ELEMENTAL_MASTER.upper()}))
    assert patch.provider_ids == {"ra_id": 4247}


def test_exactly_one_game_list_request_is_made():
    """RA asks callers to cache this endpoint rather than hammer it.

    The game list is fetched once per enrich and never twice, whatever
    else the plugin goes on to do -- it is the big response (every game on
    a console, with every hash) and the one RA's documentation is about.
    """
    provider, http = _provider()
    provider.enrich(_ref())
    assert len(http.list_calls) == 1
    url, params = http.list_calls[0]
    assert url == API
    assert params["i"] == "1" and params["h"] == "1"


def test_details_off_makes_no_second_request():
    """The switch for an operator enriching a whole library at once."""
    provider, http = _provider(config={"details": False})
    provider.enrich(_ref())
    assert [url for url, _ in http.calls] == [API]


def test_the_key_is_sent_as_y_and_the_username_only_when_set():
    provider, http = _provider()
    provider.enrich(_ref())
    assert http.calls[0][1]["y"] == KEY
    assert "z" not in http.calls[0][1]

    provider, http = _provider(config={"username": "someone"})
    provider.enrich(_ref())
    assert http.calls[0][1]["z"] == "someone"


# -- what is deliberately not written -----------------------------------


def test_no_raw_metadata_is_written_because_rpp_v1_has_no_field_for_it():
    """RPP v1's eight `raw_*_metadata` fields belong to IGDB, ScreenScraper,
    LaunchBox, Hasheous, Flashpoint, HLTB, MobyGames and manuals. None is
    RetroAchievements, and putting RA's payload in one of them would be a
    lie in the database about where the data came from."""
    from rom_hub.types import RAW_METADATA_FIELDS

    assert not any("_ra_" in field for field in RAW_METADATA_FIELDS)
    provider, _ = _provider()
    assert provider.enrich(_ref()).raw_metadata == {}


def test_the_patch_touches_nothing_it_did_not_learn():
    """Absent means leave alone.

    A game RA carries no box art for gets no artwork field at all, rather
    than an empty one that would blank a cover another plugin set.
    """
    provider, _ = _provider(FakeRA(details={"Genre": "Racing"}))
    patch = provider.enrich(_ref())
    assert patch.artwork_url is None and patch.artwork_base64 is None
    assert set(patch.form_fields()) == {"ra_id", "name", "summary"}


def test_set_name_false_writes_the_id_and_what_it_learned():
    provider, _ = _provider(config={"set_name": False})
    patch = provider.enrich(_ref())
    assert patch.name is None
    assert patch.form_fields()["ra_id"] == "4247"
    assert "name" not in patch.form_fields()


# -- the summary and the cover -------------------------------------------


def test_the_summary_carries_the_counts_and_then_the_catalogue_facts():
    provider, _ = _provider()
    summary = provider.enrich(_ref()).summary
    assert summary == (
        "44 achievements worth 500 points on RetroAchievements. "
        "Developed by David Crane, published by Activision. "
        "Released 1980. Genre: Racing. Console: Atari 2600."
    )


def test_a_trailing_space_in_ras_own_data_is_not_reproduced():
    """RA publishes `"Publisher": "Activision "`, space and all."""
    provider, _ = _provider()
    assert "Activision." in provider.enrich(_ref()).summary


def test_a_leaderboard_count_of_zero_is_left_out():
    """Elemental Master has 44 achievements and 0 leaderboards."""
    provider, _ = _provider()
    assert "leaderboard" not in provider.enrich(_ref()).summary


def test_leaderboards_are_named_when_there_are_some():
    provider, _ = _provider()
    summary = provider.enrich(_ref(extra={"source_id": GLEY_LANCER})).summary
    assert "33 leaderboards." in summary


def test_without_the_second_call_the_counts_still_arrive():
    provider, _ = _provider(config={"details": False})
    assert provider.enrich(_ref()).summary == (
        "44 achievements worth 500 points on RetroAchievements. "
        "Console: Mega Drive."
    )


def test_the_box_art_url_is_built_on_the_host_already_allowlisted():
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert patch.artwork_url == IMAGE_BASE + "/Images/026365.png"
    assert patch.artwork_filename == "cover.png"


def test_ras_no_image_placeholder_is_not_written_over_a_real_cover():
    provider, _ = _provider(FakeRA(details={**GAME_DETAILS, "ImageBoxArt": "/Images/000002.png"}))
    assert provider.enrich(_ref()).artwork_url is None


def test_a_failed_second_call_does_not_cost_the_id_that_already_matched():
    """The hash matched. Losing a correct ra_id to a rate limit on an
    optional call would be absurd."""
    provider, _ = _provider(FakeRA(details=None))
    patch = provider.enrich(_ref())
    assert patch.provider_ids == {"ra_id": 4247}
    assert patch.summary == (
        "44 achievements worth 500 points on RetroAchievements. "
        "Console: Mega Drive."
    )
    assert patch.artwork_url is None


def test_summary_false_leaves_romms_description_alone():
    provider, _ = _provider(config={"summary": False})
    patch = provider.enrich(_ref())
    assert patch.summary is None
    assert "summary" not in patch.form_fields()


def test_artwork_false_proposes_no_cover():
    provider, _ = _provider(config={"artwork": False})
    assert provider.enrich(_ref()).artwork_url is None


# -- the no-key path ----------------------------------------------------


def test_no_api_key_is_an_actionable_refusal_before_any_request():
    provider, http = _provider(config={"api_key": ""})
    with pytest.raises(NotConfigured) as exc:
        provider.enrich(_ref())
    message = str(exc.value)
    assert "api_key" in message
    assert "Settings -> Keys" in message, "it must say where to get one"
    assert "plugin secret set" in message, "and the command that stores one"
    assert "plain text" not in message, (
        "the key is no longer stored in plain text; a refusal still saying so "
        "would be the warning outliving the problem"
    )
    assert http.calls == [], "an unconfigured plugin costs no request"


def test_a_whitespace_only_key_counts_as_no_key():
    provider, _ = _provider(config={"api_key": "   "})
    with pytest.raises(NotConfigured, match="none is configured"):
        provider.enrich(_ref())


def test_the_secret_config_type_really_is_accepted_by_this_host():
    """Was `..._really_is_rejected_by_this_host`, inverted when `secret`
    landed. It pinned the README's plain-text warning; it now pins the
    README's replacement claim, which is that the key is not in the plain
    config at all."""
    from rom_hub.manifest import parse_manifest

    manifest = (
        '[plugin]\nslug="x"\nname="X"\nversion="1"\nrpp_version="1"\n'
        '[capabilities]\nmetadata="x:Y"\n'
        '[config]\napi_key = { type = "secret" }\n'
    )
    assert parse_manifest(manifest).config_schema["api_key"]["type"] == "secret"


def test_this_plugins_own_manifest_declares_the_key_as_a_secret():
    """The conversion, checked against the file that ships."""
    from pathlib import Path

    from rom_hub.manifest import load_manifest
    from rom_hub.secrets import secret_fields

    manifest = load_manifest(
        Path(__file__).resolve().parents[1]
        / "plugins-dev"
        / "retroachievements"
        / "manifest.toml"
    )
    assert secret_fields(manifest) == ["api_key"]


def test_a_key_rejected_by_ra_says_so_rather_than_leaking_a_401():
    provider, _ = _provider(FakeRA(status_code=401))
    with pytest.raises(NotConfigured, match="rejected the configured `api_key`"):
        provider.enrich(_ref())


# -- the hash -----------------------------------------------------------


@pytest.mark.parametrize("key", ["ra_hash", "md5", "md5_hash", "hash", "source_id"])
def test_a_hash_is_accepted_from_any_of_the_places_a_host_might_put_it(key):
    provider, _ = _provider()
    patch = provider.enrich(_ref(extra={key: ELEMENTAL_MASTER}))
    assert patch.provider_ids == {"ra_id": 4247}


def test_no_hash_at_all_says_where_to_find_one():
    provider, http = _provider()
    with pytest.raises(NotConfigured) as exc:
        provider.enrich(_ref(extra={}))
    message = str(exc.value)
    assert "md5_hash" in message and "--source-id" in message
    assert http.calls == []


def test_the_no_hash_message_warns_when_the_file_md5_will_not_do():
    """For the NES, rcheevos skips the 16-byte iNES header before hashing.
    An operator handed RomM's md5 for an NES rom would be chasing the wrong
    problem when it missed."""
    provider, _ = _provider()
    with pytest.raises(NotConfigured, match="is NOT the file's md5"):
        provider.enrich(_ref(platform="nes", extra={}))
    with pytest.raises(NotConfigured, match="RomM's own md5 is the right value"):
        provider.enrich(_ref(platform="genesis", extra={}))


@pytest.mark.parametrize(
    "bad", ["rubik_202308", "32e1a15161ef1f070b023738353bde5", "nothexnothexnothex" * 2]
)
def test_something_that_is_not_a_hash_is_refused_before_any_request(bad):
    provider, http = _provider()
    with pytest.raises(NotConfigured, match="not a RetroAchievements hash"):
        provider.enrich(_ref(extra={"source_id": bad}))
    assert http.calls == []


# -- the miss -----------------------------------------------------------


def test_a_hash_miss_refuses_and_never_falls_back_to_the_title():
    """The rom is named exactly like a game in the list. That must not
    matter: a wrong ra_id is an id an achievements client believes later."""
    provider, _ = _provider()
    with pytest.raises(NoMatch) as exc:
        provider.enrich(
            _ref(
                name="Elemental Master",
                extra={"source_id": "0" * 32},
            )
        )
    message = str(exc.value)
    assert "0" * 32 in message
    assert "will not fall back to matching" in message


def test_the_miss_message_distinguishes_the_two_reasons_a_miss_happens():
    provider, _ = _provider()
    with pytest.raises(NoMatch, match="hashes the whole file"):
        provider.enrich(_ref(platform="genesis", extra={"source_id": "0" * 32}))

    # Console 12 (PlayStation) hashes an executable inside the disc image.
    with pytest.raises(NoMatch, match="does NOT hash the whole file"):
        provider.enrich(_ref(platform="psx", extra={"source_id": "0" * 32}))


def test_the_miss_message_mentions_the_narrowing_the_operator_chose():
    provider, _ = _provider()
    with pytest.raises(NoMatch, match="only_with_achievements"):
        provider.enrich(_ref(extra={"source_id": "0" * 32}))

    provider, http = _provider(config={"only_with_achievements": False})
    with pytest.raises(NoMatch, match="searched all games"):
        provider.enrich(_ref(extra={"source_id": "0" * 32}))
    assert http.calls[0][1]["f"] == "0"


# -- platforms ----------------------------------------------------------


def test_an_unmapped_platform_raises_needs_mapping_and_names_itself():
    provider, http = _provider()
    with pytest.raises(NeedsMapping, match="'switch' needs mapping"):
        provider.enrich(_ref(platform="switch"))
    assert http.calls == []


def test_a_rom_with_no_platform_is_refused():
    provider, _ = _provider()
    with pytest.raises(NeedsMapping, match="no platform"):
        provider.enrich(_ref(platform=None))


def test_the_console_id_reaches_the_request():
    provider, http = _provider()
    with pytest.raises(NoMatch):
        provider.enrich(_ref(platform="snes", extra={"source_id": "0" * 32}))
    assert http.calls[0][1]["i"] == "3"


# -- the API misbehaving ------------------------------------------------


def test_a_non_json_answer_is_reported_as_such():
    """Rate limiting and maintenance both arrive as 200 + HTML."""
    provider, _ = _provider(FakeRA(text="<html>slow down</html>"))
    with pytest.raises(ApiFailed, match="not JSON"):
        provider.enrich(_ref())


def test_an_error_object_is_reported_with_ras_own_words():
    provider, _ = _provider(FakeRA(payload={"Error": "Invalid API Key"}))
    with pytest.raises(ApiFailed, match="Invalid API Key"):
        provider.enrich(_ref())


def test_a_server_error_is_reported_with_its_status():
    provider, _ = _provider(FakeRA(status_code=503))
    with pytest.raises(ApiFailed, match="503"):
        provider.enrich(_ref())


def test_a_response_the_host_refused_for_size_suggests_the_narrower_list():
    """The Hub caps one ctx.http response at 4 MiB, and a big console's
    game list with every hash is exactly what reaches that."""
    provider, _ = _provider(
        FakeRA(raises=RuntimeError("response from ... exceeded the 4194304-byte limit"))
    )
    with pytest.raises(ApiFailed, match="only_with_achievements"):
        provider.enrich(_ref())


def test_a_matched_entry_with_a_nonsense_id_is_refused_not_posted():
    entry = dict(GAME_LIST[0])
    entry["ID"] = "not-an-id"
    provider, _ = _provider(FakeRA(payload=[entry]))
    with pytest.raises(ApiFailed, match="which is not an id"):
        provider.enrich(_ref())


def test_junk_entries_in_the_list_are_stepped_over_not_crashed_on():
    payload = ["nonsense", {"Hashes": "not a list"}, {}, *GAME_LIST]
    provider, _ = _provider(FakeRA(payload=payload))
    assert provider.enrich(_ref()).provider_ids == {"ra_id": 4247}


# -- the allowlist ------------------------------------------------------


def test_the_only_url_it_uses_is_inside_its_declared_allowlist():
    from rom_hub.netpolicy import check_url

    provider, http = _provider()
    provider.enrich(_ref())
    check_url(http.calls[0][0], ["retroachievements.org"])
