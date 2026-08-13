"""Fail CI when the installable ROM Hub wheel loses its plugin catalogue."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path


MEMBER = "rom_hub/catalog/plugins.json"


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: check_wheel_catalog.py path/to/rom_hub.whl")

    wheel = Path(sys.argv[1])
    with zipfile.ZipFile(wheel) as archive:
        try:
            raw = archive.read(MEMBER)
        except KeyError as exc:
            raise SystemExit(f"{wheel.name} does not contain {MEMBER}") from exc

    catalogue = json.loads(raw)
    plugins = catalogue.get("plugins") or []
    if not plugins:
        raise SystemExit(f"{MEMBER} contains no plugins")

    print(f"{wheel.name}: {len(plugins)} bundled plugin(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
