"""libretro-overlays `assets`: bezels and gamepad overlays, one at a time.

    /git/trees/master?recursive=1 -> AssetArtifact[]
    AssetArtifact -> the same tree -> FetchPlan (cfg + its images)
    -> the HOST downloads them from raw.githubusercontent.com

The plugin never fetches an overlay. It names URLs and the **host**
fetches them, after checking each against this plugin's own `network`
allowlist -- the same gate a ROM import goes through.

## An overlay is a bundle, and now it can be installed as one

A RetroArch overlay is a `.cfg` plus the images it references, and the
`.cfg` names them relative to itself. The overwhelmingly common form in
this repository is a subdirectory:

    overlay0_desc0_overlay = img/dpad-left.png

The first release of this plugin offered only the 49 overlays whose
references were bare names, because every destination a `FetchPlan` could
express was a bare name. That was 49 of 310. `FetchFile.subdir` closes
the gap: a plugin may now name a directory *inside its own install
directory*, validated component by component against the same rule a
filename gets, and the host asserts the resolved path is inside the
target exactly as it does for a flat name.

So the whole repository is offered. Measured against the live tree on
2026-08-01: 310 `.cfg` files, 310 offered.

## The layout is upstream's, verbatim, and that is a requirement

An overlay is installed at its **repository-relative path** --
`gamepads/flat/snes.cfg` lands at `<overlays>/libretro-overlays/gamepads/
flat/snes.cfg`, and its sprites at `.../gamepads/flat/img/*.png`.

Not a stylistic choice. The `.cfg` names its images relatively, so the
image has to sit where the `.cfg` says it does; and the names cannot be
sanitised for the same reason, because a renamed `dpad-left.png` is a
sprite the `.cfg` no longer finds. This plugin therefore installs every
path **verbatim or not at all**: `plan()` checks each one against the
host's own rules before planning it, and refuses the overlay with a
message naming the offending path rather than shipping a bundle that
half-works. Checked against all 310 on 2026-08-01: every path in this
repository is expressible verbatim, so the refusal is a guard rather than
a filter.

Preserving the tree also means two overlays cannot collide. `borders/`
and `gamepads/` both contain a `snes.cfg`, and both contain an `img/`.

## What plan() still verifies

The `AssetArtifact` handed to `plan()` has been out of this process, so
its fields are not trusted to build a URL. See `libretro-cores.plan()`,
which makes the same decision for the same reason.

Beyond that, each reference is resolved against the `.cfg`'s own
directory and then checked three ways: it must stay inside the
repository (a `../../..` in somebody's `.cfg` is not something this
plugin will follow), it must actually exist in the tree (6 of the 310
name at least one image the repository does not have, and a plan
containing one would 404 halfway through an install), and its path must
be expressible verbatim.
"""

# Annotations are strings, which matters more than style here: the
# capability's own method is called `list`, so inside this class body a
# `list[dict]` return annotation would otherwise resolve against that
# method rather than the builtin and fail at import.
from __future__ import annotations

import posixpath
import re

from rom_hub_sdk import AssetArtifact, AssetProvider, FetchFile, FetchPlan

from .filenames import PathNotExpressible, check_repo_path, split_repo_path
from .github import TreeError, parse_tree, raw_url, tree_url

OWNER = "libretro"
REPO = "common-overlays"

#: Pinned to a branch, not a commit. See the README's "What this does not
#: promise".
REF = "master"

#: Verified by reading the repository's own COPYING -- the full CC-BY-4.0
#: text -- not GitHub's summary of it. See the README.
LICENSE = "CC-BY-4.0"

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tga")

#: Every line in an overlay `.cfg` that names an image file. Covers both
#: the whole-screen form (`overlay0_overlay`) and the per-control form
#: (`overlay0_desc3_overlay`), which are the only two the format has.
_IMAGE_REF = re.compile(
    r"^\s*overlay\d+(?:_desc\d+)?_overlay\s*=\s*(.+?)\s*$", re.MULTILINE
)

