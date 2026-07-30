"""Listing one directory of a GitHub repository without cloning it.

The whole reason this module exists: `retroarch-joypad-autoconfig` is
2.6 MB, `common-overlays` is 29 MB and `libretro-database` is 795 MB. An
`assets` plugin has to answer "what can I install?" without pulling any
of that, and then fetch exactly one file when the operator chooses.

Two GitHub endpoints can list a directory. Only one of them is correct,
and the difference is silent.

**The contents API truncates at 1,000 entries with no error and no
flag.** `/contents/cht/Nintendo - Nintendo Entertainment System` returns
1,000 of the 2,265 files that are actually there, answers 200, and says
nothing about it. A plugin built on it would offer a third of the
catalogue and look like it was working.

**The Git Trees API returns everything and tells you when it did not.**
`/git/trees/<ref>:<path>` answers with every entry plus a `truncated`
boolean, which this module refuses on rather than silently under-reporting
-- an honest failure beats a plausible-looking short list. It is also
*smaller*: 704 KB against the contents API's truncated 1.4 MB for that
same NES directory, because it carries no per-entry URL block.

The `<ref>:<path>` form is what makes this one request instead of two --
no walk from the root tree to find a subdirectory's sha first.

Downloads never come from the API at all. `raw_url` builds a
`raw.githubusercontent.com` URL directly, which serves the file body with
no redirect (verified with `curl -w %{num_redirects}`: 0), so the
plugin's `network` allowlist is exactly two hosts and neither of them is
a CDN hop nobody declared.
"""

import json
import urllib.parse

API_HOST = "api.github.com"
RAW_HOST = "raw.githubusercontent.com"


class TreeError(Exception):
    """A directory listing could not be obtained or trusted."""


def tree_url(owner: str, repo: str, ref: str, path: str = "") -> str:
    """The Trees API URL for one directory, non-recursive.

    `path` empty means the repository root. The `<ref>:<path>` form is not
    URL-quoted as a whole: the colon is meaningful to the endpoint, and a
    path with spaces in it (every libretro platform directory has them)
    still has to arrive percent-encoded.
    """
    target = ref if not path else f"{ref}:{path}"
    return f"https://{API_HOST}/repos/{owner}/{repo}/git/trees/" + (
        urllib.parse.quote(target, safe=":/")
    )


def raw_url(owner: str, repo: str, ref: str, path: str) -> str:
    """The download URL for one file.

    `safe="/"` so the separators survive and everything else -- spaces,
    parentheses, commas, ampersands, all of which are ordinary in these
    filenames -- is percent-encoded.
    """
    return (
        f"https://{RAW_HOST}/{owner}/{repo}/{ref}/"
        + urllib.parse.quote(path, safe="/")
    )


def parse_tree(body: str, *, what: str) -> list[dict]:
    """The entries of one Trees API response, or a refusal that says why.

    Returns a list of `{"path", "type", "size"}` dicts -- `path` is the
    entry's name within the directory listed, not a full path from the
    repository root, because that is what the non-recursive form returns.

    A `truncated` response is refused rather than returned short. GitHub
    sets that flag when it could not fit the whole tree in one answer, and
    a catalogue that is quietly missing half its entries is worse than one
    that failed: nobody goes looking for what they were not told is
    absent.
    """
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise TreeError(f"GitHub's listing for {what} was not JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise TreeError(
            f"GitHub's listing for {what} was a {type(payload).__name__}, "
            f"expected an object"
        )

    # A 404 body is a JSON object with a "message" and no "tree".
    if "tree" not in payload:
        message = str(payload.get("message", "no tree in the response"))
        raise TreeError(f"GitHub could not list {what}: {message}")

    if payload.get("truncated"):
        raise TreeError(
            f"GitHub truncated its listing of {what}, so this plugin cannot "
            f"tell you the whole catalogue and will not show you part of it "
            f"as though it were all of it."
        )

    tree = payload["tree"]
    if not isinstance(tree, list):
        raise TreeError(f"GitHub's tree for {what} was not a list")

    entries = []
    for item in tree:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue
        size = item.get("size")
        entries.append(
            {
                "path": path,
                "type": str(item.get("type", "")),
                "size": size if isinstance(size, int) and not isinstance(size, bool)
                else None,
            }
        )
    return entries


def blobs(entries: list[dict], suffix: str = "") -> list[dict]:
    """Just the files, optionally only those ending in `suffix`.

    Case-insensitive on the suffix, because these repositories contain
    both `.cfg` and the occasional `.CFG`.
    """
    lowered = suffix.lower()
    return [
        e
        for e in entries
        if e["type"] == "blob" and (not suffix or e["path"].lower().endswith(lowered))
    ]


def subtrees(entries: list[dict]) -> list[dict]:
    """Just the directories."""
    return [e for e in entries if e["type"] == "tree"]
