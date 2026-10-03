"""What a GG Requestz request *means*: parsing, the log, and the match.

No test here opens a socket, starts a plugin, or touches a library
server. `fulfil` takes its search and its import as callables for exactly
that reason -- the thing being tested is the decision, and a decision
that can only be tested with a live RomM behind it is a decision nobody
will ever test.

The HTTP half is tests/test_webhook_server.py.
"""

import json
from types import SimpleNamespace

import pytest

from rom_hub.dispatcher import PluginStatus, SearchOutcome
from rom_hub.jobs import JobState
from rom_hub.types import SearchResult
from rom_hub.webhook import (
    MAX_PLATFORMS,
    FulfilmentRefused,
    RequestEvent,
    RequestLog,
    RequestState,
    WebhookPayloadError,
    fulfil,
    parse_event,
    platform_slugs,
)

# The payload GG Requestz actually posts, verbatim from its dispatcher.
PAYLOAD = {
    "type": "game_request",
    "title": "New Game Request: Chrono Trigger",
    "message": 'alice requested "Chrono Trigger"',
    "priority": 5,
    "timestamp": "2026-01-01T00:00:00.000Z",
    "data": {
        "request_id": "eac1cd44-5f6e-4f49-8ac1-9936066105a6",
        "user_id": "12",
        "game_title": "Chrono Trigger",
        "igdb_id": "1234",
        "platforms": ["Super Nintendo"],
        "request_type": "game",
    },
}


def payload(**data):
    """PAYLOAD with `data` overridden. `None` removes a key."""
    inner = dict(PAYLOAD["data"])
    for key, value in data.items():
        if value is None:
            inner.pop(key, None)
        else:
            inner[key] = value
    return {**PAYLOAD, "data": inner}


# --- parsing ------------------------------------------------------------


def test_the_documented_payload_parses():
    event = parse_event(json.dumps(PAYLOAD).encode())
    assert event.request_id == "eac1cd44-5f6e-4f49-8ac1-9936066105a6"
    assert event.game_title == "Chrono Trigger"
    assert event.igdb_id == "1234"
    assert event.platforms == ["Super Nintendo"]
    assert event.request_type == "game"
    assert event.user_id == "12"


def test_an_empty_platforms_array_is_legal():
    event = parse_event(json.dumps(payload(platforms=[])).encode())
    assert event.platforms == []
    # And it must not become a filter of the empty string, which every
    # plugin would refuse.
    assert platform_slugs(event.platforms) == ([], [])


def test_a_missing_igdb_id_is_legal():
    event = parse_event(json.dumps(payload(igdb_id=None)).encode())
    assert event.igdb_id is None


def test_a_null_igdb_id_is_the_same_as_a_missing_one():
    event = parse_event(json.dumps(payload(igdb_id=None)).encode())
    blank = parse_event(json.dumps(payload(igdb_id="")).encode())
    assert event.igdb_id is None
    assert blank.igdb_id is None


def test_an_igdb_id_sent_as_a_number_is_still_read():
    """Nothing in the contract stops the sender switching to a JSON int."""
    event = parse_event(json.dumps(payload(igdb_id=1234)).encode())
    assert event.igdb_id == "1234"


def test_an_igdb_id_sent_as_a_boolean_is_refused():
    """`True` would otherwise become the id "True" and be compared against
    whatever a source states -- a wrong match key is worse than none."""
    with pytest.raises(WebhookPayloadError):
        parse_event(json.dumps(payload(igdb_id=True)).encode())


def test_a_null_platforms_value_is_the_same_as_an_empty_array():
    explicit_null = {**PAYLOAD, "data": {**PAYLOAD["data"], "platforms": None}}
    event = parse_event(json.dumps(explicit_null).encode())
    assert event.platforms == []


def test_unknown_fields_are_ignored_rather_than_refused():
    """The sender is merged upstream and may grow fields; we may not break."""
    grown = payload(some_future_field={"nested": True})
    grown["another_top_level"] = 7
    event = parse_event(json.dumps(grown).encode())
    assert event.game_title == "Chrono Trigger"


