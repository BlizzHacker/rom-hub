"""Core id -> the system that core emulates.

**This used to be a hand-kept table and is not one any more.** The
previous version mapped 106 core ids to a system by hand and answered
`None` for the other 112 the buildbot ships, with an honest explanation:
libretro published the mapping only inside `assets/frontend/info.zip`, and
`ctx.http` returns text, so a plugin could not open it.

That explanation stopped being true. libretro also publishes the same data
as 305 plain-text `.info` files in `libretro/libretro-core-info` (MIT,
verified from its own COPYING), one per core, each stating the system, the
manufacturer, the core's own licence, the file extensions it loads and the
BIOS files it needs. `scripts/render_core_info.py` reads them and writes
`coreinfo.py`; this module is now the lookup over that.

The properties that made the old table worth trusting are kept, and one is
strengthened:

* **Absence is still the answer, not a gap to be filled by guessing.**
  `system_for` returns None for a core libretro says nothing about -- 25
  of the 305 -- and `CoreArtifact.system` is left unset rather than
  approximated. `2048` is not a console.
* **The vocabulary is libretro's own**, and now literally so: these are
  the `systemname` strings from libretro's own files rather than a
  transcription of them.
* **Coverage went from 106 hand-written rows to 280 sourced ones**, which
  is the substantive change. A core's system is the column an operator
  scans, and it was blank for half the catalogue.

`CORE_SYSTEMS` is preserved as a name because `scripts/audit_platforms.py`
and the tests read it, and because a `{core_id: system}` mapping is still
the useful shape for anything asking "what does this plugin cover".
"""

from .coreinfo import CORE_INFO
from .coreinfo import system_for as _system_for

#: Core id -> emulated system, in libretro's own spelling. Derived from
#: `coreinfo.CORE_INFO` rather than written out again: two copies of the
#: same mapping is one copy that goes stale.
#:
#: A core libretro does not name a system for is **absent** rather than
#: present-with-None, so `core_id in CORE_SYSTEMS` means "this plugin can
#: tell you what it runs".
CORE_SYSTEMS: dict[str, str] = {
    core_id: row["system"]
    for core_id, row in CORE_INFO.items()
    if row.get("system")
}


def system_for(core_id: str) -> str | None:
    """The system a core emulates, or None when libretro does not say.

    None is a fact about libretro's data, never an instruction to
    substitute something. Callers leave `CoreArtifact.system` unset rather
    than filling it in -- a plausible-looking guess in a column an operator
    reads while choosing is worse than a blank.
    """
    return _system_for(core_id)
