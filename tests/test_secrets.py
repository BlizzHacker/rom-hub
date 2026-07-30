"""The secret store: what it keeps, what it refuses, and what it admits to.

The interesting tests here are not "a round trip works". They are:

* the value is **not** in `state.json`, and not in the ciphertext either;
* the file store detects tampering rather than returning garbage;
* `info().protection` says the word "obfuscation" when the key is sitting
  next to the ciphertext, because over-claiming there is the actual failure
  mode this feature has;
* a `keyring` install with no working backend is treated as *no keyring*,
  since storing a credential into a null backend loses it silently.
"""

import json

import pytest

from rom_hub.manifest import parse_manifest
from rom_hub.secrets import (
    KEYRING_SERVICE,
    REDACTED,
    FileStore,
    KeyringStore,
    SecretError,
    keyring_backend,
    migrate_plaintext,
    open_store,
    redact_config,
    resolve_config,
    scrub,
    secret_fields,
    secret_values,
)

KEY = "RA-live-0123456789abcdefghijklmnop"

MANIFEST = """
[plugin]
slug = "demo"
name = "Demo"
version = "1.0.0"
rpp_version = "1"

[capabilities]
metadata = "demo.metadata:Metadata"

[permissions]
network = []
romm_api = []

[config]
api_key = { type = "secret" }
depth = { type = "int", default = 3 }
"""


