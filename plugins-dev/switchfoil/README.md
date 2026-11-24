# rom-hub-switchfoil

A [ROM Hub](https://github.com/BlizzHacker/rom-hub) plugin for
[ROMarr](https://github.com/BlizzHacker/romarr) that searches Tinfoil and
CyberFoil shops, such as a self-hosted [Ownfoil](https://github.com/a1ex4/ownfoil),
and hands Nintendo Switch files to ROMarr's import.

The plugin is under development and is not yet in the ROM Hub catalogue.

## Authorised use

The plugin ships with no sources. It is a client for shops you run yourself
or are authorised to use, holding titles you are entitled to download.
Configure nothing else.

## What it does

For each configured shop the plugin reads the shop's index, matches the
query, and returns one result per file. Base titles, updates and DLC are
each labelled with their type and version, and NSP, NSZ, XCI and XCZ files
are separate results. The plugin does not compress or decompress anything.

A shop is read in the order CyberFoil uses. The plugin tries
`/api/remote/sections`, then `/api/shop/sections`, then the base URL as a
Tinfoil JSON index, following nested `directories`. It presents itself to
the shop as a Switch running CyberFoil 1.5.1, which is what Ownfoil needs
before it answers with JSON instead of its web page.

ROMarr shows a result's title and not its extra fields, so the title
carries the detail:

```text
Fixture Game [0100F1C700000800][v65536] UPD NSZ | Home | 1.4 GB smaller than Friend | behind latest v131072
```

The comparison appears when two results share a title ID and version. The
`behind latest` note appears for an update older than the newest version
listed in [titledb](https://github.com/blawar/titledb)'s `versions.json`,
which is read from `raw.githubusercontent.com` on each search that returns
an update. Set `titledb` to `false` to turn it off.

A query of 16 hex digits matches a title ID. A base title ID also returns
that title's updates and DLC. Any other query must match every word of the
name. Results are offered only when the platform is `switch` or unset.

## Install

Install the plugin with ROM Hub's CLI in the environment that runs ROMarr's
ROM Hub host, pinned to a release tag:

```bash
rom-hub plugin install https://github.com/MatthewDeSouza/rom-hub-switchfoil --ref v0.1.0
```

Then grant the shop hosts (below), restart ROMarr, and set `sources` on the
plugin's card in **Hub → Plugins**.

## Sources

`sources` holds one shop per line:

```text
name|url[|protocol][|hauth]
```

- **name**: a label shown in results. It must be unique, since a download
  finds its shop again by name, and it cannot contain `|`.
- **url**: the shop's base URL. Credentials go in the URL, as in
  `https://user:pass@shop.your-server.example`. Percent-encode `@`, `:`, `|`
  and `%` in the user name or password.
- **protocol**: `auto` (the default when empty), `cyberfoil` to use only the
  sections paths, or `tinfoil` to read only the base URL as a Tinfoil index.
- **hauth**: an Ownfoil Hauth value. See the next section.

For example:

```text
Home|https://dl-user:pass@shop.your-server.example
LAN|http://192.168.1.12:8465
Friend|https://friend.your-server.example/shop|tinfoil
```

Credentials in this field are stored unencrypted in the plugin
configuration. Use a download-only account with shop access and nothing
else, and never an Ownfoil admin account (see the next section).

The plugin removes the credentials from the URL before every request and
sends them as an `Authorization: Basic` header instead. They never appear
in a result's title, URL, ID or extra fields, or in a log or error message.
A search result stores the file's URL without credentials, and a download
reads the current `sources` setting again to build the header. The header
is sent only to the shop's own scheme, host and port, so a file or a
redirect on another origin is fetched without it.

## Network grants

The manifest declares only `raw.githubusercontent.com`. Shops are granted
by the operator with `ROM_HUB_NETWORK_SWITCHFOIL` on the ROMarr process.
A source whose host is not granted fails with an error naming this
variable, and the other sources still run.

```bash
ROM_HUB_NETWORK_SWITCHFOIL="shop.your-server.example,*.cdn.example.net,http://192.168.1.12:8465"
```

In Compose, set it in the ROMarr service's `environment`, then restart the
container.

- **HTTPS**: list a bare host, an IP address, or `*.suffix`. A wildcard does
  not cover the bare domain, so list both when you need both.
- **HTTP**: list the exact origin with its port, `http://IP:port`, on an
  RFC 1918, loopback or IPv6 unique-local address. A LAN shop reached by a
  name, including a `.local` name, must use HTTPS.
- **Downloads**: grant every host a file is served from. When a download
  redirects to another host, such as a CDN, that host needs a grant too,
  and rom-hub checks it at each redirect.

The grant comes from the environment rather than from plugin config because
config can be written by any API client, and with private `http` allowed a
config-derived grant would let that client direct requests at services on
the operator's network.

## Ownfoil over HTTPS and Hauth

Ownfoil can check a `Hauth` header on HTTPS requests from CyberFoil. The
real value is computed by a closed component of CyberFoil and the plugin
cannot reproduce it, so by default it sends `0`, as a CyberFoil build
without that component does. Ownfoil accepts this until it has recorded a
value for the host.

Ownfoil records the value sent by the first admin account to connect over
HTTPS. If the plugin used an admin account, Ownfoil would record `0` for
the host, and real CyberFoil clients would then fail the check. This is
one more reason to give the plugin a non-admin account.

Once Ownfoil has recorded a real value, because an admin connected from
CyberFoil over HTTPS, it refuses the plugin with `Incorrect Hauth`. The
value depends only on the scheme and host, so copy the one Ownfoil stored
for the host, at `shop.clients.cyberfoil.hauth` in its settings, into the
fourth field of the source line:

```text
Home|https://dl-user:pass@shop.your-server.example||0123ABCD...
```

Plain HTTP requests are not checked, and nothing is needed there.

## Unsupported

- Binary `TINFOIL` indexes, whether zstd-compressed, zlib-compressed or
  encrypted. rom-hub hands the plugin text only, so the source fails with a
  message naming the kind. Serve plain JSON instead. Ownfoil already does
  this for CyberFoil.
- Indexes over rom-hub's 4 MiB response limit. The source fails with a
  message saying so.
- Redirects when reading an index. Use the final URL in the source line.
- `gdrive:` and `jbod:` entries, and URLs other than `http` and `https`.
  They are skipped.
- Request headers that an index declares for its downloads, and
  credentials written into a shop's own index. Both are ignored.
- More than 32 nested directories, or nesting deeper than 8 levels, per
  source.
- Caching. Each search reads every source again within ROMarr's 90 second
  search budget.

## Development

The Nix flake provides Python 3.12 with ROM Hub at the revision ROMarr's
Docker image pins, along with `just`, `ruff` and `gh`.

```bash
nix develop
just ci          # lint, test, check-version
nix flake check  # the suite on Python 3.12 and 3.13
```

`just fmt` formats and applies lint fixes. `just check-version` compares
`[plugin] version` in `manifest.toml` against the latest `v*` tag and
passes while there is no tag.

### Testing against a fixture shop

`tools/fixture_shop.py` is a small fake shop using only the standard
library. It binds `127.0.0.1` and serves a CyberFoil sections response, a
Tinfoil index with one nested directory, and Ownfoil-style
`/api/download/<token>` routes, optionally behind Basic auth. One download
answers with a 302 to a second port, so a redirect to another origin can be
tested. The files are a few kilobytes of generated bytes under made-up
names and title IDs.

The suite starts it on free ports and runs the whole path, so `just test`
covers it:

```bash
nix develop -c python3 -m unittest discover -s tests -p 'test_fixture_shop.py' -v
```

That test runs the plugin as a subprocess through rom-hub's
`PluginProcess`, with `ctx.http` going through rom-hub's broker fetcher and
the `NetworkPolicy` built from `ROM_HUB_NETWORK_SWITCHFOIL`. It downloads
the plan with rom-hub's `HttpDownloader` under the same policy. It checks
the downloaded bytes, the `Authorization` header the shop received, a
redirect that passes with both origins granted, and the same redirect
refused when only the shop is granted. On macOS the subprocess runs
without seccomp, which rom-hub supports only on Linux.

To run the shop by hand against a real ROMarr or `rom-hub` on the same
machine:

```bash
python3 tools/fixture_shop.py --port 8465 --mirror-port 8466 --user fixture-user --password fixture
```

It prints the two origins and the matching grant:

```text
shop   http://127.0.0.1:8465
mirror http://127.0.0.1:8466
ROM_HUB_NETWORK_SWITCHFOIL=http://127.0.0.1:8465,http://127.0.0.1:8466
```

Set that variable on the host, restart it, and add this source line:

```text
Fixture|http://fixture-user:fixture@127.0.0.1:8465
```

A search for `fixture game` returns four files. Importing the
`Fixture Game [0100F1C700000000][v0].nsz` result follows the redirect to the
mirror port. Grant only the first origin to see that download refused.

### Testing against a real Ownfoil

`tests/test_ownfoil.py` runs the same search, plan and download path
against a real Ownfoil server. It is skipped unless
`SWITCHFOIL_OWNFOIL_URL` is set. The CI job `ownfoil` starts Ownfoil 2.5.0
in Docker, pinned by digest, and runs it there. That job is not part of the
required `ci` check.

The library holds the fixture shop's dummy files, written by
`tools/seed_ownfoil.py`. Without Switch keys, Ownfoil identifies a file
from its name alone and does not verify its contents, so generated bytes
under Switch-style names are enough. The test waits up to
`SWITCHFOIL_OWNFOIL_WAIT` seconds (300 by default) for them to be listed,
since Ownfoil updates TitleDB before its first library scan.

To run it on a machine with Docker:

```bash
nix develop -c python3 tools/seed_ownfoil.py /tmp/ownfoil-games
```

```bash
docker run -d --name ownfoil -p 127.0.0.1:8465:8465 -v /tmp/ownfoil-games:/games -e USER_ADMIN_NAME=fixture-admin -e USER_ADMIN_PASSWORD=fixture-admin-pass -e USER_GUEST_NAME=fixture-guest -e USER_GUEST_PASSWORD=fixture-guest-pass a1ex4/ownfoil@sha256:123130aad6a260b1eeacf3968b6b56fbf510662034faab2f4cd2da08d967f3f7
```

```bash
SWITCHFOIL_OWNFOIL_URL=http://127.0.0.1:8465 SWITCHFOIL_OWNFOIL_USER=fixture-guest SWITCHFOIL_OWNFOIL_PASSWORD=fixture-guest-pass nix develop -c just ownfoil-test
```

The test uses the guest account, which has shop access and is not an
admin, for the reason given under Hauth above. The admin account exists
only because Ownfoil refuses to create a guest before an admin.

## Licence

MIT. See `LICENSE`.
