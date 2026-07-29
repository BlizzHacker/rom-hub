"""Assertions CI makes about the test run itself, not about the code.

A green pytest exit code is a weaker claim than it looks. Two of this
project's load-bearing guarantees are invisible to it:

* **A skipped containment test looks exactly like a passing one.**
  `tests/test_sandbox.py`, `tests/test_runner_sandbox.py` and
  `tests/test_hostile_plugin.py` carry `skipif(sys.platform != "linux")`,
  because seccomp is a Linux facility. On Windows that skip is honest. On
  Linux it is a silent hole: if `pyseccomp` failed to build, or
  `sys.platform` stopped saying `linux` inside some container, the suite
  goes green having proven nothing about confinement -- which is the one
  thing README.md and docs/DESIGN.md promise in the strongest terms. This
  project has already shipped a "green but proved nothing" state once.

* **`-m 'not live'` is a default, and defaults get overridden.** The
  `addopts` in pyproject.toml deselects the four network-hitting tests.
  Nothing stops a `-o addopts=`, a stray `-m` on a command line, or an
  edit to pyproject.toml from putting them back. In CI that would mean
  the suite silently depends on Archive.org being up.

So CI asserts both directly, against the machine-readable record of what
actually ran rather than against a scrolled-past summary line.

Usage
-----
    python scripts/ci_gate.py no-live
        Prove the `live` marker still selects tests (so the check has not
        become vacuous) and that none of them are collected by default.

    python scripts/ci_gate.py junit REPORT.XML \
        --require-passed tests/test_sandbox.py::test_x ... \
        --allow-skip-reason REGEX ...
        Prove each named test is in the report with outcome *passed* --
        skipped and missing both fail -- and that every skip in the whole
        report is explained by one of the permitted reasons.

Both exit non-zero with an explanation on failure. Neither imports the
package under test, so a gate failure is never a code failure in disguise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _collect(*extra: str) -> set[str]:
    """Node ids pytest would run, as pytest itself reports them."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        sys.exit(
            f"collection failed for {extra or '(default)'} "
            f"(exit {proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
        )
    # `-q` prints one node id per line, then a blank line and a summary.
    # Node ids are the only lines carrying `::`.
    return {
        line.strip()
        for line in proc.stdout.splitlines()
        if "::" in line and not line.startswith(" ")
    }


def cmd_no_live(_args: argparse.Namespace) -> int:
    live = _collect("-o", "addopts=", "-m", "live")
    if not live:
        print(
            "FAIL: no test carries the `live` marker.\n"
            "  The deselection check is only meaningful while there is "
            "something to deselect. Either the marker was dropped from the "
            "network tests -- in which case CI now hits the network -- or "
            "the tests were deleted and this gate should be removed "
            "deliberately rather than left passing vacuously.",
            file=sys.stderr,
        )
        return 1

    default = _collect()
    leaked = sorted(live & default)
    if leaked:
        print(
            "FAIL: network tests are selected by default:\n  "
            + "\n  ".join(leaked)
            + "\n  Expected `addopts = \"-m 'not live'\"` in pyproject.toml to "
            "deselect them. CI must not depend on a third-party service "
            "being up.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(live)} live test(s) exist and none are collected by default:")
    for node in sorted(live):
        print(f"  deselected  {node}")
    print(f"  ({len(default)} tests collected by default)")
    return 0


def _outcomes(report: Path) -> dict[str, tuple[str, str]]:
    """node id -> (outcome, detail), from a pytest --junitxml report."""
    root = ET.parse(report).getroot()
    found: dict[str, tuple[str, str]] = {}
    for case in root.iter("testcase"):
        # pytest writes classname="tests.test_sandbox" (dots, no .py) and
        # name="test_x". Rebuild the node id a human would type.
        classname = case.get("classname") or ""
        name = case.get("name") or ""
        parts = classname.split(".")
        # A test inside a class contributes a trailing component that is not
        # part of the module path; the module is everything up to the last
        # component that looks like a module file.
        module = "/".join(parts) + ".py"
        node = f"{module}::{name}"
        for outcome in ("skipped", "failure", "error"):
            hit = case.find(outcome)
            if hit is not None:
                found[node] = (
                    outcome,
                    hit.get("message") or (hit.text or "").strip(),
                )
                break
        else:
            found[node] = ("passed", "")
    return found


def cmd_junit(args: argparse.Namespace) -> int:
    report = Path(args.report)
    if not report.is_file():
        print(f"FAIL: no junit report at {report}", file=sys.stderr)
        return 1

    outcomes = _outcomes(report)
    if not outcomes:
        print(f"FAIL: {report} records no tests at all", file=sys.stderr)
        return 1

    failed = False

    for node in args.require_passed:
        result = outcomes.get(node)
        if result is None:
            print(
                f"FAIL: {node} is not in the report.\n"
                "  It was renamed, moved or deleted. A containment test that "
                "no longer exists cannot be silently dropped from the gate.",
                file=sys.stderr,
            )
            failed = True
            continue
        outcome, detail = result
        if outcome == "passed":
            print(f"OK: passed  {node}")
            continue
        print(
            f"FAIL: {node} {outcome}: {detail or '(no detail)'}\n"
            "  On this platform it must PASS. A skip here means the sandbox "
            "was not exercised, and the confinement claim in README.md and "
            "docs/DESIGN.md is unproven for this run.",
            file=sys.stderr,
        )
        failed = True

    allowed = [re.compile(pattern) for pattern in args.allow_skip_reason]
    for node, (outcome, detail) in sorted(outcomes.items()):
        if outcome != "skipped":
            continue
        if any(pattern.search(detail) for pattern in allowed):
            print(f"OK: expected skip  {node}  ({detail})")
            continue
        print(
            f"FAIL: unexplained skip  {node}: {detail or '(no reason given)'}\n"
            "  Every skip on this platform must match one of "
            f"{args.allow_skip_reason!r}. A new skip is a test that stopped "
            "running; decide deliberately whether that is acceptable and "
            "widen the allowed reasons if it is.",
            file=sys.stderr,
        )
        failed = True

    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    live = sub.add_parser("no-live", help="network tests exist and are deselected")
    live.set_defaults(func=cmd_no_live)

    junit = sub.add_parser("junit", help="assert on a --junitxml report")
    junit.add_argument("report")
    junit.add_argument(
        "--require-passed",
        action="append",
        default=[],
        metavar="NODEID",
        help="test that must be present and passed (repeatable)",
    )
    junit.add_argument(
        "--allow-skip-reason",
        action="append",
        default=[],
        metavar="REGEX",
        help="a skip reason that is acceptable on this platform (repeatable)",
    )
    junit.set_defaults(func=cmd_junit)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
