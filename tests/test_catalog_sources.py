"""More than one plugin directory, and what a directory is still not allowed to be.

**Nothing here touches the network.** Every remote catalog is served by an
`httpx.MockTransport` over a checked-in fixture in `tests/fixtures/catalogs/`,
the same way the importer and data-asset suites work. A test that reached a
real host would be a test that fails on a train, and -- worse for this
particular feature -- a test that could not tell "the source is down" from
"the code is wrong", which is the exact distinction the degradation rules
here exist to make.

The property the whole suite is arranged around is
`test_a_remote_catalog_cannot_widen_an_installed_plugins_reach`: a directory
says where a plugin lives and never what it may do.
"""

import json
import time
from pathlib import Path

import httpx
import pytest

from rom_hub.catalog import CatalogError
from rom_hub.catalog_sources import (
    BUNDLED_NAME,
    CatalogSource,
    CatalogSourceError,
    add_source,
    bundled_source,
    cache_dir,
    check_location,
    load_all,
    load_source,
    read_sources,
    remove_source,
    sources_path,
    ttl_seconds,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalogs"
THIRD_PARTY = FIXTURES / "third_party.json"
ONE_PLUGIN = FIXTURES / "one_plugin.json"

CATALOG_URL = "https://catalog.example.test/plugins.json"


def catalog_text(name: str = "third_party.json") -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def serving(body: str, *, status: int = 200, calls: list | None = None):
    """A transport that answers exactly one URL and records every request."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(status, text=body)

    return httpx.MockTransport(handler)


def refusing(exc: Exception | None = None, *, calls: list | None = None):
    """A transport that fails the way an unreachable host fails."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        raise exc or httpx.ConnectError("name or service not known")

    return httpx.MockTransport(handler)


def remote(name: str = "remote", url: str = CATALOG_URL) -> CatalogSource:
    return CatalogSource(name=name, location=url)


# -- the source list -----------------------------------------------------


def test_a_fresh_install_has_exactly_the_bundled_directory(tmp_path):
    """The default must be indistinguishable from the way it was before.

    Multiple sources is a capability, not a change of behaviour: somebody
    who never runs `catalog add` gets the same directory, in the same
    order, from the same file.
    """
    sources = read_sources(tmp_path)
    assert [s.name for s in sources] == [BUNDLED_NAME]
    assert sources[0].bundled
    assert Path(sources[0].location).name == "plugins.json"
    assert not sources_path(tmp_path).exists()


def test_added_sources_come_after_the_bundled_one_in_the_order_given(tmp_path):
    add_source(tmp_path, "first", str(THIRD_PARTY))
    add_source(tmp_path, "second", str(ONE_PLUGIN))
    assert [s.name for s in read_sources(tmp_path)] == [
        BUNDLED_NAME,
        "first",
        "second",
    ]


def test_a_source_survives_the_process_that_added_it(tmp_path):
    add_source(tmp_path, "neighbour", str(THIRD_PARTY))
    assert json.loads(sources_path(tmp_path).read_text(encoding="utf-8")) == {
        "version": 1,
        "sources": [{"name": "neighbour", "location": str(THIRD_PARTY)}],
    }
    assert [s.name for s in read_sources(tmp_path)] == [BUNDLED_NAME, "neighbour"]


def test_removing_a_source_leaves_the_others(tmp_path):
    add_source(tmp_path, "a", str(THIRD_PARTY))
    add_source(tmp_path, "b", str(ONE_PLUGIN))
    gone = remove_source(tmp_path, "a")
    assert gone.name == "a"
    assert [s.name for s in read_sources(tmp_path)] == [BUNDLED_NAME, "b"]


def test_the_bundled_directory_cannot_be_removed(tmp_path):
    """Not a technicality: it is what makes the collision rule mean anything.

    First-source-wins protects the plugins this project ships only for as
    long as this project's directory is first. Letting it be removed would
    turn "unshadowable" into "unshadowable until somebody types one
    command".
    """
    with pytest.raises(CatalogSourceError, match="ships with rom-hub"):
        remove_source(tmp_path, BUNDLED_NAME)


def test_a_source_may_not_take_the_bundled_name(tmp_path):
    with pytest.raises(CatalogSourceError, match="one answer"):
        add_source(tmp_path, BUNDLED_NAME, str(THIRD_PARTY))


def test_two_sources_may_not_share_a_name(tmp_path):
    add_source(tmp_path, "dup", str(THIRD_PARTY))
    with pytest.raises(CatalogSourceError, match="already configured"):
        add_source(tmp_path, "dup", str(ONE_PLUGIN))


def test_removing_something_that_was_never_added_says_what_is_there(tmp_path):
    add_source(tmp_path, "real", str(THIRD_PARTY))
    with pytest.raises(CatalogSourceError, match="real"):
        remove_source(tmp_path, "imaginary")


def test_a_source_list_with_an_unknown_key_is_refused(tmp_path):
    """Default-deny for the operator's own file too, not only fetched ones.

    This file is written by the Hub, so an unknown key in it means
    something else wrote it -- a newer build, or something that should not
    have.
    """
    sources_path(tmp_path).write_text(
        json.dumps(
            {
                "version": 1,
                "sources": [
                    {
                        "name": "x",
                        "location": str(THIRD_PARTY),
                        "trusted": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogSourceError, match="unknown key"):
        read_sources(tmp_path)


# -- what may be a source ------------------------------------------------


def test_http_is_refused_and_the_reason_is_the_install_urls(tmp_path):
    """A catalog is a list of places to fetch code from. Over http anyone
    on the path rewrites that list, and every URL the reader then trusts."""
    with pytest.raises(CatalogSourceError, match="rewritten in flight"):
        check_location("http://catalog.example.test/plugins.json")


@pytest.mark.parametrize(
    "location",
    [
        "ftp://catalog.example.test/plugins.json",
        "file:///etc/passwd",
        "ext::sh -c whoami",
    ],
)
def test_only_https_and_local_paths_are_sources(location):
    with pytest.raises(CatalogSourceError):
        check_location(location)


def test_a_url_that_says_one_host_and_contacts_another_is_refused():
    """`https://github.com@evil.example/c.json` contacts evil.example.

    urlsplit strips the userinfo, so fetching it would be *safe* and
    completely misleading: the allowlist would be the real host and the
    operator would have read a different one. An operator who has to be
    told their catalog is not where they thought has already been fooled.
    """
    with pytest.raises(CatalogSourceError, match="says one thing"):
        check_location("https://github.com@evil.example.test/plugins.json")


def test_a_path_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(CatalogSourceError, match="neither an https URL"):
        check_location(str(tmp_path / "no-such-file.json"))


def test_an_https_url_with_a_plain_hostname_is_accepted():
    assert check_location(CATALOG_URL) == CATALOG_URL


def test_a_local_file_is_accepted_as_a_source():
    assert check_location(str(THIRD_PARTY)) == str(THIRD_PARTY)


@pytest.mark.parametrize("name", ["", "-leading", "has space", "quote'd", "a/b"])
def test_a_source_name_must_be_safe_to_print_and_to_use_as_a_filename(
    tmp_path, name
):
    with pytest.raises(CatalogSourceError):
        add_source(tmp_path, name, str(THIRD_PARTY))


# -- merging, and the collision rule -------------------------------------


def test_two_directories_merge_and_every_entry_knows_where_it_came_from(tmp_path):
    add_source(tmp_path, "neighbour", str(THIRD_PARTY))
    merged = load_all(tmp_path)

    origins = {e.slug: e.source.name for e in merged.entries}
    assert origins["archive-org"] == BUNDLED_NAME
    assert origins["neighbourhood-roms"] == "neighbour"
    assert merged.complete
    assert merged.coverage() == "2 of 2 catalog(s) reachable"


def test_the_first_source_wins_a_collision_and_the_loss_is_recorded(tmp_path):
    """First-source-wins, so a stranger cannot repoint a shipped slug.

    The fixture claims `archive-org` and points it at a different
    repository with a wider network ask. Last-wins would make adding any
    directory a one-command supply-chain swap of a slug people type from
    memory.
    """
    add_source(tmp_path, "impostor", str(THIRD_PARTY))
    merged = load_all(tmp_path)

    winner = merged.find("archive-org")
    assert winner.source.name == BUNDLED_NAME
    assert "not-really-archive-org" not in winner.entry.install
    assert "evil.example.test" not in winner.entry.network

    assert [c.slug for c in merged.collisions] == ["archive-org"]
    collision = merged.collisions[0]
    assert collision.winner.name == BUNDLED_NAME
    assert [s.name for s in collision.losers] == ["impostor"]
    # Visible, not merely resolved: a user must be able to see that a
    # collision happened rather than silently getting the winner.
    assert "impostor" in collision.summary()
    assert "ignored" in collision.summary()


def test_a_shadowed_entry_is_dropped_rather_than_listed_twice(tmp_path):
    add_source(tmp_path, "impostor", str(THIRD_PARTY))
    merged = load_all(tmp_path)
    slugs = [e.slug for e in merged.entries]
    assert slugs.count("archive-org") == 1


def test_precedence_follows_the_order_the_operator_wrote(tmp_path):
    """Two third-party sources both claiming a slug: the earlier one wins."""
    add_source(tmp_path, "earlier", str(THIRD_PARTY))
    add_source(tmp_path, "later", str(ONE_PLUGIN))
    merged = load_all(tmp_path)
    assert merged.find("neighbourhood-roms").source.name == "earlier"
    collision = next(
        c for c in merged.collisions if c.slug == "neighbourhood-roms"
    )
    assert collision.winner.name == "earlier"
    assert [s.name for s in collision.losers] == ["later"]


# -- honest degradation --------------------------------------------------


def test_an_unreachable_source_is_reported_rather_than_silently_dropped(
    tmp_path, monkeypatch
):
    """The rule `search` already follows, for a worse failure mode.

    A source that fails costs its own plugins. A listing that does not say
    so is indistinguishable from a complete one -- and a plugin missing
    from `browse` reads as a plugin that does not exist.
    """
    add_source(tmp_path, "down", CATALOG_URL)
    merged = load_all(tmp_path, transport=refusing())

    assert not merged.complete
    assert merged.coverage() == "1 of 2 catalog(s) reachable"
    assert [s.source.name for s in merged.failures] == ["down"]
    assert "name or service not known" in merged.failures[0].error
    # The reachable source still answers. Degradation, not collapse.
    assert merged.find("archive-org") is not None


def test_a_local_source_that_is_deleted_degrades_the_same_way(tmp_path):
    catalog = tmp_path / "local.json"
    catalog.write_text(catalog_text(), encoding="utf-8")
    add_source(tmp_path, "local", str(catalog))
    catalog.unlink()

    merged = load_all(tmp_path)
    assert not merged.complete
    assert "cannot read" in merged.failures[0].error


def test_a_source_serving_a_malformed_catalog_fails_that_source_alone(tmp_path):
    add_source(tmp_path, "broken", CATALOG_URL)
    merged = load_all(
        tmp_path, transport=serving(catalog_text("unsupported_version.json"))
    )
    assert not merged.complete
    assert "catalog_version" in merged.failures[0].error
    assert merged.find("archive-org") is not None


def test_the_failure_message_names_the_source_that_failed(tmp_path):
    add_source(tmp_path, "neighbour", CATALOG_URL)
    merged = load_all(
        tmp_path, transport=serving(catalog_text("unknown_entry_field.json"))
    )
    assert "neighbour:" in merged.failures[0].error


# -- fetching, caching, staleness ----------------------------------------


def test_a_fetched_catalog_is_cached_and_not_refetched_within_the_ttl(tmp_path):
    calls: list[str] = []
    source = remote()
    load_source(source, tmp_path, transport=serving(catalog_text(), calls=calls))
    assert calls == [CATALOG_URL]

    entries, status = load_source(
        source, tmp_path, transport=refusing(calls=calls)
    )
    # No second request at all: the refusing transport was never reached.
    assert calls == [CATALOG_URL]
    assert status.ok and status.from_cache and not status.stale
    assert {e.slug for e in entries} == {"neighbourhood-roms", "archive-org"}


def test_an_expired_cache_is_refetched(tmp_path):
    calls: list[str] = []
    source = remote()
    load_source(source, tmp_path, transport=serving(catalog_text(), calls=calls))
    load_source(
        source,
        tmp_path,
        transport=serving(catalog_text(), calls=calls),
        now=time.time() + 60 * 60 * 24,
    )
    assert len(calls) == 2


def test_a_failed_refresh_serves_the_stale_copy_and_says_how_old_it_is(tmp_path):
    """Degrade to a known-old answer, never to a silent one.

    Serving nothing would hide plugins the operator has; serving the old
    copy without saying so would be worse, because "this directory is a
    day old" is exactly the fact that explains why the plugin somebody
    published this morning is missing.
    """
    source = remote()
    load_source(source, tmp_path, transport=serving(catalog_text()))

    day_later = time.time() + 60 * 60 * 24
    entries, status = load_source(
        source, tmp_path, transport=refusing(), now=day_later
    )
    assert status.ok
    assert status.stale
    assert status.from_cache
    assert status.stale_seconds >= 60 * 60 * 24
    assert len(entries) == 2
    assert "could not refresh" in status.summary()
    assert "24" in status.summary()


def test_coverage_names_a_stale_source_rather_than_reading_as_up_to_date(tmp_path):
    """"2 of 2 reachable" alone would read as "this is current".

    A stale source did answer, so it counts as reachable -- but a day-old
    copy is exactly the fact that explains a missing plugin, so it is said
    on the same line rather than left to a later one.
    """
    add_source(tmp_path, "yesterday", CATALOG_URL)
    load_all(tmp_path, transport=serving(catalog_text()))

    merged = load_all(
        tmp_path, transport=refusing(), now=time.time() + 60 * 60 * 24
    )
    assert merged.complete
    assert "2 of 2 catalog(s) reachable" in merged.coverage()
    assert "stale cached copy" in merged.coverage()
    assert "yesterday" in merged.coverage()


def test_a_source_that_never_succeeded_has_no_stale_copy_to_serve(tmp_path):
    entries, status = load_source(remote(), tmp_path, transport=refusing())
    assert entries == []
    assert not status.ok
    assert not status.stale


def test_the_cache_is_keyed_on_the_location_not_the_name(tmp_path):
    """Renaming a source must not serve it another source's cached bytes."""
    load_source(remote("alpha"), tmp_path, transport=serving(catalog_text()))
    cached = sorted(p.name for p in cache_dir(tmp_path).glob("*.json"))
    load_source(remote("beta"), tmp_path, transport=refusing())
    assert sorted(p.name for p in cache_dir(tmp_path).glob("*.json")) == cached

    # A different URL under the same name gets its own cache slot, so a
    # repointed source cannot serve the old target's copy.
    other = remote("alpha", "https://other.example.test/plugins.json")
    _, status = load_source(other, tmp_path, transport=refusing())
    assert not status.ok


def test_ttl_zero_always_refetches(tmp_path):
    calls: list[str] = []
    source = remote()
    load_source(source, tmp_path, transport=serving(catalog_text(), calls=calls), ttl=0)
    load_source(source, tmp_path, transport=serving(catalog_text(), calls=calls), ttl=0)
    assert len(calls) == 2


def test_the_ttl_is_read_from_the_environment_at_call_time(monkeypatch):
    monkeypatch.delenv("ROM_HUB_CATALOG_TTL", raising=False)
    assert ttl_seconds() == 6 * 60 * 60
    monkeypatch.setenv("ROM_HUB_CATALOG_TTL", "90")
    assert ttl_seconds() == 90
    # A typo reads as "use the default", not as "never cache" or "forever".
    monkeypatch.setenv("ROM_HUB_CATALOG_TTL", "soon")
    assert ttl_seconds() == 6 * 60 * 60


def test_a_local_source_is_never_cached(tmp_path):
    load_source(
        CatalogSource(name="local", location=str(THIRD_PARTY)), tmp_path
    )
    assert not cache_dir(tmp_path).exists()


# -- a fetched catalog is untrusted input --------------------------------


def test_a_redirect_off_the_host_the_operator_named_ends_the_fetch(tmp_path):
    """A 302 to somewhere else is not the source that was added.

    The same rule the import path and the data-asset path enforce, reused
    rather than re-implemented: httpx follows nothing, every hop is
    re-checked against the one host in the allowlist.
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "catalog.example.test":
            return httpx.Response(
                302, headers={"Location": "https://evil.example.test/plugins.json"}
            )
        return httpx.Response(200, text=catalog_text())

    entries, status = load_source(
        remote(), tmp_path, transport=httpx.MockTransport(handler)
    )
    assert entries == []
    assert not status.ok
    assert "evil.example.test" in status.error
    # The request to the undeclared host was never made.
    assert seen == [CATALOG_URL]


def test_an_oversized_response_is_refused_before_it_is_parsed(tmp_path):
    from rom_hub.catalog_sources import MAX_CATALOG_BYTES

    body = "x" * 32
    huge = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text=body,
            headers={"Content-Length": str(MAX_CATALOG_BYTES + 1)},
        )
    )
    entries, status = load_source(remote(), tmp_path, transport=huge)
    assert entries == []
    assert not status.ok
    assert "limit" in status.error


def test_a_fetched_catalog_with_an_unknown_field_is_refused_not_ignored(tmp_path):
    """Default-deny, the same posture as `manifest.py`.

    A key this build does not read is a claim nobody will check -- the
    exact shape of a field that means nothing here and something on the
    host that grows a meaning for it later.
    """
    entries, status = load_source(
        remote(), tmp_path, transport=serving(catalog_text("unknown_entry_field.json"))
    )
    assert entries == []
    assert "unknown field" in status.error
    assert "trust_level" in status.error


def test_a_fetched_catalog_with_an_unknown_top_level_key_is_refused(tmp_path):
    entries, status = load_source(
        remote(),
        tmp_path,
        transport=serving(catalog_text("unknown_top_level_key.json")),
    )
    assert entries == []
    assert "unknown top-level key" in status.error
    assert "grants_network" in status.error


def test_a_fetched_catalog_is_held_to_the_same_https_rule_for_its_entries(tmp_path):
    doc = json.loads(catalog_text("one_plugin.json"))
    doc["plugins"][0]["install"] = "http://git.example.test/somebody/x"
    entries, status = load_source(
        remote(), tmp_path, transport=serving(json.dumps(doc))
    )
    assert entries == []
    assert "https" in status.error


def test_a_fetched_catalog_must_still_pin_its_download_to_a_tag(tmp_path):
    """A moving branch is how a directory silently ships new code later."""
    doc = json.loads(catalog_text("one_plugin.json"))
    doc["plugins"][0]["download"] = (
        "https://git.example.test/somebody/neighbourhood-roms/archive/main.tar.gz"
    )
    entries, status = load_source(
        remote(), tmp_path, transport=serving(json.dumps(doc))
    )
    assert entries == []
    assert "pinned" in status.error


def test_a_response_that_is_not_json_fails_that_source_only(tmp_path):
    entries, status = load_source(
        remote(), tmp_path, transport=serving("<html>404</html>")
    )
    assert entries == []
    assert not status.ok


# -- the property this all rests on --------------------------------------


def test_a_remote_catalog_cannot_widen_an_installed_plugins_reach(tmp_path):
    """The extension of `test_catalog_cannot_widen_permissions` to a fetched
    directory, which is where it stops being theoretical.

    The bundled catalog is written by whoever ships this repository. A
    remote one is written by a stranger, so if its `network` list could
    reach the broker, adding a source would hand that stranger every
    plugin on the host. It cannot: the entry here asks for
    `evil.example.test`, the plugin's manifest asks for one host, and what
    the broker enforces is the manifest.
    """
    from rom_hub.broker.host import PluginProcess
    from rom_hub.manifest import parse_manifest
    from rom_hub.netpolicy import check_url, PolicyViolation

    manifest = parse_manifest(
        '[plugin]\nslug="archive-org"\nname="X"\nversion="1"\nrpp_version="1"\n'
        '[capabilities]\nsearch="x.s:S"\n'
        '[permissions]\nnetwork=["archive.org"]\nromm_api=[]\n'
    )

    entries, status = load_source(
        remote(), tmp_path, transport=serving(catalog_text())
    )
    assert status.ok
    claimed = next(e for e in entries if e.slug == "archive-org")
    # The directory really is asking for more than the manifest grants --
    # otherwise this test would pass for the wrong reason.
    assert "evil.example.test" in claimed.network
    assert "evil.example.test" not in manifest.network

    # What the broker consults is the manifest, and nothing else.
    with pytest.raises(PolicyViolation):
        check_url("https://evil.example.test/steal", list(manifest.network))
    check_url("https://archive.org/ok", list(manifest.network))

    # And the code path says so structurally: the module that opens the
    # socket does not know this module exists.
    import inspect

    source = inspect.getsource(PluginProcess._serve_plugin_call)
    assert "self.manifest.network" in source
    assert "catalog" not in source.lower()


def test_the_broker_never_imports_a_catalog_module(tmp_path):
    """A stronger form of the same guarantee, at file scope rather than in
    one method: nothing on the enforcement path can read a directory at
    all, so a future edit cannot accidentally consult one."""
    import rom_hub.broker.host as host
    import rom_hub.broker.fetcher as fetcher
    import rom_hub.netpolicy as netpolicy

    for module in (host, fetcher, netpolicy):
        text = Path(module.__file__).read_text(encoding="utf-8")
        assert "catalog_sources" not in text
        assert "import catalog" not in text
        assert "from .catalog" not in text


def test_the_bundled_source_is_the_only_one_the_project_vouches_for(tmp_path):
    add_source(tmp_path, "neighbour", str(THIRD_PARTY))
    merged = load_all(tmp_path)
    vouched = {e.source.bundled for e in merged.entries if e.slug == "archive-org"}
    assert vouched == {True}
    assert not merged.find("neighbourhood-roms").source.bundled
    assert bundled_source().bundled