def test_a_body_that_is_not_json_is_refused():
    with pytest.raises(WebhookPayloadError):
        parse_event(b"{not json")


def test_a_body_that_is_not_an_object_is_refused():
    with pytest.raises(WebhookPayloadError):
        parse_event(b"[1, 2, 3]")


def test_a_payload_with_no_data_object_is_refused():
    with pytest.raises(WebhookPayloadError):
        parse_event(json.dumps({"type": "game_request"}).encode())


def test_a_payload_with_no_request_id_is_refused():
    with pytest.raises(WebhookPayloadError) as exc:
        parse_event(json.dumps(payload(request_id=None)).encode())
    assert "request_id" in str(exc.value)


def test_a_payload_with_no_game_title_is_refused():
    with pytest.raises(WebhookPayloadError) as exc:
        parse_event(json.dumps(payload(game_title=None)).encode())
    assert "game_title" in str(exc.value)


def test_a_platforms_value_that_is_not_an_array_is_refused():
    with pytest.raises(WebhookPayloadError):
        parse_event(json.dumps(payload(platforms="Super Nintendo")).encode())


def test_undecodable_bytes_are_refused_not_raised_through():
    with pytest.raises(WebhookPayloadError):
        parse_event(b"\xff\xfe\x00 not utf-8")


# --- platform resolution ------------------------------------------------


@pytest.mark.parametrize(
    "name,slug",
    [
        ("Super Nintendo", "snes"),
        ("Super Nintendo Entertainment System", "snes"),
        ("SNES", "snes"),
        ("Nintendo Entertainment System", "nes"),
        ("Nintendo 64", "n64"),
        ("Game Boy Advance", "gba"),
        ("Sega Mega Drive/Genesis", "genesis"),
        ("PlayStation", "psx"),
        ("Arcade", "arcade"),
        ("DOS", "dos"),
        ("TurboGrafx-16/PC Engine", "tg16"),
        ("Commodore C64/128/MAX", "c64"),
        # Already a Hub slug: passed through, not re-derived.
        ("gbc", "gbc"),
    ],
)
def test_platform_names_resolve_to_hub_slugs(name, slug):
    resolved, unresolved = platform_slugs([name])
    assert resolved == [slug]
    assert unresolved == []


def test_every_alias_targets_a_slug_the_hub_actually_knows():
    """A typo in the table would become a search filter every plugin
    refuses, and the request would come back NO_MATCH with no hint why.
    `playability` is the authority on which slugs are real."""
    from rom_hub.webhook import KNOWN_SLUGS, PLATFORM_NAMES

    assert sorted(set(PLATFORM_NAMES) - KNOWN_SLUGS) == []


def test_no_two_platform_names_collide_after_normalisation():
    """Two spellings that normalise to one key means one of them silently
    decides for both -- and which one depends on dict order."""
    from rom_hub.webhook import PLATFORM_NAMES
    from rom_hub.romnames import normalise_title

    seen: dict[str, str] = {}
    collisions = []
    for slug, names in PLATFORM_NAMES.items():
        for name in names:
            key = normalise_title(name)
            if key in seen and seen[key] != slug:
                collisions.append((key, seen[key], slug))
            seen[key] = slug
    assert collisions == []


def test_a_platform_name_that_is_also_a_slug_needs_no_alias():
    """Every slug is accepted verbatim, so the table never has to list the
    slugs themselves -- and cannot disagree with itself about one."""
    resolved, unresolved = platform_slugs(["z-machine", "vectrex"])
    assert resolved == ["z-machine", "vectrex"]
    assert unresolved == []


def test_an_unknown_platform_name_is_reported_not_guessed():
    resolved, unresolved = platform_slugs(["Ivy Bridge Toaster"])
    assert resolved == []
    assert unresolved == ["Ivy Bridge Toaster"]


