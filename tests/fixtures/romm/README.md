# RomM source captured verbatim

`ejs_cores_map.ts` is the `_EJS_CORES_MAP` / `_EJS_NIGHTLY_CORES_MAP` region of
RomM 4.9.2's `frontend/src/utils/index.ts`, copied byte for byte.

It is here so `tests/test_playability.py` can pin `rom_hub.playability` against
RomM's own text rather than against a second hand-maintained copy of it. When
RomM ships new cores, replace this file from the new release and re-run the
suite: the test names every slug that has appeared or disappeared, which is the
list of edits `playability.py` needs.
