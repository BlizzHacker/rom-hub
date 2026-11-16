"""Tests for the gamesgamesgamesgamesgames ROM Hub plugin."""

from types import SimpleNamespace
import pytest
from rom_hub_sdk import RomRef

from gamesgames.lexicons import (
    ATProtoClient,
    ATProtoKirbyHub,
    HappyViewAppView,
    LEXICONS,
    build_at_uri,
    generate_tid,
    parse_at_uri,
)
from gamesgames.metadata import Metadata


def test_lexicons_and_at_uri_helpers():
    assert "games.gamesgamesgamesgames.game" in LEXICONS
    assert "games.gamesgamesgamesgames.release" in LEXICONS
    assert "games.gamesgamesgamesgames.playState" in LEXICONS
    assert "games.gamesgamesgamesgames.achievement" in LEXICONS
    assert "dev.cartridge.verificationRequest" in LEXICONS

    tid = generate_tid()
    assert len(tid) == 13
    uri = build_at_uri("did:plc:alice", "games.gamesgamesgamesgames.game", tid)
    parsed = parse_at_uri(uri)
    assert parsed == {
        "did": "did:plc:alice",
        "collection": "games.gamesgamesgamesgames.game",
        "rkey": tid,
    }


def test_metadata_provider_enriches_rom_with_gamesgames_records():
    provider = Metadata(SimpleNamespace(config={"did": "did:plc:player1"}))
    rom = RomRef(
        rom_id=42,
        name="Chrono Trigger",
        filename="Chrono Trigger (USA).sfc",
        platform="snes",
        size_bytes=4194304,
        extra={"sha256": "deadbeef1234", "crc32": "a1b2c3d4"},
    )
    patch = provider.enrich(rom)
    raw = patch.raw_metadata["raw_manual_metadata"]
    assert raw["standard"] == "games.gamesgamesgamesgames.*"
    assert raw["game"]["value"]["name"] == "Chrono Trigger"
    assert raw["game"]["uri"].startswith("at://did:plc:player1/games.gamesgamesgamesgames.game/")
    assert raw["release"]["value"]["sha256"] == "deadbeef1234"
    assert raw["verification_claim"]["value"]["verified"] is True


def test_happyview_and_kirby_hub(tmp_path):
    hub = ATProtoKirbyHub(tmp_path)
    status = hub.status()
    assert status["ok"] is True
    assert status["gaming_standard"] == "games.gamesgamesgamesgames.*"

    exp = hub.export_game({
        "title": "Super Metroid",
        "platform": "snes",
        "sha256": "abcdef123456",
        "crc32": "d1e2f3",
        "verified": True,
    })
    assert exp["ok"] is True
    assert exp["gameUri"].startswith("at://")

    listed = hub.appview.handle_xrpc_list_games({"limit": 10})
    assert listed["count"] == 1
