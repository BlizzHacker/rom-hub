"""RPP v1 wire types.

These validate data coming back from untrusted plugin subprocesses, so
constraints here are load-bearing rather than cosmetic.
"""

from pydantic import BaseModel, Field


class SearchResult(BaseModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    platform: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    url: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)
    # Set by the host after the plugin returns; plugins cannot forge it.
    plugin: str = ""
