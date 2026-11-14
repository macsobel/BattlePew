# 11 — Asset Inventory (Original Install, 1.1.354)

Summary of what ships in the original Battle Tag install, where it lives,
and what it is for. This document describes formats and inventories only:
**no original asset is included here** — no audio files, no images, no
screenshots, no captures. Filenames are given so a reader with the
installer can locate each asset; spoken content is described functionally
by game event, not transcribed. The only short functional phrases used
below are countdown numbers and end-of-match callout labels, as permitted
for this inventory.

Grading used in this document: **CONFIRMED** means verified by direct
inspection of the extracted files or by captures; **INFERRED** means
derived from Lua references or file patterns; **UNVERIFIED** means an open
question. Counts come from the asset inventory taken at extraction time;
the gun-bank figures come from the decoding report, which supersedes the
inventory's preliminary bank notes where they differ (see §7).

Sources: `ASSET_INVENTORY.md` (primary), `BATTLE_TAG_MASTER.md` §§5.5,
5.7, 6, 9, `GUN_BANK_DECODING.md`, `TGA_PALETTE.md`,
`transcripts/ANNOUNCER_CATALOG.md` (file lists and durations only — its
transcript column was not used for this document; the event mapping in
§4 comes from the master document's Lua-grounded "played when" column).

## 1. Source and extraction

- Installer: `BattleTag_Installer.zip`, 523 MB, 341 entries, dated
  2010-11-02 / 2010-11-08, downloaded 2026-11-09 from Ubisoft support.
- Extraction: `msiextract` over `BattleTag.msi` (4 MB) plus `Data1.cab`
  (262 MB, the real game) into
  `game/Program Files/Ubisoft/BattleTag/`.
- Extracted game: **1,150 files, 319 MB** (315.8 MB in the BattleTag
  directory alone).
- The outer zip is mostly DirectX redistributables (275 `.cab` files,
  468.5 MB — ignore) plus one loose installer-menu music file
  (`Autorun/Resources/Audio/AutorunBackgroundMusic.wav`, 21 MB, installer
  menu music only, not game audio).

## 2. Counts by type

| Type | Count | Size | Location / role |
|---|---|---|---|
| `.tga` textures | 492 | 49.2 MB | UI textures, avatars, pictograms, icons (§5) |
| `.wav` audio | 333 | 64.6 MB | Announcer voices + UI sounds + logo sting (§§3–4) |
| `.lua` scripts | 215 | 0.9 MB | Full PC-side game logic, readable (§6) |
| `.bin` banks/firmware | 22 | 0.6 MB | Gun sound banks + firmware blobs (§6, and [09-gun-audio.md](09-gun-audio.md)) |
| `.bik` video | 9 | 172.2 MB | Bink video; credits alone 165.4 MB (§6) |
| `.ogg` music | 4 | 21.6 MB | Music tracks, playable as-is (§3) |
| `.pdf` manuals | 4 | 3.9 MB | Reference manuals |
| `.ttf` fonts | 2 | — | UbiTTB (bold) / UbiTTL (light) display fonts (§6) |
| `.nfo` / `.fontdef` / `.loc` / `.txt` | — | — | Localisation + mode descriptions (§6) |
| `.exe` / `.dll` | — | — | Windows binaries: `BattleTag.exe` (620 KB), `Ubitoys.Tag.Win32Retail.exe`, Bink/OpenAL libraries — ignore for reproduction (§7) |

The `.wav` total of 333 is 161 English announcer files + 162 French
announcer files + 9 UI sounds + 1 logo sting file.

## 3. Music and UI sounds

### Music — directly reusable, playable as-is

All four music files are standard Ogg Vorbis; headers were verified
(`OggS`, `RIFF/WAVE`). They play with no conversion.

| File | Size | When it plays (from Lua) |
|---|---|---|
| `Data/Audio/musicmenu.ogg` | 9.7 MB | Menu / title |
| `Data/Audio/musicingame.ogg` | 1.9 MB | In-game — started at RoundLoop begin in most modes |
| `Data/Audio/musicintroduction.ogg` | 10.4 MB | Introduction / tutorial |
| `Data/Audio/musicupdating.ogg` | 99 KB | Update / revision state |
| `Data/Audio/ubisoft.wav` | 1.0 MB | Ubisoft logo sting |

The menu track is the longest; the in-game track is short and loops.

### UI sounds — non-speech effects

Nine files in `Data/Audio/Ui/`. These are sound effects, not speech:
machine transcription hallucinated words on several of them, and those
hallucinations are discarded here (the corrected reading is the master
document §6.6, which supersedes the generated catalog's UI rows).

| File | Duration | Function |
|---|---|---|
| `Back.wav` | 0.16 s | Back / navigation blip |
| `ChangeValue.wav` | 0.08 s | Value-change blip |
| `CloseWindow.wav` | 3.0 s | Window-close sound |
| `GunDetect.wav` | 2.0 s | Plays when a gun is found in PlayersManagement |
| `OpenWindow.wav` | 3.0 s | Window-open sound |
| `Ranking.wav` | 2.0 s | Rankings screen sound |
| `Rollover.wav` | 0.04 s | Rollover blip |
| `Rollover2.wav` | 0.04 s | Rollover blip (variant) |
| `Validation.wav` | 0.41 s | Confirm / validation blip |

`GunDetect` and `Ranking` are game moments, not chrome; the rest are
ordinary interface sounds.

## 4. Announcer event-to-filename mapping

Location: `System/Locale/En/Audio/GameMaster/` (161 English files) and
`System/Locale/Fr/Audio/GameMaster/` (162 French files, the same set in
French). Naming pattern: `DLG_GM_<CATEGORY>_<NN>.wav`. Filenames do not
contain the spoken text. Total English audio is 6.2 minutes, averaging
2.3 s per file. All 161 English files were machine-transcribed during
the research; transcriptions are **not** reproduced here. The mapping
below gives, for each game event, the file(s) the Lua plays, with the
trigger, priority, and probability where the Lua grounds them. Blank or
"not grounded" means no Lua reference or computed-name explanation was
found — the file may be reached via a computed name, used by the native
engine, or unused. A blank is never filled in by guessing. Of the 161
files, 49 are referenced literally in the Lua; the rest are reached via
computed names or are unreferenced (checked by searching all Lua files).

Uncertain transcriptions in the research are marked `[?]` there; this
document does not quote them. Any line reused as a user-facing string
must be verified by listening to the file first.

### 4.1 GLOBAL — general callouts (110 files, numbered 01–119 with gaps)

Numbering gaps in the catalog: files 105–112 and 117 do not appear; the
catalog runs 01–104, then 113–116, 118, 119.

| Event | File(s) | Trigger / scheduling (Lua-grounded) |
|---|---|---|
| Match start, ready lines | `DLG_GM_GLOBAL_01.wav`, `DLG_GM_GLOBAL_02.wav` | BeginMatch; alternate selection probabilities 0.9 / 0.1 |
| Countdown, numbers 10 down to 1 | `DLG_GM_GLOBAL_03.wav`–`DLG_GM_GLOBAL_12.wav` | BeginMatch; file index computed from the counter |
| Round start, go lines | `DLG_GM_GLOBAL_13.wav`–`DLG_GM_GLOBAL_15.wav` | Every frag mode at round start; alternate probabilities 0.8 / 0.1 / 0.1 |
| Medkit heal acknowledgement | `DLG_GM_GLOBAL_16.wav`, `DLG_GM_GLOBAL_17.wav` | Medkit heal event, priority 2 |
| Player at 1 lifepoint, medic call | `DLG_GM_GLOBAL_18.wav`, `DLG_GM_GLOBAL_19.wav` | Player at 1 lifepoint, priority 3 |
| Low ammunition warning | `DLG_GM_GLOBAL_22.wav`, `DLG_GM_GLOBAL_23.wav` | Ammunition down to 3, priority 3 |
| Context-dependent reaction lines | `DLG_GM_GLOBAL_24.wav`, `DLG_GM_GLOBAL_25.wav` | Priority 2, play probability 0.25; exact trigger not pinned |
| Three consecutive hits on the same player | `DLG_GM_GLOBAL_26.wav` | Priority 2–3, play probability 0.33 |
| Frag acknowledgement | `DLG_GM_GLOBAL_27.wav`, `DLG_GM_GLOBAL_28.wav`, `DLG_GM_GLOBAL_37.wav`, `DLG_GM_GLOBAL_38.wav` | On a frag |
| Hit reaction | `DLG_GM_GLOBAL_29.wav`, `DLG_GM_GLOBAL_30.wav` | On a hit, priority 3, play probability 0.333 |
| Mode/activity introduction lines | `DLG_GM_GLOBAL_32.wav`–`DLG_GM_GLOBAL_35.wav` | Activity-intro registration at round start, priority 4 |
| Respawn acknowledgement | `DLG_GM_GLOBAL_36.wav` | On respawn, priority 2 |
| Teammate-frag reaction | `DLG_GM_GLOBAL_41.wav`, `DLG_GM_GLOBAL_42.wav` | TeamFrag teammate-frag event, priority 2; exact trigger not fully pinned |
| Five consecutive misses | `DLG_GM_GLOBAL_43.wav`, `DLG_GM_GLOBAL_44.wav` | After 5 consecutive misses, priority 2 |
| Remaining-time warnings | `DLG_GM_GLOBAL_45.wav` (60 s left), `DLG_GM_GLOBAL_46.wav` (30 s left), `DLG_GM_GLOBAL_47.wav` (15 s left) | Timed at playtime minus 60 / 30 / 15 s |
| Random hurry filler | `DLG_GM_GLOBAL_48.wav`, `DLG_GM_GLOBAL_49.wav` | At minus 57 s, minus 27 s, minus 12 s; priority 3, play probability 0.333 |
| End of match (game-over callouts) | `DLG_GM_GLOBAL_50.wav`–`DLG_GM_GLOBAL_52.wav` | EndMatch |
| Winner announcement, single winner | `DLG_GM_GLOBAL_53.wav` | FinalRankings, single winner |
| Winners announcement / Duel round-winner base | `DLG_GM_GLOBAL_54.wav` | FinalRankings tie/shared win (INFERRED, not fully pinned); also the base index for Duel round-winner callouts computed from classId |
| Player-number callouts, players 1–8 | `DLG_GM_GLOBAL_55.wav`–`DLG_GM_GLOBAL_62.wav` | Player-number callouts; Duel computes the index from classId |
| Player-number callouts, players 9–16 | `DLG_GM_GLOBAL_63.wav`–`DLG_GM_GLOBAL_70.wav` | Same computed-name family; no literal Lua reference |
| Tag'n Shoot, T-Base tag announcements | `DLG_GM_GLOBAL_72.wav`–`DLG_GM_GLOBAL_75.wav` | One per T-Base tagged; index computed from the stage state (0–3) |
| Tag'n Shoot, tag follow-ups | `DLG_GM_GLOBAL_78.wav`–`DLG_GM_GLOBAL_81.wav` | Random follow-up after a tag; offset 0.5 s, priority 1 |
| Tag'n Shoot, UbiConnect-hit reactions | `DLG_GM_GLOBAL_88.wav`–`DLG_GM_GLOBAL_90.wav` | UbiConnect hit (command `0xC3`), priority 1 |
| FinalRankings player-number family | `DLG_GM_GLOBAL_96.wav`–`DLG_GM_GLOBAL_104.wav` | Index computed from classId. Quirk: the base file (96) speaks a race-related line, not a player number — the mismatch is unresolved; the player-number callouts proper start at 97 |
| Round-transition lines | `DLG_GM_GLOBAL_113.wav`–`DLG_GM_GLOBAL_116.wav` | Trigger not grounded in the Lua read |
| Duel draw | `DLG_GM_GLOBAL_118.wav` | Duel draw (both hit, or both empty) |
| Duel round-win follow-up | `DLG_GM_GLOBAL_119.wav` | Duel: played after the winner callout |

Files with **no grounded trigger** in the Lua read (do not assign them
events without further evidence): `GLOBAL_20`, `GLOBAL_21`,
`GLOBAL_31`, `GLOBAL_39`, `GLOBAL_40`, `GLOBAL_71`, `GLOBAL_76`,
`GLOBAL_77`, `GLOBAL_82`–`GLOBAL_87`, `GLOBAL_91`–`GLOBAL_95`, and the
round-transition files listed above. Their spoken content (in the
research transcripts, not reproduced here) narrows several to race or
Tag'n Shoot prompts, but content alone does not establish a trigger.

### 4.2 INIT — Introduction tutorial voiceover (22 files)

Files `DLG_GM_INIT_01.wav`–`DLG_GM_INIT_23.wav` with file 16 absent from
the catalog (the set runs 01–15, then 17–23). **No Lua reference was
found** for any INIT file; the tutorial attribution is INFERRED from the
spoken content, which unambiguously follows the Introduction stages
(UbiConnect connection, batteries, pairing, shooting the UbiConnect,
vests, shooting other players, T-Boxes, readiness), matching the
tutorial script text in `Main.loc`. Treat the ordering as content order,
not as a Lua-grounded sequence. The trigger is the tutorial sequence
itself.

### 4.3 FRAG_SURVIVAL — Last Man Standing (12 files)

| Event | File(s) | Trigger / scheduling |
|---|---|---|
| Players-remaining count on each elimination | `DLG_GM_FRAG_SURVIVAL_01.wav`–`DLG_GM_FRAG_SURVIVAL_06.wav` | Index computed from players alive (8 minus players alive), priority 1 |
| Ambience after an elimination | `DLG_GM_FRAG_SURVIVAL_07.wav`–`DLG_GM_FRAG_SURVIVAL_12.wav` | Offset 4 s, play probability 0.333, priority 3 |

### 4.4 FRAG_TEAM — Team Frag (8 files)

| Event | File(s) | Trigger / scheduling |
|---|---|---|
| Team takes the lead (Red, Blue, Yellow, Green) | `DLG_GM_FRAG_TEAM_01.wav`–`DLG_GM_FRAG_TEAM_04.wav` | Priority 2 |
| Winning team at FinalRankings (Red, Blue, Yellow, Green) | `DLG_GM_FRAG_TEAM_05.wav`–`DLG_GM_FRAG_TEAM_08.wav` | FinalRankings |

### 4.5 OW_DUEL — Wild West Duel (9 files)

| Event | File(s) | Trigger / scheduling |
|---|---|---|
| Duel introduction | `DLG_GM_OW_DUEL_01.wav`, `DLG_GM_OW_DUEL_02.wav` | PlayersSetup |
| Round introduction (random pick) | `DLG_GM_OW_DUEL_03.wav`–`DLG_GM_OW_DUEL_05.wav` | Random pick per round, then phase advances |
| "Go" signal | `DLG_GM_OW_DUEL_06.wav`, `DLG_GM_OW_DUEL_07.wav` | Sets the can-shoot flag true |
| Early shot (shot before the signal) | `DLG_GM_OW_DUEL_08.wav`, `DLG_GM_OW_DUEL_09.wav` | Opponent scores |

### 4.6 Announcer engine parameters

The mapping above is used through the Game Master engine
(`UTGameMaster.lua`): immediate play with optional callback, and scheduled
registration with alternate paths, selection probabilities, priority
(0 timed/ambient, 1 event, 2 important, 3 urgent, 4 activity intro),
time offset, and overall play probability. See
[10-host-software-behavior.md](10-host-software-behavior.md) §3. Game
Master volume default is level 5 of 0–8.

### 4.7 French set

`System/Locale/Fr/Audio/GameMaster/` holds 162 files — the same lines in
French (one more file than the English set; the extra file is not
identified in the sources). The French set was **not transcribed**.
Transcribe it only if bilingual support is wanted.

### 4.8 tkdrob announcer additions (not original assets)

The tkdrob bundle adds 22 announcer files (Capture, Disarm digits,
FRAG_TEAM 09–12, Hostage, King of the Hill, LastTeam 13–18, Rustlers 13)
and re-records FRAG_TEAM 05–08 with different bytes. These are new
recordings, not Ubisoft originals, and belong to the mod's provenance.
The bundle also references announcer files that exist nowhere (dangling
references) — see [13-community-mods.md](13-community-mods.md). Do not
count mod files in the original inventory.

## 5. Graphics (`.tga`) and palette

### Counts and groups

492 `.tga` files in total across the install: 464 under `Data/Texture/`
plus Duel package textures under `Packages/OldFashionDuel/`. Groups:

- **UI:** 161 files under `Data/Texture/Ui/`, plus 74 component files.
  Includes panels, buttons, backgrounds, mode loading colour tiles, and
  rankings backgrounds. There are no per-mode `Loading_<Mode>.tga` files
  in this build — the mode loading screens are three 16×12 gradient tiles
  (orange, grey, red).
- **Avatars:** 12 animals (Bear, Bird, Bull, Cat, Dog, Dragon, Eagle,
  Lion, Panda, Puma, Shark, Snake) at multiple sizes, including 24 files
  each at 32×/64×/80× and 12 files at 256×/42×, plus `_Out` (eliminated)
  variants. Avatar art is mascot sticker heads with thick white outlines
  on transparency.
- **Icons:** 16×/32×/64×, including per-team Death/Hit/Star variants in
  Red/Blue/Green/Yellow, and class icons in the tkdrob bundle only.
- **Pictograms:** 128×/64×/32×/96×, including HUD pictograms and RFID/base
  pictograms (RF01–RF09), plus 7 mode icons.
- **Duel set:** arrow, border, round-background, and versus textures
  under the Duel package.

`.tga` files convert cleanly to PNG (ImageMagick / ffmpeg / Pillow).
Originals are low-resolution; do not stretch them as-is on high-density
displays.

### Palette summary (measured from pixels, not guessed)

The palette was measured in 2026-11-09 from 36 representative `.tga`
files converted to PNG, by median-cut quantisation of opaque pixels only
(6 clusters per file), with targeted medians for the logo lettering, the
grass field, and the countdown orange. Every hex below is a measured
pixel value. The Lua names colours but stores no hex values (confirmed).
Full per-file measurements are in `TGA_PALETTE.md`.

| Hex | Role | Measured from |
|---|---|---|
| `#F26314` | Battle Tag orange, deep | Loading-screens tile, top cluster |
| `#FB8119` | Battle Tag orange, bright (focused/hover, active state) | Focused main-menu button, top cluster |
| `#FA9D1D` | Amber orange (countdown / game-over header gradients) | Countdown header, top cluster |
| `#D41900` | Team Red, vivid pure red | Red slot, base cluster |
| `#0767C5` | Team Blue, strong medium blue | Blue slot, base cluster |
| `#00AB03` | Team Green, vivid green | Green slot, base cluster |
| `#FFB432` | Team Yellow, amber-yellow | Yellow slot cluster |
| `#A5A9BC` | Steel grey-blue (menu/ranking backgrounds, emblem grey) | Rankings background, top cluster |
| `#F7F7F7` | Panel white (panels, leaderboard rows, windows) | Panel texture |
| `#58504F` | Charcoal (logo lettering, dark text) | Median of dark pixels in the logo band |
| `#6A840E` | Grass green (playground / mode-explanation background) | Median of the playground background |

Orange family as a measured range: `#F26314` (deep) → `#ED5B1D` (logo
"TAG" median) → `#F36E1C` (Duel round background) → `#FB8119` (bright)
→ `#FC9016` (countdown median) → `#FA9D1D` (amber end). The original
uses one deep orange for headers and one bright orange for the
active/focused state (grey buttons at rest, orange on focus). Supporting
measured values: heart/health red `#DA0B0B`, countdown spotlight amber
`#FFBA00`, grey button rest state `#75746E`, window-frame tan accent
`#BC7840` (small header accent — do not promote to a theme colour),
darker leaderboard-header variants (`#B31A14` red header, `#175263` blue
header) used when a team colour is a background rather than a marker.

Visual character, from direct inspection of the textures: light, airy,
techno-toy — **not dark**. Menus sit on a pale cool grey-blue gradient
with a large faint white target/scope reticle motif; the logo is
charcoal plus vivid orange; excitement moments (countdown, game over,
lead-change banner) go full-bleed vivid orange with a fine
honeycomb/hex-mesh texture. Two background worlds: grey-blue tech for
menus/rankings, photographic grass green for mode-explanation screens.
Silver/Purple (tkdrob teams 5/6) art does not exist in the original
textures.

## 6. Code, banks, video, fonts, localisation

### Lua and ByteCode (the design reference)

215 `.lua` files, fully readable:

- `Data/Script/Lua/` — PC-side engine: game, activity, player, Game
  Master, and state machines (Title, Matchmaking, PlayersSetup,
  RoundLoop, Rankings, and others).
- `Packages/<Mode>/Data/Script/Lua/` — per-mode logic for 8 packages:
  FreeForAll, FreeFrag, Introduction, LastManStanding, OldFashionDuel,
  StarterFrag, TagThenShoot, TeamFrag.
- Each mode also has `Data/Script/ByteCode/*.ByteCode.txt` — human-readable
  gun-side logic (states, events for trigger/scan/hit/timer, sound/motor/
  LED/IR directives, CONFIG variables such as lifepoints, ammunition,
  clips, respawn time, assistance, medkit).

These files are the design reference for the replacement engine. No Lua
source is reproduced in these reverse-engineering documents; behaviour is
paraphrased with file citations (see
[10-host-software-behavior.md](10-host-software-behavior.md)).

### Gun sound banks and firmware (`.bin`, 22 files)

Located under `System/Revision/0x02000020/Banks/` and `0x01000010/`:
DSN1–DSN4 banks in EN01/EN02/FR01/FR02 variants (7–54 KB each), DANI
animation banks, and one `0x00010000.bin` firmware blob per Revision
folder. Full format, codec, and code map: [09-gun-audio.md](09-gun-audio.md).
Decoded output from the research: 57 playable files (31 original English,
17 French, 9 tkdrob-only). Firmware blobs were not decoded and are not
needed for sounds.

### Video (`.bik`, 9 files, 172.2 MB)

- `Data/Video/credits.bik` (165.4 MB) — credits; skip.
- `Data/Video/ubisoft.bik` (3.4 MB) — logo sting.
- `Data/Video/background.bik` (2.0 MB) — menu background.
- `Data/Video/UIAnimatedButton_*.bik` — 6 small button animations
  (Basics, Games, Playnow, Profile, Quit, Settings), 0.2–0.3 MB each,
  each about 5 seconds of a looping star animation.

`.bik` is Bink video. Extract audio offline with ffmpeg / RAD Video Tools
if needed; do not decode at runtime.

### Fonts

Two files under `Data/Font/`: `UbiTTB.ttf` (72,712 bytes, bold) and
`UbiTTL.ttf` (76,092 bytes, light). Font definitions in
`System/Locale/En/*.fontdef` use face names for the title face in regular
and bold, at heights 15/22/26/34/40/300 (the 300-point size is presumably
the giant countdown display type).

### Localisation and descriptions

`.loc` files (`Main.loc`, `Basics.loc`, `Tips.loc`) hold UI text under a
key system (menu, game-option, tip, in-game keys, and others) — they hold
UI strings, not announcer voice lines. `.nfo` files hold mode
descriptions; `.fontdef` files hold font definitions; `.txt` files
include the ByteCode gun programs and supporting text. English and French
locale sets both ship (`System/Locale/En/` and `System/Locale/Fr/`).

### Manuals (`.pdf`, 4 files, 3.9 MB)

Reference manuals. The hardware manual contains no GUI screenshots
(checked during the research). Manual claims that conflict with the
local files (for example the out-of-range kick time) are recorded as open
questions, with local files winning for what the build does.

## 7. What is NOT included, and why

| Excluded | Why |
|---|---|
| DirectX `.cab` files (275 files, 468.5 MB) | Old DirectX redistributables from the installer wrapper; not game content. |
| `.exe` / `.dll` Windows binaries | Windows-only executables and libraries. The native USB layer was studied statically (Ghidra pass) for HID packing; the binaries themselves are not reused. |
| `credits.bik` (165.4 MB) | Credits roll; 95%+ of the video payload for no game value. |
| Firmware blobs (`0x00010000.bin`) | Almost certainly 8051 firmware images with no readable strings; not decoded, not needed for sounds or protocol reproduction, and flashing is out of scope (brick risk). |
| DANI animation data as audio | DANI banks are LED/gauge animation programs, not audio (see [09-gun-audio.md](09-gun-audio.md) §7). |
| French announcer transcriptions | The French voice set ships but was not transcribed; transcribe only if bilingual support is wanted. |
| tkdrob dangling references | Announcer files and a gun code referenced by the mod but present in neither the mod nor the original install. They are bugs, not assets (see [13-community-mods.md](13-community-mods.md)). |
| External avatar pack (tkdrob Dropbox link) | Not in any repo or install; link likely dead; do not rely on it. |
| Any original asset file in these documents | These documents carry inventories, formats, and mappings only. No audio, image, screenshot, or capture file is embedded or attached. |

### Inventory conflict note (DANI size)

The preliminary inventory described the DANI `.bin` files as 0 KB
placeholders in the extract. The decoding report, working from the same
files, validates DANI banks of 407 bytes total with 13 entries, and the
master document agrees (407 bytes each). Resolution: trust the decoding
report's direct parse for DANI content; the inventory's 0 KB line is
treated as an extraction-listing artifact. This is the only
count-affecting conflict between the inventory and the decoding report;
the `.bin` total of 22 files is unaffected.

## Sources

- `ASSET_INVENTORY.md` (counts, locations, formats, keep/defer/ignore).
- `BATTLE_TAG_MASTER.md` §5.5 (textures, fonts, palette), §5.7 (music,
  UI sounds, banks), §6 (announcer catalog with Lua-grounded triggers),
  §9 (inventory summary).
- `GUN_BANK_DECODING.md` (bank inventory, variants, DANI, decoded output).
- `TGA_PALETTE.md` (measured palette, per-file measurements, visual
  character).
- `transcripts/ANNOUNCER_CATALOG.md` (file lists and durations only).
- `GUI_SCREENSHOTS.md` (texture/visual corroboration, video walkthrough).
