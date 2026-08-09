"""The plugin catalog: a community-kept directory of known sources.

The catalog is a convenience for *finding* plugins. It is deliberately not a
source of authority about what a plugin may do — see
test_catalog_cannot_widen_permissions, which is the property that keeps a
compromised or malicious catalog from being a privilege-escalation route.
"""

import json
from pathlib import Path

import pytest

from rom_hub.catalog import CatalogError, CatalogEntry, load_catalog, render_markdown

CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalog" / "plugins.json"


def test_shipped_catalog_parses():
    entries = load_catalog(CATALOG_PATH)
    assert entries, "the shipped catalog should not be empty"
    assert any(e.slug == "archive-org" for e in entries)


def test_the_catalog_lists_every_plugin_that_ships_in_this_repo():
    """A directory that omits half the plugins is a directory nobody trusts.

    Reads plugins-dev/ rather than a hardcoded list, so adding a plugin
    without cataloguing it fails here instead of shipping unlisted.
    """
    plugins_dev = Path(__file__).resolve().parents[1] / "plugins-dev"
    on_disk = {p.name for p in plugins_dev.iterdir() if (p / "manifest.toml").exists()}
    catalogued = {e.slug for e in load_catalog(CATALOG_PATH)}
    assert on_disk == catalogued, (
        f"in plugins-dev but not the catalog: {sorted(on_disk - catalogued)}; "
        f"in the catalog but not on disk: {sorted(catalogued - on_disk)}"
    )


def test_catalog_entries_agree_with_the_manifests_they_describe():
    """Slug, version and capabilities are facts, and the manifest owns them.

    The catalog is a copy for reading. A copy that drifts is how a reader
    ends up judging a plugin by capabilities it does not have.
    """
    import tomllib

    plugins_dev = Path(__file__).resolve().parents[1] / "plugins-dev"
    for entry in load_catalog(CATALOG_PATH):
        raw = tomllib.loads(
            (plugins_dev / entry.slug / "manifest.toml").read_text(encoding="utf-8")
        )
        assert raw["plugin"]["slug"] == entry.slug
        assert raw["plugin"]["name"] == entry.name
        assert str(raw["plugin"]["version"]) == entry.version
        assert sorted(raw["capabilities"]) == sorted(entry.capabilities)
        # The network list shown to a reader must be the one the broker will
        # actually enforce, or the directory is worse than silent.
        assert raw["permissions"]["network"] == entry.network


def entry_json(**overrides) -> str:
    """A minimal valid entry, so a test can vary the one field it is about.

    Built as a dict rather than a string literal: every new required field
    used to mean editing three separate JSON literals, and a test that fails
    because it is stale tells you nothing about the rule it was written for.
    """
    entry = {
        "slug": "x", "name": "X", "author": "a",
        "repository": "https://e/r", "install": "https://e/r",
        "download": "https://e/r/v1.tar.gz", "version": "1", "ref": "v1",
        "updated": "2026-01-01", "rpp_version": "1", "capabilities": ["search"],
        "network": [], "status": "ok", "description": "d", "terms": "t",
        "search_only": False, "key_required": False, "in_tree": False,
        "comments": "", "platforms": [],
    }
    entry.update(overrides)
    return json.dumps({"catalog_version": "1", "plugins": [entry]})


def test_archive_org_entry_is_pinned_to_a_tag():
    entry = next(e for e in load_catalog(CATALOG_PATH) if e.slug == "archive-org")
    # The download must be pinned to a tag, never a moving branch: "latest"
    # is how a directory silently ships someone new code.
    assert entry.ref.startswith("v")
    assert entry.ref in entry.download
    assert entry.download.endswith(".tar.gz")


def test_no_entry_still_points_at_an_unpublished_placeholder():
    """The plugins are published now, so `.invalid` must not survive anywhere.

    This is the successor to the rule that used to run the other way. While
    the plugins had no public repos, their URLs were required to be
    `.invalid` (RFC 2606, guaranteed never to resolve) so a reader could not
    mistake a dead link for a supply-chain swap. Each one now has a real
    repository, and a placeholder left behind would be the same lie in the
    opposite direction: a directory claiming a plugin is unpublished when it
    is one clone away.
    """
    for entry in load_catalog(CATALOG_PATH):
        for url in (entry.repository, entry.install, entry.download):
            host = url.split("/")[2]
            assert not host.endswith(".invalid"), (
                f"{entry.slug}: {url} is still an unpublished placeholder"
            )
        assert not entry.in_tree, (
            f"{entry.slug}: in_tree says it has no public repo, but "
            f"{entry.repository} is one"
        )


