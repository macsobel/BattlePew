# 07 — Mode behavior

Game version 1.1.354. This document summarizes how each of the 7 original
modes (+ the Introduction tutorial) behaves: win conditions, scoring, timers,
and quirks. The behavior descriptions are paraphrases of the original Lua
source shipped in plaintext in the installer, with file/function citations so
a reader with the installer can verify. No Lua source is reproduced here.

Wire behavior (upload, config, polling) is covered in
[04](04-bytecode-upload.md), [05](05-config-and-settings.md), and
[06](06-match-loop.md). Read those for frame formats; this document covers
what the modes *do*.

## Claim grades

- **CONFIRMED** — verified in USB captures and/or live replay on real hardware.
- **INFERRED** — original Lua source only, or a single observation.
- **UNVERIFIED** — open question; do not implement as fact.

Grading honesty for this document: only three modes have capture evidence in
the sources — FreeFrag, TeamFrag, and TagThenShoot. Everything about
FreeForAll, LastManStanding, OldFashionDuel, StarterFrag, and Introduction
behavior is **INFERRED** from Lua alone unless a specific wire fact is cited.
Each mode section states its grade.

## Payload bytes: pointer, not duplication

The per-mode gun-code payloads (TOC + bytecode + trailer, chunked for upload)
are **not** reproduced in this document. The verified payloads — capture bytes
for all 7 modes, including the two modes where the installer tables diverge
from the captures — live in the replacement repo's data file:

- `battletag/data/mode_sequences.json`, `uploads` key (per mode: `unlock`,
  `erase`, `prepare`, `lock`, `chunks[]`).

See [04](04-bytecode-upload.md) §5.4 for the per-mode total sizes, chunk
counts, and the Lua-vs-capture divergence table. Do not rebuild payloads from
the installer bytecode tables without checking them against that file — for
TeamFrag and TagThenShoot the tables are wrong (see §10).

## 1. Shared rules

### 1.1 Scoring (frag modes)

From the per-mode round-loop Lua (`*.State.RoundLoop.lua`): **a hit scores
+1 point; a frag (victim reaching 0 lifepoints) scores +3 points.** The
kill-feed text (`Main.loc`, score entry) states the same rule in words: one
hit, one point; 3 points for a frag. **INFERRED** (Lua); the TeamFrag
derivation (1 point per hit, 3 on life reaching 0, attributed to the most
recent shooter) is **CONFIRMED** by the capture-derived shift-register
reading in [06](06-match-loop.md) §4.

Accuracy: in frag modes, accuracy = 100 × (hits + deaths caused) / shots
fired; FreeFrag uses 100 × hits / shots fired. **INFERRED** (Lua formulas as
summarized in the design research). Stats tracked per player: score, ranking,
accuracy, shots fired, hits, deaths, respawns, ammo packs used, medkits used,
and per-opponent hit/frag attribution (who-hit-whom names). **INFERRED.**

### 1.2 Timers and flow

- Playtime is host-side only (see [05](05-config-and-settings.md) §6.1). The
  host announces time warnings: 1 minute left, 30 seconds left, 15 seconds
  left, plus random hurry-up filler at 57 s, 27 s, and 12 s before the end
  (priority/probability-gated announcer scheduling). Which modes register
  which warnings varies: the 60 s warning is registered by FreeForAll,
  FreeFrag, StarterFrag, and TeamFrag; the 30 s / 15 s warnings add
  TagThenShoot. LastManStanding and Duel have no playtime setting and no
  timer warnings. **INFERRED** (Lua announcer registration).
- Launch: Auto = 10 s countdown; Manual = players press the trigger when
  ready, then a 5 s countdown. TagThenShoot defaults to Manual; all other
  modes default to Auto. See [06](06-match-loop.md) §5/§8 for the wire
  behavior. **CONFIRMED** for the countdown values (captures 16/17/25/42);
  the Manual ready semantics are **INFERRED** (Lua + tips text).
- End of match: timer expiry (timed modes), one player remaining
  (LastManStanding), or 5 rounds complete (Duel). Game-over frames and
  colours: see [06](06-match-loop.md) §6.

