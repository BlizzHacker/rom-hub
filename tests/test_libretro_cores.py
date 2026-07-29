"""libretro-cores, replayed against a captured buildbot index.

`tests/fixtures/libretro_cores/` holds two verbatim `.index-extended`
bodies -- Linux x86_64 and Windows x86_64, 218 lines each, captured
2026-07-29. Both are here because the pair is what proves the suffix
table does any work: a core is `.so.zip` in one and `.dll.zip` in the
other, so a plugin that ignored the target would still pass a
single-fixture test and hand out the wrong architecture.

The Windows body also happens to contain two lines that are the bare
string ".zip" -- upstream junk, re-confirmed against the live buildbot
rather than assumed to be a capture artefact. It is kept verbatim, which
is why the two fixtures yield 218 and 216 cores respectively.

No test opens a socket.
"""

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "plugins-dev" / "libretro-cores"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "libretro_cores"
sys.path.insert(0, str(PLUGIN_ROOT))

from libretro_cores.cores import (  # noqa: E402
    MAX_CORES,
    CoreListError,
    Cores,
    UnknownCore,
)
from libretro_cores.filenames import safe_filename  # noqa: E402
from libretro_cores.index import IndexError_, core_id_for, parse_index  # noqa: E402
from libretro_cores.systems import CORE_SYSTEMS, system_for  # noqa: E402
from libretro_cores.targets import TARGETS, NeedsMapping, target_for  # noqa: E402

from rom_hub.types import CoreArtifact, bare_filename  # noqa: E402
from rom_hub_sdk.context import HttpResponse, PluginContext  # noqa: E402

LINUX = (FIXTURES / "index_linux_x86_64.txt").read_text(encoding="utf-8")
WINDOWS = (FIXTURES / "index_windows_x86_64.txt").read_text(encoding="utf-8")

#: Both captured indexes have 218 lines. The Windows one only yields 216
#: cores, because two of its lines are the bare string ".zip" -- genuine
#: upstream junk, re-confirmed against the live buildbot, not a capture
#: artefact. That difference is asserted rather than papered over: it is
#: the real-world case the parser's skip-a-line rule exists for.
LINUX_CORES = 218
WINDOWS_CORES = 216


class FakeHttp:
    """Serves one body, and records every URL it was asked for."""

    def __init__(self, body=LINUX, status=200):
        self.body = body
        self.status = status
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        return HttpResponse(status_code=self.status, text=self.body)


def make_cores(body=LINUX, config=None, status=200):
    http = FakeHttp(body, status)
    return Cores(PluginContext(config=config or {}, http=http)), http


# ----------------------------------------------------------- index parsing


def test_the_captured_index_parses_completely():
    """218 lines in, 218 cores out. A silently-dropped core is a core an
    operator cannot install and has no way to ask about."""
    entries = parse_index(LINUX, ".so.zip")
    assert len(entries) == LINUX_CORES
    assert len(LINUX.strip().splitlines()) == LINUX_CORES


def test_real_upstream_junk_costs_its_own_line_and_nothing_else():
    """The live Windows index carries two lines that are the bare string
    ".zip". Confirmed against the buildbot, not a capture artefact. The
    right answer is 216 cores, not a parse error and not 218."""
    assert [line for line in WINDOWS.splitlines() if line.strip() == ".zip"]
    assert len(parse_index(WINDOWS, ".dll.zip")) == WINDOWS_CORES


def test_entries_come_back_sorted_by_core_id():
    """The buildbot emits build order, which changes nightly. `cores list`
    must not reshuffle between two runs that found the same cores."""
    ids = [e.core_id for e in parse_index(LINUX, ".so.zip")]
    assert ids == sorted(ids)


def test_a_known_core_carries_its_filename_and_build_date():
    entry = next(e for e in parse_index(LINUX, ".so.zip") if e.core_id == "gambatte")
    assert entry.filename == "gambatte_libretro.so.zip"
    assert entry.built.startswith("20")


def test_the_suffix_decides_what_counts_as_a_core():
    """The Windows index holds the same cores under `.dll.zip`. Parsed with
    the Linux suffix it yields nothing, which is the point: the target is
    not decoration."""
    assert len(parse_index(WINDOWS, ".dll.zip")) == WINDOWS_CORES
    with pytest.raises(IndexError_):
        parse_index(WINDOWS, ".so.zip")


def test_core_id_strips_the_libretro_marker_and_the_suffix():
    assert core_id_for("2048_libretro.so.zip", ".so.zip") == "2048"
    assert core_id_for("mednafen_supergrafx_libretro.so.zip", ".so.zip") == (
        "mednafen_supergrafx"
    )
    assert core_id_for("gambatte_libretro.dll.zip", ".so.zip") is None


