"""libretro-cheats `assets`: RetroArch cheat files, one game at a time.

    /git/trees/master:cht            -> the 44 systems
    config.systems -> /git/trees/master:cht/<system> -> AssetArtifact[]
    AssetArtifact  -> the same tree  -> FetchPlan
    -> the HOST downloads one .cht from raw.githubusercontent.com

The plugin never fetches a cheat file. It names a URL and the **host**
fetches it, after checking that URL against this plugin's own `network`
allowlist -- the same gate a ROM import goes through.

## This is the source that makes the size problem real

`libretro-database` is **795 MB**. Its `cht/` tree alone holds tens of
thousands of files across 44 systems -- 2,265 for the NES, 2,050 for the
PlayStation. Nothing here downloads any of that: a catalogue is one Trees
API call per selected system, and an install is one file of a few hundred
bytes.

It is also the source that forced this plugin to have a required
narrowing step, because *every* system at once is far past the 512 assets
a plugin may return. That is not a limitation being worked around, it is
the honest shape of the data: nobody wants "all cheats", they want the
cheats for a game.

## The first run tells you what to do next

With no `systems` configured, `list()` does **not** return an empty
catalogue and it does not dump 100,000 items. It makes the one cheap call
that enumerates the 44 system directories and raises a message naming
them, so the operator's next step is in front of them rather than in the
README. An empty list would have been technically true and useless.

## `plan()` re-reads the tree

The `AssetArtifact` handed to `plan()` has been out of this process, so
its fields are not trusted to build a URL. See `libretro-cores.plan()`,
which makes the same decision for the same reason.
"""

# Annotations are strings, which matters more than style here: the
# capability's own method is called `list`, so inside this class body a
# `list[dict]` return annotation would otherwise resolve against that
# method rather than the builtin and fail at import.
from __future__ import annotations

from rom_hub_sdk import AssetArtifact, AssetProvider, FetchFile, FetchPlan

from .filenames import safe_filename
from .github import TreeError, blobs, parse_tree, raw_url, subtrees, tree_url

OWNER = "libretro"
REPO = "libretro-database"
ROOT = "cht"

#: Pinned to a branch, not a commit. See the README's "What this does not
#: promise".
REF = "master"

#: Verified by reading the repository's own LICENSE -- the full
#: CC-BY-SA-4.0 text -- not GitHub's summary of it. See the README, which
#: also records what the README of the *source* does and does not say
#: about its third-party imports.
LICENSE = "CC-BY-SA-4.0"

MAX_ASSETS = 512


class CheatListError(Exception):
    """The catalogue could not be produced, and the message says why."""


class NeedsNarrowing(CheatListError):
    """No system chosen, and "all of them" is not a catalogue.

    Its own type because this is not a failure -- it is the first run, and
    the message is the instructions.
    """


class UnknownSystem(CheatListError):
    """The configured system is not a directory this repository has."""


class UnknownCheat(Exception):
    """No such cheat file in this system's directory."""


class Assets(AssetProvider):
    def list(self) -> list[AssetArtifact]:
        systems = self._systems()
        available = self._available_systems()

        if not systems:
            raise NeedsNarrowing(
                f"libretro-database holds cheat files for "
                f"{len(available)} systems and tens of thousands of games in "
                f"total -- far more than the {MAX_ASSETS} a plugin may return "
                f"in one catalogue, and not something anybody wants listed at "
                f"once. Choose one or more with this plugin's `systems` "
                f"config key. It holds: {', '.join(available)}."
            )

        unknown = [s for s in systems if s not in available]
        if unknown:
            raise UnknownSystem(
                f"this repository has no cheat directory for "
                f"{', '.join(repr(u) for u in unknown)}. It holds: "
                f"{', '.join(available)}."
            )

        match = self._match()
        items: list[AssetArtifact] = []
        for system in systems:
            for entry in self._entries(system):
                if match and match not in entry["path"].lower():
                    continue
                items.append(
                    AssetArtifact(
                        asset_id=f"{ROOT}/{system}/{entry['path']}",
                        # The filename without its extension is the game's
                        # No-Intro name, which is what an operator scans for.
                        name=entry["path"].rsplit(".", 1)[0],
                        kind="cheat",
                        license=LICENSE,
                        system=system,
                        description=f"RetroArch cheat file for {system}",
                        size_bytes=entry["size"],
                    )
                )
                if len(items) > MAX_ASSETS:
                    raise CheatListError(
                        f"the systems you selected offer more than "
                        f"{MAX_ASSETS} cheat files, over what a plugin may "
                        f"return. Narrow it with this plugin's `match` config "
                        f"key, which keeps only files whose name contains a "
                        f"given string -- `match = \"zelda\"`, for instance -- "
                        f"or select fewer systems."
                    )
        return items

    def plan(self, asset: AssetArtifact) -> FetchPlan:
        # Never built from `asset.asset_id` directly. The id is split, the
        # system re-checked against the repository, and the URL built from
        # the tree's own path.
        system, _, wanted = asset.asset_id.partition("/")[2].partition("/")
        if not system or not wanted:
            raise UnknownCheat(
                f"{asset.asset_id!r} is not a cheat id this plugin issued; "
                f"they look like 'cht/<system>/<game>.cht'"
            )

        entry = next(
            (e for e in self._entries(system) if e["path"] == wanted), None
        )
        if entry is None:
            raise UnknownCheat(
                f"libretro-database has no cheat file {wanted!r} for "
                f"{system!r}. Run `rom-hub assets list libretro-cheats` to "
                f"see what it does have."
            )

        path = f"{ROOT}/{system}/{entry['path']}"
        return FetchPlan(
            files=[
                FetchFile(
                    url=raw_url(OWNER, REPO, REF, path),
                    filename=safe_filename(entry["path"]),
                    size_bytes=entry["size"],
                )
            ],
            # A label for the operator, not a library platform slug --
            # nothing about a cheat file is filed in a library.
            platform=system,
        )

    # -- configuration ---------------------------------------------------

    def _systems(self) -> list[str]:
        raw = self.ctx.config.get("systems") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(s).strip() for s in raw if str(s).strip()]

    def _match(self) -> str:
        return str(self.ctx.config.get("match") or "").strip().lower()

    # -- the network -----------------------------------------------------

    def _available_systems(self) -> list[str]:
        """The system directories under `cht/`. One 12 KB call.

        Cheap enough to make unconditionally, which is what lets the
        no-systems case answer with the real list instead of a guess.
        """
        return sorted(e["path"] for e in subtrees(self._tree(ROOT, "cht/")))

    def _entries(self, system: str) -> list[dict]:
        """One system's cheat files, listed.

        Not cached. `assets list` and `assets install` are separate CLI
        invocations and therefore separate plugin processes, so a cache
        would never be hit across the pair it would exist to help.
        """
        entries = self._tree(f"{ROOT}/{system}", f"cht/{system}")
        return sorted(blobs(entries, ".cht"), key=lambda e: e["path"].lower())

    def _tree(self, path: str, what: str) -> list[dict]:
        url = tree_url(OWNER, REPO, REF, path)
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise CheatListError(
                f"GitHub answered HTTP {response.status_code} for the {what} "
                f"listing ({url})"
            )
        try:
            return parse_tree(response.text, what=what)
        except TreeError as exc:
            raise CheatListError(str(exc)) from exc
