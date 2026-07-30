# Gaseous API contract, captured from live servers

`api-contract.json` is a recording, not a design. Every entry is one real
request/response pair taken from a running gaseous-server, with `curl`,
against a throwaway container. It exists because a mocked test that
replays this project's *own* assumption about a request shape cannot
notice when the server stops agreeing -- which is exactly how the
`POST /Games` 400 recorded here reached a release with a green suite.

## What was captured, and against what

Two containers, both disposable, neither on a default port:

| `generation` key | Image |
|---|---|
| `1.7.14.0` | `ghcr.io/gaseous-project/gaseousserver:latest` |
| `2.0.0.0` | `ghcr.io/gaseous-project/gaseousserver:v2.0.0-rc.3` |

`generation` is the string the server itself answers at
`GET /api/v1.1/System/Version` -- the same pin `docs/PROOF.md` uses -- and
not a tag, because `:latest` moves and a recording that named it would
stop meaning anything.

Each case is `{"request": ..., "status": ..., "response": ...}`, where
`request` is the JSON body sent (absent for the multipart and empty-filter
cases, which have nothing interesting to record) and `response` is the
body verbatim.

## The cases and what each one proves

| case | 1.7.14.0 | 2.0.0.0 |
|---|---|---|
| `games.minimal-2.0-body` | **400** — Genre, Theme, GameMode, Platform, PlayerPerspective | 200 |
| `games.union-body` | 200 | 200 |
| `games.union-body-with-nulls` | **400**, same five | 200 |
| `games.union-body-plus-unknown-field` | 200 | 200 |
| `games.no-sorting` | 200 | **400** — Sorting |
| `roms.multipart-field-file` | 200 `{"count":0,"size":0}` — stored nothing | 200, a bare session GUID |
| `roms.multipart-field-files` | 200 `{"count":1,...}` | **400** — file |
| `roms.imports` | **404** — no such route | 200, the queue |

Read together those rows say three things the source alone does not make
obvious:

1. **Neither generation's minimal body is accepted by the other.** 1.7.x
   demands the five list filters; 2.0 demands `Sorting`. The union of the
   two is accepted by both, which is what `client._MATCH_EVERYTHING`
   sends.
2. **`null` is not "unset".** An explicit null fails 1.7.x's validation
   exactly as an absent key does, so the empty value has to be `[]`.
3. **1.7.x answers the 2.0 upload with a 200 that stored nothing.** That
   is the one shape in this file that is dangerous rather than merely
   different, and `client._reject_1_7_upload_response` exists for it.

## Two fields were normalised

The recording is verbatim except that `traceId` (a per-request id, noise)
is dropped, and in `roms.imports` the throwaway account's `userId` and the
container-internal upload paths are replaced with placeholders. Nothing
else is edited; if a shape here looks wrong, it is what the server said.

## Re-capturing

Stand up either image, complete `POST /api/v1.1/FirstSetup/0`, log in for
the cookie, then replay the `request` bodies above against
`POST /api/v1.1/Games` and the two multipart variants against
`POST /api/v1.1/Roms`. Add the new generation as another key rather than
editing an existing one -- a recording that gets overwritten stops being
evidence of what an older server did.
