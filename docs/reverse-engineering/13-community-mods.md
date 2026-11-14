# 13 — Community Mods and Prior Reverse-Engineering Projects

This document covers the tkdrob community mod (the largest body of
community work on the game), the Efeion Java project (the only prior code
that ever spoke HID to a UbiConnect), and briefly the btasm/btbase
projects. It states what each project got right, what the captures
contradicted, and — most importantly — what **not** to copy. No mod code
is reproduced here; behaviour is described, with file citations for
verification against the tkdrob clone.

Grading used in this document: **CONFIRMED** means verified in USB
captures and/or live replay; **INFERRED** means Lua/file evidence only;
**UNVERIFIED** means an open question. Capture verdicts below come from
the session-2 review recorded in the master document (§3, item 32) and
the protocol map's reconciliation notes.

## 1. Provenance warning — read first

Claims of the form "tkdrob added X" must be read as **"X is present in
tkdrob's bundle"**, not as first-invention claims. A full UI walkthrough
video from June 2014 shows the game running at version **1.2.439** —
two years before tkdrob's first commit — already containing the
community modes (Capture, Infection, Disarm, King of the Hill, and
others), the classes/handicap settings with the same stat values, 16-
player / 6-team support, and the Game / Activity / UI settings panels.
Either Ubisoft shipped later 1.2.x updates after 1.1.354, or (more
likely, given the community modes) that install is 1.2.439 plus a
pre-tkdrob community pack from the forum era, by the same authors tkdrob
credits. Either way, tkdrob's mod **consolidated and bugfixed that
lineage rather than inventing those features**. The 1.1.354-grounded
claims in these documents (7 modes, 8 players, 4 teams) describe the
earliest build, not the game as it existed by 2014. What exactly 1.2.439
is, and whether a 1.2.x installer is obtainable, are open questions (see
[14-open-questions.md](14-open-questions.md)).

## 2. tkdrob Battle-Tag mod — summary

| Fact | Detail |
|---|---|
| Repository | `https://github.com/tkdrob/Battle-Tag/` — "Mod for Ubisoft's Laser Tag system" |
| Author | tkdrob, sole committer on all 25 commits. Contributions credited in `Changelog.txt`: Random Person (team defaults, Capture, Classic Capture), Tatecg (announcer audio, King of the Hill / Hostage graphics, Disarm), Grimor (avatars, Silver/Purple images), j141 (SoloFrag, originally his mode) |
| Active | 2016-08-18 (initial commit) → 2018-03-10 (last commit, HEAD `84f857d`). **Abandoned.** 6 stars, 3 forks (checked 2026-11-09). No commits, issues, or releases since. |
| Versions | `Changelog.txt` v1.1 → v2.00 (newest-first). Git tags use a different scheme: 1.2.455 / 1.2.456 / 1.2.457b / 1.2.458a. 4 GitHub releases. |
| Base game | Official Battle Tag **1.1.354**, Windows only. The mod ships no executable and no copy of the game. |
| Shape | **Delta overlay, not a full game:** 523 tracked files, 204 Lua files, about 35 MB including git history. It contains only the files the mod adds or changes, laid out in the game's own directory shape. It does not contain the executable, the Quartz engine, music files, fonts, most UI sounds, the full Lua engine, or most original announcer files. "File X exists in the original but not in tkdrob" means the mod did not ship it, not that the mod removed it. **87 shared files differ** from the original install. Install: copy over an installed 1.1.354; run `setup.bat`. |
| Licence | MIT for tkdrob's own files (copyright 2016). The mod also modifies and redistributes Ubisoft's copyrighted Lua, audio, and textures and bundles Ubisoft's gun sound banks — the licence situation for the *original* assets is unchanged. Keep the two provenances separate. |
| USB/HID content | **None.** No protocol documentation, no report descriptor, no pairing code, no captures. The HID layer is Ubisoft's closed Quartz engine, not Lua. What the mod does contain is application-level protocol evidence (see §2.3). |

### 2.1 What the bundle adds

