"""ROM Hub plugin connecting ROMarr's direct downloads to Mister Download Manager.

Mister Download Manager (https://github.com/anefzaoui/misterdownloadmanager,
by anefzaoui, GPL-3.0-or-later) is a multi-connection HTTP and BitTorrent
download manager with a CLI and a Remote HTTPS API. This plugin contains none
of its code. It holds the settings ROMarr uses to drive MDM, and offers MDM's
official release archives, fetched from the upstream GitHub release, as
installable artifacts.
"""

UPSTREAM_REPO = "anefzaoui/misterdownloadmanager"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
UPSTREAM_LICENSE = "GPL-3.0-or-later"
CREDIT = (
    "Mister Download Manager is by anefzaoui "
    "(https://github.com/anefzaoui/misterdownloadmanager), licensed "
    "GPL-3.0-or-later. This plugin only connects ROMarr to it and includes "
    "none of its code."
)

#: Values ROMarr accepts for the `mode` setting. Blank means "not set".
MODES = ("cli", "remote")
#: Categories MDM's Remote API accepts. Blank means "no category".
CATEGORIES = ("programs", "compressed", "documents", "music", "videos", "other")
#: Bounds MDM puts on parallel range connections.
MIN_CONNECTIONS = 1
MAX_CONNECTIONS = 128