#: An overlay bundle is a `.cfg` and its images, and some of them are
#: large: the biggest in this repository needs 180 files. `FetchPlan`
#: allows 256, and this bound is stated separately so that going over it
#: fails with a sentence naming the overlay instead of a validation error
#: about a list length. Measured across all 310 on 2026-08-01: the median
#: bundle is 16 files, ten need more than 64, none needs more than 256.
MAX_FILES = 256

MAX_ASSETS = 512


class OverlayListError(Exception):
    """The catalogue could not be produced, and the message says why."""


class UnknownOverlay(Exception):
    """No such overlay in this repository."""


class BrokenOverlay(Exception):
    """This overlay's own references cannot be followed."""


def image_references(cfg_text: str) -> list[str]:
    """Every image path an overlay `.cfg` names, in order, deduplicated.

    Quotes are stripped because the format permits them and real files use
    them inconsistently. Order is preserved so a `FetchPlan` is stable.
    """
    seen: dict[str, None] = {}
    for raw in _IMAGE_REF.findall(cfg_text):
        ref = raw.strip().strip('"').strip("'").strip()
        if ref:
            seen.setdefault(ref, None)
    return list(seen)


def resolve_reference(directory: str, ref: str) -> str:
    """A reference resolved against the `.cfg`'s own directory.

    Returns a repository-relative path. Raises `BrokenOverlay` for a
    reference that leaves the repository or that uses a backslash --
    RetroArch reads these on Windows too, and a `img\\gb.png` that looked
    bare to `posixpath` would plan a path meaning two different things on
    two different machines.

    Verified against all 310 `.cfg` files on 2026-08-01: not one of them
    escapes the repository root, so this is a guard rather than a filter.
    It is here because "no overlay does this today" is not a property of
    a repository that takes contributions continuously.
    """
    if "\\" in ref:
        raise BrokenOverlay(
            f"reference {ref!r} uses a backslash, which is a path separator "
            f"under Windows rules and a filename character under POSIX ones; "
            f"this plugin will not guess which the author meant"
        )
    joined = posixpath.join(directory, ref) if directory else ref
    target = posixpath.normpath(joined)
    if target.startswith("../") or target == ".." or target.startswith("/"):
        raise BrokenOverlay(
            f"reference {ref!r} resolves to {target!r}, which is outside the "
            f"repository. This plugin installs an overlay at its own path in "
            f"the tree and does not follow a reference out of it."
        )
    return target


