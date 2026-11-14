# 05 — Config and settings (0x92)

Game version 1.1.354. This document describes the per-gun configuration message
(command 0x92) sent after the bytecode upload: its framing, the field layout,
the volume and beam-power scales, the per-mode defaults, and the mode catalog's
player bounds.

The 0x92 message is where a match's settings reach the guns. The bytecode
upload carries no settings (see [04](04-bytecode-upload.md)); duration and
launch mode are host-side only and appear in neither the upload nor the
config. Team assignment is baked into the config — changing teams after config
requires returning to setup.

## Claim grades

- **CONFIRMED** — verified in USB captures and/or live replay on real hardware.
- **INFERRED** — original Lua source only, or a single observation.
- **UNVERIFIED** — open question; do not implement as fact.

Evidence is cited per claim. No Lua source is reproduced here; all logic is
paraphrased, with file/function citations for verification against the
installer. Byte offsets in frame tables are 0-based report offsets (byte 0 =
size byte).

Related docs: [bytecode upload](04-bytecode-upload.md) (the step before
config), [match loop](06-match-loop.md) (countdown and polling after config),
[mode behavior](07-mode-behavior.md) (what each mode does with these settings).

## 1. 0x92 framing

The config is **unicast**: one message per gun, addressed to that gun's id, sent
in turn after the upload lock is acknowledged by all guns. Construction (per
`PlayersSetup.lua`, config send site): a size byte, the gun id, command `0x92`,
a volume byte, a beam-power byte, a count byte `n`, then `n` 16-bit big-endian
values.

Layout: `[size][id][92][volume][beamPower][n][n × BE16 values]`, where
`size = 3 + 2n` (the volume, beam-power, and count bytes, plus two bytes per
value). Total meaningful length is `size + 3`.

**CONFIRMED** — capture 17 frames 217–231 (TeamFrag), capture 16 frames
115/119 (FreeFrag), capture 24 frames 121/125 (TagThenShoot), and the
one-setting experiments in captures 21/29/31/33/35/36/38/39/46/47 (see §7).
The volume and beam-power bytes are present in every 0x92 message, even when
`n = 0`.

Worked examples (meaningful bytes only; HID reports are zero-padded to 64
bytes — see [04](04-bytecode-upload.md) §7):

| Form | Bytes | Meaning |
|---|---|---|
| TeamFrag, gun id 02 | `15 02 92 00 03 09 00 05 00 09 00 05 00 00 00 00 00 01 00 00 00 01 00 00` | size `0x15` = 21, n = 9 (see §2). Capture 17 frames 217/219. Gun id 03 is identical except the team word (`0002`). |
| FreeFrag, gun id 02 | `03 02 92 00 03 00` | size 3, n = 0, no config values. Capture 16 frames 115/119. |
| TagThenShoot, gun id 02 | `07 02 92 00 03 02 00 04 00 01` | size 7, n = 2: numberOfBase = 4, assist = 1. Capture 24 frames 121/125. |

Note on wording: design sources say FreeFrag and Duel send "no gameplay
config" — that means no config *values* (`n = 0`), not that no 0x92 message is
sent. The 6-byte `n = 0` form above is the FreeFrag config message, and it is
still sent per gun. **CONFIRMED** for FreeFrag; the Duel form is **INFERRED**
(see §5).

## 2. Field map (TeamFrag 9-value order — CONFIRMED)

Offsets are 0-based within the meaningful message. The nine 16-bit values are
big-endian.

| Offset(s) | Field | TeamFrag example value | Evidence |
|---|---|---|---|
| 0 | Size byte (`3 + 2n`) | `0x15` (21) | Capture 17 |
| 1 | Gun id | `02` / `03` | Captures 16/17/24 |
| 2 | Command `0x92` | `92` | Captures 16/17/24 |
| 3 | Volume (gun audio) | `00` | All captures; semantics §3 |
| 4 | Beam power | `03` (default) | Captures 16/17; experiment 33 (§7) |
| 5 | Count `n` | `09` | Captures 17/24/16 |
| 6–7 | Health (lifepoints) | `0005` | Experiment 35: `0007` |
| 8–9 | Ammo per clip | `0009` | Experiment 20: byte 9 = `0C` (12); experiment 39: `0014` (20 = unlimited) |
| 10–11 | Clips | `0005` | Experiment 36: `0006` |
| 12–13 | Swap (alternate boxes) | `0000` | Experiment 39: `0001` |
| 14–15 | Respawn time | `0000` | Experiments 29/31: `0005` |
| 16–17 | Team | `0001` (gun 02) / `0002` (gun 03) | Experiment 46: `0001`/`0002`/`0003`/`0004` for ids 02/03/04/05 |
| 18–19 | Friendly fire (`teamFrag`) | `0000` | Experiment 38: `0001` |
| 20–21 | Assist (voice prompts) | `0001` | Experiment 38 (later upload): `0000` |
| 22–23 | Medkit | `0000` (pack off) / `0001` (pack on) | Experiment 21: `0001` (was `0000` in capture 20) |

