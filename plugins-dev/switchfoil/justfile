default: ci

# Run the unittest suite. It reaches no network beyond loopback.
test:
    python3 -m unittest discover -s tests -v

lint:
    ruff check .
    ruff format --check .

fmt:
    ruff format .
    ruff check --fix .

# The manifest version must match the latest v* tag. No tag yet passes.
check-version:
    #!/usr/bin/env bash
    set -euo pipefail
    manifest=$(python3 -c 'import tomllib; print(tomllib.load(open("manifest.toml", "rb"))["plugin"]["version"])')
    tag=$(git tag --list 'v*' --sort=-v:refname | head -n 1)
    if [ -z "$tag" ]; then
        echo "manifest version $manifest, no v* tag yet"
        exit 0
    fi
    if [ "${tag#v}" != "$manifest" ]; then
        echo "manifest.toml says $manifest but the latest tag is $tag" >&2
        exit 1
    fi
    echo "manifest version $manifest matches $tag"

ci: lint test check-version

# Run tests/test_ownfoil.py against a running Ownfoil. Needs
# SWITCHFOIL_OWNFOIL_URL, SWITCHFOIL_OWNFOIL_USER and SWITCHFOIL_OWNFOIL_PASSWORD.
# Fails rather than skipping when the URL is unset, so a job cannot pass empty.
ownfoil-test:
    #!/usr/bin/env bash
    set -euo pipefail
    if [ -z "${SWITCHFOIL_OWNFOIL_URL:-}" ]; then
        echo "SWITCHFOIL_OWNFOIL_URL is not set" >&2
        exit 1
    fi
    python3 -m unittest discover -s tests -p test_ownfoil.py -v
