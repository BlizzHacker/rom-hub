"""retroarch-autoconfig `assets`: controller profiles, one at a time.

    config.driver -> /git/trees/master:<driver> -> AssetArtifact[]
    AssetArtifact -> /git/trees/master:<driver> -> FetchPlan
    -> the HOST downloads one .cfg from raw.githubusercontent.com

The plugin never fetches a profile. It names a URL and the **host**
fetches it, after checking that URL against this plugin's own `network`
allowlist -- the same gate a ROM import goes through.

## Why this plugin is worth having

It solves a small, genuinely annoying problem completely. A pad that
RetroArch does not recognise is a pad you remap by hand, and the profile
that would have fixed it is a 1.7 KB text file somebody already wrote.
There are 437 of them for the `udev` driver alone.

## The driver is configuration, not detection

`input_driver` is a RetroArch setting, and the repository is organised by
it: the same physical pad has a different profile under `udev` (Linux),
`dinput` and `xinput` (Windows), `mfi` (Apple) and `android`. This plugin
does **not** detect the host OS, for the reason `libretro-cores` does not
detect the build target: the Hub may well be running somewhere other than
the machine the pad is plugged into. An unrecognised driver is refused by
name against the list of what exists, never defaulted.

## `plan()` re-reads the tree

The `AssetArtifact` handed back to `plan()` has been out of this process:
the host serialised it, the operator's command chose it, and it arrives
as a dict this plugin did not construct. Building a download URL out of
its fields would mean trusting a value that made a round trip through
somewhere else. Re-reading costs one request and means the URL is always
built from what the repository says now -- the same decision
`libretro-cores.plan()` makes, for the same reason.
"""

# Annotations are strings, which matters more than style here: the
# capability's own method is called `list`, so inside this class body a
# `list[dict]` return annotation would otherwise resolve against that
# method rather than the builtin and fail at import.
from __future__ import annotations

from rom_hub_sdk import AssetArtifact, AssetProvider, FetchFile, FetchPlan

from .filenames import safe_filename
from .github import TreeError, blobs, parse_tree, raw_url, tree_url

OWNER = "libretro"
REPO = "retroarch-joypad-autoconfig"

#: Pinned to a branch, not a commit. These profiles are added and
#: corrected continuously -- a pad released next month is the whole point
#: -- so pinning a sha would freeze the catalogue at packaging time. The
#: integrity story here is the allowlist and HTTPS, not a digest; see the
#: README's "What this does not promise".
REF = "master"

#: The input-driver directories, as they exist in the repository. An
#: allowlist rather than "whatever the operator typed", so a typo is a
#: refusal naming the real ones instead of a confusing 404 from GitHub.
DRIVERS = (
    "android",
    "dinput",
    "hid",
    "linuxraw",
    "mfi",
    "parport",
    "qnx",
    "sdl2",
    "sdl3",
    "udev",
    "winraw",
    "x",
    "xinput",
)

DEFAULT_DRIVER = "udev"

#: What `AssetArtifact.license` carries on every item this plugin offers.
#: Verified by reading the repository's own COPYING, not GitHub's summary
#: of it -- see the README. GitHub reports NOASSERTION because that one
#: file states two licences; the profiles are the MIT half.
LICENSE = "MIT"

#: The host refuses a catalogue over `rom_hub.types.MAX_ASSETS_PER_PLUGIN`
#: (512). `udev` held 437 profiles on 2026-07-29, close enough that the
#: ceiling is a real event rather than a theoretical one -- so it is
#: checked here, where the message can name the config key that fixes it.
MAX_ASSETS = 512


class UnknownDriver(Exception):
    """The configured input driver is not one this repository has."""


class ProfileListError(Exception):
    """The catalogue could not be produced, and the message says why."""


class UnknownProfile(Exception):
    """No such profile in this driver's directory."""


class Assets(AssetProvider):
    def list(self) -> list[AssetArtifact]:
        driver = self._driver()
        entries = self._entries(driver)

        match = self._match()
        if match:
            entries = [e for e in entries if match in e["path"].lower()]

        if len(entries) > MAX_ASSETS:
            raise ProfileListError(
                f"libretro's {driver!r} directory offers {len(entries)} "
                f"controller profiles, over the {MAX_ASSETS} a plugin may "
                f"return. Narrow it with this plugin's `match` config key, "
                f"which keeps only profiles whose filename contains a given "
                f"string -- `match = \"8bitdo\"`, for instance."
            )

        return [
            AssetArtifact(
                asset_id=f"{driver}/{entry['path']}",
                # The filename without its extension is the pad's name as
                # the repository knows it, which is exactly what an
                # operator is scanning the listing for.
                name=entry["path"].rsplit(".", 1)[0],
                kind="controller",
                license=LICENSE,
                # Not a console. The driver is the thing this profile is
                # specific to, and putting it in the column an operator
                # reads is more use than a blank.
                system=driver,
                description=f"RetroArch controller profile for the {driver} input driver",
                size_bytes=entry["size"],
            )
            for entry in entries
        ]

    def plan(self, asset: AssetArtifact) -> FetchPlan:
        driver = self._driver()
        entries = self._entries(driver)

        # Never built from `asset.asset_id` directly -- see the module
        # docstring. The id is matched against what the tree says now, and
        # the URL is built from the tree's own path.
        wanted = asset.asset_id.split("/", 1)[-1]
        entry = next((e for e in entries if e["path"] == wanted), None)
        if entry is None:
            raise UnknownProfile(
                f"libretro's {driver!r} directory has no controller profile "
                f"{wanted!r}. Run `rom-hub assets list retroarch-autoconfig` "
                f"to see what it does have -- profiles are renamed upstream "
                f"when a pad's reported name changes."
            )

        path = f"{driver}/{entry['path']}"
        return FetchPlan(
            files=[
                FetchFile(
                    url=raw_url(OWNER, REPO, REF, path),
                    filename=safe_filename(entry["path"]),
                    size_bytes=entry["size"],
                )
            ],
            # A label for the operator, not a library platform slug --
            # nothing about a controller profile is filed in a library.
            platform=driver,
        )

    # -- configuration ---------------------------------------------------

    def _driver(self) -> str:
        raw = str(self.ctx.config.get("driver") or DEFAULT_DRIVER).strip()
        if raw not in DRIVERS:
            raise UnknownDriver(
                f"{raw!r} is not an input driver this repository has. It "
                f"holds: {', '.join(DRIVERS)}. Set this plugin's `driver` "
                f"config key to the one your RetroArch is using -- it is "
                f"`input_driver` in retroarch.cfg, and it is not detected "
                f"from this machine because the Hub need not be running on "
                f"the machine the pad is plugged into."
            )
        return raw

    def _match(self) -> str:
        return str(self.ctx.config.get("match") or "").strip().lower()

    # -- the network -----------------------------------------------------

    def _entries(self, driver: str) -> list[dict]:
        """This driver's directory, listed.

        Not cached. `assets list` and `assets install` are separate CLI
        invocations and therefore separate plugin processes, so a cache
        would never be hit across the pair it would exist to help; within
        one call the tree is read once.
        """
        url = tree_url(OWNER, REPO, REF, driver)
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise ProfileListError(
                f"GitHub answered HTTP {response.status_code} for the "
                f"{driver!r} controller profile listing ({url})"
            )
        try:
            entries = parse_tree(response.text, what=f"the {driver!r} directory")
        except TreeError as exc:
            raise ProfileListError(str(exc)) from exc
        return sorted(blobs(entries, ".cfg"), key=lambda e: e["path"].lower())
