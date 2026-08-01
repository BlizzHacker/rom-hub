"""Reading `ludusavi-manifest`'s `data/manifest.yaml`, without a YAML library.

The file is 17,460,574 bytes and 870,349 lines describing 52,886 games. It
arrives through `[[data_assets]]`, so what this module gets is a path to
bytes the host has already verified against the sha256 in `manifest.toml`.

**Why there is a parser here at all.** `ctx.http` caps a response at 4 MiB
and carries text, so the file cannot come down that channel; that is what
data assets are for and the choice is easy. What is not easy is *reading*
it: the Hub depends on pydantic, httpx and python-socketio, and **PyYAML is
not among them**. A plugin cannot add a host dependency, and adding one to
`rom-hub` so that one plugin can read one file would put a C-accelerated
YAML loader in every operator's install for a source most of them do not
use. So this module implements the small, closed subset of YAML that
`serde_yaml` actually emits, and refuses anything outside it rather than
guessing.

The subset, verified against the whole file rather than assumed:

* two-space block indentation, no tabs (0 tab characters in the file)
* block mappings and block sequences only; `{}` and `[]` appear as empty
  collections and never with contents
* scalars are double-quoted strings, bare strings, integers or booleans
* **no** anchors, aliases, block scalars, multi-document markers or tags
  (`|` appears 0 times; `&`, `*` and `!!` appear only inside quoted
  strings)

Anything this parser does not recognise raises `ManifestUnreadable` naming
the line. A YAML reader that quietly skips what it does not understand
would turn an upstream format change into wrong save paths rather than a
visible failure, and wrong save paths are the one outcome this plugin
exists to avoid.

**One pass, no index.** `find()` walks the file once, normalising each
top-level title and keeping only the blocks whose key is one the caller
asked for. That is ~0.3 s and a few kilobytes of memory for one rom, and
it means nothing is cached between calls that could go stale. An index
over all 52,886 titles would save time on `enrich --all` and cost 15 MB;
the tradeoff is available and deliberately not taken.
"""

from dataclasses import dataclass, field

#: Ludusavi's own tag vocabulary for a file or registry entry. `save` is
#: what this plugin is for; `config` is carried because "where are the
#: settings" is the same question with the same answer shape, and the
#: consumer can tell them apart from the tags.
SAVE_TAG = "save"
CONFIG_TAG = "config"

#: A game's block is a description, not a payload. The largest real block
#: in the manifest is well under this; a file that suddenly has a
#: million-line block for one title is a file that changed shape.
MAX_BLOCK_LINES = 5000

#: How many games one key may resolve to before this stops collecting them
#: for the refusal message. The refusal happens either way.
MAX_AMBIGUOUS = 8


class ManifestUnreadable(Exception):
    """The manifest could not be read, or is not in the shape expected."""


@dataclass(frozen=True)
class Location:
    """One place a game keeps something, as the manifest describes it."""

    #: The path (with ludusavi's `<base>`, `<winAppData>` … placeholders
    #: left in) or the registry key.
    where: str
    #: `save`, `config`, or both. Empty when the manifest gives none.
    tags: tuple[str, ...] = ()
    #: The conditions under which it applies: `{"os": "windows"}`,
    #: `{"store": "steam"}`, `{"bit": 64}`. Empty means unconditional.
    when: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        out: dict = {"path": self.where}
        if self.tags:
            out["tags"] = list(self.tags)
        if self.when:
            out["when"] = [dict(condition) for condition in self.when]
        return out


@dataclass
class Game:
    """One manifest entry, reduced to what this plugin reports."""

    title: str
    files: list[Location] = field(default_factory=list)
    registry: list[Location] = field(default_factory=list)
    steam_id: int | None = None
    #: Which stores sync this game's saves to their own cloud, per the
    #: manifest's `cloud` block.
    cloud: dict = field(default_factory=dict)

    def has_locations(self) -> bool:
        return bool(self.files or self.registry)


