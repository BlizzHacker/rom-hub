#!/usr/bin/env bash
# First-boot accounts for the disposable stack in proof-stack.compose.yml.
#
#     bash scripts/proof-stack-bootstrap.sh
#
# Neither RomM nor Gaseous will answer an API call until somebody has
# logged in once, and both make the very first account a special case that
# no ordinary API call can create. This does that once, with the throwaway
# credentials the compose file and scripts/proof_matrix.py both assume.
#
# Retrom is absent on purpose: it has no accounts at all, which is why
# `RetromBackend.authenticate()` only proves reachability.
#
# Idempotent -- a second run reports the accounts already exist and
# succeeds, so it is safe to re-run while waiting for a slow first boot.
set -uo pipefail

ROMM_URL="${ROMM_URL:-http://127.0.0.1:8085}"
ROMM_USER="${ROMM_USER:-proof}"
ROMM_PASSWORD="${ROMM_PASSWORD:-proofproof1}"

GASEOUS_URL="${GASEOUS_URL:-http://127.0.0.1:8086}"
GASEOUS_USER="${GASEOUS_USER:-proof@proof.invalid}"
GASEOUS_PASSWORD="${GASEOUS_PASSWORD:-Proofproof1!}"

jar="$(mktemp)"
trap 'rm -f "$jar"' EXIT

wait_for() {
    local name="$1" url="$2" tries=60
    printf 'waiting for %s at %s ' "$name" "$url"
    while (( tries-- > 0 )); do
        if curl -fsS -m 5 -o /dev/null "$url" 2>/dev/null; then
            printf ' up\n'
            return 0
        fi
        printf '.'
        sleep 5
    done
    printf ' TIMED OUT\n' >&2
    return 1
}

# -- RomM -----------------------------------------------------------------
#
# RomM guards every mutating call with a double-submit CSRF cookie, so the
# first account needs a GET to mint `romm_csrftoken` and the same value
# echoed back in the `x-csrftoken` header.
wait_for RomM "$ROMM_URL/api/heartbeat" || exit 1

if curl -fsS -m 10 --user "$ROMM_USER:$ROMM_PASSWORD" \
        -o /dev/null "$ROMM_URL/api/users/me" 2>/dev/null; then
    echo "RomM: $ROMM_USER already exists"
else
    curl -fsS -m 10 -c "$jar" -o /dev/null "$ROMM_URL/api/heartbeat"
    token="$(awk '$6 == "romm_csrftoken" { print $7 }' "$jar")"
    if [[ -z "$token" ]]; then
        echo "RomM: could not read romm_csrftoken from the cookie jar" >&2
        exit 1
    fi
    code="$(curl -s -m 20 -b "$jar" -H "x-csrftoken: $token" \
        -H 'Content-Type: application/json' \
        -o /tmp/proof-romm-user.json -w '%{http_code}' \
        -X POST "$ROMM_URL/api/users" -d "$(printf '{"username":"%s","email":"%s","password":"%s","role":"admin"}' \
            "$ROMM_USER" "$ROMM_USER@proof.invalid" "$ROMM_PASSWORD")")"
    if [[ "$code" != "200" && "$code" != "201" ]]; then
        echo "RomM: creating $ROMM_USER failed with HTTP $code:" >&2
        cat /tmp/proof-romm-user.json >&2
        exit 1
    fi
    echo "RomM: created $ROMM_USER (admin)"
fi

# A platform, because none of the three will invent one.
#
# `LibraryBackend.platform_id()` resolves a name and raises when there is
# no match -- deliberately, since filing a ROM under a platform the
# operator did not choose is the kind of wrong that is not noticed for
# months. So the platform has to exist before the matrix runs, and each
# server makes one in a different way.
#
# RomM: a directory under the library root plus a `POST /api/platforms`,
# which is the only one of the three with an API that creates a platform
# record outright.
#
# Guarded, because `POST /api/platforms` is not idempotent: it happily
# creates a *second* row with the same slug and fs_slug. Two platforms
# called `nes` is worse than none -- the scan files the uploaded ROM under
# one of them and `platform_id()` resolves to the other, so the import
# uploads successfully and then fails its own post-condition with a message
# about the ROM not landing. That is a full hour of looking in the wrong
# place; do not remove this check.
docker exec proofromm mkdir -p /romm/library/roms/nes
if curl -fsS -m 20 -u "$ROMM_USER:$ROMM_PASSWORD" "$ROMM_URL/api/platforms" \
        2>/dev/null | grep -q '"fs_slug":"nes"'; then
    echo "RomM: platform 'nes' already present"
elif curl -fsS -m 20 -u "$ROMM_USER:$ROMM_PASSWORD" \
        -H 'Content-Type: application/json' \
        -o /dev/null "$ROMM_URL/api/platforms" -d '{"fs_slug":"nes"}' 2>/dev/null; then
    echo "RomM: platform 'nes' created"
else
    echo "RomM: could not create platform 'nes'" >&2
fi

# -- Gaseous --------------------------------------------------------------
#
# Gaseous seeds no account at all: `Users` is empty on a fresh database and
# every API call 302s to a login page. The web UI's first-run screen posts
# to `FirstSetup/0`, which is the only route that will create a user
# without one, so that is what is called here. The password must be at
# least ten characters and the username must look like an e-mail address --
# Gaseous validates both, which is why `GaseousClient.authenticate()` has a
# dedicated hint for the second one.
wait_for Gaseous "$GASEOUS_URL/api/v1.1/System/Version" || exit 1

