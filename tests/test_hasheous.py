"""The hasheous `metadata` capability.

**Where the fixture comes from, and what it is not.** Like the
`retroachievements` suite, `lookup_by_md5.json` is *not* a capture we made
from the live API, and for a reason worth stating rather than papering
over: `hasheous.org` publishes a `robots.txt` that disallows `ClaudeBot`
outright, so this suite's author did not crawl it.

The fixture is instead assembled from shapes the project publishes under
an open licence on GitHub:

* the response contract is `Classes.HashLookup` and its members, taken
  from the OpenAPI document checked into `sargunv/hasheous-cli`
  (`src/api/schema.d.ts`);
* the hash pair is the maintainers' own documented example, from the
  `<example>` block on `LookupPost` in
  `gaseous-project/hasheous`'s `hasheous/Controllers/V1.0/LookupController.cs`
  (`"MD5": "5d7550788a4d1b47ad81fbbbf5c615a9", "SHA1":
  "274ed5c2ea2ddc855f67d4c4e61c9d9b7eb68403"`);
* the game is the one the project's own MCP documentation uses in its
  worked example (`README-MCP.MD`: "Altered Beast", "Sega Mega Drive");
* `signature.game.system` carries the No-Intro DAT header name, because
  that is what `gaseous-signature-parser`'s `NoIntrosParser.cs` assigns
  (`gameObject.System = noIntrosObject.Name`), and the header name for
  that DAT is `Sega - Mega Drive - Genesis` — read from
  `libretro/libretro-database`, which carries the same DATs.

So: these are the maintainers' shapes, not ours, and the live path is
verified separately by running the shipped plugin (which is an ordinary
client under `User-agent: *`), not by this suite. Everything below runs
fully offline and opens no socket.
"""

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "hasheous"
sys.path.insert(0, str(PLUGIN_ROOT))

from hasheous.hashes import BadHash, offered, parse  # noqa: E402
from hasheous.metadata import (  # noqa: E402
    BASE,
    LookupFailed,
    Metadata,
    NoMatch,
)
from hasheous.platforms import PLATFORMS, NeedsMapping, key  # noqa: E402

from rom_hub.types import RomRef  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "hasheous"
LOOKUP = json.loads((FIXTURES / "lookup_by_md5.json").read_text(encoding="utf-8"))

MD5 = "5d7550788a4d1b47ad81fbbbf5c615a9"
SHA1 = "274ed5c2ea2ddc855f67d4c4e61c9d9b7eb68403"
CRC = "05cb39e5"


class FakeHasheous:
    """Answers like hasheous.org does, from the published shape."""

    def __init__(self, payload=None, status_code=200, text=None, raises=None):
        self.payload = LOOKUP if payload is None else payload
        self.status_code = status_code
        self.text = text
        self.raises = raises
        self.calls: list[str] = []

    def get(self, url, params=None):
        self.calls.append(url)
        if self.raises is not None:
            raise self.raises
        body = self.text if self.text is not None else json.dumps(self.payload)
        return HttpResponse(status_code=self.status_code, text=body)


def _provider(http=None, **config):
    http = http or FakeHasheous()
    return Metadata(PluginContext(config=dict(config), http=http)), http


def _ref(**kwargs):
    base = {
        "rom_id": 7,
        "name": "Altered Beast (USA, Europe)",
        "filename": "Altered Beast (USA, Europe).md",
        "platform": "genesis",
        "extra": {"source_id": f"md5:{MD5}"},
    }
    base.update(kwargs)
    return RomRef(**base)


# -- hashes --------------------------------------------------------------


def test_a_bare_digest_names_its_own_algorithm_by_length():
    assert parse(CRC) == ("crc", CRC)
    assert parse(MD5) == ("md5", MD5)
    assert parse(SHA1) == ("sha1", SHA1)
    assert parse("a" * 64) == ("sha256", "a" * 64)


def test_a_prefixed_digest_is_accepted_and_lowercased():
    assert parse(f"MD5:{MD5.upper()}") == ("md5", MD5)
    assert parse(f"sha-1:{SHA1.upper()}") == ("sha1", SHA1)
    assert parse(f"crc32:{CRC.upper()}") == ("crc", CRC)


def test_a_prefix_whose_length_is_wrong_is_refused_rather_than_sent():
    """A truncated digest would 404 and read as "hasheous does not know it"."""
    with pytest.raises(BadHash, match="32 hex characters"):
        parse("md5:abcdef")


def test_a_hash_that_is_not_hex_is_refused():
    with pytest.raises(BadHash, match="hexadecimal"):
        parse("md5:zzzz")
    with pytest.raises(BadHash, match="neither a hash"):
        parse("Altered Beast")


def test_offered_returns_the_strongest_hash_first():
    got = offered({"md5": MD5, "sha1": SHA1, "crc": CRC})
    assert got == [("sha1", SHA1), ("md5", MD5), ("crc", CRC)]


