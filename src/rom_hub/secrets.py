"""Where a `secret` config value lives, and what that actually protects.

RPP v1 specifies a `secret` config type for credentials. This module is the
storage behind it. Everything here exists to keep one promise:

> **A value typed `secret` is never written to the Hub's plain config.**

`state.json` holds a plugin's ordinary settings and is exactly the file an
operator opens, screenshots, pastes into an issue, or sweeps up with
`git add -A`. A credential in it is a credential in all of those. So a
secret goes somewhere else, and the CLI, the job queue and every error
message the host builds redact it on the way out.

## The threat model, stated before the mechanism

The threat is **accidental disclosure**: a log line, a screenshot, a config
file in a public repo, a support paste, a backup that goes somewhere it
should not. It is *not* a plugin stealing its own key — a plugin already
runs arbitrary code and is handed the value because it needs it to make its
request, which is the whole point of configuring one. And it is not an
attacker with a shell as the operator: nothing a user-level process can
reach is secret from a user-level process.

Saying that plainly matters more than the cipher, because the honest
description of the default file store is **obfuscation, not secrecy** — see
`FileStore` below.

## Two stores

`keyring`, when the `keyring` package is installed *and* reports a usable
backend. Protection is whatever the OS gives: a locked login keychain is a
real boundary; a desktop keyring unlocked at login is readable by anything
running as you.

`file`, otherwise, and this is the one that matters — the Hub's primary
deployment is headless in Docker on Linux, where there is no keyring at all
and a keyring-only design would simply fail. `<root>/secrets.json` holds
authenticated ciphertext; the key comes from one of two places:

* `ROM_HUB_SECRET_KEY` in the environment — supplied from outside the box
  (a Docker secret, a systemd credential, a password manager). The
  ciphertext at rest is then genuinely unreadable without it.
* a generated `<root>/secret.key`, written next to `secrets.json` — the
  zero-configuration default. **This is obfuscation.** Anything that can
  read one file can read the other. What it buys is real but narrow: the
  value is not in `state.json`, so it is not in the file people open, dump,
  screenshot or commit. It does not survive somebody reading the directory.

That distinction is printed by `rom-hub plugin secret list`, so an operator
is never guessing which one they have.

## The cipher, and why it is stdlib

scrypt for the KDF, an HMAC-SHA256 counter-mode keystream for
confidentiality, and an encrypt-then-MAC HMAC-SHA256 tag with an
independent key for integrity — all from `hashlib`/`hmac`. No `cryptography`
dependency is taken for this, because a compiled dependency would not
change the honest answer above: in the default configuration the key is
next to the ciphertext, and no cipher fixes that. When the key *is* supplied
from outside, this construction is sound for the job (authenticated
encryption of a short value at rest).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as _stdlib_secrets
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import env

SECRETS_FILENAME = "secrets.json"
KEYFILE_NAME = "secret.key"

#: The service name a keyring entry is filed under.
KEYRING_SERVICE = "rom-hub"

#: What `ROM_HUB_SECRET_STORE` accepts.
STORE_KINDS = ("auto", "keyring", "file")

#: scrypt parameters. n=2**14 keeps a CLI invocation under ~100 ms while
#: costing 16 MiB, which is the usual interactive trade.
_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_DK_LEN = 64  # 32 bytes of encryption key + 32 bytes of MAC key

_FILE_VERSION = 1

#: Values shorter than this are not scrubbed from output. A one- or
#: two-character "secret" would turn redaction into corruption of every
#: message that happened to contain that letter, and nothing that short is
#: a credential.
MIN_REDACTABLE_LENGTH = 4

REDACTED = "***"


class SecretError(Exception):
    """A secret could not be read, written, or decrypted."""


# -- honest descriptions -------------------------------------------------


@dataclass(frozen=True)
class StoreInfo:
    """What the active store is and what it really protects.

    `protection` is written to be printed verbatim to an operator. It is
    deliberately not marketing: the default file store says the word
    "obfuscation" about itself.
    """

    kind: str
    detail: str
    protection: str
    #: True only when the ciphertext at rest is unreadable to somebody who
    #: can read the Hub's directory. The generated-key file store is False.
    at_rest_secret: bool


# -- the file store ------------------------------------------------------


def _derive(master: bytes, salt: bytes) -> tuple[bytes, bytes]:
    material = hashlib.scrypt(
        master, salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_DK_LEN
    )
    return material[:32], material[32:]


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    """HMAC-SHA256 in counter mode. A block is HMAC(key, nonce || counter)."""
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(
            key, nonce + counter.to_bytes(4, "big"), hashlib.sha256
        ).digest()
        counter += 1
    return bytes(out[:length])


def _seal(enc_key: bytes, mac_key: bytes, plaintext: bytes) -> dict:
    nonce = _stdlib_secrets.token_bytes(16)
    ciphertext = bytes(
        a ^ b for a, b in zip(plaintext, _keystream(enc_key, nonce, len(plaintext)))
    )
    tag = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    return {
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
        "tag": tag.hex(),
    }


def _open(enc_key: bytes, mac_key: bytes, entry: dict, label: str) -> str:
    try:
        nonce = bytes.fromhex(entry["nonce"])
        ciphertext = bytes.fromhex(entry["ciphertext"])
        tag = bytes.fromhex(entry["tag"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SecretError(f"the stored secret {label} is malformed: {exc}") from exc
    expected = hmac.new(mac_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        raise SecretError(
            f"the stored secret {label} did not authenticate. Either the file "
            f"was modified, or it was written with a different key -- if you "
            f"set ROM_HUB_SECRET_KEY after storing it, or changed the value, "
            f"the old ciphertext cannot be read back. Set the secret again."
        )
    return bytes(
        a ^ b for a, b in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext)))
    ).decode("utf-8")


def _write_private(path: Path, text: str) -> None:
    """Write `text` to `path`, owner-only, replacing atomically.

    The permission bits are set on the temporary file *before* the rename,
    so there is no window in which the final path exists world-readable.
    On Windows `chmod` only moves the read-only bit; that is stated in the
    docs rather than papered over.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class FileStore:
    """Authenticated ciphertext in `<root>/secrets.json`.

    Read `KEY_SOURCE_*` protection strings below before describing this to
    anyone: with a generated key file it is obfuscation, and it says so.
    """

    kind = "file"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / SECRETS_FILENAME
        self.keyfile = self.root / KEYFILE_NAME
        # Keyed by salt: scrypt is deliberately expensive, so deriving once
        # per process is worth caching -- but a cache that ignored the salt
        # would silently encrypt under the wrong key if the store file were
        # replaced underneath a long-lived instance.
        self._cached: dict[bytes, tuple[bytes, bytes]] = {}

    # -- key material ----------------------------------------------------

    def _passphrase(self) -> tuple[bytes, str]:
        """The master secret, and where it came from."""
        supplied = env.get("ROM_HUB_SECRET_KEY")
        if supplied:
            return supplied.encode("utf-8"), "env"
        return self._local_key(), "local-file"

    def _local_key(self) -> bytes:
        if self.keyfile.exists():
            try:
                return bytes.fromhex(self.keyfile.read_text(encoding="utf-8").strip())
            except (OSError, ValueError) as exc:
                raise SecretError(
                    f"cannot read the local secret key at {self.keyfile}: {exc}"
                ) from exc
        key = _stdlib_secrets.token_bytes(32)
        _write_private(self.keyfile, key.hex())
        return key

    def key_source(self) -> str:
        return self._passphrase()[1]

    def _keys(self, salt: bytes) -> tuple[bytes, bytes]:
        if salt not in self._cached:
            master, _ = self._passphrase()
            self._cached[salt] = _derive(master, salt)
        return self._cached[salt]

    # -- the document ----------------------------------------------------

    def _read(self) -> dict:
        if not self.path.exists():
            return {
                "version": _FILE_VERSION,
                "salt": _stdlib_secrets.token_bytes(16).hex(),
                "entries": {},
            }
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SecretError(f"cannot read {self.path}: {exc}") from exc
        if not isinstance(doc, dict) or doc.get("version") != _FILE_VERSION:
            raise SecretError(
                f"{self.path} is not a secret store this version understands "
                f"(expected version {_FILE_VERSION})"
            )
        doc.setdefault("entries", {})
        return doc

    def _save(self, doc: dict) -> None:
        _write_private(self.path, json.dumps(doc, indent=2, sort_keys=True))

    # -- the interface ---------------------------------------------------

    def get(self, slug: str, key: str) -> str | None:
        doc = self._read()
        entry = doc["entries"].get(_name(slug, key))
        if entry is None:
            return None
        enc, mac = self._keys(bytes.fromhex(doc["salt"]))
        return _open(enc, mac, entry, f"{slug}.{key}")

    def set(self, slug: str, key: str, value: str) -> None:
        doc = self._read()
        enc, mac = self._keys(bytes.fromhex(doc["salt"]))
        doc["entries"][_name(slug, key)] = _seal(enc, mac, value.encode("utf-8"))
        self._save(doc)

    def delete(self, slug: str, key: str) -> bool:
        doc = self._read()
        if doc["entries"].pop(_name(slug, key), None) is None:
            return False
        self._save(doc)
        return True

    def info(self) -> StoreInfo:
        source = self.key_source()
        if source == "env":
            return StoreInfo(
                kind="file",
                detail=f"{self.path} (key from ROM_HUB_SECRET_KEY)",
                protection=(
                    "Encrypted with a key supplied through ROM_HUB_SECRET_KEY "
                    "and never written to disk, so the file at rest is "
                    "unreadable without that key. The key is in this process's "
                    "environment while it runs."
                ),
                at_rest_secret=True,
            )
        return StoreInfo(
            kind="file",
            detail=f"{self.path} (key generated in {self.keyfile})",
            protection=(
                "Encrypted, but the key that decrypts it sits in the same "
                "directory -- that is obfuscation, not secrecy: whoever can "
                "read one file can read the other. What it does buy is that "
                "the value is not in state.json, so it is not in the file you "
                "open, dump, screenshot or commit. Set ROM_HUB_SECRET_KEY from "
                "outside the box (a Docker secret, a systemd credential) to "
                "make the encryption mean something."
            ),
            at_rest_secret=False,
        )