def test_platform_resolution_is_deduplicated_and_ordered():
    resolved, _ = platform_slugs(["SNES", "Super Nintendo", "Nintendo 64"])
    assert resolved == ["snes", "n64"]


def test_a_blank_platform_name_is_skipped_not_reported():
    """An empty string is a sender artefact, not a platform somebody asked
    for, so it is neither searched nor complained about."""
    resolved, unresolved = platform_slugs(["", "   ", "Nintendo 64"])
    assert resolved == ["n64"]
    assert unresolved == []


def test_platform_resolution_is_capped():
    """A request naming twenty platforms must not mean twenty fan-outs."""
    names = [
        "Super Nintendo",
        "Nintendo 64",
        "Game Boy",
        "Game Boy Color",
        "Game Boy Advance",
        "PlayStation",
        "Arcade",
    ]
    resolved, unresolved = platform_slugs(names)
    assert len(resolved) == MAX_PLATFORMS
    # The ones that did not fit are reported, never silently dropped.
    assert len(resolved) + len(unresolved) == len(names)


# --- the request log ----------------------------------------------------


def event(**kwargs) -> RequestEvent:
    base = {
        "request_id": "req-1",
        "game_title": "Chrono Trigger",
        "igdb_id": "1234",
        "platforms": ["Super Nintendo"],
        "request_type": "game",
        "user_id": "12",
    }
    return RequestEvent(**{**base, **kwargs})


def test_claim_accepts_a_new_request(tmp_path):
    with RequestLog(tmp_path / "requests.db") as log:
        assert log.claim(event()) is True
        row = log.get("req-1")
        assert row.state is RequestState.RECEIVED
        assert row.game_title == "Chrono Trigger"


def test_claim_refuses_the_same_request_id_twice(tmp_path):
    with RequestLog(tmp_path / "requests.db") as log:
        assert log.claim(event()) is True
        assert log.claim(event()) is False
        assert len(log.list()) == 1


def test_a_second_claim_does_not_overwrite_the_first_outcome(tmp_path):
    """A re-dispatch must not erase what the first run concluded."""
    with RequestLog(tmp_path / "requests.db") as log:
        log.claim(event())
        log.finish("req-1", RequestState.FULFILLED, "imported as rom 7", job_id=7)
        assert log.claim(event(game_title="Something Else")) is False
        row = log.get("req-1")
        assert row.state is RequestState.FULFILLED
        assert row.job_id == 7
        assert row.game_title == "Chrono Trigger"


def test_the_log_survives_being_reopened(tmp_path):
    path = tmp_path / "requests.db"
    with RequestLog(path) as log:
        log.claim(event())
    with RequestLog(path) as log:
        assert log.claim(event()) is False
        assert log.get("req-1").state is RequestState.RECEIVED


def test_forget_makes_a_request_claimable_again(tmp_path):
    with RequestLog(tmp_path / "requests.db") as log:
        log.claim(event())
        log.finish("req-1", RequestState.NO_MATCH, "nothing matched")
        assert log.forget("req-1") is True
        assert log.claim(event()) is True
        assert log.get("req-1").state is RequestState.RECEIVED


def test_forgetting_an_unknown_request_says_so(tmp_path):
    with RequestLog(tmp_path / "requests.db") as log:
        assert log.forget("never-seen") is False


def test_in_flight_rows_are_failed_when_the_receiver_restarts(tmp_path):
    path = tmp_path / "requests.db"
    with RequestLog(path) as log:
        log.claim(event())
        log.claim(event(request_id="req-2"))
        log.begin("req-1", RequestState.SEARCHING)
        log.finish("req-2", RequestState.FULFILLED, "done")
    with RequestLog(path) as log:
        stranded = log.mark_interrupted()
        assert [r.request_id for r in stranded] == ["req-1"]
        assert log.get("req-1").state is RequestState.FAILED
        assert "forget" in log.get("req-1").detail
        # A finished row is not touched.
        assert log.get("req-2").state is RequestState.FULFILLED