def find(path: str, wanted: set[str], normalise) -> dict[str, list[Game]]:
    """Every manifest entry whose normalised title is in `wanted`.

    Returns `{normalised key: [Game, ...]}`, with the list carrying more
    than one game exactly when the manifest has two titles that normalise
    the same way -- which happens for 243 keys covering 491 titles, so it
    is the ordinary case rather than a corner one, and the caller has to
    decide what to do about it.
    """
    if not wanted:
        return {}
    found: dict[str, list[Game]] = {}
    try:
        with open(path, encoding="utf-8") as handle:
            block: list[str] = []
            key = ""
            title = ""
            for line in handle:
                if line[:1] in (" ", "\t", "", "\n", "\r") or line[:1] == "#":
                    if key:
                        if len(block) > MAX_BLOCK_LINES:
                            raise ManifestUnreadable(
                                f"the entry for {title!r} is over "
                                f"{MAX_BLOCK_LINES} lines; this file is a "
                                f"description of save locations and that is "
                                f"not the shape of one"
                            )
                        block.append(line.rstrip("\n"))
                    continue
                if line.startswith("- ") or line.rstrip() in ("---", "..."):
                    # A document marker, or a top-level sequence. The
                    # manifest is one mapping document; neither belongs to
                    # an entry, so whatever was open is finished.
                    if key:
                        _keep(found, key, title, block)
                    key = ""
                    block = []
                    continue
                if key:
                    _keep(found, key, title, block)
                title = _unquote_key(_split_key(line.rstrip("\n"), title)[0])
                candidate = normalise(title)
                key = candidate if candidate in wanted else ""
                block = []
            if key:
                _keep(found, key, title, block)
    except OSError as exc:
        raise ManifestUnreadable(
            f"the ludusavi manifest at {path!r} could not be read: {exc}"
        ) from exc
    return found


def _keep(found, key, title, block) -> None:
    games = found.setdefault(key, [])
    if len(games) >= MAX_AMBIGUOUS:
        return
    games.append(parse_game(title, block))


# -- the YAML subset -----------------------------------------------------


def parse_game(title: str, block: list[str]) -> Game:
    """One game's block, as a `Game`. `block` excludes the title line."""
    data = parse_block(block, label=title)
    if not isinstance(data, dict):
        raise ManifestUnreadable(
            f"the entry for {title!r} is a {type(data).__name__}, and every "
            f"entry in this manifest is a mapping"
        )
    game = Game(title=title)
    game.files = _locations(data.get("files"), title, "files")
    game.registry = _locations(data.get("registry"), title, "registry")
    steam = data.get("steam")
    if isinstance(steam, dict) and isinstance(steam.get("id"), int):
        game.steam_id = steam["id"]
    cloud = data.get("cloud")
    if isinstance(cloud, dict):
        game.cloud = {k: v for k, v in cloud.items() if isinstance(v, bool)}
    return game


def _locations(raw, title: str, section: str) -> list[Location]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise ManifestUnreadable(
            f"{title!r}: the {section!r} section is a {type(raw).__name__}, "
            f"and the schema says it is a mapping of path to properties"
        )
    out: list[Location] = []
    for where, properties in raw.items():
        tags: tuple[str, ...] = ()
        when: tuple[dict, ...] = ()
        if isinstance(properties, dict):
            raw_tags = properties.get("tags")
            if isinstance(raw_tags, list):
                tags = tuple(t for t in raw_tags if isinstance(t, str))
            raw_when = properties.get("when")
            if isinstance(raw_when, list):
                when = tuple(w for w in raw_when if isinstance(w, dict))
        out.append(Location(where=where, tags=tags, when=when))
    return out


def parse_block(lines: list[str], label: str = ""):
    """The subset parser. `lines` are a block's lines, indent included."""
    rows = []
    for line in lines:
        if not line.strip():
            continue
        if "\t" in line:
            raise ManifestUnreadable(
                f"{label!r}: a tab appears in {line!r}; this manifest is "
                f"space-indented and a tab is not indentation in YAML"
            )
        stripped = line.lstrip(" ")
        rows.append((len(line) - len(stripped), stripped.rstrip()))
    if not rows:
        return {}
    value, index = _parse(rows, 0, rows[0][0], label)
    if index != len(rows):
        raise ManifestUnreadable(
            f"{label!r}: the block does not parse as one value; stopped at "
            f"{rows[index][1]!r}"
        )
    return value