def _name(slug: str, key: str) -> str:
    return f"{slug}.{key}"


# -- the keyring store ---------------------------------------------------


def keyring_backend() -> tuple[object | None, str]:
    """The usable keyring module, or None and the reason there is none.

    A `keyring` install is not the same as a working keyring: with no
    service available it installs a *fail* or *null* backend whose
    `priority` is zero or less, and whose `get_password` either raises or
    silently returns None. Storing a credential into either of those would
    be the worst possible outcome -- the operator is told it is safe and it
    is nowhere at all -- so both are treated as "no keyring here".
    """
    try:
        import keyring  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001 - any import failure means "no"
        return None, f"the `keyring` package is not installed ({exc})"
    try:
        backend = keyring.get_keyring()
    except Exception as exc:  # noqa: BLE001
        return None, f"keyring could not select a backend: {exc}"
    name = f"{type(backend).__module__}.{type(backend).__name__}"
    try:
        priority = float(getattr(backend, "priority", 0))
    except (TypeError, ValueError):
        priority = 0.0
    if priority <= 0 or "fail" in name.lower() or "null" in name.lower():
        return None, f"keyring reports no usable backend ({name})"
    return keyring, name


class KeyringStore:
    """The OS keyring, via the `keyring` package."""

    kind = "keyring"

    def __init__(self, module, backend_name: str):
        self._keyring = module
        self.backend_name = backend_name

    def get(self, slug: str, key: str) -> str | None:
        try:
            return self._keyring.get_password(KEYRING_SERVICE, _name(slug, key))
        except Exception as exc:  # noqa: BLE001
            raise SecretError(f"the OS keyring refused a read: {exc}") from exc

    def set(self, slug: str, key: str, value: str) -> None:
        try:
            self._keyring.set_password(KEYRING_SERVICE, _name(slug, key), value)
        except Exception as exc:  # noqa: BLE001
            raise SecretError(f"the OS keyring refused a write: {exc}") from exc

    def delete(self, slug: str, key: str) -> bool:
        try:
            self._keyring.delete_password(KEYRING_SERVICE, _name(slug, key))
        except Exception:  # noqa: BLE001 - "not there" is not an error here
            return False
        return True

    def info(self) -> StoreInfo:
        return StoreInfo(
            kind="keyring",
            detail=f"OS keyring ({self.backend_name}), service {KEYRING_SERVICE!r}",
            protection=(
                "Held by the operating system's credential store. What that "
                "protects is the OS's answer, not the Hub's: a locked login "
                "keychain is a real boundary, while a desktop keyring that is "
                "unlocked at login can be read by anything running as you."
            ),
            at_rest_secret=True,
        )


