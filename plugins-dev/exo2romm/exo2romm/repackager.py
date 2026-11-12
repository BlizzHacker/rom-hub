"""Cross-platform pure-Python eXo2romm repackager for eXoDOS -> RomM.

Credits:
- Adapted from `eXo2romm` by derkommutator (`https://github.com/derkommutator/eXo2romm`, Unlicense).
- This plugin was inspired to assist with more users having this capability directly inside ROMarr and ROM Hub.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

UPSTREAM_AUTHOR = "derkommutator"
UPSTREAM_REPO = "derkommutator/eXo2romm"
UPSTREAM_URL = "https://github.com/derkommutator/eXo2romm"
INSPIRED_NOTE = (
    "Crediting derkommutator (https://github.com/derkommutator/eXo2romm, Unlicense) "
    "— this plugin was inspired to assist with more users having this."
)

DEFAULT_DOS_DIR = "/data/eXoDOS/Content/eXo/eXoDOS/!dos"
DEFAULT_OUTPUT_DIR = "/var/lib/romm/library/roms/dos"

_EXO_NEEDLE = ".\\exodos\\"
_MOUNT_KEYWORDS = frozenset({"mount", "imgmount", "boot"})
_BOUNDARY_CHARS = ("", " ", "\t", '"', "\r")


@dataclass(frozen=True)
class ZipResult:
    """Outcome for one input eXoDOS archive."""

    zip_path: str
    status: str  # processed | skipped_exists | skipped_error | dry_run
    top_dir: str = ""
    output_zip: str = ""
    rewritten_lines: int = 0
    detail: str = ""
    preview_conf: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class BatchResult:
    """Aggregate result of an eXo2romm repackaging batch."""

    ok: bool
    processed: int
    skipped: int
    dry_run: bool
    dos_dir: str
    output_dir: str
    error_log: str
    credit: str
    inspired_note: str
    upstream_url: str
    results: tuple[ZipResult, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "processed": self.processed,
            "skipped": self.skipped,
            "dry_run": self.dry_run,
            "dos_dir": self.dos_dir,
            "output_dir": self.output_dir,
            "error_log": self.error_log,
            "credit": self.credit,
            "inspired_note": self.inspired_note,
            "upstream_url": self.upstream_url,
            "summary": f"{self.processed} processed, {self.skipped} skipped",
            "results": [item.as_dict() for item in self.results],
        }


def rewrite_dosbox_conf_bytes(raw: bytes, top: str) -> tuple[bytes, int]:
    """Rewrite pack-root `.\\eXoDOS\\` mounts in `dosbox.conf` bytes while preserving EOLs.

    Mirrors `derkommutator/eXo2romm`'s exact rules:
    - Only lines whose first whitespace-separated token (ignoring leading whitespace
      and optional `@`) is `mount`, `imgmount`, or `boot` (case-insensitive) are edited.
    - Non-regex literal case-insensitive matching on `.\\exodos\\` protects game names
      that contain brackets, parentheses, or `+` metacharacters.
    - Comments (`#`, `rem`) and paths referencing a different game directory remain untouched.
    - CRLF / LF line endings and untouched lines stay byte-identical.
    """
    ltop = top.lower()
    nlen = len(_EXO_NEEDLE)
    out_chunks: list[bytes] = []
    rewritten = 0

    for raw_line in raw.splitlines(keepends=True):
        if raw_line.endswith(b"\r\n"):
            body, eol = raw_line[:-2], b"\r\n"
        elif raw_line.endswith(b"\n"):
            body, eol = raw_line[:-1], b"\n"
        elif raw_line.endswith(b"\r"):
            body, eol = raw_line[:-1], b"\r"
        else:
            body, eol = raw_line, b""

        line_str = body.decode("utf-8", errors="surrogateescape")
        probe = line_str.lstrip(" \t")
        if probe.startswith("@"):
            probe = probe[1:].lstrip(" \t")
        first_word = re.split(r"[ \t]+", probe, maxsplit=1)[0].lower() if probe else ""
        if first_word not in _MOUNT_KEYWORDS:
            out_chunks.append(raw_line)
            continue

        built: list[str] = []
        rest = line_str
        lc = rest.lower()
        while (pos := lc.find(_EXO_NEEDLE)) != -1:
            built.append(rest[:pos])
            after = rest[pos + nlen :]
            cafter = lc[pos + nlen :]

            cut = -1
            if cafter.startswith(ltop):
                nxt = cafter[len(ltop) : len(ltop) + 1]
                if nxt == "\\":
                    cut = len(ltop) + 1
                elif nxt in _BOUNDARY_CHARS:
                    cut = len(ltop)

            if cut >= 0:
                built.append(".\\")
                rest = after[cut:]
                lc = rest.lower()
            else:
                nxt2 = cafter[:1]
                if nxt2 in _BOUNDARY_CHARS:
                    built.append(".\\")
                    rest = after
                    lc = rest.lower()
                else:
                    built.append(rest[pos : pos + nlen])
                    rest = after
                    lc = cafter

        built.append(rest)
        new_str = "".join(built)
        if new_str != line_str:
            rewritten += 1
        out_chunks.append(new_str.encode("utf-8", errors="surrogateescape") + eol)

    return b"".join(out_chunks), rewritten


def inspect_exo_zip(zip_path: Path) -> str:
    """Validate an eXoDOS zip and return its single safe top-level directory name."""
    if not zip_path.is_file():
        raise ValueError(f"file not found: {zip_path}")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            infos = zf.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("unreadable zip") from exc

    if not infos:
        raise ValueError("empty zip")

    has_slash = False
    tops: set[str] = set()
    for info in infos:
        name = info.filename
        if not name:
            continue
        if name.startswith(("/", "\\")) or (len(name) >= 2 and name[1] == ":"):
            raise ValueError(f"unsafe absolute zip member path: {name!r}")
        parts = [seg for seg in re.split(r"[\\/]+", name) if seg]
        if any(seg == ".." for seg in parts):
            raise ValueError(f"unsafe traversal zip member path: {name!r}")
        if "/" in name:
            has_slash = True
        head = name.split("/", 1)[0]
        if head:
            tops.add(head)

    if len(tops) != 1:
        raise ValueError("zip does not unzip into a single top-level directory")
    if not has_slash:
        raise ValueError("files sit at the zip root (no subdirectory)")

    top = next(iter(tops))
    if top in ("", ".", "..") or "\\" in top or "/" in top or ":" in top:
        raise ValueError(f"unsafe top-level directory name: {top!r}")
    return top


def resolve_template_conf(dos_dir: Path, top: str) -> Path:
    """Resolve `$DOS_DIR/<top>/dosbox.conf` safely inside `dos_dir`."""
    base = dos_dir.resolve()
    candidate = (base / PurePosixPath(top).name / "dosbox.conf").resolve()
    if not candidate.is_relative_to(base):
        raise ValueError(f"template path escapes DOS_DIR: {top!r}")
    if not candidate.is_file():
        raise ValueError(f'no dosbox.conf found in "{dos_dir / top}"')
    return candidate


def _append_error_log(error_log: Path, message: str) -> None:
    try:
        error_log.parent.mkdir(parents=True, exist_ok=True)
        stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with error_log.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp}  {message}\n")
    except OSError:
        pass


def repackage_zip(
    zip_path: Path,
    dos_dir: Path,
    output_dir: Path,
    *,
    dry_run: bool = False,
    overwrite: bool = False,
    error_log: Path | None = None,
) -> ZipResult:
    """Clone one eXoDOS zip into `output_dir` with rewritten `<top>/dosbox.conf`."""
    zip_path = Path(zip_path)
    dos_dir = Path(dos_dir)
    output_dir = Path(output_dir)
    tag = zip_path.name

    try:
        top = inspect_exo_zip(zip_path)
        src_conf = resolve_template_conf(dos_dir, top)
    except ValueError as exc:
        msg = f"SKIP [{tag}]: {exc}"
        if error_log and not dry_run:
            _append_error_log(error_log, msg)
        return ZipResult(zip_path=str(zip_path), status="skipped_error", detail=msg)

    out_base = output_dir.resolve()
    out_zip = (out_base / tag).resolve()
    if not out_zip.is_relative_to(out_base):
        msg = f"SKIP [{tag}]: unsafe output archive name"
        if error_log and not dry_run:
            _append_error_log(error_log, msg)
        return ZipResult(zip_path=str(zip_path), status="skipped_error", top_dir=top, detail=msg)

    if out_zip.exists() and not overwrite:
        return ZipResult(
            zip_path=str(zip_path),
            status="skipped_exists",
            top_dir=top,
            output_zip=str(out_zip),
            detail=f"SKIP [{tag}]: output already exists: {out_zip} (left untouched)",
        )

    try:
        raw_conf = src_conf.read_bytes()
        edited_conf, rewritten_lines = rewrite_dosbox_conf_bytes(raw_conf, top)
    except OSError as exc:
        msg = f"SKIP [{tag}]: could not prepare edited dosbox.conf: {exc}"
        if error_log and not dry_run:
            _append_error_log(error_log, msg)
        return ZipResult(zip_path=str(zip_path), status="skipped_error", top_dir=top, detail=msg)

    if dry_run:
        preview = edited_conf.decode("utf-8", errors="replace")[:2000]
        return ZipResult(
            zip_path=str(zip_path),
            status="dry_run",
            top_dir=top,
            output_zip=str(out_zip),
            rewritten_lines=rewritten_lines,
            detail=f"DRY-RUN [{tag}] -> {out_zip} ({rewritten_lines} line(s) rewritten from {top}/dosbox.conf)",
            preview_conf=preview,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    target_entry = f"{top}/dosbox.conf".casefold()
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_dir,
            prefix=f".{zip_path.stem}.",
            suffix=".tmp",
            delete=False,
        ) as tf:
            tmp_path = Path(tf.name)

        with zipfile.ZipFile(zip_path, "r") as src_zf, zipfile.ZipFile(tmp_path, "w") as dst_zf:
            for info in src_zf.infolist():
                if info.filename.rstrip("/").casefold() == target_entry:
                    continue
                new_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                new_info.compress_type = info.compress_type
                new_info.external_attr = info.external_attr
                new_info.create_system = info.create_system
                if info.is_dir():
                    dst_zf.writestr(new_info, b"")
                else:
                    with src_zf.open(info, "r") as src_member, dst_zf.open(new_info, "w") as dst_member:
                        shutil.copyfileobj(src_member, dst_member, length=65536)

            conf_info = zipfile.ZipInfo(
                f"{top}/dosbox.conf",
                date_time=_dt.datetime.now().timetuple()[:6],
            )
            conf_info.compress_type = zipfile.ZIP_DEFLATED
            dst_zf.writestr(conf_info, edited_conf)

        os.replace(tmp_path, out_zip)
        tmp_path = None
    except Exception as exc:
        msg = f"SKIP [{tag}]: failed to write output zip: {exc}"
        if error_log:
            _append_error_log(error_log, msg)
        return ZipResult(zip_path=str(zip_path), status="skipped_error", top_dir=top, detail=msg)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return ZipResult(
        zip_path=str(zip_path),
        status="processed",
        top_dir=top,
        output_zip=str(out_zip),
        rewritten_lines=rewritten_lines,
        detail=f"OK [{tag}] -> {out_zip} (conf from {top}/, {rewritten_lines} mount line(s) rewritten)",
    )


def repackage_batch(
    input_paths: list[str | Path],
    *,
    dos_dir: str | Path = DEFAULT_DOS_DIR,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    error_log: str | Path | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> BatchResult:
    """Process one or more eXoDOS zips (or directories containing `.zip` files)."""
    dos_path = Path(str(dos_dir or DEFAULT_DOS_DIR).strip() or DEFAULT_DOS_DIR)
    out_path = Path(str(output_dir or DEFAULT_OUTPUT_DIR).strip() or DEFAULT_OUTPUT_DIR)
    err_path = (
        Path(str(error_log).strip())
        if error_log and str(error_log).strip()
        else out_path / "eXo2romm-errors.log"
    )

    zips: list[Path] = []
    for raw_item in input_paths:
        text = str(raw_item or "").strip()
        if not text:
            continue
        candidate = Path(text)
        if candidate.is_dir():
            zips.extend(sorted(p for p in candidate.iterdir() if p.is_file() and p.suffix.lower() == ".zip"))
        else:
            zips.append(candidate)

    results: list[ZipResult] = []
    processed = 0
    skipped = 0
    for zp in zips:
        res = repackage_zip(
            zp,
            dos_path,
            out_path,
            dry_run=dry_run,
            overwrite=overwrite,
            error_log=err_path,
        )
        results.append(res)
        if res.status in ("processed", "dry_run"):
            processed += 1
        else:
            skipped += 1

    ok = bool(zips) and skipped == 0
    return BatchResult(
        ok=ok,
        processed=processed,
        skipped=skipped,
        dry_run=dry_run,
        dos_dir=str(dos_path),
        output_dir=str(out_path),
        error_log=str(err_path),
        credit=f"eXo2romm by {UPSTREAM_AUTHOR} ({UPSTREAM_URL}, Unlicense)",
        inspired_note=INSPIRED_NOTE,
        upstream_url=UPSTREAM_URL,
        results=tuple(results),
    )
