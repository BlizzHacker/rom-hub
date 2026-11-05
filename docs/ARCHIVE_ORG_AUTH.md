# Archive.org authentication

ROM Hub owns Archive.org login and supplies authentication to host HTTP requests
for the `archive-org` plugin. The same implementation serves the standalone CLI
and ROMarr. The plugin protocol, plugin configuration and search results contain
no credentials or cookies. ROMarr continues to use live plugin search; building a
CLI census does not make ROMarr consume that catalogue.

## Standalone CLI

Run these in the virtual environment that contains this ROM Hub version:

```sh
rom-hub auth login archive-org
rom-hub auth status archive-org
rom-hub search "Final Fantasy VIII" --platform psx --live
rom-hub census build archive-org --units --verbose
rom-hub census report archive-org --units
rom-hub search "Final Fantasy VIII" --platform psx
```

Login prompts for an email and a hidden password, verifies the login, then saves
one credential record. A failed login preserves the previous saved record.
Passwords are never command-line arguments. Status is local: it reports the ROM
Hub home, credential source and store, without contacting Archive.org or printing
secret values. Successful login proves account authentication; it does not prove
that a particular restricted ZIP is readable. Check the live results and census
unit failures separately.

`ROM_HUB_HOME` selects the state directory (normally `~/.rom-hub`). Keep the same
home and secret-store settings for login, search, census and import. Existing
ROMarr credentials under that home work without migration. A different user or
home has separate state. No ROMarr installation or running service is required.

```sh
rom-hub auth logout archive-org
```

Logout deletes this home's saved record and invalidates its session in the
current process. It does not revoke other Archive.org sessions or log another
running process out immediately. New operations reread credentials, including
changed external credential files; an operation already running may finish with
its existing session.

A census reads metadata and ZIP member listings, not ROM payloads or entire ZIPs.
Rebuilding uses the existing resumable census implementation: failed units are
retried, but collection units with changed or unknown totals can be enumerated
again. There is no failed-units-only command. A partial catalogue remains
searchable; inspect the report before treating it as complete. Searching without
`--live` uses catalogue matches when available, and falls back to live search
where necessary. Catalogue matches need no login.

For an actual download, use the exact source identifier from the selected result:

```sh
rom-hub import archive-org 'EXACT_SOURCE_ID' --platform psx
```

Import requires a configured library backend and writes/downloads the selected
payload. It obtains a fresh plugin plan and authenticates both planning requests
and payload requests. Merely finding a cached result does not prove its download
is accessible.

## External credentials

Set both `ROM_HUB_ARCHIVE_ORG_EMAIL_FILE` and
`ROM_HUB_ARCHIVE_ORG_PASSWORD_FILE` to readable secret files. Alternatively set
`ROM_HUB_ARCHIVE_ORG_EMAIL` and `ROM_HUB_ARCHIVE_ORG_PASSWORD`; environment values
are more readily exposed by process inspection. Files take precedence per field.

The legacy `ROMARR_ARCHIVE_ORG_*` names remain supported. If any new-prefix
variable is present, only that family is used; otherwise the legacy family is
used, then the saved record. Partial or invalid external configuration fails
rather than mixing accounts or falling back anonymously. Login/logout refuse
saved-record changes while external credentials are configured. External values
are never copied into the store. Do not put passwords in the Nix store.

## Storage and request boundaries

The existing `archive-org-host-auth.credentials` secret record stores email and
password together. `ROM_HUB_SECRET_STORE=auto` uses a usable native keyring or the
existing encrypted file store. The file store writes owner-only `secrets.json`
and, by default, `secret.key` under the selected home. A key next to ciphertext
protects against accidental disclosure, not a reader of both files. Supply
`ROM_HUB_SECRET_KEY` externally or use a native keyring for stronger separation.

Only the Archive.org plugin receives an authenticated HTTP client. Cookies are
held in process memory and attached only to HTTPS `archive.org` and numbered
`ia*.us.archive.org` / `ia*.ca.archive.org` storage hosts. Existing URL allowlists
and redirect checks still apply. Other plugins use anonymous clients even if
their own allowlist permits Archive.org. Plugin processes do not receive the
credentials through their environment or protocol; the sandbox does not prevent
malicious plugins from reading files accessible to the same OS account.

Sessions renew before known expiry. A 401 or 403 triggers one login renewal and
one retry; a second refusal reaches the plugin. Its item-level 403 handling then
stops further ZIP probes for that item. No credentials means anonymous access;
configured credentials that cannot be used produce an error. Cookies are not
persisted, and a login does not override an item's access restrictions.