**Modes — 15 new packages (23 total against 8 original).** Each is a full
package with its own Lua, gun program, description, and icons. In brief:
Capture / CaptureMax / ClassicCapture (team flag capture with RFID
T-Bases; energy-scoring variant; an earlier implementation kept
separately); Disarm (asymmetric 4-digit bomb code via T-Bases 1–4);
Hostage Situation (single-flag variant, defending-team select); Infection
(zombie conversion, cure-by-medkit with diminishing lifepoint returns,
survivor tick-score); King of the Hill (hold-the-hill scoring with
interval, bonus, and delay settings); LastTeamStanding (team
elimination); Last of the Species (all-start-wolf tag, last wolf loses);
Liquidator (asymmetric hunter mode); ProTeamFrag (TeamFrag plus lives and
shield); Rustlers (energy-based team scoring, score equals energy times
5); SoloFrag (free-for-all with lives and health regeneration);
Stalk the Lantern (asymmetric Stalker/Defender base-scan mode); TheWolf /
Martian Wolf (tag-transfer wolf mode with wolf-time scoring). Full
settings and defaults for each are in `TKDROB_RESEARCH.md` §3.1. The
bundle's ByteCode overhaul made the gun programs more concise and shared
a common structure across modes; the author warns it "may have bugs so
please test" (see §3).

**Classes and handicaps.** Seven per-player classes (Soldier, Sniper,
Heavy, Stealth, Medic, Munitions, Birthday), selectable per player and
enabled per mode, with configurable health/ammunition values. Reference
defaults: Sniper 2 HP / 5 ammo, silent gun, longest-range beam; Heavy 8 HP
/ 18 ammo, silent gun, shortest-range beam; Stealth 5 HP / 8 ammo, quiet
gun; Medic 3 HP / 5 ammo; Munitions 5 HP / 15 ammo; Birthday 100 HP /
unlimited ammunition. Per-player handicaps (health, ammunition, clips,
lives; defaults 0) provide kids-vs-adults levelling. A random-
invulnerability option gives a random player brief invulnerability for
the respawn-time duration.

**Connection supervision.** Rejoin for dropped guns (whitelisted device
reconnecting to a running match), unregister after 80 seconds of silence,
vest-disconnect anti-cheat (trigger disabled after a 5-second vest-clock
stall, plus a manual leaderboard toggle), Prevent Launch (blocks starting
while a gun is disconnected), and dual-wield merging (two same-name,
same-team guns become one player, secondary mirroring the primary).
Mechanics are described in
[10-host-software-behavior.md](10-host-software-behavior.md) §4; whether
each mechanism is original 1.1.354 behaviour or community-lineage
behaviour is not settled by the sources.

**Scoring changes.** Frag-point values moved from per-mode constants to
global settings (player points 1–5, team points 0–4). Energy scoring for
Capture (energy mode) and Rustlers. Assistance (gun prompts to scan a
T-Base) became a global toggle; changing it forces a gun-program
re-upload.

**UI and settings.** New settings panels (per-mode Advanced Settings;
Game / Activity / UI / Test settings), a player-slot grid supporting up
to 32 slots with a scrollbar, 2-team side-by-side leaderboard option,
team ribbon controls, slide-animation and aspect-ratio options, a
last-selected-game memory, a team-cycle button, and a Force Revision
button (see §3). The "Add New T-Blasters" screen was renamed "T-Blaster
Options".

**Audio.** 22 new announcer files plus re-recorded versions of four
existing team files (different bytes, same filenames — new recordings by
Tatecg, not Ubisoft's). New gun banks (DANI and DSN variants, plus a new
DSN5 bank) carrying the new mode sounds and animations, requiring a
forced revision flash to reach guns. A Game Master chattiness toggle and
manual-launch countdown audio with a length setting. No new music.

**Results database.** An MS Access 2013 database (`BattleTag.accdb`,
856,064 bytes) for manual post-game reporting: fill Player/Victim tables,
export the game's per-game CSV, append it to the Scores table. It is not
written by the game; it is an optional viewer with a manual import
workflow. Windows/Access-only.

### 2.2 The fast-timer change — NOT original behaviour