### 1.3 Gun-side vocabulary (shared)

All modes' gun bytecode shares an event/state vocabulary (from the
`*.ByteCode.txt` files): trigger press, scan-button press, dashboard-swap
button, hit received, timer expiry, animation finished, data change, and
state entry; states include initialization, game, reload, medkit, respawn,
death, duel, wait, tag, and shoot. The gun plays sounds, flashes LEDs, and
vibrates from its own bytecode — no host command drives lights or rumble
during play ([06](06-match-loop.md) §9.2). **INFERRED** (bytecode sources);
the no-host-command conclusion is **CONFIRMED** by absence across 45 captures.

### 1.4 Settings

Setting ranges and per-mode defaults (playtime, launch, beam power, ammo,
clips, health, swap, teams, respawn, friendly fire, assistance, bases) are
tabulated in [05](05-config-and-settings.md) §6 and are not repeated here
except where a mode's behavior turns on them. Player bounds (min/max/teams)
are in [05](05-config-and-settings.md) §7.

## 2. Free For All (FreeForAll) — flagship deathmatch

**Players:** 2–8, no teams. **Grade: INFERRED** (Lua only; no FreeForAll
capture exists in the sources — including no verified status layout; do not
assume the TeamFrag 0x0E layout for this mode).

- **Objective / win:** most points when the timer ends. Hit +1, frag +3 (§1.1).
- **Health / ammo:** lifepoints 3/5/7/9 (default 5); ammo per clip 6/9/12/20
  (default 9; 20 = infinite, shown with the infinity icon); clips 1–6
  (default 3); respawn time 0/3/5/10 s (default 0).
- **Hit behavior (gun bytecode):** a hit decrements lifepoints by 1; the gun
  plays its hurt cue, vibrates, and flashes red. At 0 lifepoints the gun
  enters its respawn state (death cue, red flash), then returns after the
  respawn time (respawn / back-in-game cues). Paraphrased from
  `FreeForAll ByteCode` behavior as summarized in the design research.
- **Respawn, three ways** (manual glossary + `Basics.loc` text): shoot the
  UbiConnect, tag a T-Base, or wait the set respawn time. The "shoot the
  UbiConnect" route presumes the 0xC3 mechanism, which is **UNVERIFIED** on
  real hardware (see [06](06-match-loop.md) §10.1) — do not treat it as
  wire-confirmed. The timed route has no wire confirmation either; TeamFrag
  captures show no automatic return (see §8 and [06](06-match-loop.md) §7.3).
  The three-routes description is Lua/manual text only.
- **Reload / heal:** scan an RFID Ammo Pack → reload state (reload cue, green
  flash, reload animation), consumes one clip, refills ammo. Scan a Med-Kit →
  good-scan cue and medkit animation, restores maximum lifepoints.
- **Swap mode:** when on, scanning the same T-Box twice in a row is rejected
  (bad-scan cue) — the player must alternate boxes. See
  [06](06-match-loop.md) §7.4 for the wire-visible form (accepted scans only).
- **Assist voice prompts (gun):** a scan-ammo prompt after 5+ empty clicks; a
  scan-life prompt at 1 lifepoint when the medic option is on (repeated on a
  tick interval). **INFERRED** (bytecode logic as summarized).
- **Gun dashboard:** the swap button toggles ammo view (bullet icon, digit =
  ammo, gauge = clips) vs health view (life icon, digit = lifepoints).
  Special case: maximum ammo of 20 (infinite) shows the digit off with a
  blinking gauge. **INFERRED.**
