"""Where a Demozoo production's file actually comes from.

Demozoo indexes productions; it does not host them. Every record carries a
`download_links` array of `{link_class, url}` pointing at somebody else's
archive, and the `link_class` is the only structured statement about which
archive that is. A sample of 48 records across seven platforms
(2026-07-29, honouring demozoo.org's `Crawl-delay: 10`) produced 32 links
across twelve hosts, of which twenty were `https` and twelve were plain
`http`.

Two of those hosts are supported, and the rest are refused **by name**.
That is not shyness -- it is the only shape that can work here, because
`FetchPlan` URLs are gated against this plugin's `network` allowlist by
the host and an undeclared archive would fail as an opaque policy
violation rather than as a sentence. So the allowlist and this table are
written from the same list, and a link on a host that is not in it is
refused here, with the host named, before a plan is ever built.


scene.org, and the redirect that is the whole problem
-----------------------------------------------------

`SceneOrgFile` links point at `https://files.scene.org/view/<path>`, which
is an HTML page rather than the file. The download entry point is
`/get/<path>` -- and that is where the trap is, because `/get/` picks a
mirror and answers `302`:

    /get/<path>          -> http://http.us.scene.org/pub/scene.org/<path>
    /get:us-http/<path>  -> http://http.us.scene.org/pub/scene.org/<path>
    /get:pl-http/<path>  -> http://http.pl.scene.org/pub/scene.org/<path>
    /get:de-https/<path> -> https://mirror.netcologne.de/scene.org/<path>
    /get:nl-https/<path> -> https://archive.scene.org/pub/<path>

All five verified live on 2026-07-29. Every hop is re-checked against the
allowlist, so the mirror is not an implementation detail the plugin can
ignore: an undeclared one breaks the download at the moment the bytes
would start arriving. Worse, the first two land on **plain http**, and
`rom_hub.netpolicy.ALLOWED_SCHEMES` is `{"https"}` -- so the default
`/get/` form cannot work at all, whatever is in the allowlist.

So the plugin pins `get:nl-https`, which resolves to exactly one https
host, and declares both ends of that hop. Pinning rather than declaring
the whole mirror pool: the pool is scene.org's business and can change,
and an allowlist naming a dozen hosts to cover a choice made at random is
a much larger permission than this plugin needs. Mirror *selection* is
still scene.org's -- the plugin asks files.scene.org for a mirror by
label, it does not construct an archive.scene.org URL itself.


Hosts refused, and why each one
-------------------------------

* **csdb.dk** (`BaseUrl`, 5 of 32 sampled links). Its `/robots.txt`
  carries `User-agent: ClaudeBot` / `Disallow: /` and the same for
  `anthropic-ai`, plus `Disallow: /release/download.php` for everybody.
  Nothing here was fetched from it and nothing here will be: a source
  that cannot be verified without breaching a crawl directive is not a
  source this plugin ships. This is the single biggest gap in coverage
  and it is a deliberate one.
* **ftp.amigascne.org** (`AmigascneFile`, 6 of 32). Its `/robots.txt` is
  `User-agent: *` / `Disallow: /` -- refused to everyone, not only to us.
  Its links are also plain `http`, and several have no file extension at
  all (`Skid_Row-Cr3DConstKit`).
* **files.zxdemo.org** (3 of 32). An S3 bucket that answers `403` to its
  own root; unverified, so unshipped.
* **Everything reached through `BaseUrl`.** That link class is Demozoo's
  "some URL", and the sample found it pointing at nine different hosts
  including personal sites. There is no allowlist that covers "anywhere",
  and one that tried would be the opposite of what the manifest's
  `network` list is for.
"""

import posixpath
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

#: files.scene.org mirror label to pin. Chosen because it is the one whose
#: 302 target is https; see the module docstring.
SCENE_ORG_MIRROR = "get:nl-https"

#: The hosts this plugin knows how to download from. Must stay in step
#: with `permissions.network` in manifest.toml -- there is a test for it.
SUPPORTED_HOSTS: frozenset[str] = frozenset({"files.scene.org", "fujiology.org"})