The bundle changed the gun-program load interval from the original fixed
value to a value scaling with gun count. The original interval is a
fixed **250,000 µs**. The bundle's formula is **21,000 µs plus 16,000 µs
per player** (`UTActivity.State.Bytecode.lua:319-329`). Evaluated at 2
players, that formula gives 53,000 µs — the "53 ms" figure sometimes
quoted for the mod. **Do not copy the scaled interval, or the 53 ms
figure, as original behaviour.** The original is the fixed 250 ms
interval. The scaling insight (load time grows with gun count) may be
worth keeping as a timing budget in new work, but the bundle's formula is
a mod change, and the author's own warning about ByteCode-overhaul bugs
applies to the surrounding changes. The bundle also raised the USB
update frame rate from 4 to 15 (`ULUsb.lua:26`) and made the RoundLoop
cycle a Test setting (30,000–100,000 µs); both are mod changes, not
original values. A changelog mention of a frame rate of 12 in a "Test
folder" variant refers to files that are not in the repository — an
unshipped experiment, not a tested value.

### 2.3 What the bundle contributes to protocol understanding

The bundle's Lua is the best surviving documentation of the
*application-level* message semantics, because every mode decodes the
gun's periodic status message identically. Specifically: the UbiConnect
message dispatch (initialisation completion, connection request,
firmware update); the 16-byte connection-request argument and
lock/whitelist logic; the status-message field layout as decoded by the
game (hit count, ammunition, clips/score, gun clock, and mode-specific
fields, all as big-endian 16-bit pairs in the decoded argument list);
the downlink state values (normal, dual-wield mirror, trigger disabled);
and the per-mode CONFIG/RECEIVE/SEND variable declarations that define
the gun-side program interface. The status field layout in Lua argument
indexing must not be confused with wire-byte offsets in the HID report —
the protocol map's wire layout governs the wire, and the Lua layout
governs the decoded arguments. The bundle contributes **no capture data**
and no HID-layer information.

### 2.4 Team assignment mechanisms (bundle layering)

Three mechanisms layered over time: stored gun default teams; a
profile-icon prefix rule under which an icon filename starting with a
digit and underscore sets the profile team from that digit
(`ULUsbDevice.lua:268-274`); and a UI team-colours option assigning teams
purely by gun default. Names set in bulk for all guns on a team via the
bundle's bulk-set feature are **not** stored on the guns (per the
changelog). The icon-prefix rule is a bundle hack; a real settings model
replaces it.

## 3. tkdrob warnings — what not to copy

1. **The fast timer (§2.2).** The scaled load interval and the 53 ms
   figure at 2 players are mod changes. Original behaviour is the fixed
   250 ms interval. Do not present the mod timing as the original, and do
   not assume guns tolerate arbitrary speedups without testing.
2. **The 5th/6th-team firmware hack.** Gun firmware does not address
   teams 5 and 6. The bundle works around this by forcing friendly fire
   on for 5–6 team games and requiring a custom Silver/Purple T-Base pack
   (major/minor 54/55) and a King-of-the-Hill T-Base 7 (56) that most
   owners do not have. Do not promise 5–6 teams until sniffing proves the
   guns can address them. Silver/Purple art exists only in the bundle;
   no such art exists in the original textures.
3. **The 32-player claim.** The changelog advertises 32 players; the code
   ceiling for activities is 48 and for profiles is 32. The 48 value is
   unadvertised and presumably untested at that scale. Quote 32 as the
   bundle's supported number, and treat both numbers as host-data-model
   limits, not as proven radio/host behaviour: the capture evidence
   covers at most 4 guns in one session, and no capture demonstrates
   32-player (or 16-player) operation on the wire. Designing for more
   than 8 players is reasonable; claiming the hardware supports 32 is
   UNVERIFIED.
4. **The flashing path.** New DANI/DSN banks require a forced-revision
   flash (`REG_FORCEREVISION`, default false, toggleable from the
   T-Blaster Options UI). Custom animations are made by hex-editing DANI
   banks, preserving the `22 04 19 86` trailer and recomputing the MD5
   footer. The author says a flashing error "does not brick a T-Blaster"
   in his testing but disclaims all responsibility for damage. Do not
   build a flashing path: the project decision is no custom gun firmware
   and no flashing (brick risk), and no needed sound justifies it — the
   original banks already cover the original modes.
