"""RPP v1 wire types.

These validate data coming back from untrusted plugin subprocesses, so
constraints here are load-bearing rather than cosmetic.
"""

import posixpath

from pydantic import BaseModel, Field, field_validator


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
        if v in {".", ".."}:
            raise ValueError("filename must not be a path segment")
        if "/" in v or "\\" in v or v != posixpath.basename(v):
            raise ValueError("filename must be a bare name, not a path")
        return v


class FetchPlan(BaseModel):
    files: list[FetchFile] = Field(min_length=1)
    platform: str = Field(min_length=1)
    collection: str | None = None
