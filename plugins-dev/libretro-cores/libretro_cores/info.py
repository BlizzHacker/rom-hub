"""Where a core's `.info` file lives, and what it is called on disk.

RetroArch reads `<core>_libretro.info` from its `libretro_info_dir` to
learn a core's display name, the file extensions it loads, the BIOS it
wants and its licence. A core installed without one is a `.so` the
frontend can load and cannot describe: it appears under its filename,
offers no extension filter, and says nothing about the firmware it will
sit there silently waiting for.

The buildbot does not serve these. It publishes them only as
`assets/frontend/info.zip`, and `ctx.http` returns text, so a plugin
cannot open a zip. The same files are individually addressable in
`libretro/libretro-core-info`, which is why `raw.githubusercontent.com`
is in this plugin's manifest allowlist -- a second host, declared, for a
second file per install.

**The naming is the buildbot's own, inverted.** `index.core_id_for`
strips `_libretro` off `snes9x_libretro.so.zip` to get `snes9x`; this
puts it back to get `snes9x_libretro.info`. Two functions, one
convention, and a test asserts they round-trip rather than trusting the
resemblance.
"""

import urllib.parse

OWNER = "libretro"
REPO = "libretro-core-info"

#: A branch, deliberately. The `.info` files are corrected continuously --
#: a newly required BIOS or a renamed extension is the point of fetching
#: the live one at all -- so pinning a commit here would install a
#: snapshot beside a nightly core. The integrity story is the allowlist
#: and HTTPS, as it is for the core itself.
REF = "master"

INFO_HOST = "raw.githubusercontent.com"

#: The suffix libretro gives every core's info file.
SUFFIX = "_libretro.info"


def info_filename(core_id: str) -> str:
    """`snes9x` -> `snes9x_libretro.info`, which is the name RetroArch
    looks for. Validated by `FetchFile.filename` on the way out, as every
    filename this plugin produces is."""
    return f"{core_id}{SUFFIX}"


def info_url(core_id: str) -> str:
    """The raw URL for one core's info file.

    Quoted rather than interpolated raw. A core id is already constrained
    by `index._FILENAME` to letters, digits, dot, plus, dash and
    underscore, so there is nothing here to encode today -- which is
    exactly the situation in which an unquoted interpolation survives
    review and then breaks the first time upstream ships a core with a
    space in its name.
    """
    path = urllib.parse.quote(info_filename(core_id), safe="")
    return f"https://{INFO_HOST}/{OWNER}/{REPO}/{REF}/{path}"