- **Gun sound set:** back-in-game, scan-ammo, scan-life, bip, dead, empty,
  hurt, ok, reload, reload-clip, respawn, scan-bad, scan-good, shoot, start
  (SND names per the mode's bytecode file).
- **Config shape:** 7 values (lifepoints, ammo, clips, swap, respawnTime,
  assist, medkit) — **INFERRED**, order unverified (see
  [05](05-config-and-settings.md) §5). Upload: 877 bytes, 16 chunks; installer
  table matches the capture ([04](04-bytecode-upload.md) §5.4).

## 3. Free Frag (FreeFrag) — casual warm-up

**Players:** 2–8. **Grade: wire behavior CONFIRMED** (captures 04/05/08/09/
10/16 + live replay 2026-11-11); scoring/behavior **INFERRED** (Lua) with
capture-consistent counters.

- **Objective / win:** most hits when the timer ends. Hit +1; **no frag
  concept** (no lifepoints). Winner = most hits *landed* (opponents'
  hits-received deltas), verified live — see [06](06-match-loop.md) §6.3.
- **Health / ammo:** infinite everything. No settings are sent to the guns
  (config `n = 0`). Playtime 5/10 min only (default 5).
- **Behavior:** no reload, no death, no missions — shoot and count hits. The
  minimal gun sound set reflects that: hurt, shoot, start only.
- **Wire notes:** status layout 0x0A (hits received, shots fired, hit-id
  register at bytes 8–12); config `03 <id> 92 00 03 00`; countdown 10 s
  (Auto); game-over SD16; upload 120 bytes, 3 chunks; installer table matches
  the capture. The full loop (pair → upload → config → countdown → poll →
  game-over, correct winner) ran live with two guns. **CONFIRMED.**
- **Quirk:** accuracy uses hits/shots (no deaths term), unlike the frag
  modes (§1.1). **INFERRED** (Lua formula).

## 4. Last Man Standing (LastManStanding) — elimination

**Players:** 2–8. **Grade: INFERRED** (Lua only; no capture exists in the
sources, including no verified status layout).

- **Objective / win:** be the last player alive. **No timer** — the playtime
  setting is absent (timer zero/commented out in the mode Lua); the match ends
  when 1 player remains (round-loop end condition: players alive ≤ 1).
- **Health / ammo:** lifepoints 3/5/7/9 (default 5); ammo 6/9/12/20 (default
  9); clips 1–6 (default 3). **No respawn — death is permanent:** at 0
  lifepoints the gun enters its death state (death cue, red flash, looping
  out-animation). A `respawnTime` value of 10 exists in the mode file but is
  not used — death is permanent regardless. Do not implement a respawn for
  this mode.
- **Scoring (unusual — keep exactly):** while alive, score = lifepoints +
  number of players; on elimination, score = player count − players alive.
  Surviving with more health beats merely surviving longer. Paraphrased from
  `UALastManStanding.State.RoundLoop.lua` scoring. **INFERRED.**
- **Announcer:** "X players still in game" lines on each elimination (index
  computed from players alive), plus ambience lines after an elimination
  (offset 4 s, probability-gated). **INFERRED.**
- **Config shape:** 6 values (lifepoints, ammo, clips, swap, assist, medkit;
  no respawnTime) — **INFERRED**, order unverified. Upload: 801 bytes, 14
  chunks; installer table matches the capture.
- **Open:** whether eliminated players' guns stop registering hits entirely,
  or only stop scoring, is not separated in the sources. **UNVERIFIED.**

## 5. Wild West Duel (OldFashionDuel) — 1v1 quick-draw

**Players:** exactly 2. **Grade: INFERRED** (Lua only; no Duel capture exists
in the sources — no verified config form, status layout, or game-over frame).

- **Objective / win:** win the most of **5 rounds** (round count fixed in the
  mode Lua). +1 point per round won.
- **Health / ammo:** 6 ammo per round; **one hit = dead** (a hit drops the
  victim to 0 in the duel bytecode). No lifepoints, no clips, no respawn, no
  playtime setting.
- **Round flow** (paraphrased from
  `UAOldFashionDuel.State.RoundLoop.lua`): each round plays a random intro
  line, then moves to the waiting phase; waits a **random 5–8 s** (base 5 s
  plus a random 0–3 s, in microsecond timer units); then plays a random "Go"
  line, which sets the can-shoot flag. **Shooting before "Go" loses the
  point** — the opponent scores. If both players hit, or both run out of
  ammo, the round is a draw. The round winner is announced with a computed
  player-number callout followed by a "wins the round" line. Duel intro lines
  play at the players-setup stage.
- **Gun phase variable:** the gun receives a phase value (wait / duel / end)
  via the downlink mechanism. The exact poll-downlink encoding for the Duel
  phase was not captured — the TagThenShoot downlink form (§7) is the only
  capture-verified downlink. **UNVERIFIED** for Duel; do not assume the
  TagThenShoot encoding carries over.
- **Gun sound set:** empty, hurt, reload, shoot. No start/death/assist cues
  in this mode's set.
- **Config shape:** no gameplay config (`n = 0` form, as FreeFrag) —
  **INFERRED**. Game-over sting SD07 (override, as TagThenShoot) —
  **INFERRED**. Upload: 225 bytes, 4 chunks; installer table matches the
  capture.
- **Quirks to preserve:** the random delay *is* the game; the early-shot
  penalty; the draw rule. The 5–8 s wait, the 5-round count, and the 6-ammo
  magazine are all fixed values in the mode Lua, not settings.

## 6. Starter Frag (StarterFrag) — beginner frag mode

**Players:** 2–8. **Grade: INFERRED** (Lua only; no capture exists in the
sources, including no verified status layout). Scoring detail **UNVERIFIED**
(see below).

- **Objective / win:** most hits when the timer ends. Hit-based scoring; the
  gun config sends assist/ammo/clips only — **no lifepoints, so no frag/death
  mechanic** (unlike Free For All).
- **Health / ammo:** playtime **fixed 5 min**; ammo per clip **fixed 12**;
  clips 1–6 (default 3). No lifePoints setting, no respawn setting.
- **Scoring open question:** whether a hit is +1 and whether any bonus exists
  needs one read of the mode's round-loop Lua — the design research flags it
  explicitly as unverified (open question Q7), and no source since has closed
  it. **UNVERIFIED.** Do not assert StarterFrag scoring beyond "hit-based,
  most hits wins" until the round-loop read is done. (Flagged for collection
  into the open-questions doc.)