@pytest.fixture
def manifest():
    return parse_manifest(MANIFEST)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A file store with no ROM_HUB_SECRET_KEY, i.e. the generated-key case."""
    monkeypatch.delenv("ROM_HUB_SECRET_KEY", raising=False)
    monkeypatch.delenv("ROMM_HUB_SECRET_KEY", raising=False)
    return FileStore(tmp_path)


# -- the file store ------------------------------------------------------


def test_a_stored_secret_comes_back(store):
    store.set("demo", "api_key", KEY)
    assert store.get("demo", "api_key") == KEY


def test_an_unset_secret_is_none_not_an_error(store):
    assert store.get("demo", "api_key") is None


def test_the_value_is_not_in_the_file_in_readable_form(store):
    store.set("demo", "api_key", KEY)
    raw = store.path.read_text(encoding="utf-8")
    assert KEY not in raw
    # Nor in a naive encoding of it. The point is not that hex is clever,
    # it is that grepping the file for the key finds nothing.
    assert KEY.encode().hex() not in raw


def test_the_field_name_is_visible_but_the_value_is_not(store):
    """Deliberate. An operator must be able to see *that* a key is set."""
    store.set("demo", "api_key", KEY)
    doc = json.loads(store.path.read_text(encoding="utf-8"))
    assert "demo.api_key" in doc["entries"]
    assert KEY not in json.dumps(doc)


def test_a_tampered_ciphertext_is_refused_rather_than_decrypted(store):
    store.set("demo", "api_key", KEY)
    doc = json.loads(store.path.read_text(encoding="utf-8"))
    entry = doc["entries"]["demo.api_key"]
    flipped = bytearray(bytes.fromhex(entry["ciphertext"]))
    flipped[0] ^= 0x01
    entry["ciphertext"] = bytes(flipped).hex()
    store.path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(SecretError, match="did not authenticate"):
        FileStore(store.root).get("demo", "api_key")


def test_a_different_key_cannot_read_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ROM_HUB_SECRET_KEY", "one")
    FileStore(tmp_path).set("demo", "api_key", KEY)
    monkeypatch.setenv("ROM_HUB_SECRET_KEY", "two")
    with pytest.raises(SecretError, match="did not authenticate"):
        FileStore(tmp_path).get("demo", "api_key")


def test_delete_removes_it_and_reports_whether_there_was_anything(store):
    assert store.delete("demo", "api_key") is False
    store.set("demo", "api_key", KEY)
    assert store.delete("demo", "api_key") is True
    assert store.get("demo", "api_key") is None


def test_two_plugins_do_not_collide(store):
    store.set("demo", "api_key", "aaaa1111")
    store.set("other", "api_key", "bbbb2222")
    assert store.get("demo", "api_key") == "aaaa1111"
    assert store.get("other", "api_key") == "bbbb2222"


def test_unicode_survives_the_round_trip(store):
    store.set("demo", "api_key", "clé-secrète-日本語")
    assert store.get("demo", "api_key") == "clé-secrète-日本語"


# -- the honest description ----------------------------------------------


def test_a_generated_key_file_is_described_as_obfuscation(store):
    """The single most important assertion in this file.

    A key next to the ciphertext is obfuscation. If this text ever starts
    claiming otherwise, the docs built on it become a lie.
    """
    store.set("demo", "api_key", KEY)
    info = store.info()
    assert info.at_rest_secret is False
    assert "obfuscation" in info.protection.lower()
    assert "not secrecy" in info.protection.lower()


def test_a_supplied_key_is_described_as_real_encryption(tmp_path, monkeypatch):
    monkeypatch.setenv("ROM_HUB_SECRET_KEY", "from-a-docker-secret")
    info = FileStore(tmp_path).info()
    assert info.at_rest_secret is True
    assert "obfuscation" not in info.protection.lower()


def test_no_key_file_is_written_when_the_key_comes_from_the_environment(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ROM_HUB_SECRET_KEY", "from-a-docker-secret")
    store = FileStore(tmp_path)
    store.set("demo", "api_key", KEY)
    assert not store.keyfile.exists()


# -- keyring detection ---------------------------------------------------


class _FakeKeyring:
    """Minimal stand-in for the `keyring` package."""

    def __init__(self, backend, values=None):
        self._backend = backend
        self.values = values if values is not None else {}

    def get_keyring(self):
        return self._backend

    def get_password(self, service, name):
        return self.values.get((service, name))

    def set_password(self, service, name, value):
        self.values[(service, name)] = value

    def delete_password(self, service, name):
        del self.values[(service, name)]


class _Working:
    priority = 5


class _NullBackend:
    priority = 0


def test_a_keyring_store_round_trips():
    fake = _FakeKeyring(_Working())
    store = KeyringStore(fake, "fake.Working")
    store.set("demo", "api_key", KEY)
    assert fake.values[(KEYRING_SERVICE, "demo.api_key")] == KEY
    assert store.get("demo", "api_key") == KEY
    assert store.delete("demo", "api_key") is True
    assert store.get("demo", "api_key") is None


def test_a_keyring_with_no_usable_backend_counts_as_no_keyring(monkeypatch):
    """The failure this check exists for.

    `pip install keyring` on a headless box installs a backend whose
    `priority` is 0 and whose `get_password` returns None forever. Storing
    a credential into it would tell the operator their key is safe while
    putting it precisely nowhere.
    """
    import sys

    monkeypatch.setitem(
        sys.modules, "keyring", _FakeKeyring(_NullBackend())
    )
    module, reason = keyring_backend()
    assert module is None
    assert "no usable backend" in reason


def test_auto_falls_back_to_the_file_store_when_there_is_no_keyring(
    tmp_path, monkeypatch
):
    """The headless-Docker case, which is this Hub's primary deployment."""
    monkeypatch.setattr(
        "rom_hub.secrets.keyring_backend", lambda: (None, "no keyring here")
    )
    monkeypatch.delenv("ROM_HUB_SECRET_STORE", raising=False)
    assert isinstance(open_store(tmp_path), FileStore)


def test_asking_for_a_keyring_that_is_not_there_refuses_rather_than_pretending(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "rom_hub.secrets.keyring_backend", lambda: (None, "no keyring here")
    )
    with pytest.raises(SecretError, match="no keyring here"):
        open_store(tmp_path, kind="keyring")


def test_an_unknown_store_kind_is_refused(tmp_path):
    with pytest.raises(SecretError, match="unknown secret store"):
        open_store(tmp_path, kind="vault")


def test_forcing_the_file_store_never_touches_the_keyring(tmp_path, monkeypatch):
    def explode():
        raise AssertionError("the keyring must not be consulted at all")

    monkeypatch.setattr("rom_hub.secrets.keyring_backend", explode)
    assert isinstance(open_store(tmp_path, kind="file"), FileStore)


# -- schema helpers ------------------------------------------------------


def test_secret_fields_reads_the_schema(manifest):
    assert secret_fields(manifest) == ["api_key"]


def test_redact_config_replaces_only_the_secret(manifest):
    shown = redact_config(manifest, {"api_key": KEY, "depth": 3})
    assert shown == {"api_key": REDACTED, "depth": 3}


def test_redact_config_leaves_an_unset_secret_alone(manifest):
    """`***` for a secret nobody set would say the opposite of the truth."""
    assert redact_config(manifest, {"api_key": "", "depth": 3})["api_key"] == ""


