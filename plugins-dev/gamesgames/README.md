# `gamesgames` — gamesgamesgamesgamesgames Plugin for ROM Hub

A [ROM Hub](https://github.com/BlizzHacker/rom-hub) plugin that brings **[gamesgamesgamesgamesgames](https://github.com/gamesgamesgamesgamesgames)**' decentralized video game data architecture into your library without bloating core **ROMarr**.

Rather than absorbing a generic social network, this plugin integrates `gamesgamesgamesgamesgames`' specific **video game data standard, gaming AppView, and player data sovereignty**:

---

## Purpose & Concept

Self-hosted game libraries (RomM, ROMarr, Gaseous, Retrom) store game metadata, verified ROM hashes, play states, and achievements inside isolated local databases. **[gamesgamesgamesgamesgames](https://github.com/gamesgamesgamesgamesgames)** defines an open, portable schema layer (`games.gamesgamesgamesgames.*`) on top of the AT Protocol so players and preservationists own their game shelf, play history, and dump verification proofs across every frontend and device.

### 1. Video Game Lexicons ([gamesgamesgamesgamesgames/lexicon](https://github.com/gamesgamesgamesgamesgames/lexicon))
- **`games.gamesgamesgamesgames.game`**: Unified open standard for video games with IGDB, Wikidata, and ROMarr/RomM cross-references.
- **`games.gamesgamesgamesgames.release`**: Platform editions, dump formats, and cryptographic DAT verification checksums (`SHA-256`, `CRC32`, `MD5`).
- **`games.gamesgamesgamesgames.playState`**: Decentralized player progression (`playing`, `completed`, `backlog`, `dropped`, `wishlist`), play time in minutes, and ratings synced across your screens.
- **`games.gamesgamesgamesgames.achievement`**: Cross-game trophies and achievements recorded on your personal game shelf.
- **`dev.cartridge.verificationRequest`**: Verification assertions signing authentic No-Intro and Redump matches to your DID.

### 2. Embedded Gaming AppView ([gamesgamesgamesgamesgames/happyview](https://github.com/gamesgamesgamesgamesgames/happyview))
- **Schema-Driven Gaming XRPC Queries**: Serves `games.gamesgamesgamesgames.getGame`, `listGames`, and `getPlayState` against your indexed game records.
- **Jetstream Event Consumer**: Processes real-time `commit` events (`create`, `update`, `delete`) for `games.gamesgamesgamesgames.*` collections.

### 3. Sovereign Gaming Studio ([gamesgamesgamesgamesgames/cartridge](https://github.com/gamesgamesgamesgamesgames/cartridge))
- **Portable Collection Export**: Converts verified library entries into sovereign `games.gamesgamesgamesgames.game` and `release` records with deterministic CIDs.
- **DID / PDS Sync**: Connects your gaming handle (`com.atproto.server.createSession` / `com.atproto.repo.putRecord`) for portable, player-owned game libraries across any device.

---

## Install in ROM Hub / ROMarr

From the command line:

```bash
rom-hub plugin install gamesgames
```

Or in **ROMarr**: open **Hub → Plugins** (`/#hub`), select **gamesgamesgamesgamesgames (Video Game Lexicons, HappyView & Cartridge Studio)**, and click **Install**.