The value order — health, ammo, clips, swap, respawn, team, friendly fire,
assist, medkit — is **CONFIRMED** byte-exact by the one-setting experiments:
in each experiment only the targeted word changes (settings accumulate across
experiments, so each diff is clean). Full-config confirmation: capture 47
frames 1163–1175 (health 7, ammo 6, clips 6, swap 1, respawn 5, friendly fire
1, assist 0, medkit 1, teams 1–4).

The medkit word reads `0000` with the Med-Kit Expansion Pack off and `0001`
with it on (captures 20 vs 21). Whether the word means "medkit scanning
enabled" or "expansion pack present" beyond that correlation is not separated
in the sources — treat the pack correlation as **CONFIRMED** and any finer
semantics as **INFERRED**.

## 3. Volume byte (offset 3)

Observed value: `00` in every captured 0x92 message (TeamFrag, FreeFrag, and
TagThenShoot forms). The byte's position as the volume/audio field is
**CONFIRMED** by the layout (Lua `PlayersSetup.lua` construction + the
Java reference's `configBlaster` framing, both corroborated by captures).

The value scale is **UNVERIFIED** on the wire — no one-setting volume
experiment exists in the sources, and the two scale descriptions in the
research files do not reconcile cleanly:

- The design research (global settings, `UTGame.lua` /
  `UTGame.Ui.Settings.Sound.lua`) records the T-Blasters volume control as
  level 0, with choices Off / Low / Medium / High carrying the values
  16 / 4 / 1 / 0 in that listed order, and labels for Low/Medium/High in
  `Main.loc`. Read literally, that listing pairs High with 0 — the value
  observed on the wire.
- The tkdrob-era class table (a later/community source, not 1.1.354) labels
  gun audio value 0 as "silent" (Sniper) and 16 as "quiet" (Stealth) — which
  points the other way.

Do not implement a volume mapping from either description alone. The only
wire-grounded facts are: offset 3 is the volume byte, and its observed value
is `00` under the default global volume setting. The mapping from UI volume
steps to byte values, and the meaning of `00` (loudest? silent?), is an open
question (see §8). The global T-Blasters volume default (level 0 in the
original's settings) is a host-side setting; do not confuse the UI level
number with the wire byte without the missing experiment.

## 4. Beam-power byte (offset 4)

Scale 1–5, default 3 in every mode. UI guidance (tips text): 1 = indoors / low
light, 3 = in a house, 5 = outside in sunny sun; icons run from a house to a
sun. The manual warns shooting performance may be affected in full sunshine
and by electromagnetic disturbance. **CONFIRMED** as a setting (design
research + Lua settings); the wire position and default are **CONFIRMED**
(capture 17 byte 4 = `03`; experiment 33 changes byte 4 `03` → `05` with
nothing else moving).

The exact IR behavior behind the 1–5 scale (pulse timing, power mapping) is
not in any source file. **UNVERIFIED** — open question. Do not invent an IR
model; send the configured 1–5 value and let the gun's bytecode interpret it.

## 5. Per-mode config shapes

Only three shapes are capture-confirmed. The rest are **INFERRED** from the
per-mode Lua `self.settings` / config-data summaries in the design research:
the value *sets* are sourced, but the *order* of values within the message and
the exact `n` are not capture-verified for those modes. The orders below for
unconfirmed modes follow the listing order in the design research and must be
verified against a capture before being treated as wire truth.

| Mode | n | Values (in message order) | Grade |
|---|---|---|---|
| FreeFrag | 0 | — (volume + beam bytes only) | **CONFIRMED** (capture 16) |
| TeamFrag | 9 | health, ammo, clips, swap, respawn, team, friendly fire, assist, medkit | **CONFIRMED** (§2) |
| TagThenShoot | 2 | numberOfBase, assist | **CONFIRMED** (capture 24) |
| FreeForAll | 7 (inferred) | lifepoints, ammo, clips, swap, respawnTime, assist, medkit | **INFERRED** — value set from design research; order and n unverified on the wire |
| LastManStanding | 6 (inferred) | lifepoints, ammo, clips, swap, assist, medkit (no respawnTime) | **INFERRED** — value set from design research; order and n unverified on the wire |
| StarterFrag | 3 (inferred) | assist, ammunitions, clips (no lifepoints) | **INFERRED** — value set from design research; order and n unverified on the wire |
| OldFashionDuel | 0 (inferred) | — (volume + beam bytes only, as FreeFrag) | **INFERRED** — design research says Duel sends no gameplay config; no Duel capture exists in the sources |

Notes:

- FreeForAll's config set includes `respawnTime`; LastManStanding's does not,
  even though a `respawnTime` value of 10 exists in the LastManStanding Lua
  file — death is permanent in that mode regardless (see
  [07](07-mode-behavior.md)).
- StarterFrag sends no lifepoints, so it has no frag/death mechanic; its
  config is the only one whose listed order starts with assist. That ordering
  is the least certain in this table — verify before implementing.
- TagThenShoot's `numberOfBase` default is `2 + 2 × medkitPack` (2 normally, 4
  with the pack). Capture 24 shows 4 with the pack on. **CONFIRMED**.
- The gun-side heap checklist in the master document (per-mode main Lua
  `entity.data.heap.*`: `lifePoints`, `ammunitions`, `clips`, `beamPower`,
  `audio`, `swap`, `respawnTime`, `medkit`, `respawnmode`, plus per-mode
  extras) is the Lua-side view of the same values. `respawnmode` appears in
  the heap list but its settings entry (`goption011`) is commented out of the
  options UI with value 0; its possible values and effect are unknown — do not
  resurrect it (see §8).

## 6. Per-mode defaults

Ranges are the choices the original UI offered; defaults are the values in the
per-mode Lua settings. "Host-side" settings never reach the gun (see §6.1).

| Setting | Range | FreeForAll | FreeFrag | LastManStanding | OldFashionDuel | StarterFrag | TagThenShoot | TeamFrag |
|---|---|---|---|---|---|---|---|---|
| Playtime (host-side) | 5/10/15/20 min | 5 | 5 (5/10 only) | — (no timer) | — (none) | fixed 5 | 1 | 5 |
| Game launch (host-side) | Auto / Manual | Auto | Auto | Auto | Auto | Auto | Manual | Auto |
| Beam power | 1–5 | 3 | 3 | 3 | 3 | 3 | 3 | 3 |
| Ammo per clip | 6/9/12/20 (20 = infinite) | 9 | — (infinite) | 9 | 6 per round (fixed, not a setting) | fixed 12 | — | 9 |
| Clips | 1–6 | 3 | — | 3 | — | 3 | — | 5 |
| Health (lifepoints) | 3/5/7/9 | 5 | — | 5 | — (one hit = dead) | — | — | 5 |
| Swap (alternate boxes) | Off / On | Off | — | Off | — | — | — | Off |
| Number of teams | 2/3/4 | — | — | — | — | — | — | 2 |
| Respawn time | 0/3/5/10 s | 0 | — | 10 (unused) | — | — | — | 0 |
| Friendly fire | Off / On | — | — | — | — | — | — | Off |
| Assistance | Off / On | On | — | On | — | On | On | On |
| Number of bases | 2/4 | — | — | — | — | — | 2 + 2×pack | — |
| Medkit word | 0/1 | pack-gated | — | pack-gated | — | — | (via pack → bases) | pack-gated |

**INFERRED** as a table (Lua settings + design research). Individual cells are
**CONFIRMED** where a capture shows the default: TeamFrag health 5 / ammo 9 /
clips 5 / swap 0 / respawn 0 / friendly fire 0 / assist 1 (capture 17),
beam 3 (captures 16/17/24), TagThenShoot bases 4 with pack on (capture 24).
The 0 s respawn default means instant respawn in the original's terms — but
see the respawn-timer open question in [06](06-match-loop.md): no automatic
return was observed in TeamFrag captures, so the interaction of the respawn
value with scan-gated respawn is unresolved.

Med-Kit Expansion Pack gating (original commercial behavior): 3–4 teams,
4 bases, and the medkit/ammo-pack options were conditional on the pack
(`medkitPack = 1`; default 0). Treating all of it as included is a
replacement-design decision, not original behavior — recorded here only so an
implementer does not mistake the gate for a protocol requirement. The wire
facts (team words 1–4, bases 2/4, medkit word 0/1) are unaffected by the gate.

Global settings (host-side, `UTGame.lua`): music volume level 5 of 0–8, sound
FX level 2 of 0–8, Game Master (announcer) volume level 5 of 0–8, T-Blasters
volume level 0 with Off/Low/Medium/High choices (see §3), language
English/French. None of these except the gun volume byte travels in 0x92.

### 6.1 What is NOT in the config

- **Duration and launch mode are host-side.** Captures 41 (duration change)
  and 42 (launch change) have configs and all 20 bytecode chunks byte-identical
  to capture 39. Duration and launch appear nowhere in 0x92 or 0x83.
  **CONFIRMED** (negative). The host owns the match timer and the
  countdown/launch behavior (see [06](06-match-loop.md)).
- **No profile data.** Name/icon live in the profile flash block (address 0),
  written by the separate unicast profile sequence — never in 0x92.
- **No bytecode.** Mode code travels only in the upload ([04](04-bytecode-upload.md)).

## 7. Mode catalog: player bounds

From `UTActivity.lua` (`maxNumberOfPlayer = 8`, `minNumberOfPlayer = 2`) and
the per-mode definitions. **INFERRED** (Lua source); 2-gun and 4-gun play is
**CONFIRMED** on the wire (captures 16/17/46/47), but the bounds themselves
were not probed at the limits (no 1-player, 8-player, or 0-player capture is
in the sources).

| Mode | Players | Teams |
|---|---|---|
| FreeForAll | 2–8 | None |
| FreeFrag | 2–8 | None |
| LastManStanding | 2–8 | None |
| OldFashionDuel | Exactly 2 | None |
| StarterFrag | 2–8 | None |
| TagThenShoot | 1–8 (the only solo mode) | None |
| TeamFrag | 2–8 | 2–4 (default 2) |
| Introduction (tutorial, not a game mode) | Max 4 | None (0 teams in Lua) |

The 8-player cap is per activity in this build. Wider claims (40 players via
multiple UbiConnects, 16 players in later builds) are not evidenced in the
1.1.354 files and are out of scope for this document. The Introduction's
"max 4 players, no game settings" is Lua-only; no Introduction wire capture
exists in the sources.

## 8. Config-step discipline and open questions

Discipline (from the upload spec's boundary section): config must not start
until the upload has fully succeeded, and a config failure must fail loudly —
no silent partial config, no countdown on an unconfigured gun. Team is baked
into the config; changing teams after config requires returning to setup. The
original's own config retry/acknowledgement behavior is not characterized in
the sources beyond the per-gun unicast sends observed in captures — a
replacement must define its own config-failure handling rather than copying an
undocumented original behavior. **UNVERIFIED** detail (original's config
retry discipline).

Open questions (flagged for collection into the open-questions doc):

- **Volume byte mapping.** UI steps Off/Low/Medium/High vs wire values, and
  the meaning of observed `00`. The two scale descriptions in the sources
  conflict (§3). Needs a one-setting volume experiment: vary the global
  T-Blasters volume and capture the 0x92 byte 3, plus a listening check.
- **Non-TeamFrag value order and `n`.** FreeForAll, LastManStanding, and
  StarterFrag value orders and counts are Lua-listing inferences (§5). Needs
  one setup capture per mode.
- **Duel config form.** No Duel capture exists. The `n = 0` form is inferred
  from "no gameplay config." Needs a Duel setup capture.
- **Respawn-mode setting (`goption011`).** Present in settings data (value 0),
  commented out of the options UI. Possible values and effect unknown.
- **Exact beam-power IR behavior.** The 1–5 scale is documented; the IR
  pulse/power mapping is not in any file.
- **Medkit word semantics.** Pack correlation is confirmed; whether the word
  independently enables medkit scanning (vs merely reflecting pack presence)
  is not separated in the sources.
- **TagThenShoot bases without the pack.** Capture 24 shows 4 (pack on); the
  2-base form (pack off) follows from the `2 + 2 × medkitPack` formula but was
  not captured. **INFERRED**.

## Sources

- `sniffing-results/protocol_map.json` — `GAME_START_CONFIG` entry (full
  24-byte TeamFrag frame, field layout, one-setting experiment summary),
  `0x92` framing. Byte-level authority; followed wherever any summary
  differed.
- `REVIEW_2026-11-11.md` §a2 (one-setting experiments, frame citations),
  §b-10/11 (config layout, TagThenShoot 2-value form), §c (Java `configBlaster`
  framing confirmed; Java's own default values are the Java author's choices,
  NOT the original game's — do not use them).
- `GAME_DESIGN_RESEARCH.md` §1 (per-mode settings), §2 (settings catalogue,
  global settings, config-value sets per mode), §7 Q6/Q8 (beam power,
  respawn mode).
- `BATTLE_TAG_MASTER.md` §5.1 (mode catalogue), §5.2 (settings catalogue),
  §4.3d (gun-side heap checklist), §7.3 (tkdrob class audio values — cited
  only for the §3 conflict note; tkdrob is not 1.1.354 authority).
- `ANTIGRAVITY_UPLOAD_SPEC.md` §9 (config boundary: unicast, after upload,
  team baked in, loud failure).