setup="$(curl -s -m 30 -X POST "$GASEOUS_URL/api/v1.1/FirstSetup/0" \
    -H 'Content-Type: application/json' \
    -d "$(printf '{"userName":"%s","email":"%s","password":"%s","confirmPassword":"%s"}' \
        "$GASEOUS_USER" "$GASEOUS_USER" "$GASEOUS_PASSWORD" "$GASEOUS_PASSWORD")")"
case "$setup" in
    *'"succeeded":true'*) echo "Gaseous: created $GASEOUS_USER" ;;
    *) echo "Gaseous: FirstSetup answered $setup (already set up is fine)" ;;
esac

# Gaseous' platform list is the hardest of the three, and the reason the
# matrix uses NES rather than DOS.
#
# `GET /Platforms` does not list the platforms Gaseous *knows about* -- it
# lists the ones already represented in the library. Gaseous decides a
# file's platform from the file, so the only way to make a platform appear
# is to give it a file it recognises as that platform. `.nes` is on the NES
# entry's `supportedFileExtensions` in the built-in platform map; DOS' list
# is empty, so no DOS platform can be conjured at all.
#
# The metadata behind that map comes from IGDB. Without
# PROOF_IGDB_CLIENT_ID / PROOF_IGDB_CLIENT_SECRET in a `.env` beside the
# compose file, Gaseous ingests no platform metadata and this seed cannot
# resolve either.
docker exec proofgaseous sh -c \
    'printf "NES\032\001\001" > "/root/.gaseous-server/Data/Import/proof-seed.nes"' \
    2>/dev/null || echo "Gaseous: could not write the import seed"
echo "Gaseous: seeded an import; its ImportQueueProcessor runs on a timer"

# -- Retrom ---------------------------------------------------------------
#
# Retrom has no CreatePlatform RPC at all: a platform comes into being when
# a scan finds a *directory* inside a content directory. An empty directory
# is not enough either -- UpdateLibrary answers INTERNAL "No library
# content found" -- so one file is seeded. It is named for what it is, so
# nobody mistakes it for something the matrix uploaded.
RETROM_LIBRARY=/app/data/library

# The content directory has to be declared in config.json: the service
# rewrites that file from defaults at boot, so the environment cannot say
# it. `storageType: 0` is SINGLE_FILE_GAME -- see the compose file for why
# the default of 1 makes an import unconfirmable.
if ! docker exec proofretrom grep -q '"storageType": 0' /app/config/config.json 2>/dev/null; then
    docker exec -u root proofretrom sh -c "cat > /app/config/config.json <<'JSON'
{
  \"connection\": null,
  \"contentDirectories\": [
    { \"path\": \"$RETROM_LIBRARY\", \"storageType\": 0 }
  ],
  \"igdb\": null,
  \"steam\": null,
  \"saves\": { \"maxSaveFilesBackups\": 5, \"maxSaveStatesBackups\": 5 },
  \"telemetry\": null,
  \"metadata\": { \"storeMetadataLocally\": false }
}
JSON"
    docker exec -u root proofretrom chown retrom:retrom /app/config/config.json
    docker restart proofretrom >/dev/null
    echo "Retrom: config.json installed (SINGLE_FILE_GAME at $RETROM_LIBRARY); restarted"
fi

# After a restart the port answers well before the gRPC services are
# mounted, so a scan fired at the first successful connect is accepted and
# dropped -- and the only symptom is a platform that never appears. The
# wait is generous for that reason. If it is still not there, re-run this
# script: it is idempotent, and the second pass costs seconds.
wait_for Retrom "${RETROM_URL:-http://127.0.0.1:5102}/" || true
sleep 20

# Owned by `retrom`, not root: the service writes uploads here as its own
# user, and a root-owned directory turns every WebDAV PUT into a 403 that
# reads like an upload bug.
docker exec -u root proofretrom mkdir -p "$RETROM_LIBRARY/nes"
docker exec -u root proofretrom sh -c \
    "test -f $RETROM_LIBRARY/nes/proof-seed.nes || printf 'NES\\032\\001\\001' > $RETROM_LIBRARY/nes/proof-seed.nes"
docker exec -u root proofretrom chown -R retrom:retrom "$RETROM_LIBRARY"

# And scan it, so the platform exists before anything asks for it.
# `UpdateLibraryRequest` is empty, so the whole gRPC-Web body is one
# five-byte frame of zeroes: a compression flag and a big-endian length of
# 0. It has to arrive down a pipe -- a bash `$'\x00...'` string is
# truncated at the first NUL, which sends an empty body and gets back
# "Missing request message" with an HTTP 200 in front of it.
#
# `x-grpc-web: 1` is not optional: tonic-web uses it to tell a gRPC-Web
# client from something else posting the same content type, and without it
# the request is routed away and answered with a 200 that did nothing.
printf '\0\0\0\0\0' | curl -s -m 120 -o /tmp/proof-retrom-scan.bin \
    -X POST "${RETROM_URL:-http://127.0.0.1:5102}/retrom.LibraryService/UpdateLibrary" \
    -H 'content-type: application/grpc-web+proto' \
    -H 'accept: application/grpc-web+proto' \
    -H 'x-grpc-web: 1' \
    --data-binary @-
echo "Retrom: $RETROM_LIBRARY/nes seeded and scanned"

echo
echo "Bootstrap complete."
echo "  ROMM_URL=$ROMM_URL ROMM_USER=$ROMM_USER ROMM_PASSWORD=$ROMM_PASSWORD"
echo "  GASEOUS_URL=$GASEOUS_URL GASEOUS_USER=$GASEOUS_USER"
echo "  RETROM_URL=http://127.0.0.1:5102"