#: Hosts seen in the sample and deliberately not supported. The value is
#: the sentence an operator gets when a production has only these.
DECLINED_HOSTS: dict[str, str] = {
    "csdb.dk": (
        "csdb.dk's robots.txt disallows ClaudeBot and anthropic-ai entirely, "
        "and disallows /release/download.php for every crawler; this plugin "
        "will not work around that"
    ),
    "ftp.amigascne.org": (
        "ftp.amigascne.org's robots.txt is Disallow: / for every user agent, "
        "and its links are plain http, which the Hub refuses outright"
    ),
    "files.zxdemo.org": (
        "files.zxdemo.org answers 403 to its own root and could not be "
        "verified, so it is not shipped"
    ),
}


class NoUsableDownload(Exception):
    """No link on this production is one the plugin can fetch."""


@dataclass(frozen=True)
class Download:
    """A resolved, https, allowlisted download."""

    #: The URL to put in the FetchPlan.
    url: str
    #: The upstream basename, before sanitising.
    raw_name: str
    #: Which archive this came from, for the operator's benefit.
    host: str


def _basename(path: str) -> str:
    """The last path segment, percent-decoded.

    Decoded because scene.org percent-encodes spaces and the name an
    operator should see is `2.8K Nuance!.zip`, not `2.8K%20Nuance!.zip`.
    Whatever comes out still goes through `filenames.safe_filename` before
    it reaches a `FetchFile`, so decoding cannot widen anything -- a `%2F`
    that decodes to a separator is stripped there.
    """
    return unquote(posixpath.basename(path.rstrip("/")))


def _scene_org(parts) -> Download | None:
    """`files.scene.org/view/<path>` (or `/get.../<path>`) -> a pinned https get."""
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        return None
    head, rest = segments[0], segments[1:]
    if not (head == "view" or head == "get" or head.startswith("get:")):
        return None
    if not rest:
        return None
    path = "/".join(rest)
    return Download(
        url=f"https://files.scene.org/{SCENE_ORG_MIRROR}/{path}",
        raw_name=_basename(path),
        host="files.scene.org",
    )


def _direct(parts) -> Download | None:
    """A host that serves the file at the URL Demozoo already carries."""
    name = _basename(parts.path)
    if not name:
        return None
    return Download(
        url=f"https://{parts.hostname}{parts.path}",
        raw_name=name,
        host=parts.hostname,
    )


def resolve(links) -> Download:
    """The first link this plugin can actually fetch, or a refusal.

    Order is Demozoo's own. It puts the archive links a human would click
    first, and imposing a preference of our own would mean claiming to
    know which mirror is better, which we do not.
    """
    seen: list[str] = []
    for entry in links or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not isinstance(url, str) or not url:
            continue
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host:
            continue
        seen.append(f"{host} ({entry.get('link_class') or 'unknown class'})")
        if host not in SUPPORTED_HOSTS:
            continue
        # Scheme is not read from the link. Both supported hosts serve
        # https, the plugin rebuilds the URL as https, and the host would
        # refuse anything else anyway -- so an http link to a supported
        # host is upgraded rather than discarded.
        resolved = _scene_org(parts) if host == "files.scene.org" else _direct(parts)
        if resolved is not None:
            return resolved

    if not seen:
        raise NoUsableDownload(
            "Demozoo lists no download link for this production at all. That "
            "is common for old records -- the production is indexed but "
            "nobody has a copy."
        )

    declined = []
    for host, reason in DECLINED_HOSTS.items():
        if any(entry.startswith(host + " ") for entry in seen):
            declined.append(f"{host}: {reason}")
    detail = (
        "\n  " + "\n  ".join(declined)
        if declined
        else ""
    )
    raise NoUsableDownload(
        f"none of this production's download links is on a host this plugin "
        f"supports. It saw: {', '.join(sorted(set(seen)))}. It can fetch from "
        f"{', '.join(sorted(SUPPORTED_HOSTS))} and nothing else, because every "
        f"URL a plugin returns is checked against its manifest's own network "
        f"allowlist.{detail}"
    )