def _parse(rows, index: int, indent: int, label: str):
    if rows[index][1].startswith("-"):
        return _parse_sequence(rows, index, indent, label)
    return _parse_mapping(rows, index, indent, label)


def _parse_mapping(rows, index: int, indent: int, label: str):
    mapping: dict = {}
    while index < len(rows) and rows[index][0] == indent:
        text = rows[index][1]
        if text.startswith("- "):
            break
        key, rest = _split_key(text, label)
        key = _unquote_key(key)
        index += 1
        if rest:
            mapping[key] = _scalar(rest, label)
            continue
        if index < len(rows) and rows[index][0] > indent:
            mapping[key], index = _parse(rows, index, rows[index][0], label)
        else:
            mapping[key] = None
    return mapping, index


def _parse_sequence(rows, index: int, indent: int, label: str):
    items: list = []
    while index < len(rows) and rows[index][0] == indent:
        text = rows[index][1]
        if not text.startswith("-"):
            break
        rest = text[1:].lstrip(" ")
        index += 1
        if not rest:
            if index < len(rows) and rows[index][0] > indent:
                item, index = _parse(rows, index, rows[index][0], label)
            else:
                item = None
            items.append(item)
            continue
        if _looks_like_key(rest):
            # `- os: windows` followed by `  store: steam` at indent + 2.
            # Re-present the inline part as its own row so the mapping
            # parser sees one block.
            inner = [(indent + 2, rest)]
            while index < len(rows) and rows[index][0] > indent:
                inner.append(rows[index])
                index += 1
            item, consumed = _parse_mapping(inner, 0, indent + 2, label)
            if consumed != len(inner):
                raise ManifestUnreadable(
                    f"{label!r}: a sequence item does not parse; stopped at "
                    f"{inner[consumed][1]!r}"
                )
            items.append(item)
            continue
        items.append(_scalar(rest, label))
    return items, index


def _looks_like_key(text: str) -> bool:
    try:
        _, _ = _split_key(text, "")
    except ManifestUnreadable:
        return False
    return True


def _split_key(text: str, label: str) -> tuple[str, str]:
    """`'files:'` -> `('files', '')`; `'id: 12'` -> `('id', '12')`.

    A quoted key is scanned to its closing quote first, because a title
    like `"!4RC4N01D! 2: Retro Edition"` carries a colon and splitting on
    the first one would cut it in half.
    """
    if text.startswith('"'):
        position = 1
        while position < len(text):
            character = text[position]
            if character == "\\":
                position += 2
                continue
            if character == '"':
                break
            position += 1
        else:
            raise ManifestUnreadable(f"{label!r}: unterminated quoted key {text!r}")
        if position >= len(text) or not text[position + 1 :].startswith(":"):
            raise ManifestUnreadable(
                f"{label!r}: quoted key {text!r} is not followed by ':'"
            )
        return text[: position + 1], text[position + 2 :].strip()

    head, separator, rest = text.partition(":")
    if not separator:
        raise ManifestUnreadable(
            f"{label!r}: {text!r} is neither a mapping key nor a sequence item"
        )
    while rest and not (rest.startswith(" ") or rest == ""):
        # A bare key may not contain ": ", so a colon with no space after
        # it belongs to the key -- `HKEY.../Software/A:B` if it ever
        # appeared unquoted. Keep consuming until a real separator.
        extra_head, extra_separator, rest = rest.partition(":")
        if not extra_separator:
            head = head + ":" + extra_head
            rest = ""
            break
        head = head + ":" + extra_head
    return head, rest.strip()


def _unquote_key(key: str) -> str:
    if len(key) >= 2 and key.startswith('"') and key.endswith('"'):
        return key[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return key


def _scalar(text: str, label: str):
    if text in ("{}", "[]"):
        return {} if text == "{}" else []
    if text[:1] in ("&", "*", "|", ">") or text.startswith("!!"):
        raise ManifestUnreadable(
            f"{label!r}: {text!r} uses a YAML feature this reader does not "
            f"implement (anchor, alias, block scalar or tag). Refusing rather "
            f"than guessing what it means"
        )
    if text.startswith('"'):
        return _unquote_key(text)
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "null" or text == "~":
        return None
    try:
        return int(text)
    except ValueError:
        return text