5. **Dangling audio references (bugs in the bundle).** Referenced in code
   but present in neither the bundle nor the original install, so the
   calls fail or are skipped silently: `DLG_GM_INFECT_01.wav`,
   `DLG_GM_INFECT_02.wav`, `DLG_GM_WOLF_01_alt.wav` (registered by the
   Infection and TheWolf RoundLoop modules), and
   `DLG_GM_GLOBAL_125.wav`, `DLG_GM_GLOBAL_126.wav`,
   `DLG_GM_GLOBAL_132.wav` (added to a sound list in `UTGameMaster.lua`;
   the original GLOBAL files top out at 119 and the bundle ships no
   GLOBAL files at all). Do not treat these as assets to reuse. The same
   class of bug appears in gun codes: SC11 is referenced in bundle
   ByteCode but present in no bank (see
   [09-gun-audio.md](09-gun-audio.md)).
6. **Orphan files.** `GunDisconnected.wav` is referenced only in the
   changelog, never in code. `ByteCode2.txt`/`.lua` files are referenced
   by no Lua code (Infection's differs from its ByteCode file by one
   blank line). The changelog's "Test folder" USB variant is not in the
   repository. Treat all three as orphans until proven otherwise.
7. **Author's bug warning.** The changelog warns the ByteCode overhaul
   "may have bugs so please test and leave feedback on the forum." Treat
   mode rules as reference designs, not battle-tested specs — especially
   Infection, TheWolf, and Last of the Species, whose transfer/cure
   timing has edge cases.
8. **`setup.bat` is 2016-era Windows only.** Win7/XP paths, admin/UAC
   prompt, deletes per-user settings files, kills a placeholder process
   name as a no-op, then self-deletes. Do not run it outside its
   intended context; it only deletes settings files.
9. **External dependency.** The Ben10/RNC avatar pack is a Dropbox link,
   not in the repository — likely dead; do not rely on it.
10. **Re-recorded announcer files.** The bundle's FRAG_TEAM 05–08 files
    have the same filenames as originals but different bytes (new
    performances). Do not ship them as replacements for the originals
    without deciding which performance is wanted, and keep their
    provenance (Tatecg recordings) separate from Ubisoft's.
11. **Licence hygiene.** The bundle is MIT for tkdrob's own files but
    redistributes Ubisoft's Lua, audio, textures, and gun banks. Reusing
    the bundle's *new* recordings or Lua is a different question from
    reusing Ubisoft's originals.

## 4. Efeion — "Unofficial BattleTag" (Java)

| Fact | Detail |
|---|---|
| Project | "Unofficial BattleTag" (Efeion), SourceForge, registered 2012-05-13, last update 2015-07-04, Alpha. Files: `BT 5_27_12.zip` (434.8 KB, May 2012) and `BT.zip`. Licence GPLv3. |
| Language | Java (Swing) + C, using hidapi. |
| Significance | **The only known code that ever spoke HID to a UbiConnect.** FreeFrag and TeamFrag reportedly worked. Treat as a Rosetta Stone for framing, report handling, and the pairing state machine — not as a drop-in. |
| Key components (as documented) | HID enumeration; a send-packet path with a UbiConnect pair request; a USB write path; a message handler with pair acceptance; a game-mode loader; CONFIG/SEND/RECEIVE variables. |

### 4.1 What matched the captures — CONFIRMED

Session-2 review re-checked every cited frame against raw captures and
confirmed most of the Java project's model:

