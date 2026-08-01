"""Screenshot a populated backend's web UI, at a named route.

The first version of this only ever photographed the home page, and that
is how a screenshot of RomM's *Recently Added* row -- the newest imports,
which happened to be the homebrew with no box art -- got published as if it
were the library. So this one takes a route, and can scroll, wait for a
selector and click before it shoots.

Each backend needs a real login: a screenshot of a login page proves
nothing.

    python shoot.py <name> <url> [user] [pass] [--goto PATH] [--wait-for SEL]
                    [--click SEL] [--scroll PX] [--settle S] [--full]
                    [--viewport WxH]

Writes /shots/<name>.png and prints what it actually saw, so a blank page,
an error page or a grid of placeholders is reported at capture time rather
than discovered by whoever opens the finished document.
"""

from __future__ import annotations

import argparse
import sys
import time

from playwright.sync_api import sync_playwright


def log(*a):
    print(*a, flush=True)


def try_login(page, user, password):
    """Best-effort form login. Backends differ; report what happened."""
    if not user:
        return "no credentials supplied"
    user_sel = (
        "input[name='username'], input#username, input[type='text']"
        ", input[autocomplete='username']"
    )
    pass_sel = "input[type='password'], input[name='password'], input#password"
    try:
        page.wait_for_selector(pass_sel, timeout=8000)
    except Exception:
        return "no password field found (already authenticated, or no auth)"
    try:
        page.fill(user_sel, user, timeout=5000)
        page.fill(pass_sel, password, timeout=5000)
        page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle", timeout=25000)
        return "submitted login form"
    except Exception as exc:  # noqa: BLE001
        return f"login attempt failed: {type(exc).__name__}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("name")
    ap.add_argument("url", help="base URL, used for the login round")
    ap.add_argument("user", nargs="?", default="")
    ap.add_argument("password", nargs="?", default="")
    ap.add_argument("--goto", default=None, help="navigate here after logging in")
    ap.add_argument("--wait-for", default=None, help="CSS selector to wait for")
    ap.add_argument(
        "--click",
        action="append",
        default=None,
        help=(
            "CSS or text selector to click before the shot; repeatable. Used "
            "to close the update banner and toasts a live server pops up over "
            "the library -- they are the server's, not the Hub's, and they are "
            "not what the picture is of"
        ),
    )
    ap.add_argument("--scroll", type=int, default=0, help="pixels to scroll down")
    ap.add_argument("--settle", type=float, default=6.0, help="seconds before the shot")
    ap.add_argument(
        "--login-settle",
        type=float,
        default=6.0,
        help="seconds to let the session settle after login, before --goto",
    )
    ap.add_argument("--full", action="store_true", help="full-page screenshot")
    ap.add_argument("--viewport", default="1680x1050")
    ap.add_argument("--count", default=None, help="CSS selector to count and report")
    args = ap.parse_args()

    width, _, height = args.viewport.partition("x")
    viewport = {"width": int(width), "height": int(height)}

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport=viewport, device_scale_factor=2)
        page.goto(args.url, timeout=45000, wait_until="domcontentloaded")
        log(f"[{args.name}] loaded {args.url} -> {page.title()!r}")
        log(f"[{args.name}] {try_login(page, args.user, args.password)}")

        if args.goto:
            # The login is a SPA round trip: pressing Enter returns before
            # the session cookie is set, and navigating in that window
            # lands back on the login page with the shot taken of it. This
            # was how a login screen nearly got published as a library.
            time.sleep(args.login_settle)
            target = args.url.rstrip("/") + args.goto
            for attempt in (1, 2):
                page.goto(target, timeout=45000, wait_until="domcontentloaded")
                time.sleep(3)
                if "login" not in page.title().lower():
                    break
                log(f"[{args.name}] bounced to a login page (attempt {attempt})")
                page.goto(args.url, timeout=45000, wait_until="domcontentloaded")
                log(f"[{args.name}] {try_login(page, args.user, args.password)}")
                time.sleep(args.login_settle)
            log(f"[{args.name}] navigated to {target} -> {page.title()!r}")

        for selector in args.click or ():
            try:
                page.click(selector, timeout=8000)
                log(f"[{args.name}] clicked {selector!r}")
                time.sleep(1)
            except Exception as exc:  # noqa: BLE001
                log(f"[{args.name}] click {selector!r} skipped: {type(exc).__name__}")

        if args.wait_for:
            try:
                page.wait_for_selector(args.wait_for, timeout=30000)
                log(f"[{args.name}] saw {args.wait_for!r}")
            except Exception:  # noqa: BLE001
                log(f"[{args.name}] WARNING: never saw {args.wait_for!r}")

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:  # noqa: BLE001
            log(f"[{args.name}] note: network never went idle")

        if args.scroll:
            page.mouse.wheel(0, args.scroll)
            time.sleep(2)

        time.sleep(args.settle)

        # Again, after the page has settled. RomM's "new version available"
        # banner arrives on a delay, so a click issued before the settle
        # misses it and it ends up in the photograph.
        for selector in args.click or ():
            try:
                page.click(selector, timeout=4000)
                log(f"[{args.name}] clicked {selector!r} (late)")
                time.sleep(1.5)
            except Exception:  # noqa: BLE001
                pass

        if args.count:
            try:
                n = page.locator(args.count).count()
                log(f"[{args.name}] {args.count!r} matched {n} element(s)")
            except Exception:  # noqa: BLE001
                log(f"[{args.name}] could not count {args.count!r}")

        out = f"/shots/{args.name}.png"
        page.screenshot(path=out, full_page=args.full)
        body = page.inner_text("body")[:260].replace("\n", " ")
        log(f"[{args.name}] title={page.title()!r}")
        log(f"[{args.name}] visible text: {body}")
        log(f"[{args.name}] wrote {out}")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
