"""Latest versions from blawar's titledb `versions.json`.

The file maps a title ID to `{version: release date}`. It is about 1.7 MB,
under rom-hub's 4 MiB response limit, and is read once per search that has
an update to compare.
"""

from .names import base_title_id

VERSIONS_URL = "https://raw.githubusercontent.com/blawar/titledb/master/versions.json"


class TitleDBError(Exception):
    pass


def load_latest(http) -> dict[str, int]:
    """Map each base title ID, uppercase, to the highest version listed."""
    try:
        response = http.get(VERSIONS_URL)
    except Exception as exc:
        raise TitleDBError(f"titledb versions.json could not be read: {exc}") from None
    if response.status_code != 200:
        raise TitleDBError(f"titledb versions.json answered HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise TitleDBError("titledb versions.json is not JSON") from None
    if not isinstance(data, dict):
        raise TitleDBError("titledb versions.json is not an object")
    latest: dict[str, int] = {}
    for title_id, versions in data.items():
        if not isinstance(title_id, str) or len(title_id) != 16 or not isinstance(versions, dict):
            continue
        numbers = [int(v) for v in versions if isinstance(v, str) and v.isdigit()]
        if not numbers:
            continue
        try:
            key = base_title_id(title_id.upper())
        except ValueError:
            continue
        latest[key] = max(latest.get(key, 0), max(numbers))
    return latest