def test_list_can_be_narrowed_to_one_state(tmp_path):
    with RequestLog(tmp_path / "requests.db") as log:
        log.claim(event())
        log.claim(event(request_id="req-2"))
        log.finish("req-2", RequestState.NO_MATCH, "nope")
        assert [r.request_id for r in log.list(RequestState.NO_MATCH)] == ["req-2"]
        assert len(log.list()) == 2


def test_the_log_records_the_platforms_as_sent(tmp_path):
    """An operator reading the log must see what GG Requestz said, not our
    interpretation of it -- that is how a bad mapping gets noticed."""
    with RequestLog(tmp_path / "requests.db") as log:
        log.claim(event(platforms=["Super Nintendo", "Wii U"]))
        assert log.get("req-1").platforms == ["Super Nintendo", "Wii U"]


# --- fulfilment ---------------------------------------------------------


def result(title, *, platform="snes", plugin="archive-org", source_id=None, **extra):
    return SearchResult(
        source_id=source_id or f"{plugin}-{title}",
        title=title,
        platform=platform,
        plugin=plugin,
        extra={k: str(v) for k, v in extra.items()},
    )


def outcome(*results, responded=1, total=1):
    return SearchOutcome(
        results=list(results),
        statuses=[
            PluginStatus(f"p{i}", ok=i < responded) for i in range(max(total, 1))
        ],
    )


class FakeSearch:
    """Stands in for the plugin fan-out. Records what it was asked for."""

    def __init__(self, *, results=None, by_platform=None):
        self._results = results or []
        self._by_platform = by_platform
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, query, platform):
        self.calls.append((query, platform))
        if self._by_platform is not None:
            return outcome(*self._by_platform.get(platform, []))
        return outcome(*self._results)


class FakeImporter:
    """Stands in for run_import. Never touches a library server."""

    def __init__(self, state=JobState.DONE, message="imported", job_id=1, error=None):
        self._state = state
        self._message = message
        self._job_id = job_id
        self._error = error
        self.warnings: tuple[str, ...] = ()
        self.imported: list[SearchResult] = []

    def __call__(self, chosen):
        if self._error is not None:
            raise self._error
        self.imported.append(chosen)
        return SimpleNamespace(
            job_id=self._job_id,
            state=self._state,
            message=self._message,
            rom_id=42,
            warnings=self.warnings,
        )


def test_a_matching_title_is_imported(tmp_path):
    search = FakeSearch(results=[result("Chrono Trigger")])
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.FULFILLED
    assert len(importer.imported) == 1
    assert importer.imported[0].title == "Chrono Trigger"
    assert done.job_id == 1


def test_the_requested_platform_narrows_the_search(tmp_path):
    search = FakeSearch(results=[result("Chrono Trigger")])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        fulfil(event(), log=log, search=search, importer=FakeImporter())
    assert search.calls == [("Chrono Trigger", "snes")]


def test_an_empty_platforms_array_searches_without_a_filter(tmp_path):
    search = FakeSearch(results=[result("Chrono Trigger", platform=None)])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event(platforms=[]))
        done = fulfil(
            event(platforms=[]), log=log, search=search, importer=FakeImporter()
        )
    assert search.calls == [("Chrono Trigger", None)]
    assert done.state is RequestState.FULFILLED


def test_an_unresolvable_platform_searches_unfiltered_and_says_so(tmp_path):
    search = FakeSearch(results=[result("Chrono Trigger", platform=None)])
    ev = event(platforms=["Ivy Bridge Toaster"])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(ev)
        done = fulfil(ev, log=log, search=search, importer=FakeImporter())
    assert search.calls == [("Chrono Trigger", None)]
    assert "Ivy Bridge Toaster" in done.detail