- **Gun sound set:** scan-ammo assist, empty, hurt, ok, reload, reload-clip,
  scan-bad, shoot, start.
- **Config shape:** 3 values (assist, ammunitions, clips) — **INFERRED**,
  order unverified (and the assist-first listing order is the least certain
  in [05](05-config-and-settings.md) §5). Upload: 409 bytes, 8 chunks;
  installer table matches the capture.

## 7. Tag'n Shoot (TagThenShoot) — solo / objective mode

**Players:** 1–8 — the only mode that works solo. **Grade: wire behavior
CONFIRMED** (captures 24–28); behavior/rules **INFERRED** (Lua) consistent
with the captures.

- **Objective / win:** tag T-Bases in order, then shoot the UbiConnect;
  **+1 point per T-Base tag and +1 per UbiConnect hit.** (Score text in
  `Main.loc`: shooting the UbiConnect or tagging a T-Base earns 1 point.)
- **Timers / settings:** playtime **1 min**; **launch defaults to Manual**
  (the only mode where it does); number of bases 2/4 (default
  `2 + 2 × medkitPack` — 2 normally, 4 with the pack). No lifepoints/ammo/
  clips settings.
- **Flow (gun bytecode + round loop):** the gun tracks a stage counter and an
  assist flag from config; the player scans RFID base packs in order, then
  shoots the UbiConnect. Each tag plays a per-base announcer line plus a
  random follow-up (offset 0.5 s); each UbiConnect hit plays one of three
  hit lines. **INFERRED** (Lua/announcer registration); the scan/hit
  *detection* is **CONFIRMED** on the wire (§7.1).
- **Gun sound set:** base-1–4 assists, UbiConnect assist, scan-bad,
  scan-good, shoot, start.
- **Config shape:** 2 values (numberOfBase, assist) — **CONFIRMED** (capture
  24). Countdown 3 s; game-over SD07; poll downlink carries the hit count —
  all **CONFIRMED** (captures 24–28). Upload: 439 bytes, 8 chunks;
  installer table does NOT match the capture (390 bytes) — use capture bytes
  (see §10).

### 7.1 Wire behavior (summary — full detail in [06](06-match-loop.md) §10)