def test_resolve_config_merges_the_store_in(manifest, store):
    store.set("demo", "api_key", KEY)
    resolved = resolve_config(manifest, {"depth": 3}, store)
    assert resolved == {"depth": 3, "api_key": KEY}


def test_resolve_config_gives_an_unset_secret_an_empty_string(manifest, store):
    """So the plugin's own refusal fires, not a KeyError from inside it."""
    assert resolve_config(manifest, {}, store)["api_key"] == ""


def test_secret_values_covers_both_the_store_and_un_migrated_plaintext(
    manifest, store
):
    store.set("demo", "api_key", KEY)
    values = secret_values(manifest, {"api_key": "older-plaintext-value"}, store)
    assert set(values) == {KEY, "older-plaintext-value"}


def test_scrub_removes_every_known_value():
    text = f"boom: key={KEY} again {KEY}"
    assert KEY not in scrub(text, (KEY,))
    assert scrub(text, (KEY,)).count(REDACTED) == 2


def test_scrub_ignores_values_too_short_to_redact_safely():
    """Redacting 'a' would corrupt every message containing the letter a."""
    assert scrub("a banana", ("a",)) == "a banana"


# -- migration -----------------------------------------------------------


class _FakePlugin:
    def __init__(self, manifest, config):
        self.slug = manifest.slug
        self.manifest = manifest
        self.config = config


class _FakeRegistry:
    def __init__(self):
        self.written = None

    def set_config(self, slug, config):
        self.written = (slug, config)


def test_a_pre_secret_plaintext_value_is_moved_not_lost(manifest, store):
    plugin = _FakePlugin(manifest, {"api_key": KEY, "depth": 3})
    registry = _FakeRegistry()
    notices = []

    moved = migrate_plaintext(registry, plugin, store, announce=notices.append)

    assert moved == ["api_key"]
    assert store.get("demo", "api_key") == KEY, "the value still works"
    assert registry.written == ("demo", {"depth": 3}), "and left the plain config"
    assert plugin.config == {"depth": 3}
    assert notices and KEY not in notices[0], "the notice never carries the value"
    assert "rotate" in notices[0]


def test_migration_is_idempotent(manifest, store):
    plugin = _FakePlugin(manifest, {"api_key": KEY})
    assert migrate_plaintext(_FakeRegistry(), plugin, store) == ["api_key"]
    assert migrate_plaintext(_FakeRegistry(), plugin, store) == []


def test_migration_does_not_overwrite_a_value_already_in_the_store(manifest, store):
    store.set("demo", "api_key", "the-newer-one")
    plugin = _FakePlugin(manifest, {"api_key": "the-older-plaintext"})
    migrate_plaintext(_FakeRegistry(), plugin, store)
    assert store.get("demo", "api_key") == "the-newer-one"


def test_a_plugin_with_no_secrets_is_not_touched(store):
    plain = parse_manifest(MANIFEST.replace(
        'api_key = { type = "secret" }', 'api_key = { type = "str" }'
    ))
    plugin = _FakePlugin(plain, {"api_key": KEY})
    registry = _FakeRegistry()
    assert migrate_plaintext(registry, plugin, store) == []
    assert registry.written is None


def test_an_empty_placeholder_is_dropped_without_a_notice(manifest, store):
    plugin = _FakePlugin(manifest, {"api_key": "", "depth": 3})
    registry = _FakeRegistry()
    notices = []
    assert migrate_plaintext(registry, plugin, store, announce=notices.append) == []
    assert registry.written == ("demo", {"depth": 3})
    assert notices == []


# -- file permissions ----------------------------------------------------


def test_the_store_is_owner_only_where_the_os_has_modes(store):
    """Not a `skipif`.

    CI's Windows gate permits exactly one skip reason ("seccomp"), and a
    second one here would be a test that quietly stopped running on half
    the matrix. So the *assertion* is platform-shaped instead: POSIX gets
    the real 0600 check, Windows gets the weaker claim that is actually
    true there, and both platforms run the code path that sets the bits.
    """
    import stat
    import sys

    store.set("demo", "api_key", KEY)
    for path in (store.path, store.keyfile):
        assert path.is_file()
        if sys.platform != "win32":
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == 0o600, f"{path} is {oct(mode)}"