def test_every_entry_is_pinned_to_the_tag_of_the_version_it_names():
    """Three facts, one truth: the manifest's version, the catalog's, and the tag.

    A plugin whose code moved without its version moving is the failure this
    cannot catch on its own -- see `test_the_published_tag_still_matches_the
    _development_copy`, which is the live check for that. What it *does*
    catch is the cheap and much more likely mistake: bumping a plugin and
    forgetting to move the ref, so the directory keeps installing the old
    tag while advertising the new version.
    """
    import tomllib

    plugins_dev = Path(__file__).resolve().parents[1] / "plugins-dev"
    for entry in load_catalog(CATALOG_PATH):
        manifest = tomllib.loads(
            (plugins_dev / entry.slug / "manifest.toml").read_text(encoding="utf-8")
        )
        version = str(manifest["plugin"]["version"])
        assert entry.version == version, (
            f"{entry.slug}: catalog says {entry.version}, manifest says {version}"
        )
        assert entry.ref == f"v{version}", (
            f"{entry.slug}: version {version} should be installed from tag "
            f"v{version}, not {entry.ref!r}"
        )
        # And the tarball a reader would click must be the same tag the CLI
        # would clone, not merely *a* tag from the same repository.
        assert entry.download.endswith(f"/{entry.ref}.tar.gz")
        assert entry.download.startswith(entry.repository + "/")


def test_entries_must_declare_a_known_status():
    with pytest.raises(CatalogError, match="status"):
        load_catalog_from_text(entry_json(status="vibes"))


def test_non_https_urls_rejected():
    with pytest.raises(CatalogError, match="https"):
        load_catalog_from_text(
            entry_json(repository="http://e/r", install="http://e/r",
                       download="http://e/r/v1.tar.gz")
        )


def test_the_reader_facing_prose_cannot_be_left_blank():
    """A blank cell reads as "nothing to declare", not "nobody filled it in".

    `terms` is the licensing position of the plugin's *source*. It is the
    field most likely to be skipped and the one a reader most needs.
    """
    for field in ("description", "terms"):
        with pytest.raises(CatalogError, match=field):
            load_catalog_from_text(entry_json(**{field: "   "}))


def test_the_flags_a_reader_needs_before_installing_must_be_booleans():
    for field in ("search_only", "key_required", "in_tree"):
        with pytest.raises(CatalogError, match=field):
            load_catalog_from_text(entry_json(**{field: "yes"}))


def test_search_only_and_key_required_are_surfaced_in_the_page():
    """Both have a failure mode that looks like a bug to whoever hits it.

    itch-io's importer always refuses; retroachievements returns nothing
    without a key. Saying so in the directory is cheaper than the bug report.
    """
    entries = load_catalog(CATALOG_PATH)
    itch = next(e for e in entries if e.slug == "itch-io")
    ra = next(e for e in entries if e.slug == "retroachievements")
    assert itch.search_only
    assert ra.key_required

    md = render_markdown(entries)
    # Rendered as the behaviour, not the category: itch-io implements
    # `metadata` and still cannot import anything, so "search-only" would
    # be wrong where "cannot import" is exactly right.
    assert "cannot import" in md
    assert "search-only" not in md
    # The storage question is the part that must not be buried. It used to
    # be "clear text"; now that `secret` is implemented the directory
    # points at the command that prints the honest, per-host answer rather
    # than repeating a claim it cannot check.
    assert "clear text" not in md.lower()
    assert "plugin secret list" in md