Status byte 5 = hits scored; byte 7 = phase flag (1 = scan phase, 0 = shoot
phase). Accepted base scans flip byte 7 to 0; accepted UbiConnect hits raise
byte 5 and return byte 7 to 1. Byte 5 resets after the 4th hit completes the
round. Wrong-base attempts, misses, and pre-countdown triggers change nothing.
The byte-7 transition does not identify which base was scanned.

### 7.2 The 0xC3 caveat (applies here most directly)

The round-loop Lua handles command 0xC3 as the UbiConnect hit, but **no 0xC3
frame appears in any capture in the sources, and the protocol map has no 0xC3
entry**. The 0xC3 tower-hit path is **UNVERIFIED** on real hardware. The
capture-verified hit signal is the status transition + poll-downlink count
(§7.1). Do not implement TagThenShoot hit detection as "wait for 0xC3." See
[06](06-match-loop.md) §10.1.

## 8. Team Frag (TeamFrag) — team deathmatch

**Players:** 2–8, 2–4 teams. **Grade: wire behavior CONFIRMED** (captures
13/15/17/19/20/21/29/31/33/35/36/38/39/40/41/42/46/47); team-scoring rules
**INFERRED** (Lua) consistent with the captures.

- **Objective / win:** the team with the most points when the timer ends.
  Hit +1, frag +3 — **only when shooter and victim are on different teams**
  (round-loop team comparison). Friendly fire (the `teamFrag` setting)
  controls whether same-team hits count; default **off**. When friendly fire
  is off, same-team hits still decrement the victim's lifepoints in the gun
  bytecode but score nothing — the scoring filter is host-side, the damage is
  gun-side. **INFERRED** (Lua + bytecode summary); the capture evidence
  (4-team game, friendly fire on, capture 47) is consistent but does not
  isolate the friendly-fire-off scoring filter on the wire.
- **Health / ammo:** lifepoints 3/5/7/9 (default 5); ammo 6/9/12/20 (default
  9); clips 1–6 (default **5** — the only mode with 5); respawn 0/3/5/10 s
  (default 0).
