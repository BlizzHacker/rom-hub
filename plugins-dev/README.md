# `plugins-dev/` — the development copy

Each plugin here has a **public repository of its own, and that repository is
canonical.** What lives in this directory is a copy, kept so the test suite can
run offline.

| Plugin | Canonical repository | Pinned tag |
|---|---|---|
| `aminet` | <https://github.com/BlizzHacker/rom-hub-aminet> | `v0.1.0` |
| `archive-org` | <https://github.com/BlizzHacker/rom-hub-archive-org> | `v0.2.0` |
| `demozoo` | <https://github.com/BlizzHacker/rom-hub-demozoo> | `v0.1.0` |
| `emulators` | <https://github.com/BlizzHacker/rom-hub-emulators> | `v0.1.0` |
| `hasheous` | <https://github.com/BlizzHacker/rom-hub-hasheous> | `v0.1.0` |
| `homebrew` | <https://github.com/BlizzHacker/rom-hub-homebrew> | `v0.2.0` |
| `if-archive` | <https://github.com/BlizzHacker/rom-hub-if-archive> | `v0.1.0` |
| `itch-io` | <https://github.com/BlizzHacker/rom-hub-itch-io> | `v0.3.0` |
| `libretro-content` | <https://github.com/BlizzHacker/rom-hub-libretro-content> | `v0.1.0` |
| `libretro-cores` | <https://github.com/BlizzHacker/rom-hub-libretro-cores> | `v0.1.0` |
| `libretro-database` | <https://github.com/BlizzHacker/rom-hub-libretro-database> | `v0.1.0` |
| `libretro-thumbnails` | <https://github.com/BlizzHacker/rom-hub-libretro-thumbnails> | `v0.1.0` |
| `ludusavi` | <https://github.com/BlizzHacker/rom-hub-ludusavi> | `v0.1.0` |
| `nointro-archive` | <https://github.com/BlizzHacker/rom-hub-nointro-archive> | `v0.2.1` |
| `openvgdb` | <https://github.com/BlizzHacker/rom-hub-openvgdb> | `v0.2.0` |
| `retroachievements` | <https://github.com/BlizzHacker/rom-hub-retroachievements> | `v0.2.0` |
| `scummvm-freeware` | <https://github.com/BlizzHacker/rom-hub-scummvm-freeware> | `v0.1.0` |

**`aminet`, `demozoo`, `emulators`, `if-archive`, `libretro-content`,
`ludusavi` and `scummvm-freeware` are new and their repositories do not
exist yet.** Everything else on this page resolves; those seven have to be
created and tagged `v0.1.0` before `rom-hub plugin install <slug>` will work
for them, and `pytest -m live` is what will say so.

`catalog/plugins.json` points at those repositories, pinned to those tags, and
that is what `rom-hub plugin install <slug>` clones. **Nothing installs from
this directory.** If the copy and the tag ever disagree, the tag is right.

## Why the copy is still here

Because the alternative is worse. Nineteen test modules — `test_aminet`,
`test_archive_org*`, `test_hasheous`, `test_homebrew`, `test_itch_io`,
`test_libretro_*`, `test_nointro_archive`, `test_openvgdb`,
`test_retroachievements*`, `test_scummvm_freeware`, `test_stream`,
`test_live_e2e` — import plugin code
directly from these paths and exercise it against recorded fixtures. **No test
may clone from the network**, so deleting this directory would mean either
deleting those suites or moving the same files under `tests/fixtures/`, which
is the identical two-copy problem with a name that hides it.

Keeping the copy is the smaller risk *provided the drift is detectable*, which
is the part that is actually enforced:

- **`test_every_entry_is_pinned_to_the_tag_of_the_version_it_names`** (offline)
  ties three things together: the version in the manifest here, the version in
  the catalog, and the tag the catalog installs. Bump a plugin and forget to
  move the ref and the suite fails.
- **`test_catalog_entries_agree_with_the_manifests_they_describe`** (offline)
  holds the slug, name, capabilities and network allowlist to the manifest.
- **`test_the_published_tag_still_matches_the_development_copy`** (`live`,
  deselected by default) downloads each pinned tag and diffs it against the
  directory beside it, ignoring only `.git`, `__pycache__` and line endings.
  This is the one that catches an edit made here and never published.

Run the last one before publishing anything:

    ROM_HUB_ALLOW_UNSANDBOXED=1 python -m pytest -m live -q

## Changing a plugin

The copy is not the place to finish work. The order that keeps them in step:

1. Edit here and get the offline suite green — this is where the tests are.
2. Bump `version` in that plugin's `manifest.toml`.
3. Push the same change to the plugin's own repository and tag it `v<version>`.
4. Update that plugin's `version` and `ref` in `catalog/plugins.json`, then
   run `python scripts/render_directory.py`.
5. Re-run `pytest -m live` to confirm the tag and the copy agree.

Steps 2 and 4 are checked offline; step 3 is what step 5 checks. Skipping
step 3 is the only way to leave the two out of step, and it is the one thing
the live test exists to catch.

## Each plugin's own licence

Every plugin here is MIT, with its own `LICENSE` file naming its own
copyright holder. That is separate from this repository's `LICENSE` — a
plugin published on its own is a separate work, and its terms travel with it,
not with the host that runs it.