def test_the_api_key_really_is_not_stored_in_clear_text_any_more():
    """Was `test_the_api_key_really_is_stored_in_clear_text`, inverted.

    It existed to catch the host quietly improving while the directory
    still warned about plain text. The host did improve -- `secret` is
    implemented (`rom_hub.secrets`) -- so the same test now guards the
    opposite drift: a catalog that goes back to promising plain text, or a
    `secret` field that quietly becomes storable in `state.json`.
    """
    from rom_hub.manifest import (
        RESERVED_CONFIG_TYPES,
        SUPPORTED_CONFIG_TYPES,
        parse_manifest,
    )
    from rom_hub.secrets import secret_fields

    assert "secret" in SUPPORTED_CONFIG_TYPES
    assert "secret" not in RESERVED_CONFIG_TYPES
    manifest = parse_manifest(
        '[plugin]\nslug="x"\nname="X"\nversion="1"\nrpp_version="1"\n'
        '[capabilities]\nmetadata="x.m:M"\n'
        '[permissions]\nnetwork=[]\nromm_api=[]\n'
        '[config]\napi_key={type="secret"}\n'
    )
    assert secret_fields(manifest) == ["api_key"]
    # And the registry seeds `state.json` from schema defaults, so a type
    # that may not carry one can never seed a credential into that file.
    assert "default" not in manifest.config_schema["api_key"]


def test_unsupported_catalog_version_rejected():
    with pytest.raises(CatalogError, match="catalog_version"):
        load_catalog_from_text('{"catalog_version":"2","plugins":[]}')


def test_catalog_cannot_widen_permissions():
    """The catalog's `network` list is advisory only.

    An installed plugin's real allowlist comes from its own manifest, read at
    install time. If the catalog could grant network access, then whoever hosts
    the catalog could silently widen every plugin's reach — which is exactly
    the authority a directory must not have.
    """
    from rom_hub.broker.host import PluginProcess
    import inspect

    source = inspect.getsource(PluginProcess._serve_plugin_call)
    assert "self.manifest.network" in source
    assert "catalog" not in source.lower()


def test_render_markdown_carries_every_column_a_reader_compares_on():
    md = render_markdown(load_catalog(CATALOG_PATH))
    for column in ("Source", "Author (Repository)", "Version", "Last update",
                   "Install", "Comments"):
        assert column in md
    assert "archive-org" in md or "Archive.org" in md


def load_catalog_from_text(text: str):
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(text)
        path = Path(fh.name)
    try:
        return load_catalog(path)
    finally:
        path.unlink(missing_ok=True)


def test_status_symbol_degrades_on_a_terminal_that_cannot_encode_it():
    """A Windows console is cp1252 and cannot encode these at all.

    Printing one raises UnicodeEncodeError and takes the command down, so the
    status column would break `browse` on the platform this was built on.
    """
    from rom_hub.catalog import symbol_for

    assert symbol_for("ok", "utf-8") == "✔"
    assert symbol_for("ok", "cp1252") == "ok"
    assert symbol_for("broken", "cp1252") == "x"
    # An unknown or absent encoding must degrade, not raise.
    assert symbol_for("caveat", None) == "!"
    assert symbol_for("caveat", "definitely-not-a-codec") == "!"


def test_directory_is_in_sync_with_the_catalog():
    """docs/PLUGINS.md is generated; a stale page contradicts what the CLI does.

    A published directory that disagrees with the catalog the CLI installs from
    is worse than no directory, so drift fails here rather than misleading
    somebody choosing what to trust.
    """
    from rom_hub.catalog import render_markdown

    page = (Path(__file__).resolve().parents[1] / "docs" / "PLUGINS.md").read_text(
        encoding="utf-8"
    )
    assert render_markdown(load_catalog(CATALOG_PATH)) in page, (
        "docs/PLUGINS.md is out of date -- run python scripts/render_directory.py"
    )


# ------------------------------------------- which backends a plugin suits


def test_the_backend_table_matches_what_the_backends_declare():
    """The whole point of deriving this is that it cannot disagree with the
    backends themselves. If one gains a capability, this test is what says
    the directory has to be regenerated."""
    from rom_hub.backends import available, describe
    from rom_hub.catalog import backend_capabilities

    assert backend_capabilities() == {
        name: describe(name).capabilities for name in available()
    }


def test_a_metadata_only_plugin_is_no_use_against_gaseous():
    """The example the directory has to make obvious: Gaseous writes no
    metadata, so libretro-thumbnails has nothing it can do there."""
    from rom_hub.catalog import backend_fit

    entries = load_catalog(CATALOG_PATH)
    thumbs = next(e for e in entries if e.slug == "libretro-thumbnails")
    fit = {f.backend: f for f in backend_fit(thumbs)}
    assert fit["gaseous"].verdict == "none"
    assert fit["gaseous"].blocked == ("metadata",)
    assert fit["retrom"].verdict == "full"
    assert fit["romm"].verdict == "full"