def test_every_resolved_platform_is_searched(tmp_path):
    ev = event(platforms=["Super Nintendo", "Nintendo 64"])
    search = FakeSearch(
        by_platform={
            "snes": [],
            "n64": [result("Chrono Trigger", platform="n64")],
        }
    )
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(ev)
        done = fulfil(ev, log=log, search=search, importer=FakeImporter())
    assert sorted(p for _, p in search.calls) == ["n64", "snes"]
    assert done.state is RequestState.FULFILLED


def test_no_igdb_id_still_matches_on_the_title(tmp_path):
    ev = event(igdb_id=None)
    search = FakeSearch(results=[result("Chrono Trigger")])
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(ev)
        done = fulfil(ev, log=log, search=search, importer=importer)
    assert done.state is RequestState.FULFILLED
    assert importer.imported[0].title == "Chrono Trigger"


def test_an_igdb_id_beats_a_title_that_also_matches(tmp_path):
    """When a source states an igdb id, it decides -- it is an identifier
    and the title is a string two different games can share."""
    search = FakeSearch(
        results=[
            result("Chrono Trigger", plugin="a", igdb_id="9999"),
            result("Chrono Trigger (USA)", plugin="b", igdb_id="1234"),
        ]
    )
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.FULFILLED
    assert importer.imported[0].plugin == "b"


def test_a_title_that_only_nearly_matches_is_not_imported(tmp_path):
    search = FakeSearch(results=[result("Chrono Trigger 2: The Sequel")])
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.NO_MATCH
    assert importer.imported == []
    assert "Chrono Trigger" in done.detail


def test_punctuation_and_case_do_not_prevent_a_match(tmp_path):
    """`normalise_title` already decides what "the same game" means; this
    is here so the receiver keeps using it rather than growing its own."""
    search = FakeSearch(results=[result("CHRONO  TRIGGER (USA) [!]")])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=FakeImporter())
    assert done.state is RequestState.FULFILLED


def test_nothing_at_all_is_a_no_match_not_a_failure(tmp_path):
    search = FakeSearch(results=[])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=FakeImporter())
        assert log.get("req-1").state is RequestState.NO_MATCH
    assert done.state is RequestState.NO_MATCH


def test_two_platforms_offering_the_same_title_is_ambiguous(tmp_path):
    """Two different machines, one title, nothing to choose between them.
    A guess here files a ROM under the wrong system."""
    ev = event(platforms=[])
    search = FakeSearch(
        results=[
            result("Chrono Trigger", platform="snes", plugin="a"),
            result("Chrono Trigger", platform="nds", plugin="b"),
        ]
    )
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(ev)
        done = fulfil(ev, log=log, search=search, importer=importer)
    assert done.state is RequestState.NO_MATCH
    assert importer.imported == []
    assert "snes" in done.detail and "nds" in done.detail


def test_a_requested_platform_resolves_that_ambiguity(tmp_path):
    search = FakeSearch(
        by_platform={
            "snes": [
                result("Chrono Trigger", platform="snes", plugin="a"),
                result("Chrono Trigger", platform="nds", plugin="b"),
            ]
        }
    )
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.FULFILLED
    assert importer.imported[0].platform == "snes"


def test_a_stream_only_copy_is_not_importable(tmp_path):
    search = FakeSearch(results=[result("Chrono Trigger", stream_only="true")])
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.NO_MATCH
    assert importer.imported == []
    assert "stream" in done.detail


def test_a_downloadable_copy_wins_over_a_stream_only_one(tmp_path):
    search = FakeSearch(
        results=[
            result("Chrono Trigger", plugin="a", source_id="s", stream_only="true"),
            result("Chrono Trigger (USA)", plugin="b", source_id="d"),
        ]
    )
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.FULFILLED
    assert importer.imported[0].source_id == "d"


def test_an_already_present_rom_is_fulfilled_not_failed(tmp_path):
    importer = FakeImporter(
        state=JobState.SKIPPED_DUPLICATE, message="already in the library"
    )
    search = FakeSearch(results=[result("Chrono Trigger")])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.FULFILLED
    assert "already" in done.detail