# -- selection -----------------------------------------------------------


def open_store(root: Path, kind: str | None = None):
    """Pick a store. `auto` prefers a working keyring and falls back to file.

    The fallback is not a nicety. This Hub's primary deployment is headless
    in Docker on Linux, where there is no keyring at all -- a keyring-only
    design would mean the feature does not exist on the platform it was
    built for.
    """
    kind = (kind or env.get("ROM_HUB_SECRET_STORE") or "auto").strip().lower()
    if kind not in STORE_KINDS:
        raise SecretError(
            f"unknown secret store {kind!r}; ROM_HUB_SECRET_STORE accepts "
            f"{', '.join(STORE_KINDS)}"
        )
    if kind == "file":
        return FileStore(root)
    module, detail = keyring_backend()
    if module is not None:
        return KeyringStore(module, detail)
    if kind == "keyring":
        raise SecretError(
            f"ROM_HUB_SECRET_STORE=keyring was asked for, but {detail}. "
            f"Unset it to use the encrypted file store instead."
        )
    return FileStore(root)


# -- schema helpers ------------------------------------------------------


def secret_fields(manifest) -> list[str]:
    """The config keys this manifest declares as `secret`, in schema order."""
    schema = getattr(manifest, "config_schema", None) or {}
    return [
        key
        for key, spec in schema.items()
        if isinstance(spec, dict) and spec.get("type") == "secret"
    ]