def test_a_cores_plugin_works_against_every_backend_including_none():
    """`cores` writes to the Hub's own directory, not to a library. There is
    no backend capability it could need."""
    from rom_hub.catalog import backend_fit

    entries = load_catalog(CATALOG_PATH)
    cores = next(e for e in entries if e.slug == "libretro-cores")
    assert {f.verdict for f in backend_fit(cores)} == {"full"}
    assert {f.verdict for f in backend_fit(cores, {"nothing": frozenset()})} == {
        "full"
    }


def test_a_mixed_plugin_reports_the_blocked_and_the_merely_reduced_apart():
    """archive-org against Gaseous loses `metadata` outright and loses only
    the collection from `importer`. Those are different news and the page
    must not flatten them together."""
    from rom_hub.catalog import backend_fit

    entries = load_catalog(CATALOG_PATH)
    archive = next(e for e in entries if e.slug == "archive-org")
    fit = {f.backend: f for f in backend_fit(archive)}

    assert fit["gaseous"].verdict == "partial"
    assert fit["gaseous"].blocked == ("metadata",)
    assert fit["gaseous"].reduced == (("importer", "collections"),)
    # `census` joins them: it writes a catalogue under the Hub's own
    # `var/`, so which library server is configured cannot reduce it.
    assert set(fit["gaseous"].unaffected) == {"search", "stream", "census"}

    # Retrom writes metadata but has no collections.
    assert fit["retrom"].verdict == "reduced"
    assert fit["retrom"].blocked == ()
    assert fit["retrom"].reduced == (("importer", "collections"),)


def test_the_page_carries_a_backend_column_and_says_how_to_read_it():
    md = render_markdown(load_catalog(CATALOG_PATH))
    assert "| Backends |" in md
    # A metadata-only plugin against Gaseous is the case a reader must not
    # have to work out for themselves.
    assert "~~Gaseous~~" in md
    assert "Backends." in md


def test_every_capability_the_host_gates_on_is_classified_here():
    """A capability added to RPP without a row in either table would render
    as "works everywhere", which is the one wrong answer that looks fine."""
    from rom_hub.catalog import CAPABILITY_EXTRAS, CAPABILITY_NEEDS
    from rom_hub.manifest import KNOWN_CAPABILITIES

    # Every capability that needs or gains something also appears in the
    # manifest's vocabulary; nothing is claimed for one that does not exist.
    assert set(CAPABILITY_NEEDS) <= KNOWN_CAPABILITIES
    assert set(CAPABILITY_EXTRAS) <= KNOWN_CAPABILITIES

    # An extra need not hang off a need. `firmware` needs nothing -- the
    # BIOS is installed once it is on disk -- and still gains something
    # from a backend that can store firmware.
    assert set(CAPABILITY_EXTRAS) - set(CAPABILITY_NEEDS) == {"firmware"}

    # And the ones that genuinely touch no backend at all are named, so
    # "absent from both tables" is a decision rather than an oversight.
    # Asserted against the set the backends package declares rather than a
    # list repeated here, so a capability cannot be backend-independent in
    # one file and forgotten in the other.
    from rom_hub.backends import BACKEND_INDEPENDENT_CAPABILITIES

    assert KNOWN_CAPABILITIES - set(CAPABILITY_NEEDS) - set(
        CAPABILITY_EXTRAS
    ) == BACKEND_INDEPENDENT_CAPABILITIES
    # Every name in it is a real plugin capability, not a backend one.
    assert BACKEND_INDEPENDENT_CAPABILITIES <= KNOWN_CAPABILITIES


def test_the_needs_and_extras_are_real_backend_capabilities():
    from rom_hub.backends import ALL_CAPABILITIES, OPTIONAL_CAPABILITIES
    from rom_hub.catalog import CAPABILITY_EXTRAS, CAPABILITY_NEEDS

    assert set(CAPABILITY_NEEDS.values()) <= ALL_CAPABILITIES
    # An "extra" that the host would refuse to degrade is not an extra.
    assert set(CAPABILITY_EXTRAS.values()) <= OPTIONAL_CAPABILITIES



# ------------------------------------------ the copy vs the published tag


