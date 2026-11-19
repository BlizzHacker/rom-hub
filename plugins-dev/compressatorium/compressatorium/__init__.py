"""ROM Hub plugin connecting ROMarr's batch decompression to Compressatorium.

Compressatorium (https://github.com/pacnpal/compressatorium, by pacnpal)
converts and checks game images: CHD, RVZ, Z3DS, NSZ, CSO and more. This
plugin contains none of its code. It tells ROMarr where your Compressatorium
container is, and offers Compressatorium's own docker-compose.yml, fetched
from the upstream repository, as an installable artifact.
"""

UPSTREAM_REPO = "pacnpal/compressatorium"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
CREDIT = (
    "Compressatorium is by pacnpal (https://github.com/pacnpal/compressatorium). "
    "This plugin only connects ROMarr to it and includes none of its code."
)