def test_source_id_beats_a_same_kind_hash_from_the_host():
    """The operator typed it for this rom, on purpose."""
    other = "f" * 32
    got = offered({"source_id": f"md5:{MD5}", "md5": other})
    assert got == [("md5", MD5)]


def test_a_host_supplied_field_that_is_not_a_hash_is_ignored_not_fatal():
    """It was not typed by anyone, and a good hash may be sitting next to it."""
    assert offered({"md5": "not-a-hash", "sha1": SHA1}) == [("sha1", SHA1)]


def test_a_malformed_source_id_is_fatal_because_someone_typed_it():
    with pytest.raises(BadHash):
        offered({"source_id": "md5:oops"})


# -- the lookup ----------------------------------------------------------


def test_a_md5_lookup_uses_the_get_route_for_that_algorithm():
    provider, http = _provider()
    provider.enrich(_ref())
    assert http.calls == [f"{BASE}md5/{MD5}"]


def test_the_patch_carries_the_name_and_the_id_romm_merely_stores():
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert patch.name == "Altered Beast"
    assert patch.provider_ids["hasheous_id"] == 4321


def test_ids_that_make_romm_call_a_keyed_service_are_off_by_default():
    """RomM 4.9.2 re-fetches from IGDB / RA when their id *changes*, and a
    RomM with no RetroAchievements key answers 500 rather than degrading.
    Verified live on 2026-07-29 -- see ALWAYS_SAFE_IDS."""
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert "igdb_id" not in patch.provider_ids
    assert "ra_id" not in patch.provider_ids


def test_cross_provider_ids_turns_them_on_for_an_operator_who_has_the_keys():
    provider, _ = _provider(cross_provider_ids=True)
    patch = provider.enrich(_ref())
    assert patch.provider_ids["igdb_id"] == "1234"
    assert patch.provider_ids["ra_id"] == "7195"


def test_a_notmapped_entry_is_never_written_as_a_provider_id():
    """`NotMapped` is a search hasheous has scheduled, not an answer it has.

    Its `id` is null, and an id written from it would afterwards be
    indistinguishable from one that was actually resolved.
    """
    provider, _ = _provider(cross_provider_ids=True)
    patch = provider.enrich(_ref())
    assert "tgdb_id" not in patch.provider_ids


def test_a_source_romm_has_no_field_for_is_left_out_of_provider_ids():
    """GiantBomb is mapped in the fixture. RomM's endpoint has no field."""
    provider, _ = _provider(cross_provider_ids=True)
    patch = provider.enrich(_ref())
    assert set(patch.provider_ids) == {"hasheous_id", "igdb_id", "ra_id"}


def test_a_partial_answer_never_blanks_a_curated_field():
    """The whole point of MetadataPatch: absent means "leave RomM alone"."""
    payload = {"id": 4321, "signature": LOOKUP["signature"]}
    provider, _ = _provider(FakeHasheous(payload))
    patch = provider.enrich(_ref())
    fields = patch.form_fields()
    assert fields == {"hasheous_id": "4321", "raw_hasheous_metadata": json.dumps(payload)}
    assert "name" not in fields
    assert "igdb_id" not in fields
    assert patch.artwork_url is None


def test_the_whole_answer_is_kept_as_raw_hasheous_metadata():
    provider, _ = _provider()
    patch = provider.enrich(_ref())
    assert patch.raw_metadata["raw_hasheous_metadata"] == LOOKUP


def test_raw_metadata_can_be_turned_off():
    provider, _ = _provider(raw_metadata=False)
    patch = provider.enrich(_ref())
    assert patch.raw_metadata == {}


def test_set_name_false_keeps_the_operators_own_naming():
    provider, _ = _provider(set_name=False)
    patch = provider.enrich(_ref())
    assert patch.name is None


def test_an_oversized_answer_sheds_signatures_rather_than_being_truncated():
    payload = dict(LOOKUP)
    payload["signatures"] = {"NoIntros": [{"game": {"name": "x" * 300_000}}]}
    provider, _ = _provider(FakeHasheous(payload))
    patch = provider.enrich(_ref())
    kept = patch.raw_metadata["raw_hasheous_metadata"]
    assert "signatures" not in kept
    assert kept["id"] == 4321


def test_a_rom_with_no_hash_is_refused_and_the_message_says_how_to_fix_it():
    provider, http = _provider()
    with pytest.raises(NoMatch, match="--source-id"):
        provider.enrich(_ref(extra={}))
    assert http.calls == []


def test_a_404_is_a_miss_not_a_failure():
    provider, _ = _provider(FakeHasheous(status_code=404))
    with pytest.raises(NoMatch, match="no signature"):
        provider.enrich(_ref())


