"""libretro-overlays `assets`: bezels and gamepad overlays, one at a time.

    /git/trees/master?recursive=1 -> AssetArtifact[]
    AssetArtifact -> the same tree -> FetchPlan (cfg + its images)
    -> the HOST downloads them from raw.githubusercontent.com

The plugin never fetches an overlay. It names URLs and the **host**
fetches them, after checking each against this plugin's own `network`
allowlist -- the same gate a ROM import goes through.

## The hard part: an overlay is a bundle, and most of them cannot be one here

A RetroArch overlay is a `.cfg` plus the images it references. The `.cfg`
names them relative to itself, and in this repository the overwhelmingly
common form is a *subdirectory*:

    overlay0_desc0_overlay = img/dpad-left.png

A `FetchPlan` cannot express that. `FetchFile.filename` must be a bare
name, which is the rule that keeps a plugin from writing outside the
directory chosen for it -- the same rule that stops `../../etc/passwd`.
Widening it so overlays could carry subdirectories would trade a
containment guarantee for a file layout, which is not a trade worth
making for anybody.

So this plugin **offers only the self-contained overlays**: those whose
`.cfg` references its images as bare names, sitting in the same directory.
Measured against the live repository on 2026-07-29: 310 `.cfg` files, of
which **49 are self-contained** and 260 reference a subdirectory. The 49
include the whole `gamepads/lite/` set, which is the flat overlay pack
most people actually want.

Listing an overlay that would fail to install is worse than not listing
it, so the catalogue is filtered rather than the install being left to
discover the problem.

**How the filter is cheap.** Reading 310 `.cfg` bodies to find out which
are self-contained would be 310 requests for a catalogue. Instead the
tree itself is the predictor: an overlay is self-contained exactly when
its own directory also contains image files. That was checked against the
content of all 310 -- the heuristic and the truth agree on every single
one, with no false positives and no false negatives -- so one recursive
tree call classifies the whole repository.

**And it is still verified at install.** `plan()` fetches the one chosen
`.cfg` and re-reads its references. If any of them is not a bare name the
item is refused with a message saying so, rather than planning a download
the host would reject or, worse, one that would land a `.cfg` whose
images are missing. The heuristic decides what to *offer*; the file
itself decides what to *install*.

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

import posixpath
import re

from rom_hub_sdk import AssetArtifact, AssetProvider, FetchFile, FetchPlan

from .filenames import safe_filename
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

#: An overlay bundle is a `.cfg` and its images. `FetchPlan` allows 256
#: files; the largest self-contained overlay here is far under that, and a
#: bound stated here fails with a message instead of a validation error.
MAX_FILES = 64

MAX_ASSETS = 512


class OverlayListError(Exception):
    """The catalogue could not be produced, and the message says why."""


class UnknownOverlay(Exception):
    """No such overlay in this repository."""


class NotSelfContained(Exception):
    """This overlay references an image the host cannot place."""


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


def is_self_contained(refs: list[str]) -> bool:
    """True when every reference is a bare name this host can write.

    A backslash counts as a separator too: the format is read by RetroArch
    on Windows as well, and a `img\\gb.png` that looked bare to
    `posixpath` would plan a filename the host then refuses.
    """
    return bool(refs) and all(
        "/" not in ref and "\\" not in ref for ref in refs
    )


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
            # The heuristic: an overlay is self-contained exactly when its
            # own directory also holds images. Verified against the content
            # of all 310 cfgs -- see the module docstring.
            if not self._has_image_sibling(tree, posixpath.dirname(path)):
                continue
            candidates.append(entry)

        if len(candidates) > MAX_ASSETS:
            raise OverlayListError(
                f"this repository offers {len(candidates)} self-contained "
                f"overlays, over the {MAX_ASSETS} a plugin may return. Narrow "
                f"it with this plugin's `section` config key, which takes one "
                f"top-level directory name such as \"gamepads\" or \"borders\"."
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
                description="RetroArch overlay (self-contained: cfg and images)",
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

        if not is_self_contained(refs):
            # The heuristic said yes and the file says no. Refuse plainly
            # rather than plan a download the host would reject.
            offenders = [r for r in refs if "/" in r or "\\" in r][:3]
            raise NotSelfContained(
                f"overlay {cfg_path!r} references images in a subdirectory "
                f"({', '.join(offenders)}), which a FetchPlan cannot express "
                f"-- every filename the host writes must be a bare name, and "
                f"that is the rule that keeps a plugin's downloads inside the "
                f"directory chosen for them. This plugin offers only "
                f"self-contained overlays; this one is not, despite sitting "
                f"beside images."
            )

        # Only the images that are really in the tree. A `.cfg` naming a
        # file the repository does not have would otherwise become a
        # download that 404s halfway through an install.
        present = {
            e["path"]: e
            for e in tree
            if e["type"] == "blob" and posixpath.dirname(e["path"]) == directory
        }
        files = [
            FetchFile(
                url=raw_url(OWNER, REPO, REF, cfg_path),
                filename=safe_filename(cfg_path),
                size_bytes=entry["size"],
            )
        ]
        used: set[str] = {safe_filename(cfg_path).casefold()}
        for ref in refs:
            ref_path = posixpath.join(directory, ref) if directory else ref
            image = present.get(ref_path)
            if image is None:
                continue
            name = safe_filename(ref)
            if name.casefold() in used:
                # FetchPlan refuses colliding filenames outright, and two
                # references that sanitise to one name is a real (if rare)
                # possibility. Skipping the duplicate keeps the plan valid;
                # the image is already being fetched under that name.
                continue
            used.add(name.casefold())
            files.append(
                FetchFile(
                    url=raw_url(OWNER, REPO, REF, ref_path),
                    filename=name,
                    size_bytes=image["size"],
                )
            )

        if len(files) > MAX_FILES:
            raise OverlayListError(
                f"overlay {cfg_path!r} needs {len(files)} files, over the "
                f"{MAX_FILES} this plugin will plan in one install"
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

        One request rather than one per directory: the response is 732 KB
        for 2,359 entries, which is both smaller and fewer round trips
        than walking eight top-level directories would be, and it is what
        makes the self-contained heuristic computable at all -- that needs
        to see images and cfgs together.
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

    @staticmethod
    def _has_image_sibling(tree: list[dict], directory: str) -> bool:
        return any(
            e["type"] == "blob"
            and posixpath.dirname(e["path"]) == directory
            and e["path"].lower().endswith(IMAGE_SUFFIXES)
            for e in tree
        )
