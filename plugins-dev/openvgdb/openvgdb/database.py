"""Reading `openvgdb.sqlite`, which is a file on disk and not a service.

There is no OpenVGDB API. The project publishes one artefact -- a SQLite
database attached to a GitHub release -- and its repository holds a
`.gitignore` and a 28-byte `README.md` and nothing else. So this module
opens a local file, and `metadata.py`'s docstring explains why the plugin
cannot fetch that file for you.

**Opened read-only, through a URI, deliberately.** `sqlite3.connect` on a
plain path *creates* an empty database when the path does not exist, so a
typo in `db_path` would otherwise produce a valid connection to a
zero-table file and a stream of "no match" answers that look like data.
`file:...?mode=ro` fails instead, which is the answer an operator can act
on. It also guarantees this plugin never writes to a database the
operator may be sharing with OpenEmu.

**The schema is checked before it is trusted.** Four tables, named here,
because "you pointed me at some other SQLite file" is a much more likely
mistake than "OpenVGDB changed its schema" -- the database has not been
republished since 2021.

**Hashes are stored upper-case and bare.** `romHashCRC` is `46DF91AD`,
not `0x46df91ad` and not lower case, so every lookup upper-cases its
argument rather than hoping the caller did.

**There are no indexes at all.** `SELECT name FROM sqlite_master WHERE
type='index'` returns nothing on release v29.0, so every query below is a
scan of 51,742 rows. That is a few milliseconds and not worth a schema
migration on a file the operator may share with another program.
"""

import sqlite3
from pathlib import Path

REQUIRED_TABLES = frozenset({"ROMs", "RELEASES", "SYSTEMS", "REGIONS"})

# `RELEASES.regionLocalizedID` -> `REGIONS.regionName`, best first, when the
# rom's own name says nothing about where it is from. The same order the
# rest of this repo uses to choose between releases of one title.
REGION_PREFERENCE: tuple[str, ...] = ("World", "USA", "Europe", "Japan")


class DatabaseUnavailable(Exception):
    """`db_path` does not point at a readable OpenVGDB."""


class Rom:
    """One `ROMs` row and the `RELEASES` rows that hang off it."""

    __slots__ = ("rom_id", "file_name", "serial", "crc", "md5", "sha1", "releases")

    def __init__(self, row: sqlite3.Row):
        self.rom_id = row["romID"]
        self.file_name = row["romFileName"]
        self.serial = row["romSerial"]
        self.crc = row["romHashCRC"]
        self.md5 = row["romHashMD5"]
        self.sha1 = row["romHashSHA1"]
        self.releases: list[dict] = []


def open_database(path: str) -> sqlite3.Connection:
    """Open an OpenVGDB read-only, or say precisely what is wrong."""
    if not path:
        raise DatabaseUnavailable(
            "openvgdb needs a local copy of openvgdb.sqlite and has none: set "
            "`db_path` in the plugin's config. The Hub cannot fetch it for "
            "this plugin -- see the plugin's README, 'Why the database is not "
            "downloaded'. Get it from "
            "https://github.com/OpenVGDB/OpenVGDB/releases/latest "
            "(openvgdb.zip, 8.7 MiB; 40 MiB unpacked) and unzip it anywhere."
        )

    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise DatabaseUnavailable(
            f"db_path {str(resolved)!r} is not a file. It should be the "
            f"openvgdb.sqlite unpacked from OpenVGDB's release asset."
        )

    uri = resolved.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise DatabaseUnavailable(
            f"could not open {str(resolved)!r} as a read-only SQLite "
            f"database: {exc}"
        ) from exc
    connection.row_factory = sqlite3.Row

    try:
        present = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    except sqlite3.DatabaseError as exc:
        connection.close()
        raise DatabaseUnavailable(
            f"{str(resolved)!r} is not a SQLite database ({exc})"
        ) from exc

    missing = sorted(REQUIRED_TABLES - present)
    if missing:
        connection.close()
        raise DatabaseUnavailable(
            f"{str(resolved)!r} is a SQLite database but not an OpenVGDB: it "
            f"has no {', '.join(missing)} table. OpenVGDB's release asset "
            f"unpacks to a file named openvgdb.sqlite."
        )
    return connection


_SELECT_ROM = """
    SELECT romID, systemID, romHashCRC, romHashMD5, romHashSHA1,
           romFileName, romExtensionlessFileName, romSerial
      FROM ROMs
     WHERE systemID = ?
"""

_SELECT_RELEASES = """
    SELECT r.releaseID, r.romID, r.releaseTitleName, r.releaseCoverFront,
           r.releaseCoverBack, r.releaseDeveloper, r.releasePublisher,
           r.releaseGenre, r.releaseDate, g.regionName AS regionName
      FROM RELEASES r
      LEFT JOIN REGIONS g ON g.regionID = r.regionLocalizedID
     WHERE r.romID = ?
"""


def by_hash(connection, system_id: int, kind: str, digest: str) -> list[Rom]:
    """ROM rows whose `crc`/`md5`/`sha1` is `digest`, within one system."""
    column = {"crc": "romHashCRC", "md5": "romHashMD5", "sha1": "romHashSHA1"}[kind]
    rows = connection.execute(
        f"{_SELECT_ROM} AND UPPER({column}) = ?", (system_id, digest.upper())
    ).fetchall()
    return _with_releases(connection, rows)


def by_serial(connection, system_id: int, serial: str) -> list[Rom]:
    rows = connection.execute(
        f"{_SELECT_ROM} AND UPPER(romSerial) = ?", (system_id, serial.upper())
    ).fetchall()
    return _with_releases(connection, rows)


def by_filename(connection, system_id: int, name: str) -> list[Rom]:
    """ROM rows matching a filename **exactly**, ignoring case and extension.

    Exact, never fuzzy. `romExtensionlessFileName` is compared too so a
    library that stores `Tetris (World) (Rev A).zip` still meets a
    database that stores `.gb` -- but nothing is stripped beyond the
    extension, so `Tetris` will not pick up `Tetris 2`.
    """
    stem = name.rsplit(".", 1)[0] if "." in name else name
    rows = connection.execute(
        f"{_SELECT_ROM} AND (UPPER(romFileName) = ? "
        f"OR UPPER(romExtensionlessFileName) = ?)",
        (system_id, name.upper(), stem.upper()),
    ).fetchall()
    return _with_releases(connection, rows)


def _with_releases(connection, rows) -> list[Rom]:
    out = []
    for row in rows:
        rom = Rom(row)
        rom.releases = [
            dict(release)
            for release in connection.execute(_SELECT_RELEASES, (rom.rom_id,))
        ]
        out.append(rom)
    return out


def rank_release(release: dict, preferred: str | None) -> tuple:
    """Sort key for one release of a title. Lower is better.

    The region the operator's own filename mentions wins outright; after
    that, `REGION_PREFERENCE`; after that, the release id, so the choice
    is stable rather than whatever SQLite happened to return first.
    """
    region = release.get("regionName") or ""
    if preferred and region == preferred:
        rank = -1
    elif region in REGION_PREFERENCE:
        rank = REGION_PREFERENCE.index(region)
    else:
        rank = len(REGION_PREFERENCE)
    return (rank, release.get("releaseID") or 0)