def test_every_offered_hash_is_tried_before_giving_up():
    class Ladder(FakeHasheous):
        def get(self, url, params=None):
            self.calls.append(url)
            if url.endswith(SHA1):
                return HttpResponse(status_code=404, text="")
            return HttpResponse(status_code=200, text=json.dumps(LOOKUP))

    http = Ladder()
    provider, _ = _provider(http)
    patch = provider.enrich(_ref(extra={"sha1": SHA1, "md5": MD5}))
    assert patch.name == "Altered Beast"
    assert http.calls == [f"{BASE}sha1/{SHA1}", f"{BASE}md5/{MD5}"]


def test_a_service_error_is_reported_rather_than_swallowed():
    provider, _ = _provider(FakeHasheous(status_code=503))
    with pytest.raises(LookupFailed, match="503"):
        provider.enrich(_ref())


def test_html_where_json_was_expected_is_reported_as_such():
    provider, _ = _provider(FakeHasheous(text="<html>maintenance</html>"))
    with pytest.raises(LookupFailed, match="not JSON"):
        provider.enrich(_ref())


def test_a_broker_refusal_is_passed_through_named():
    provider, _ = _provider(FakeHasheous(raises=RuntimeError("blocked request")))
    with pytest.raises(LookupFailed, match="blocked request"):
        provider.enrich(_ref())


# -- CRC-32 --------------------------------------------------------------


def test_crc32_is_refused_by_default_because_32_bits_collide():
    provider, http = _provider()
    with pytest.raises(NoMatch, match="allow_crc32"):
        provider.enrich(_ref(extra={"crc": CRC}))
    assert http.calls == []


def test_crc32_is_used_when_the_operator_turns_it_on():
    provider, http = _provider(allow_crc32=True)
    patch = provider.enrich(_ref(extra={"crc": CRC}))
    assert patch.name == "Altered Beast"
    assert http.calls == [f"{BASE}crc/{CRC}"]


# -- the platform cross-check --------------------------------------------


def test_an_answer_about_another_console_is_refused():
    """What a CRC-32 collision looks like, and the reason for the check."""
    provider, _ = _provider(allow_crc32=True)
    with pytest.raises(LookupFailed, match="filed in RomM under 'gb'"):
        provider.enrich(_ref(platform="gb", extra={"crc": CRC}))


def test_the_signature_system_is_preferred_over_the_curated_platform_name():
    """`platform.name` in the fixture is 'Sega Mega Drive', which is not a
    DAT header name. The check passes anyway, because the signature says
    `Sega - Mega Drive - Genesis` and that is what the table is built from."""
    assert LOOKUP["platform"]["name"] == "Sega Mega Drive"
    assert LOOKUP["signature"]["game"]["system"] == "Sega - Mega Drive - Genesis"
    provider, _ = _provider()
    assert provider.enrich(_ref()).name == "Altered Beast"


def test_the_curated_platform_name_is_used_when_there_is_no_signature():
    payload = {k: v for k, v in LOOKUP.items() if k != "signature"}
    payload["platform"] = {"name": "Sega - Mega Drive - Genesis"}
    provider, _ = _provider(FakeHasheous(payload))
    assert provider.enrich(_ref()).name == "Altered Beast"


def test_an_unmapped_platform_raises_needs_mapping_and_names_itself():
    provider, _ = _provider()
    with pytest.raises(NeedsMapping, match="needs mapping: RomM platform 'dos'"):
        provider.enrich(_ref(platform="dos"))


def test_verify_platform_false_is_the_documented_override():
    provider, _ = _provider(verify_platform=False)
    assert provider.enrich(_ref(platform="dos")).name == "Altered Beast"


def test_punctuation_never_decides_whether_two_dats_mean_the_same_machine():
    """TOSEC writes `Nintendo Game Boy` where No-Intro writes
    `Nintendo - Game Boy`. Both are the same console and the same slug."""
    assert key("Nintendo - Game Boy") == key("Nintendo Game Boy")
    assert key("Nintendo - Game Boy") != key("Nintendo - Game Boy Color")


def test_no_two_platforms_share_a_normalised_name():
    """If they did, the cross-check would approve the wrong console."""
    seen: dict[str, str] = {}
    for slug, names in PLATFORMS.items():
        for name in names:
            normalised = key(name)
            if normalised in seen and seen[normalised] != slug:
                # `famicom`/`nes` and `sfam`/`snes` share a DAT on purpose.
                assert {slug, seen[normalised]} in (
                    {"nes", "famicom"},
                    {"snes", "sfam"},
                    {"atari8bit", "atari800"},
                )
            seen.setdefault(normalised, slug)


def test_the_table_is_keyed_by_real_romm_slugs():
    """Cross-checked against the slug set libretro-thumbnails verified
    against RomM 4.9.2's own `GET /api/platforms/supported`."""
    thumbnails = PLUGIN_ROOT.parent / "libretro-thumbnails"
    sys.path.insert(0, str(thumbnails))
    from libretro_thumbnails.systems import SYSTEMS  # noqa: PLC0415

    known = set(SYSTEMS) | {"atari-jaguar-cd"}
    assert set(PLATFORMS) <= known, sorted(set(PLATFORMS) - known)