- The `[size][id][cmd][args]` message framing.
- The `0x92` configuration layout, the `0x83` bytecode upload, the `0x93`
  countdown, the `0x94` game-over colours, and the `0x95` status field
  map — all matched the captures (this cross-check was the "work from
  other projects" step, and it worked).
- Hit-id placement: the Java reading that TeamFrag hit ids start at wire
  byte 12 is CONFIRMED (5-slot shift register, bytes 12–16, most-recent-
  first; decisive frame in capture 47 game 2). FreeFrag hit ids starting
  at wire byte 8 also matches the Java model. An interim Nov 10 verdict
  questioning the byte-12 reading is superseded by the session-2 result.
- The vest-connected status poll reading is CONFIRMED (the `0xC1` harness
  check; see [10-host-software-behavior.md](10-host-software-behavior.md)).
- The CONFIG/SEND/RECEIVE variable model, including the requirement that
  the counts match exactly between host and gun program.

### 4.2 What the captures contradicted — do not copy

| Java behaviour / claim | Capture verdict |
|---|---|
| Revision request "received no response" (comment in the Java source) | **Contradicted.** Revision replies exist in captures (init and pairing sequences). The Java project likely lost them to a double-read bug in its USB read path (reading twice and discarding a reply). Do not copy the no-response assumption or the read pattern. |
| Ping / vest-poll size byte: Java sends a leading size byte of 1 with a trailing zero | **Contradicted.** The official game sends the 3-byte forms with a leading zero byte and no trailing zero. The size byte differs; use the captured forms. |
| Cadence: ping every 2 s, status polling every 100 ms | **Contradicted.** Measured cadence is about 6 s idle for the ping (about 0.311 s per poll cycle in-game for the ping's in-game occurrence) and about 0.311 s per gun for the status poll. Do not copy the Java cadence figures. |
| End-of-match sequencing: send one colour to all guns, then the correct colours | **Apparent sequencing bug.** The correct behaviour is to send each gun its correct final colour once. Do not copy the two-phase sequence. |
| Profile icon hardcoded to one filename | **Wrong.** Real `0x84` replies carry the per-profile icon, which differs between profiles. Never hardcode an icon. |
| Pairing on 64-bit Windows | **Broken in the Java project.** The USB write path threw an "incorrect parameter" I/O error, fixed by prepending a `0x00` report-ID byte — but pair acceptance was never set and the UbiConnect light never stopped pulsing. The report-ID fix is real and matches the HID packing finding; the pairing failure is a Java-project defect, not a protocol fact. Modern replay pairs successfully. |
| Multi-UbiConnect support | **Unfinished** in the Java project. No multi-hub behaviour can be taken from it. See the multi-UbiConnect open question in [14-open-questions.md](14-open-questions.md). |

### 4.3 Firmware work — dead end

Efeion disassembled gun firmware with an 8051 disassembler and saw
"clues", but concluded modifying it would take a great deal of effort and
time; nothing was published. Forum discussion confirms no firmware source
exists. The firmware blobs describe the gun's internals (radio plus
gun-side game logic), not the PC-to-UbiConnect HID layer. Do not pursue
firmware work for protocol reproduction.

## 5. btasm and btbase (perotinus) — brief

Two further abandoned projects (last activity 2015) document adjacent
layers, not the HID layer:

- **btasm** — a gun-bytecode compiler (C / Yacc / Lex) plus an unfinished
  specification. CONFIG/SEND confirmed working; RECEIVE uncertain. It
  establishes that guns run bytecode sent from the PC at game start and
  exchange CONFIG / SEND / RECEIVE variables with the PC, with counts
  required to match exactly. No licence file is stated.
- **btbase / BTBaseUtil** — T-Base tooling (C, libnfc, Qt GUI; LGPL-3.0).
  T-Bases are Mifare Ultralight / Ultralight C tags. The project documents
  16 medkit + 16 ammo + 16 base types per major number, 256 major-minor
  combinations recognised by guns, with only 8 base types shipped by
  Ubisoft. It is the only fully documented on-air format — but it is
  gun-to-tag communication, not PC-to-UbiConnect communication. The host
  learns of RFID scans only indirectly, through the next status report.

No Python, Rust, Go, or Node project for this hardware exists, and a
2026-11-10 search for further Battle Tag projects (code hosts, forum,
web) found nothing beyond the four projects covered here and in §4–§5.
That negative result is stated explicitly: there is no fifth project to
mine.

## Sources

- `TKDROB_RESEARCH.md` §§1–7 (identity, shape, features, protocol
  evidence, warnings, priorities).
- `BATTLE_TAG_MASTER.md` §4.2 (prior projects), §7.0 (version correction
  1.2.439), §§7.1–7.4 (mod summary, caveats), §3 items 12/32 (session-2
  Efeion review), §10 Q20–Q21 (1.2.x questions).
- `SPEEDUP_RESEARCH_2026-11-10.md` §2 (search for other projects: none
  exist; firmware dead end; forum threads; FCC report context).
- `GHIDRA_PASS_2026-11-10.md` Q1–Q2 (HID packing confirming the report-ID
  finding; native transport; no hidden command path).
- `GUI_SCREENSHOTS.md` §10.3 (1.2.439 version finding).
- `sniffing-results/protocol_map.json` (reconciliation notes: Java
  contradictions, hit-id model, game-over sequencing).
- `GUN_BANK_DECODING.md` §§4–5 (SC11 dangling reference; DANI/animation
  guide; SD19 slip).
