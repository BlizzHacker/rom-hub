import pytest

from rom_hub.manifest import ManifestError, parse_manifest

GOOD = """
[plugin]
slug = "archive-org"
name = "Archive.org"
version = "1.0.0"
rpp_version = "1"
license = "MIT"

[capabilities]
search = "archive_org.search:Search"

[permissions]
network = ["archive.org", "*.archive.org"]
romm_api = []

[config]
collections = { type = "list[str]", default = ["softwarelibrary"] }
"""


def test_parses_good_manifest():
    m = parse_manifest(GOOD)
    assert m.slug == "archive-org"
    assert m.rpp_version == "1"
    assert m.capabilities == {"search": "archive_org.search:Search"}
    assert m.network == ["archive.org", "*.archive.org"]
    assert m.config_schema["collections"]["default"] == ["softwarelibrary"]


def test_wrong_rpp_version_rejected():
    with pytest.raises(ManifestError, match="rpp_version"):
        parse_manifest(GOOD.replace('rpp_version = "1"', 'rpp_version = "2"'))


def test_unknown_capability_rejected():
    bad = GOOD.replace("search =", "teleport =")
    with pytest.raises(ManifestError, match="teleport"):
        parse_manifest(bad)


def test_reserved_capability_rejected():
    bad = GOOD.replace("search =", "peer =")
    with pytest.raises(ManifestError, match="reserved"):
        parse_manifest(bad)


def test_secret_config_type_is_supported():
    """Was `test_secret_config_rejected_in_phase1`, inverted deliberately.

    RPP v1 always specified `secret`; Phase 1 rejected it rather than
    half-implementing storage. The store landed (`rom_hub.secrets`), so the
    refusal is gone and this is the test that says so. Kept as the *same*
    assertion turned around rather than deleted, so the history shows the
    contract moving rather than a check disappearing.
    """
    m = parse_manifest(GOOD + '\napi_key = { type = "secret" }\n')
    assert m.config_schema["api_key"]["type"] == "secret"


def test_nothing_is_reserved_but_unimplemented_any_more():
    from rom_hub.manifest import RESERVED_CONFIG_TYPES, SUPPORTED_CONFIG_TYPES

    assert "secret" in SUPPORTED_CONFIG_TYPES
    assert RESERVED_CONFIG_TYPES == frozenset()


def test_a_secret_may_not_carry_a_default():
    """A manifest is a public file in a git repo.

    `default = "sk-live-..."` in one is a credential published on purpose,
    so the parser refuses it rather than trusting every plugin author to
    notice.
    """
    bad = GOOD + '\napi_key = { type = "secret", default = "hunter2" }\n'
    with pytest.raises(ManifestError, match="must not declare a default"):
        parse_manifest(bad)


def test_an_unknown_config_type_is_still_rejected():
    bad = GOOD + '\napi_key = { type = "encrypted" }\n'
    with pytest.raises(ManifestError, match="unknown type"):
        parse_manifest(bad)


def test_bad_entrypoint_rejected():
    bad = GOOD.replace("archive_org.search:Search", "archive_org.search")
    with pytest.raises(ManifestError, match="module:Class"):
        parse_manifest(bad)


def test_bad_slug_rejected():
    with pytest.raises(ManifestError, match="slug"):
        parse_manifest(GOOD.replace('slug = "archive-org"', 'slug = "Archive Org!"'))


def test_missing_capabilities_rejected():
    bad = GOOD.replace('search = "archive_org.search:Search"', "")
    with pytest.raises(ManifestError, match="at least one capability"):
        parse_manifest(bad)


def test_importer_entrypoint_parses():
    with_importer = GOOD.replace(
        'search = "archive_org.search:Search"',
        'search = "archive_org.search:Search"\n'
        'importer = "archive_org.importer:Importer"',
    )
    m = parse_manifest(with_importer)
    assert m.capabilities["importer"] == "archive_org.importer:Importer"


def test_an_integer_rpp_version_is_rejected():
    """The spec says exactly the string "1"; str() coercion accepted a TOML int.

    This file drives an allowlist, so "everything unknown is rejected" has to
    include the wrong type for a known key.
    """
    text = GOOD.replace('rpp_version = "1"', "rpp_version = 1")
    with pytest.raises(ManifestError, match="rpp_version"):
        parse_manifest(text)
