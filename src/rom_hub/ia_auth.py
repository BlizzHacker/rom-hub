"""Persistent Archive.org credentials for host-side HTTP.

UI credentials live in ROM Hub's existing secret store under a *host-only*
slug, never under an installed plugin slug. External files/env take precedence
and are never copied into the store. No cookies are persisted.
"""
import json
import os
from pathlib import Path

STORE_SLUG = "archive-org-host-auth"


class ArchiveAuthError(RuntimeError):
    """Errors must never contain a password or response body."""


def _external_prefix() -> str | None:
    for prefix in ("ROM_HUB_ARCHIVE_ORG_", "ROMARR_ARCHIVE_ORG_"):
        if any(prefix + suffix in os.environ for suffix in (
                "EMAIL_FILE", "PASSWORD_FILE", "EMAIL", "PASSWORD")):
            return prefix
    return None


def _external() -> bool:
    return _external_prefix() is not None


def _read_external(name: str) -> str:
    prefix = _external_prefix()
    path = os.environ.get(prefix + name + "_FILE")
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeError):
            raise ArchiveAuthError("Could not read Archive.org " + name.lower() + " file") from None
    return os.environ.get(prefix + name, "")


def _store(root: Path):
    from rom_hub.secrets import open_store
    return open_store(root)


def credentials(root: Path) -> tuple[str, str] | None:
    if _external():
        email, password = _read_external("EMAIL"), _read_external("PASSWORD")
    else:
        try:
            value = _store(root).get(STORE_SLUG, "credentials")
            if not value:
                return None
            data = json.loads(value)
            email, password = data["email"], data["password"]
        except Exception:
            raise ArchiveAuthError("Could not read Archive.org credentials") from None
    if not email and not password and not _external():
        return None
    if (not isinstance(email, str) or not isinstance(password, str)
            or not email or not password or len(email) > 320
            or len(password) > 4096 or "\r" in email or "\n" in email):
        raise ArchiveAuthError("Archive.org email and password must both be configured")
    return email, password


def status(root: Path) -> dict:
    try:
        configured = credentials(root) is not None
        error = ""
    except ArchiveAuthError as exc:
        configured, error = False, str(exc)
    return {"configured": configured, "external": _external(), "error": error}


def configure(root: Path, email="", password="", clear=False) -> dict:
    if _external():
        return {"ok": False, "error": "External credentials override UI settings"}
    if not isinstance(clear, bool):
        return {"ok": False, "error": "Invalid clear flag"}
    if not clear and (not isinstance(email, str) or not isinstance(password, str)
                      or not email or "@" not in email or len(email) > 320
                      or "\r" in email or "\n" in email
                      or not password or len(password) > 4096):
        return {"ok": False, "error": "Valid email and password are required"}
    try:
        store = _store(root)
        if clear:
            store.delete(STORE_SLUG, "credentials")
        else:
            store.set(STORE_SLUG, "credentials",
                      json.dumps({"email": email, "password": password}))
        from .ia_session import invalidate
        invalidate(root)
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": "Could not update Archive.org credentials"}


def login(root: Path, email: str, password: str) -> dict:
    if _external():
        return {"ok": False, "error": "External credentials override saved settings"}
    if (not isinstance(email, str) or not isinstance(password, str)
            or "@" not in email or len(email) > 320 or "\r" in email
            or "\n" in email or not password or len(password) > 4096):
        return {"ok": False, "error": "Valid email and password are required"}
    from .ia_session import ArchiveOrgSession
    try:
        ArchiveOrgSession(root, pair=(email, password)).header()
    except ArchiveAuthError as exc:
        return {"ok": False, "error": str(exc)}
    return configure(root, email, password)
