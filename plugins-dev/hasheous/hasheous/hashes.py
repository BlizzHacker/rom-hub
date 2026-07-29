"""Where the hash comes from, and which one to ask with.

Hasheous is keyed by hash and by nothing else. Its four GET lookup routes
(`ByHash/md5`, `ByHash/sha1`, `ByHash/sha256`, `ByHash/crc`) take one hash
each, and there is no name search on the GET surface at all -- the
`hasheous_search_games` tool that would do that lives behind the MCP
JSON-RPC endpoint, which is a **POST**, and `ctx.http` offers `get()`
only. So a rom this plugin is asked about either arrives with a hash or
is refused.

**A hash is supplied, never derived.** The plugin cannot read the ROM: it
has no filesystem mount and `RomRef` carries `name`, `filename`,
`platform` and `size_bytes`, none of which is a hash. Two sources are
accepted, in this order:

1. `rom.extra["source_id"]` -- what `rom-hub enrich <plugin> <id>
   --source-id ...` puts there. `md5:<hex>`, `sha1:<hex>`,
   `sha256:<hex>`, `crc:<hex>` or a bare hex string whose *length* names
   the algorithm.
2. `rom.extra["md5"] / ["sha1"] / ["sha256"] / ["crc"]`, for a host that
   computed them. Nothing in RPP v1 requires a host to, and the reference
   host does not today, so this is a door left open rather than a
   dependency.

**Strongest first.** When several are available the plugin asks with the
longest, because the answer is only as trustworthy as the key: SHA-256
and SHA-1 identify a dump, and CRC-32 is 32 bits over a corpus of
millions of ROMs, where a collision is an ordinary event rather than a
theoretical one. CRC-32 is therefore refused unless `allow_crc32` is set,
and even then the platform cross-check is what catches the collision.
"""

import re

# Hex length -> the route that takes it, strongest first. The lengths are
# what make a bare `--source-id` unambiguous: no two of these collide.
BY_LENGTH: dict[int, str] = {64: "sha256", 40: "sha1", 32: "md5", 8: "crc"}

# Strongest first, which is the order enrich() tries them in.
ORDER: tuple[str, ...] = ("sha256", "sha1", "md5", "crc")

# What an operator may reasonably type. `crc32` is spelled out because it
# is what every other tool calls the thing hasheous routes as `crc`.
ALIASES: dict[str, str] = {
    "crc": "crc",
    "crc32": "crc",
    "md5": "md5",
    "sha1": "sha1",
    "sha-1": "sha1",
    "sha256": "sha256",
    "sha-256": "sha256",
}

_HEX = re.compile(r"\A[0-9a-fA-F]+\Z")


class BadHash(Exception):
    """A hash was offered that no hasheous route can take."""


def parse(text: str) -> tuple[str, str]:
    """`"md5:5D75..."` or `"5d75..."` -> `("md5", "5d75...")`.

    Lowercased on the way out. Hasheous stores its signature hashes
    upper-case and its own CLI sends them upper-case, and the server
    compares case-insensitively -- but a URL path component is the one
    place where "the server probably normalises it" is a bad thing to
    assume, so this normalises rather than hoping.
    """
    raw = (text or "").strip()
    if not raw:
        raise BadHash("no hash was given")

    prefix, sep, rest = raw.partition(":")
    if sep:
        kind = ALIASES.get(prefix.strip().lower())
        if kind is None:
            raise BadHash(
                f"{prefix.strip()!r} is not a hash hasheous can look up; "
                f"use one of {sorted(set(ALIASES))}"
            )
        digest = rest.strip()
        if not _HEX.fullmatch(digest):
            raise BadHash(f"{digest!r} is not hexadecimal")
        expected = next(n for n, k in BY_LENGTH.items() if k == kind)
        if len(digest) != expected:
            raise BadHash(
                f"a {kind} digest is {expected} hex characters; this one is "
                f"{len(digest)}"
            )
        return kind, digest.lower()

    if not _HEX.fullmatch(raw):
        raise BadHash(
            f"{raw!r} is neither a hash nor a <kind>:<hex> pair. Hasheous is "
            f"keyed by hash alone -- there is no name search on its GET API -- "
            f"so pass --source-id md5:<hex>, sha1:<hex>, sha256:<hex> or "
            f"crc:<hex>, or a bare digest of 8, 32, 40 or 64 hex characters."
        )
    kind = BY_LENGTH.get(len(raw))
    if kind is None:
        raise BadHash(
            f"a bare hash of {len(raw)} hex characters is not one hasheous "
            f"routes; it takes {sorted(BY_LENGTH)} characters for "
            f"{[BY_LENGTH[n] for n in sorted(BY_LENGTH)]}"
        )
    return kind, raw.lower()


def offered(extra: dict) -> list[tuple[str, str]]:
    """Every usable hash in a `RomRef.extra`, strongest first.

    Deduplicated by kind: `source_id` wins over a same-kind entry from the
    host, because the operator typed it for this one rom on purpose.
    """
    found: dict[str, str] = {}

    source_id = (extra.get("source_id") or "").strip()
    if source_id:
        # A malformed --source-id is the operator's typo and has to be
        # said out loud, so this one is allowed to raise.
        kind, digest = parse(source_id)
        found[kind] = digest

    for kind in ORDER:
        if kind in found:
            continue
        for key in (kind, "crc32" if kind == "crc" else kind):
            value = (extra.get(key) or "").strip()
            if not value:
                continue
            try:
                parsed_kind, digest = parse(f"{kind}:{value}")
            except BadHash:
                # A host-supplied field that is not a hash is ignored
                # rather than fatal: the operator did not type it, and
                # there may be a good hash sitting next to it.
                continue
            found[parsed_kind] = digest
            break

    return [(kind, found[kind]) for kind in ORDER if kind in found]