def test_a_stem_that_does_not_end_in_libretro_keeps_its_whole_name():
    """The buildbot really ships these -- `reminiscence_libretro_ios` is in
    the iOS index. Dropping the core or guessing where to cut are both
    worse than an id that matches the file."""
    assert core_id_for("reminiscence_libretro_ios.dylib.zip", ".dylib.zip") == (
        "reminiscence_libretro_ios"
    )


@pytest.mark.parametrize(
    "line",
    [
        "2026-07-29 edf888ae ../../../etc/passwd.so.zip",
        "2026-07-29 edf888ae sub/dir/core_libretro.so.zip",
        "2026-07-29 edf888ae C:evil_libretro.so.zip",
        "2026-07-29 edf888ae .hidden_libretro.so.zip",
        "2026-07-29 edf888ae core libretro.so.zip",
        "not-a-date edf888ae core_libretro.so.zip",
        "2026-07-29 core_libretro.so.zip",
    ],
)
def test_a_line_that_is_not_a_plain_bare_name_is_skipped(line):
    """The host opens this string for writing. A name that could be read as
    a path must never reach it, so the parser refuses rather than repairs."""
    with pytest.raises(IndexError_):
        parse_index(line, ".so.zip")


def test_a_bad_line_costs_one_core_and_not_the_index():
    body = "2026-07-29 aaaaaaaa a/b_libretro.so.zip\n" + LINUX
    assert len(parse_index(body, ".so.zip")) == LINUX_CORES


def test_a_body_that_is_not_an_index_is_refused_rather_than_read_as_empty():
    """An empty list would be indistinguishable from a healthy build target
    with no cores, which does not exist."""
    with pytest.raises(IndexError_, match="no core entries"):
        parse_index("<html><body>503 Service Unavailable</body></html>", ".so.zip")


def test_a_repeated_core_id_resolves_deterministically():
    body = (
        "2026-07-29 aaaaaaaa gambatte_libretro.so.zip\n"
        "2020-01-01 bbbbbbbb gambatte_libretro.so.zip\n"
    )
    entries = parse_index(body, ".so.zip")
    assert [e.built for e in entries] == ["2026-07-29"]


# ------------------------------------------------------------ every filename


def test_every_filename_in_the_real_index_is_a_bare_name():
    """Asserted against the host's own validator, not a copy of its rules."""
    for entry in parse_index(LINUX, ".so.zip"):
        assert bare_filename(entry.filename) == entry.filename


def test_the_sanitiser_keeps_the_compound_extension():
    long_name = "x" * 400 + "_libretro.so.zip"
    out = safe_filename(long_name)
    assert out.endswith(".so.zip")
    assert len(out) <= 200
    assert bare_filename(out) == out


def test_the_sanitiser_is_deterministic():
    name = "y" * 400 + "_libretro.dll.zip"
    assert safe_filename(name) == safe_filename(name)


@pytest.mark.parametrize(
    "evil",
    ["../../etc/passwd", "a/b.so.zip", "a\\b.so.zip", "C:evil.so.zip", "NUL.so.zip"],
)
def test_the_sanitiser_defuses_a_path(evil):
    assert bare_filename(safe_filename(evil)) == safe_filename(evil)


# ----------------------------------------------------------------- targets


def test_an_unknown_target_is_refused_by_name_never_defaulted():
    with pytest.raises(NeedsMapping, match="needs mapping"):
        target_for("plan9/vax")


def test_the_refusal_lists_what_is_available():
    with pytest.raises(NeedsMapping, match="linux/x86_64"):
        target_for("")


def test_target_lookup_is_case_and_separator_tolerant():
    assert target_for("Linux/X86_64") is TARGETS["linux/x86_64"]
    assert target_for("windows\\x86_64") is TARGETS["windows/x86_64"]


def test_every_target_builds_urls_on_the_declared_host():
    """The manifest allows exactly one host. A row pointing anywhere else
    would be a policy violation per request rather than a bug here."""
    for target in TARGETS.values():
        assert target.index_url.startswith("https://buildbot.libretro.com/nightly/")
        assert target.file_url("x.so.zip").startswith(
            "https://buildbot.libretro.com/nightly/"
        )


# ----------------------------------------------------------------- systems


def test_a_core_the_table_does_not_know_has_no_system():
    assert system_for("2048") is None
    assert system_for("mame2003_plus") == "Arcade"


def test_every_mapped_core_id_is_in_the_real_index():
    """A table row for a core the buildbot does not ship is a row nobody
    will ever see fire, and quietly rots."""
    available = {e.core_id for e in parse_index(LINUX, ".so.zip")}
    unknown = sorted(set(CORE_SYSTEMS) - available)
    assert unknown == [], f"systems.py names cores the buildbot does not ship: {unknown}"


def test_every_system_name_fits_the_wire_type():
    for system in set(CORE_SYSTEMS.values()):
        assert 1 <= len(system) <= 64


# ------------------------------------------------------------------- list()


