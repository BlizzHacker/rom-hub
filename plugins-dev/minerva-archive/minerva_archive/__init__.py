"""MiNERVA Archive — a ROM Hub plugin.

Myrient shut down on 31 March 2026; MiNERVA is the community effort to keep
its entire catalogue available, distributed as collection torrents over its
public, keyless JSON API. This plugin brings that archive to ROMarr — and to
anything else that speaks the ROM Hub plugin protocol — as a sandboxed source
rather than as code welded into a host.

Two capabilities:

  * `search`  — query MiNERVA, one result per collection torrent.
  * `torrent` — resolve a result's bare magnet into a tracker-ful one the
                operator's client can actually announce to.
"""