def redact_config(manifest, config: dict) -> dict:
    """`config` with every `secret`-typed field replaced by `***`.

    Used by every path that prints a plugin's configuration. A field that is
    absent stays absent -- printing `***` for a secret nobody has set would
    tell the operator the opposite of the truth.
    """
    fields = set(secret_fields(manifest))
    return {
        key: (REDACTED if key in fields and value not in ("", None) else value)
        for key, value in (config or {}).items()
    }


def resolve_config(manifest, config: dict, store) -> dict:
    """The config a plugin subprocess is given: plain settings + secrets.

    Secrets are read from the store *here*, at the moment a process is
    about to start, and go over the pipe in the `init` frame. They are never
    added to `state.json`, and never put in the subprocess environment --
    `broker.host.SAFE_ENV_VARS` is built from `{}` upward precisely so that
    nothing secret-shaped can arrive that way, and routing a credential
    through it would undo that on purpose.
    """
    resolved = dict(config or {})
    for key in secret_fields(manifest):
        value = store.get(manifest.slug, key)
        if value is not None:
            resolved[key] = value
        else:
            # The empty string, not a missing key. A manifest may not give a
            # secret a default (that would be a credential in a public git
            # repo), so the plugin needs *something* here for its own "not
            # configured" refusal to fire with its own wording rather than a
            # KeyError raised from inside plugin code.
            resolved.setdefault(key, "")
    return resolved


def secret_values(manifest, config: dict, store) -> tuple[str, ...]:
    """Every secret value in play, for scrubbing output.

    Includes any *legacy plaintext* still sitting in `config` as well as
    what the store holds: during the window before migration runs, the value
    that could leak is the one in `state.json`.
    """
    values: list[str] = []
    for key in secret_fields(manifest):
        for candidate in (store.get(manifest.slug, key), (config or {}).get(key)):
            if isinstance(candidate, str) and len(candidate) >= MIN_REDACTABLE_LENGTH:
                values.append(candidate)
    return tuple(dict.fromkeys(values))


def scrub(text: str, values) -> str:
    """Replace every known secret value in `text` with `***`.

    Defence in depth against the one leak the host cannot prevent by
    construction: a plugin that prints its own key to stderr, or echoes it
    into a value the host then quotes in an error. The host knows exactly
    which strings it handed over, so it can take them back out of anything
    it is about to print.
    """
    if not text:
        return text
    for value in values:
        if value and len(value) >= MIN_REDACTABLE_LENGTH:
            text = text.replace(value, REDACTED)
    return text


# -- migration -----------------------------------------------------------


def migrate_plaintext(registry, plugin, store, announce=None) -> list[str]:
    """Move any plaintext value for a now-`secret` field out of state.json.

    The upgrade path for anyone who configured `retroachievements` before
    `secret` existed: their key is sitting in `state.json` as a plain `str`.
    Nothing about that breaks -- the value is read, put in the store, and
    removed from the plain config, so the next run behaves identically and
    the credential is no longer in the file people dump.

    It is done here, on the way to starting a plugin, rather than only at
    install: an operator who never reinstalls would otherwise keep the
    plaintext forever and never be told.

    Returns the field names that moved. Never returns or logs a value.
    """
    fields = secret_fields(plugin.manifest)
    if not fields:
        return []
    config = dict(plugin.config or {})
    moved: list[str] = []
    for key in fields:
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            # An empty default is not a credential; drop it silently so the
            # plain config does not keep a placeholder for a secret field.
            config.pop(key, None)
            continue
        if store.get(plugin.slug, key) is None:
            store.set(plugin.slug, key, value)
        config.pop(key, None)
        moved.append(key)
    if config != dict(plugin.config or {}):
        registry.set_config(plugin.slug, config)
        plugin.config = config
    if moved and announce is not None:
        announce(
            f"moved {', '.join(sorted(moved))} for plugin {plugin.slug!r} out of "
            f"the Hub's plain config and into the secret store "
            f"({store.info().kind}). The value is no longer in state.json -- "
            f"rotate it if that file was ever shared, committed or backed up."
        )
    return moved