class Assets(AssetProvider):
    def list(self) -> list[AssetArtifact]:
        tree = self._tree()
        section = self._section()

        candidates = []
        for entry in tree:
            path = entry["path"]
            if entry["type"] != "blob" or not path.lower().endswith(".cfg"):
                continue
            if section and not path.startswith(section + "/"):
                continue
            candidates.append(entry)

        if len(candidates) > MAX_ASSETS:
            raise OverlayListError(
                f"this repository offers {len(candidates)} overlays, over the "
                f"{MAX_ASSETS} a plugin may return. Narrow it with this "
                f"plugin's `section` config key, which takes one top-level "
                f'directory name such as "gamepads" or "borders".'
            )

        return [
            AssetArtifact(
                asset_id=entry["path"],
                name=posixpath.basename(entry["path"]).rsplit(".", 1)[0],
                kind="overlay",
                license=LICENSE,
                # The directory is what these are organised by, and it is
                # the most useful thing to put in a column an operator
                # reads: "gamepads/lite" says more than a guess at a
                # console would.
                system=posixpath.dirname(entry["path"]) or "(root)",
                description=(
                    "RetroArch overlay; installs at its own path in the "
                    "repository tree, with the images its cfg references"
                ),
                size_bytes=entry["size"],
            )
            for entry in sorted(candidates, key=lambda e: e["path"].lower())
        ]

    def plan(self, asset: AssetArtifact) -> FetchPlan:
        tree = self._tree()

        # Never built from `asset.asset_id` directly -- matched against
        # what the tree says now, and the URL built from the tree's path.
        entry = next(
            (
                e
                for e in tree
                if e["type"] == "blob" and e["path"] == asset.asset_id
            ),
            None,
        )
        if entry is None:
            raise UnknownOverlay(
                f"this repository has no overlay {asset.asset_id!r}. Run "
                f"`rom-hub assets list libretro-overlays` to see what it does "
                f"have."
            )

        cfg_path = entry["path"]
        directory = posixpath.dirname(cfg_path)
        refs = image_references(self._fetch_cfg(cfg_path))

        present = {e["path"]: e for e in tree if e["type"] == "blob"}

        # The cfg first, then its images in the order it names them, each
        # at its own path in the tree. Nothing is renamed: a sanitised
        # sprite is a sprite the cfg no longer finds.
        wanted: list[tuple[str, int | None]] = [(cfg_path, entry["size"])]
        seen = {cfg_path}
        for ref in refs:
            target = resolve_reference(directory, ref)
            image = present.get(target)
            if image is None:
                # A `.cfg` naming a file the repository does not have would
                # otherwise become a download that 404s halfway through an
                # install. Six of the 310 do this; the overlay is still
                # worth having without the missing sprite, which is what
                # RetroArch itself does with one.
                continue
            if target in seen:
                continue
            seen.add(target)
            wanted.append((target, image["size"]))

        if len(wanted) > MAX_FILES:
            raise OverlayListError(
                f"overlay {cfg_path!r} needs {len(wanted)} files, over the "
                f"{MAX_FILES} this plugin will plan in one install"
            )

        files = []
        for path, size in wanted:
            subdir, filename = split_repo_path(path)
            try:
                check_repo_path(subdir, filename)
            except PathNotExpressible as exc:
                # Verbatim or not at all. Renaming to fit would produce a
                # bundle whose cfg points at files that are no longer
                # there, which is worse than a refusal that says why.
                raise BrokenOverlay(
                    f"overlay {cfg_path!r} needs {path!r}, which this host "
                    f"cannot write under that name: {exc}. This plugin "
                    f"installs an overlay's files verbatim, because the cfg "
                    f"references them by name -- so it refuses the overlay "
                    f"rather than install one that would not load."
                ) from exc
            files.append(
                FetchFile(
                    url=raw_url(OWNER, REPO, REF, path),
                    filename=filename,
                    subdir=subdir,
                    size_bytes=size,
                )
            )

        return FetchPlan(
            files=files,
            # A label for the operator, not a library platform slug --
            # nothing about an overlay is filed in a library.
            platform=directory or "overlays",
        )

    # -- configuration ---------------------------------------------------

    def _section(self) -> str:
        return str(self.ctx.config.get("section") or "").strip().strip("/")

    # -- the network -----------------------------------------------------

    def _tree(self) -> list[dict]:
        """The whole repository tree, in one recursive call.

        One request rather than one per directory: the response is 583 KB
        for 2,359 entries, which is both smaller and fewer round trips
        than walking eight top-level directories would be, and `plan()`
        needs to see images and cfgs together anyway to confirm that every
        reference exists.
        """
        url = tree_url(OWNER, REPO, REF) + "?recursive=1"
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise OverlayListError(
                f"GitHub answered HTTP {response.status_code} for the overlay "
                f"repository listing ({url})"
            )
        try:
            return parse_tree(response.text, what="the overlay repository")
        except TreeError as exc:
            raise OverlayListError(str(exc)) from exc

    def _fetch_cfg(self, path: str) -> str:
        url = raw_url(OWNER, REPO, REF, path)
        response = self.ctx.http.get(url)
        if response.status_code != 200:
            raise OverlayListError(
                f"GitHub answered HTTP {response.status_code} for the overlay "
                f"config {path!r} ({url})"
            )
        return response.text
