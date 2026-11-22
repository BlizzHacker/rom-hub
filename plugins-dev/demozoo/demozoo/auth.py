"""Demozoo API keys.

Demozoo's `/api/v1/` was anonymous and read-only until 2026-11-01, when the
site turned on `ENFORCE_API_KEYS` (demozoo/demozoo commit "Enforce API
keys"): every API request without a key now answers 403, "Authentication
credentials were not provided." The web pages are still public; the API is
not.

A key is per account and free. It goes in the `Authorization` header as
`Api-Key <key>`, which is what `rest_framework_api_key` parses; Demozoo also
accepts `?api_key=`, but a query string ends up in logs and a header does
not, so the header is the only form used here.
"""

KEY_HELP = (
    "Demozoo has required an API key for /api/v1/ since 2026-11-01. Log in "
    "at https://demozoo.org/, create a key on your account page, and set it "
    "as this plugin's `api_key` config."
)


def api_key(config) -> str:
    return str(config.get("api_key") or "").strip()


def get(ctx, url: str, params: dict):
    """`ctx.http.get` with the key attached, when there is one."""
    key = api_key(ctx.config)
    if not key:
        return ctx.http.get(url, params=params)
    return ctx.http.get(url, params=params, headers={"Authorization": f"Api-Key {key}"})


def hint(status: int, config) -> str:
    """The extra sentence a 401/403 deserves: nothing else explains it."""
    if status not in (401, 403):
        return ""
    if api_key(config):
        return " The configured api_key was refused; check it on your Demozoo account page."
    return " " + KEY_HELP
