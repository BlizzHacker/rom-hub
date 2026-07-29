"""RPP v1 wire types.

These validate data coming back from untrusted plugin subprocesses, so
constraints here are load-bearing rather than cosmetic.
"""

from pathlib import PurePosixPath, PureWindowsPath

from pydantic import BaseModel, Field, field_validator

# Windows refuses these whatever the extension, and several of them are
# devices rather than files: bytes written to NUL are silently discarded,
# so the ROM would hash as empty and the upload would then fail with a
# misleading "cannot upload empty file".
_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)

# An allowlist of permitted characters, not a denylist of bad forms. The
# previous version of this validator enumerated the bad forms it could
# think of -- separators, "." and ".." -- and was breached by one it had
# not: "C:evil.zip" carries no separator, so posixpath.basename left it
# alone, but on Windows `job_dir / "C:evil.zip"` discards the job
# directory entirely and resolves against C:'s current directory. A
# denylist of path syntax cannot be finished; a list of what a ROM
# filename may contain can be. `str.isalnum` is unicode-aware, so real
# non-ASCII names still pass.
_ALLOWED_PUNCTUATION = frozenset(" .-_()[]+,'!&~@#=")

# Long enough for any real ROM name, short enough to stay clear of
# NAME_MAX (255) and of Windows' 260-character full-path ceiling once the
# job directory has been prepended.
_MAX_FILENAME_CHARS = 200


class SearchResult(BaseModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    platform: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    url: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)
    # Set by the host after the plugin returns; plugins cannot forge it.
    plugin: str = ""


class FetchFile(BaseModel):
    url: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    size_bytes: int | None = Field(default=None, ge=0)

    @field_validator("filename")
    @classmethod
    def _bare_name_only(cls, v: str) -> str:
        # The host writes this to disk. A plugin must not be able to point
        # that write anywhere but the job's own download directory.
        #
        # Every rule below is applied on every platform. A name refused on
        # Linux must be refused on Windows and vice versa: if this
        # validator's answer depended on which OS the Hub happens to run
        # on, a plugin could pick a name that is inert on the developer's
        # machine and an escape on the operator's.
        if len(v) > _MAX_FILENAME_CHARS:
            raise ValueError(
                f"filename must be at most {_MAX_FILENAME_CHARS} characters"
            )

        bad = sorted({c for c in v if not (c.isalnum() or c in _ALLOWED_PUNCTUATION)})
        if bad:
            raise ValueError(
                f"filename contains characters that are not permitted in a "
                f"ROM filename: {bad!r}"
            )

        # Redundant given the character allowlist -- ":", "/" and "\" are
        # all excluded by it already -- but stated separately so that the
        # invariant survives any future widening of that allowlist.
        if PureWindowsPath(v).parts != (v,) or PurePosixPath(v).parts != (v,):
            raise ValueError(
                "filename must be a single bare name: no drive, anchor, "
                "UNC prefix or path separator, under either Windows or "
                "POSIX path rules"
            )

        # "." and ".." and anything else that is only dots and spaces:
        # "..." resolves to a *directory*, which makes dest.exists() true
        # and seeds the resume logic with a bogus offset.
        if not v.strip(". "):
            raise ValueError("filename must not be made only of dots and spaces")

        # Windows silently strips a trailing dot or space, so "g.zip." and
        # "g.zip " both open the same file as "g.zip" -- two plan entries
        # that look distinct would collide on disk.
        if v != v.rstrip(". "):
            raise ValueError("filename must not end in a dot or a space")

        if v.split(".")[0].upper() in _RESERVED_STEMS:
            raise ValueError(
                "filename must not be a Windows reserved device name "
                f"(got {v!r})"
            )
        return v


class FetchPlan(BaseModel):
    files: list[FetchFile] = Field(min_length=1)
    platform: str = Field(min_length=1)
    collection: str | None = None

    @field_validator("files")
    @classmethod
    def _filenames_must_be_distinct(cls, v: list[FetchFile]) -> list[FetchFile]:
        # Two entries writing to one path do not merely overwrite. The
        # second download finds the first file already on disk, takes its
        # size as a resume offset, sends `Range: bytes=<n>-`, and a server
        # that honours Range (Archive.org does) answers 206 -- so the
        # second file's tail is *appended* to the first file's body. The
        # result hashes cleanly, uploads once per entry, and reports DONE.
        #
        # Refuse the plan rather than renaming one of them: a plugin that
        # names two files identically has a bug, and quietly fixing it up
        # hides the bug while still importing something nobody asked for.
        # Compared case-insensitively because Windows opens "g.zip" and
        # "G.zip" as the same file, and this must not depend on the OS.
        names = [f.filename.casefold() for f in v]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        if duplicated:
            raise ValueError(
                f"every file in a FetchPlan needs a distinct filename; "
                f"these are repeated: {duplicated!r}"
            )
        return v
