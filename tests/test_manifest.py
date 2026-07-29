import pytest

from romm_hub.manifest import ManifestError, parse_manifest

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


def test_secret_config_rejected_in_phase1():
    bad = GOOD + '\napi_key = { type = "secret" }\n'
    with pytest.raises(ManifestError, match="not implemented"):
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


def test_an_integer_rpp_version_is_rejected():
    """The spec says exactly the string "1"; str() coercion accepted a TOML int.

    This file drives an allowlist, so "everything unknown is rejected" has to
    include the wrong type for a known key.
    """
    text = GOOD.replace('rpp_version = "1"', "rpp_version = 1")
    with pytest.raises(ManifestError, match="rpp_version"):
        parse_manifest(text)
