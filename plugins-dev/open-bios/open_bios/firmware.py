"""open-bios `firmware`: a fixed catalogue, and one download per install.

    catalogue.SOURCES -> FirmwareArtifact[]
    FirmwareArtifact  -> FetchPlan -> the HOST downloads (and unpacks)

The plugin never fetches anything. It names a URL and the **host** fetches
it, after checking that URL against this plugin's own `network` allowlist,
re-checking every redirect hop, and re-validating the filename -- the same
gate a ROM import goes through, because a BIOS is a binary landing on the
operator's disk and that is exactly as privileged as a ROM.

Three decisions here are the careful half of choices that could have gone
the other way.

**`plan()` re-reads the catalogue instead of trusting the artifact.** The
`FirmwareArtifact` handed back to `plan()` has been out of this process:
the host serialised it, the operator's command chose it, and it arrives as
a dict this plugin did not construct. Believing its fields would mean
building a URL out of a value that made a round trip through somewhere
else. Looking it up costs a dict scan.

**The release tag is validated, not interpolated.** `sameboy_release` is
operator configuration that becomes part of a URL. It is checked against
a tag shape first, so a value with a `/` or a `?` in it is refused by name
rather than producing a URL that means something other than it looks like.
The host's allowlist would catch a host swap regardless; this catches the
path.

**A licence is never inferred.** Each `Source` carries the licence as
stated by the project that publishes it, with the evidence in a comment
beside it. Nothing here derives a licence from a repository field --
SameBoy's is reported by GitHub as NOASSERTION, and the answer that
matters (the boot ROMs are Expat) is in the LICENSE file's own carve-out
text, not in the metadata.
"""

import re

from rom_hub_sdk import FetchFile, FetchPlan, FirmwareArtifact, FirmwareProvider

from . import catalogue
from .platforms import NeedsMapping, platform_for  # noqa: F401

#: What a git tag may look like here. Deliberately narrow: this value is
#: interpolated into a URL path and into a filename, and every real
#: SameBoy tag is `v` followed by dotted digits.
_TAG_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")

SAMEBOY_RELEASE_URL = (
    "https://github.com/LIJI32/SameBoy/releases/download/{release}/{asset}"
)


class ConfigError(Exception):
    """A config value this plugin will not build a URL out of."""


class UnknownFirmware(Exception):
    """No such item in this plugin's catalogue."""


class Firmware(FirmwareProvider):
    def list(self) -> list[FirmwareArtifact]:
        # Reading the config here as well as in `plan()` means a bad tag
        # is reported by `firmware list`, before an operator picks
        # something and finds out during an install.
        release = self._release()
        return [self._artifact(source, release) for source in catalogue.SOURCES]

    def plan(self, firmware: FirmwareArtifact) -> FetchPlan:
        release = self._release()
        try:
            source = catalogue.find(firmware.firmware_id)
        except KeyError as exc:
            raise UnknownFirmware(str(exc)) from None

        if source.asset:
            asset = source.asset.format(release=release)
            url = SAMEBOY_RELEASE_URL.format(release=release, asset=asset)
            filename = asset
            size_bytes = None
        else:
            url = source.url
            filename = source.filename
            size_bytes = source.size_bytes

        return FetchPlan(
            files=[FetchFile(url=url, filename=filename, size_bytes=size_bytes)],
            # The host reads the *artifact's* platform for a firmware
            # install, not this one; FetchPlan requires the field, so it is
            # set to the same value rather than to a placeholder that would
            # disagree with what the operator was shown.
            platform=platform_for(source.system),
        )

    # -- building the catalogue -------------------------------------------

    def _artifact(self, source, release: str) -> FirmwareArtifact:
        return FirmwareArtifact(
            firmware_id=source.firmware_id,
            name=source.name,
            # Raises "needs mapping" naming the system rather than guessing.
            # A BIOS under the wrong platform is invisible, not visibly
            # wrong -- the emulator just keeps saying it has no BIOS.
            platform=platform_for(source.system),
            license=source.license,
            version=release if source.asset else catalogue.CULT_OF_GBA_COMMIT[:12],
            description=f"{source.description} Source: {source.project}",
            archive=source.archive,
            members=list(source.members),
        )

    # -- configuration -----------------------------------------------------

    def _release(self) -> str:
        raw = str(self.ctx.config.get("sameboy_release") or "").strip()
        if not raw:
            return catalogue.DEFAULT_SAMEBOY_RELEASE
        if not _TAG_RE.match(raw):
            raise ConfigError(
                f"sameboy_release {raw!r} is not a plausible git tag. It "
                f"becomes part of a download URL and of a filename, so it "
                f"is restricted to letters, digits, dot, dash and "
                f"underscore -- for example 'v1.0.3'."
            )
        return raw