def test_list_returns_the_whole_catalogue_as_core_artifacts():
    cores, http = make_cores()
    listed = cores.list()
    assert len(listed) == LINUX_CORES
    assert all(isinstance(c, CoreArtifact) for c in listed)
    assert http.calls == [
        "https://buildbot.libretro.com/nightly/linux/x86_64/latest/.index-extended"
    ]


def test_a_listed_core_carries_the_build_date_as_its_version():
    cores, _ = make_cores()
    gambatte = next(c for c in cores.list() if c.core_id == "gambatte")
    assert gambatte.system == "Nintendo - GameBoy"
    assert gambatte.version.startswith("20")
    assert "linux" in gambatte.description.lower()


def test_a_core_outside_the_systems_table_lists_with_no_system():
    cores, _ = make_cores()
    game = next(c for c in cores.list() if c.core_id == "2048")
    assert game.system is None


def test_the_target_config_chooses_the_url_and_the_suffix():
    cores, http = make_cores(WINDOWS, {"target": "windows/x86_64"})
    assert len(cores.list()) == WINDOWS_CORES
    assert http.calls == [
        "https://buildbot.libretro.com/nightly/windows/x86_64/latest/.index-extended"
    ]


def test_an_unknown_target_refuses_before_any_request():
    cores, http = make_cores(config={"target": "plan9/vax"})
    with pytest.raises(NeedsMapping):
        cores.list()
    assert http.calls == []


def test_only_narrows_the_catalogue():
    cores, _ = make_cores(config={"only": ["gambatte", "snes9x", "nope"]})
    assert sorted(c.core_id for c in cores.list()) == ["gambatte", "snes9x"]


def test_a_catalogue_over_the_host_limit_is_refused_with_the_way_out():
    body = "\n".join(
        f"2026-07-29 aaaaaaaa core{i:04d}_libretro.so.zip"
        for i in range(MAX_CORES + 1)
    )
    cores, _ = make_cores(body)
    with pytest.raises(CoreListError, match="`only`"):
        cores.list()


def test_a_non_200_names_the_status_and_the_url():
    cores, _ = make_cores(status=503)
    with pytest.raises(IndexError_, match="503"):
        cores.list()


# ------------------------------------------------------------------- plan()


def test_plan_builds_the_download_url_from_the_index():
    cores, _ = make_cores()
    plan = cores.plan(CoreArtifact(core_id="gambatte", name="gambatte"))
    assert len(plan.files) == 1
    assert plan.files[0].url == (
        "https://buildbot.libretro.com/nightly/linux/x86_64/latest/"
        "gambatte_libretro.so.zip"
    )
    assert plan.files[0].filename == "gambatte_libretro.so.zip"
    assert plan.files[0].size_bytes is None


def test_plan_re_reads_the_index_rather_than_trusting_the_artifact():
    """The CoreArtifact made a round trip through the host and the operator's
    command line. Building a URL out of its fields would mean believing a
    value this plugin did not construct."""
    cores, http = make_cores()
    forged = CoreArtifact(
        core_id="gambatte", name="totally-different", version="9.9", system="Nowhere"
    )
    plan = cores.plan(forged)
    assert plan.files[0].filename == "gambatte_libretro.so.zip"
    assert len(http.calls) == 1


def test_plan_refuses_a_core_that_is_not_in_this_target():
    cores, _ = make_cores()
    with pytest.raises(UnknownCore, match="cores list"):
        cores.plan(CoreArtifact(core_id="not-a-core", name="x"))


def test_plan_refuses_a_linux_core_asked_for_against_the_windows_target():
    """`2048` exists in both, but the file does not: a plan built from the
    wrong index would 404 at best and install the wrong architecture at
    worst."""
    cores, _ = make_cores(WINDOWS, {"target": "windows/x86_64"})
    plan = cores.plan(CoreArtifact(core_id="2048", name="2048"))
    assert plan.files[0].filename == "2048_libretro.dll.zip"


def test_plan_labels_the_platform_with_the_system_when_it_knows_one():
    cores, _ = make_cores()
    assert cores.plan(
        CoreArtifact(core_id="gambatte", name="g")
    ).platform == "Nintendo - GameBoy"


def test_plan_falls_back_to_the_build_target_never_to_a_guess():
    cores, _ = make_cores()
    plan = cores.plan(CoreArtifact(core_id="2048", name="2048"))
    assert plan.platform == "Linux x86_64"


def test_every_core_in_the_catalogue_can_be_planned():
    """The catalogue is a promise. A core `list` offers and `plan` refuses
    is a promise broken at the moment somebody acts on it."""
    cores, _ = make_cores()
    for core in cores.list():
        plan = cores.plan(core)
        assert plan.files[0].url.endswith(".so.zip")
        assert bare_filename(plan.files[0].filename) == plan.files[0].filename