- **Respawn:** death → respawn requires scanning your own team's T-Base
  (base-pack minor value matching the gun's configured team), with gun
  prompts for bases 1–4; the team value in config programs the gun's team.
  The scan→restore mechanism is **CONFIRMED** ([06](06-match-loop.md) §7.3);
  the "own team only" restriction is **INFERRED** (no wrong-team-base capture
  for TeamFrag is in the sources); what the respawn-time value times is
  **UNVERIFIED**. No automatic return was observed in any capture.
- **Teams:** number of teams 2/3/4 (default 2); 3–4 teams were conditional on
  the Med-Kit Expansion Pack in the original (see [05](05-config-and-settings.md)
  §6). Team colours: Red, Blue, Yellow, Green. Team words 1–4 in config are
  **CONFIRMED** (capture 46: ids 02/03/04/05 → teams 1/2/3/4).
- **Gun sound set:** the full Free For All set plus base-1–4 assists.
- **Wire notes:** status layout 0x0E; 9-value config (§2 of
  [05](05-config-and-settings.md)); countdown 10 s Auto / 5 s Manual; game-over
  SD16 with win/lose colours; upload 1,152 bytes, 20 chunks; installer table
  does NOT match the capture (1,110 bytes) — use capture bytes (see §10).
- **Quirks:** clips default 5 (vs 3 elsewhere) — keep it. The clips status
  byte starts at configured clips − 1 in the captures; see
  [06](06-match-loop.md) §3.2 for the off-by-one note.

## 9. Introduction — the tutorial (not a game mode)

**Players:** max 4, no game settings (0 teams in Lua). Category: closed.
**Grade: INFERRED** (Lua + locale text only; no Introduction capture exists in
the sources, and no Introduction wire behavior — upload, config, status — is
characterized anywhere in them).

- **Stages** (tutorial sequence Lua + bytecode stage names): Batteries →
  Pairing → Ready → Stage 1 "Shoot the UbiConnect" → Stage 2 "Put on your
  sensor vest" → Stage 3 "Connect the T-Blasters to the sensor vests" →
  Stage 4 "Shoot your buddies" → Stage 5 "Here are the T-Boxes" → Stage 6
  "How to use the T-Boxes" → Quit / "You're ready to play."
- **Script text** (`Main.loc` tutorial entries): put on vests, connect guns,
  insert 4× AA batteries, pair, shoot the UbiConnect. One entry notes the
  introduction was designed for the two guns in the box — the second gun
  should be switched on even when playing alone.
- **Announcer:** the `INIT` voice group (22 files on disk) exists but **no
  Lua file references those files by name** — the trigger is unknown
  (possibly the engine or computed names). The transcripts match the tutorial
  script unambiguously, so the grouping is effectively resolved by content;
  the triggering mechanism is **UNVERIFIED.**
- **0xC3 caveat:** Stage 1 ("Shoot the UbiConnect") presumes the same
  UbiConnect-hit mechanism as TagThenShoot — **UNVERIFIED** on real hardware
  (see [06](06-match-loop.md) §10.1). No Introduction upload/config/poll
  behavior is documented in the sources; do not assume it reuses a game
  mode's wire sequence.
- **No payload entry:** the Introduction has no entry in the per-mode upload
  table ([04](04-bytecode-upload.md) §5.4), and no Introduction bytecode size
  is stated in the sources. **UNVERIFIED** whether the tutorial uploads gun
  code at all, and if so, which bytes. (Flagged for collection into the
  open-questions doc.)

## 10. Capture-vs-Lua divergences (capture wins)

Where the installer Lua (tables or logic) and the USB captures disagree, the
captures describe what the guns actually received and did. The divergences
found in the sources:

1. **TeamFrag bytecode size.** Installer table: 1,110 bytes. Capture 17:
   1,152 bytes (20 chunks, 19×58 + 50). Content diverges at byte 164 (per the
   upload-spec comparison). **Use the capture bytes** in
   `battletag/data/mode_sequences.json`. The nature of the byte-level
   difference beyond size/divergence point is not characterized in the
   sources. **CONFIRMED** divergence.
2. **TagThenShoot bytecode size.** Installer table: 390 bytes. Capture 24:
   439 bytes (8 chunks). **Use the capture bytes.** No divergence point is
   stated in the sources. **CONFIRMED** divergence.
3. **Hit-id model (analysis supersession, not a Lua conflict).** The Nov 10
   analysis concluded hit ids were written at bytes 14–16 only (Models A/B)
   and that "byte 12" was unconfirmed. Session-2 captures proved the 5-slot
   shift register at bytes 12–16 / 8–12. The Nov 10 conclusion is superseded;
   the shift-register model is **CONFIRMED** ([06](06-match-loop.md) §4).
4. **TeamFrag total-byte miscounts (documentation errors, now corrected).**
   "About 1,097 bytes" (early findings) and "1,155 bytes" (Nov 10 merge, which
   conflated the last chunk's message size 53 with its data length 50) are
   both wrong. Correct: **1,152 data bytes.** **CONFIRMED** (re-verified
   arithmetic).
5. **Health 5-vs-4 (resolved, NOT a divergence).** Capture 15's target starts
   at 4 hit points while the config says health 5. Resolution: capture 14
   started mid-game; the 4 is damage taken before capture 15 began, not a
   different setting. Recorded here so the resolved question is not
   re-opened. **CONFIRMED** resolution.
6. **Respawn behavior tension (unresolved).** FreeForAll Lua/manual text
   describes timed respawn among three routes; TeamFrag captures show
   scan-gated respawn with no automatic return and an unproven timer role.
   Different modes, different evidence — do not merge. **UNVERIFIED**
   resolution; see [06](06-match-loop.md) §7.3.
7. **Java reference defaults ≠ original defaults.** The Efeion Java project's
   TeamFrag defaults (e.g. ammo 8, clips 3, assist 0) are the Java author's
   choices, contradicted by the original's defaults (ammo 9, clips 5, assist
   1) and captures. This is a third-party-code divergence, not a Lua
   divergence — recorded because implementers may read the Java code. Use
   [05](05-config-and-settings.md) §6. **CONFIRMED** difference.
8. **tkdrob timer ≠ original timer.** The community mod's player-count-scaled
   upload interval (53 ms at 2 players) is not original behavior; the original
   uses a fixed 250 ms floor. See [04](04-bytecode-upload.md) §10, trap 1.
   **CONFIRMED** difference. Relatedly: the later/community modes (Wolf,
   Capture, King of the Hill, Infection, Disarm, etc.) and systems (classes,
   handicaps) documented in tkdrob-era sources are **not** part of the 1.1.354
   build and are out of scope for this document — do not treat them as
   original 1.1.354 modes.

## 11. Open questions

- **StarterFrag exact scoring** (hit value, any bonus): needs a read of the
  mode's round-loop Lua. **UNVERIFIED** (design-research Q7, still open).
- **Wire behavior for FreeForAll, LastManStanding, OldFashionDuel,
  StarterFrag, Introduction**: no setup/match captures exist in the sources
  (config order, status layout, countdown/game-over forms all unverified
  except where §2/§4–§6 state a Lua inference). **UNVERIFIED.**
- **Duel phase downlink encoding**: Lua describes a phase variable; no capture
  shows its wire form. **UNVERIFIED.**
- **0xC3 UbiConnect-hit path**: Lua-only, no capture evidence (TagThenShoot,
  Introduction Stage 1, FreeForAll respawn route). **UNVERIFIED** — see
  [06](06-match-loop.md) §10.1.
- **Introduction upload**: whether the tutorial uploads gun code, and which
  bytes. **UNVERIFIED.**
- **LastManStanding elimination semantics**: whether eliminated guns stop
  registering hits entirely vs only stop scoring. **UNVERIFIED.**
- **Respawn timer semantics + own-team-base restriction**: see
  [06](06-match-loop.md) §7.3/§11. **UNVERIFIED.**
- **Tie colour rule / FreeFrag both-green observation**: see
  [06](06-match-loop.md) §6.3. **UNVERIFIED.**
- **TeamFrag divergence detail** (how table bytes differ from capture bytes
  beyond size/point): **UNVERIFIED** detail; capture bytes authoritative
  regardless.

## Sources

- `GAME_DESIGN_RESEARCH.md` §1 (mode catalogue — behavior, settings, flows,
  sound sets), §2 (settings catalogue), §3 (game flow), §5.3 (announcer
  triggers), §7 Q7 (StarterFrag scoring open question). Primary behavior
  source; all mode sections paraphrase it and the Lua files it cites.
- `BATTLE_TAG_MASTER.md` §5.1 (mode catalogue, condensed), §5.2–§5.4
  (settings, flow, announcer engine), §6 (transcript catalog — announcer
  line triggers), §7.0 (1.2.439/tkdrob provenance caveat — later modes are
  not 1.1.354), §10 Q7/Q14 (open questions).
- `ANTIGRAVITY_UPLOAD_SPEC.md` §5 (per-mode sizes, Lua-vs-capture
  divergences, capture-bytes rule), §10 trap 1 (tkdrob timer).
- `sniffing-results/protocol_map.json` (TeamFrag/TagThenShoot/FreeFrag wire
  facts; game-over SD codes; config forms).
- `REVIEW_2026-11-11.md` §a2/a5–a9 (config experiments, shift register,
  TagThenShoot decode, game-over colours), §d (corrections: 1,152 bytes,
  hit-id supersession), §c (Java defaults difference).
- `STATUS_DIFF_RESULTS.md` (FreeFrag/TeamFrag counter behavior; health 5-vs-4
  resolution).
- Original Lua (installer, plaintext), cited per mode: `UA<Mode>.lua`
  (settings), `UA<Mode>.State.RoundLoop.lua` (scoring, timers, announcer),
  `UTActivity.State.BeginMatch.lua` (countdown), `UTActivity.lua` (player
  bounds), `UTGameMaster.lua` (game-over sting), mode `*.ByteCode.txt` (gun
  behavior, sound sets). Paraphrased throughout; no source reproduced.