def test_a_failed_import_is_recorded_as_failed(tmp_path):
    importer = FakeImporter(state=JobState.FAILED, message="download refused")
    search = FakeSearch(results=[result("Chrono Trigger")])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
        assert log.get("req-1").state is RequestState.FAILED
    assert done.state is RequestState.FAILED
    assert "download refused" in done.detail


def test_a_raising_search_fails_the_request_rather_than_the_worker(tmp_path):
    def boom(query, platform):
        raise RuntimeError("registry unreadable")

    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=boom, importer=FakeImporter())
    assert done.state is RequestState.FAILED
    assert "registry unreadable" in done.detail


def test_a_raising_import_fails_the_request_rather_than_the_worker(tmp_path):
    search = FakeSearch(results=[result("Chrono Trigger")])
    importer = FakeImporter(error=RuntimeError("backend gone"))
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.FAILED
    assert "backend gone" in done.detail


def test_a_refused_fulfilment_is_recorded_as_the_sentence_it_came_with(tmp_path):
    """`FulfilmentRefused` already reads as an explanation. Wrapping it in
    a type name would add nothing and bury it."""
    search = FakeSearch(results=[result("Chrono Trigger")])
    importer = FakeImporter(
        error=FulfilmentRefused("that plugin cannot import anything")
    )
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.FAILED
    assert done.detail.startswith("that plugin cannot import anything")
    assert "FulfilmentRefused" not in done.detail


def test_an_import_warning_reaches_the_request_record(tmp_path):
    """A ROM that landed on a platform with no emulator core is a
    successful import the requester still needs told about."""
    search = FakeSearch(results=[result("Chrono Trigger")])
    importer = FakeImporter()
    importer.warnings = ("the platform 'vectrex' has no emulator core",)
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=search, importer=importer)
    assert done.state is RequestState.FULFILLED
    assert "no emulator core" in done.detail
    assert "no emulator core" in log_detail(tmp_path)


def log_detail(tmp_path) -> str:
    with RequestLog(tmp_path / "r.db") as log:
        return log.get("req-1").detail or ""


def test_an_update_request_is_recorded_and_not_imported(tmp_path):
    """`update` and `fix` are complaints about a rom already in the
    library. The Hub cannot patch one, and importing a second copy is not
    what was asked for."""
    ev = event(request_type="update")
    importer = FakeImporter()
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(ev)
        done = fulfil(ev, log=log, search=FakeSearch(), importer=importer)
    assert done.state is RequestState.IGNORED
    assert importer.imported == []
    assert "update" in done.detail


def test_an_operator_can_widen_which_request_types_are_fulfilled(tmp_path):
    ev = event(request_type="fix")
    search = FakeSearch(results=[result("Chrono Trigger")])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(ev)
        done = fulfil(
            ev,
            log=log,
            search=search,
            importer=FakeImporter(),
            fulfil_types=("game", "fix"),
        )
    assert done.state is RequestState.FULFILLED


def test_the_log_shows_the_source_that_was_chosen(tmp_path):
    search = FakeSearch(results=[result("Chrono Trigger", plugin="archive-org")])
    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        fulfil(event(), log=log, search=search, importer=FakeImporter())
        row = log.get("req-1")
    assert row.plugin == "archive-org"
    assert row.source_id == "archive-org-Chrono Trigger"


def test_a_partial_fan_out_is_reported_in_the_detail(tmp_path):
    """The project's rule: a partial answer must say it is partial. A
    request that found nothing because two of three sources were down is
    a different event from one that found nothing at all."""

    def half_down(query, platform):
        return outcome(responded=1, total=3)

    with RequestLog(tmp_path / "r.db") as log:
        log.claim(event())
        done = fulfil(event(), log=log, search=half_down, importer=FakeImporter())
    assert done.state is RequestState.NO_MATCH
    assert "1 of 3" in done.detail
