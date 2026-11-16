"""RPP v1 `metadata` provider for `gamesgames` (`gamesgamesgamesgamesgames`).

Enriches a `RomRef` with `games.gamesgamesgamesgames.*` records (`game`,
`release`, and `dev.cartridge.verificationRequest`) stored in
`MetadataPatch.raw_metadata["raw_gamesgames_metadata"]`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from rom_hub_sdk import MetadataPatch, MetadataProvider, RomRef

from .lexicons import LEXICONS, build_at_uri, generate_tid


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Metadata(MetadataProvider):
    """Emits `games.gamesgamesgamesgames.*` lexicon records for a library ROM."""

    def enrich(self, rom: RomRef) -> MetadataPatch:
        cfg = getattr(self.ctx, "config", {}) or {}
        did = str(cfg.get("did") or "did:plc:romarr-local").strip() or "did:plc:romarr-local"
        emit_release = bool(cfg.get("emit_release", True))
        emit_verification = bool(cfg.get("emit_verification", True))
        dat_catalog = str(cfg.get("dat_catalog") or "No-Intro").strip() or "No-Intro"

        title = (rom.name or rom.filename or f"rom-{rom.rom_id}").strip()
        platform = (rom.platform or "retro").strip()
        now = _utc_now()

        game_rkey = generate_tid()
        game_uri = build_at_uri(did, "games.gamesgamesgamesgames.game", game_rkey)
        game_record = {
            "$type": "games.gamesgamesgamesgames.game",
            "name": title,
            "createdAt": now,
            "platforms": [platform],
            "rommId": str(rom.rom_id),
        }

        extra = dict(rom.extra or {})
        sha256 = (extra.get("sha256") or "").strip().lower()
        crc32 = (extra.get("crc32") or "").strip().lower()
        md5 = (extra.get("md5") or "").strip().lower()

        bundle: dict[str, object] = {
            "standard": "games.gamesgamesgamesgames.*",
            "lexicons": list(LEXICONS.keys()),
            "game": {
                "uri": game_uri,
                "cid": hashlib.sha256(json.dumps(game_record, sort_keys=True).encode()).hexdigest(),
                "value": game_record,
            },
        }

        if emit_release:
            rel_rkey = generate_tid()
            rel_uri = build_at_uri(did, "games.gamesgamesgamesgames.release", rel_rkey)
            rel_record = {
                "$type": "games.gamesgamesgamesgames.release",
                "gameUri": game_uri,
                "platform": platform,
                "sha256": sha256,
                "crc32": crc32,
                "md5": md5,
                "sizeBytes": rom.size_bytes or 0,
                "verified": bool(sha256 or crc32 or md5),
                "datSource": dat_catalog,
                "createdAt": now,
            }
            bundle["release"] = {
                "uri": rel_uri,
                "cid": hashlib.sha256(json.dumps(rel_record, sort_keys=True).encode()).hexdigest(),
                "value": rel_record,
            }

        if emit_verification and (sha256 or crc32):
            claim_rkey = generate_tid()
            claim_uri = build_at_uri(did, "dev.cartridge.verificationRequest", claim_rkey)
            claim_record = {
                "$type": "dev.cartridge.verificationRequest",
                "romTitle": title,
                "platform": platform,
                "sha256": sha256,
                "crc32": crc32,
                "datCatalog": dat_catalog,
                "verified": True,
                "createdAt": now,
            }
            bundle["verification_claim"] = {
                "uri": claim_uri,
                "cid": hashlib.sha256(json.dumps(claim_record, sort_keys=True).encode()).hexdigest(),
                "value": claim_record,
            }

        return MetadataPatch(
            raw_metadata={"raw_manual_metadata": bundle},
        )
