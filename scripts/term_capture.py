"""Run commands, capture their real output, and write a term_shot session.

Separated from `term_shot.py` so the two halves cannot drift into each
other: this half only ever *runs* things and writes down what came back,
and it has no way to edit the text. `term_shot.py` only ever renders a
file it is given. Nothing between the command and the PNG can retouch the
output, which is the property the whole showcase rests on.

    python term_capture.py out.json "title" -- "cmd one" "cmd two" ...

stdout and stderr are merged, in order, exactly as a terminal would show
them. A non-zero exit is captured too -- a refusal is a thing worth
showing, and hiding it would be the lie this file exists to prevent.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out")
    ap.add_argument("title")
    ap.add_argument("--note", default=None)
    ap.add_argument("commands", nargs="+")
    args = ap.parse_args()

    blocks = []
    for command in args.commands:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            errors="replace",
        )
        merged = proc.stdout + proc.stderr
        blocks.append({"cmd": command, "out": merged})
        print(f"[{proc.returncode}] {command}", file=sys.stderr)

    session = {"title": args.title, "blocks": blocks}
    if args.note:
        session["note"] = args.note
    pathlib.Path(args.out).write_text(
        json.dumps(session, indent=1), encoding="utf-8"
    )
    print(f"wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