def _tree(root: Path) -> dict[str, bytes]:
    """Every file under `root`, keyed by posix path, newline-normalised.

    Build artefacts are excluded and CRLF is folded to LF: this repository
    is developed on Windows with `core.autocrlf`, so a checkout of the same
    commit differs from a tarball of it in every text file and in nothing
    that matters.
    """
    out = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or "__pycache__" in rel:
            continue
        out[rel] = path.read_bytes().replace(b"\r\n", b"\n")
    return out


@pytest.mark.live
def test_the_published_tag_still_matches_the_development_copy():
    """`plugins-dev/` is a copy, and this is what stops it drifting silently.

    The published repository at the pinned tag is canonical; `plugins-dev/`
    exists so the plugin test suites can run with no network. Two copies of
    anything drift, so the drift is made *detectable* rather than merely
    discouraged: this downloads each tag and diffs it against the copy.

    Marked `live` and therefore deselected by default. No test in the
    offline suite may reach the network -- that is the whole reason the
    development copy is there.
    """
    import io
    import tarfile
    import tempfile

    import httpx

    plugins_dev = Path(__file__).resolve().parents[1] / "plugins-dev"
    drifted = []
    for entry in load_catalog(CATALOG_PATH):
        response = httpx.get(entry.download, follow_redirects=True, timeout=60)
        assert response.status_code == 200, (
            f"{entry.slug}: {entry.download} answered {response.status_code}"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as tar:
                tar.extractall(tmp, filter="data")
            (extracted,) = Path(tmp).iterdir()
            published = _tree(extracted)
        local = _tree(plugins_dev / entry.slug)
        if published != local:
            differing = sorted(
                name
                for name in set(published) | set(local)
                if published.get(name) != local.get(name)
            )
            drifted.append(f"{entry.slug} @ {entry.ref}: {differing}")

    assert not drifted, (
        "plugins-dev has drifted from the published tags. The tag is "
        "canonical -- see plugins-dev/README.md:\n  " + "\n  ".join(drifted)
    )


def test_the_display_name_comes_from_the_backend_not_from_this_module():
    """`"romm".title()` is "Romm". The only place that is known to be wrong
    is inside the package that implements it, so that is where the label
    lives -- and this module keeps no table of product names."""
    from rom_hub.backends import available, describe
    from rom_hub.catalog import backend_labels

    labels = backend_labels()
    assert labels == {name: describe(name).label for name in available()}
    assert labels["romm"] == "RomM"
    assert all(label for label in labels.values())

    source = (
        Path(__file__).resolve().parents[1] / "src" / "rom_hub" / "catalog.py"
    ).read_text(encoding="utf-8")
    for product in ("RomM", "Gaseous", "Retrom"):
        assert product not in source, (
            f"catalog.py names {product!r}; backend-specific knowledge belongs "
            f"in that backend's package"
        )


# --------------------------------------- will what a plugin imports play?
#
# The catalog's `platforms` list is what the directory renders and what
# `rom-hub platforms` reads. It is hand-kept, so these are the tests that
# stop it from becoming a hand-kept fiction.


def _audit():
    """The real platform tables, read by scripts/audit_platforms.py."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "_audit_platforms", root / "scripts" / "audit_platforms.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_platforms_match_the_plugin_source():
    """The honesty check, and the reason the field can be trusted at all.

    A plugin that gains a platform gains it in its own `platforms.py`,
    where nothing about the directory is in view. This is what makes that
    edit also an edit to `catalog/plugins.json` -- and therefore to the
    published page, which then says out loud whether the new platform can
    be played. Without it, a plugin could quietly start importing to a
    platform with no emulator core and the directory would go on promising
    the old answer.
    """
    audited = _audit().audit()
    mismatched = []
    for entry in load_catalog(CATALOG_PATH):
        expected = audited[entry.slug]["platforms"]
        if list(entry.platforms) != expected:
            gained = sorted(set(expected) - set(entry.platforms))
            lost = sorted(set(entry.platforms) - set(expected))
            mismatched.append(f"{entry.slug}: +{gained} -{lost}")
    assert not mismatched, (
        "catalog/plugins.json disagrees with the plugins' own platform "
        "tables. Re-run `python scripts/audit_platforms.py --json`, update "
        "the `platforms` lists, then regenerate docs/PLUGINS.md:\n  "
        + "\n  ".join(mismatched)
    )


def test_every_platform_named_is_one_romm_actually_has():
    """A slug RomM does not know fails much later, at `platform_id()`, with
    a far less useful message than this one."""
    from rom_hub.playability import EJS_CORES

    # The core map is a subset of RomM's platform enum rather than all of
    # it, so this can only check the ones it covers -- which is exactly the
    # set where a typo would masquerade as "catalogue-only" instead of
    # showing up as a bad slug.
    near_misses = []
    for entry in load_catalog(CATALOG_PATH):
        for platform in entry.platforms:
            if platform != platform.strip().lower():
                near_misses.append(f"{entry.slug}: {platform!r} is not normalised")
            if platform.replace("_", "-") in EJS_CORES and platform not in EJS_CORES:
                near_misses.append(
                    f"{entry.slug}: {platform!r} looks like a typo for "
                    f"{platform.replace('_', '-')!r}, which does play"
                )
    assert not near_misses, near_misses


def test_a_plugin_that_can_import_nothing_playable_says_so_in_the_directory():
    """`none - catalogue only` has to be legible on the page, not merely
    derivable from it. These four exist to catalogue rather than to play,
    and a reader choosing between plugins must be able to see that before
    installing."""
    md = render_markdown(load_catalog(CATALOG_PATH))
    from rom_hub.catalog import playability_fit

    catalogue_only = [
        e.slug
        for e in load_catalog(CATALOG_PATH)
        if playability_fit(e).verdict == "catalogue-only"
    ]
    assert sorted(catalogue_only) == ["if-archive", "itch-io", "scummvm-freeware"]
    assert "**none** — catalogue only" in md


def test_every_unplayable_import_target_is_named_on_the_page():
    """A count tells a reader there is a problem; only the names tell them
    whether it is theirs."""
    md = render_markdown(load_catalog(CATALOG_PATH))
    from rom_hub.playability import verdict_for

    missing = []
    for entry in load_catalog(CATALOG_PATH):
        if "importer" not in entry.capabilities:
            continue
        for platform in entry.platforms:
            if verdict_for(platform).plays:
                continue
            if f"`{platform}`" not in md:
                missing.append(f"{entry.slug}: {platform}")
    assert not missing, (
        "these platforms cannot be played and the directory never says so: "
        + ", ".join(missing)
    )


def test_a_metadata_plugin_is_not_scored_on_playability():
    """`libretro-thumbnails` covers 56 platforms with no emulator core and
    that is not a defect: it files no ROM, so it cannot file a dead one.
    Scoring it like an importer would invent a problem and bury the real
    ones under it."""
    from rom_hub.catalog import playability_cell, playability_fit

    (entry,) = [e for e in load_catalog(CATALOG_PATH) if e.slug == "libretro-thumbnails"]
    fit = playability_fit(entry)
    assert not fit.files_roms
    assert fit.catalogue_only, "the fixture is only meaningful while it has some"
    assert playability_cell(fit) == "n/a (imports nothing)"


def test_a_plugin_with_no_platform_table_renders_as_not_applicable():
    from rom_hub.catalog import playability_cell, playability_fit

    (entry,) = [e for e in load_catalog(CATALOG_PATH) if e.slug == "libretro-cores"]
    assert entry.platforms == []
    assert playability_cell(playability_fit(entry)) == "—"


def test_platforms_must_be_a_sorted_list_of_slugs():
    base = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    base["plugins"] = base["plugins"][:1]

    base["plugins"][0]["platforms"] = ["snes", "nes"]
    with pytest.raises(CatalogError, match="sorted"):
        load_catalog_from_text(json.dumps(base))

    base["plugins"][0]["platforms"] = "nes"
    with pytest.raises(CatalogError, match="list of platform slugs"):
        load_catalog_from_text(json.dumps(base))

    base["plugins"][0]["platforms"] = ["nes", ""]
    with pytest.raises(CatalogError, match="list of platform slugs"):
        load_catalog_from_text(json.dumps(base))


def test_the_page_carries_a_playable_column_and_says_how_to_read_it():
    md = render_markdown(load_catalog(CATALOG_PATH))
    assert "| Playable |" in md
    assert "Reading the Playable column" in md
    assert "does nothing at all when clicked" in md
