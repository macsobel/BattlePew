# 10 — Host Software Behavior (Original Game, Lua-Derived)

This document paraphrases how the original Windows host software behaves,
as derived from the game's own Lua source (shipped in plaintext in the
installer) and corroborated where stated by USB captures and live replay.
All logic below is described in the author's own words; no Lua source is
reproduced. Citations give the original file name and line or function so
a reader with the installer can verify each claim. The baseline version
is **1.1.354**, confirmed as the version on the sniffing machine. Where
behaviour is known only from the later 1.2.439-era video walkthrough, it
is flagged as such and must not be read as 1.1.354 behaviour.

Grading used in this document: **CONFIRMED** means verified in USB captures
and/or live replay; **INFERRED** means Lua source only, or a single
observation; **UNVERIFIED** means an open question. Most claims here are
INFERRED (Lua-only) by construction; capture confirmation is noted where
it exists. Wire formats themselves are covered by the protocol documents
in this directory; this document covers what the host *does* with them.

The original 1.1.354 game has no pause feature.

## 1. State machines

### 1.1 Application level (`UTGame.lua`, `UTGame` states)

The host application moves through these states in order: Connection,
Connected, Revision, Localization, Loading, Session, Selector, Title,
Settings, Video, Ignition. The Revision state is a device firmware/update
check. The wire-level handshake that underpins Connection/Connected —
the init poll, the init report, the connection reply, and the revision
exchange — is CONFIRMED against capture 01 (see the protocol documents).

### 1.2 Match level (`UTActivity.lua`, `UTGame.lua`)

A match moves through: begin, title, settings, playground,
playersmanagement, playerssetup, bytecode, matchmaking (beginmatch /
endmatch), beginround, roundloop, endround, intermediaterankings,
finalrankings, end. Section 2 walks each stage. The two rankings stages
are distinct: intermediate rankings appear between rounds or phases where
a mode has them, and final rankings close the match.

## 2. UI flows

### 2.1 Title and settings

The main menu offers Play, Games, Options, Tutorial, Basics, and Quit
(menu labels from `Main.loc`). Games leads to mode selection and then to
that mode's settings (see §5 for the settings the host actually sends to
guns). Options covers sound volumes, language, gun-sound customisation
per action, and player profiles; global volumes are music level 5 of 0–8,
sound-effects level 2 of 0–8, Game Master (announcer) level 5 of 0–8, and
T-Blaster volume level 0 with Off / Low / Medium / High choices
(`UTGame.lua`; `UTGame.Ui.Settings.Sound.lua`). Settings are saved to
`settings/user.settings` as a Lua table in the original.

The full-UI video walkthrough (version 1.2.439, see §2.7) corroborates a
single window template across screens: a white rounded window, an orange
header bar with the screen title in orange capitals, a grey/orange striped
tab at the header's right end, orange pill buttons (Back at bottom-left,
Next/OK/Accept at bottom-right), and a one-line grey hint at the very
bottom of every screen. Unselected values are grey pills; the selected
value is a solid orange pill. That chrome description is video evidence
from a later build, graded INFERRED for 1.1.354.

### 2.2 AddNewDevice / pairing flow

Pairing is driven from the "Add New T-Blasters" settings screen. The Lua
pairing module (`UTGame.Ui.Settings.AddNewDevice.Pairing.lua:43,60,82`)
sends a pair-start message, then a pair-stop message, after which the
proxy acknowledges connection requests. The capture-confirmed order is:
pair-start, pair-stop, then the two acknowledgement forms (capture 02
frames 159/161/163/165/169; the same pattern in capture 03). Pairing
itself is engine-level; the Lua only scripts the start/stop and the
acknowledgement handling.

Connection handling (`ULUsbProxy.lua`, `ProcessMessage0x12`) works as
follows, paraphrased:

- A connecting gun presents a 16-byte argument, understood as two 8-byte
  device references. The field layout of those 16 bytes beyond the two
  references is not documented in the Lua.
- The proxy can be locked. A device may connect while locked only if it
  is whitelisted and the game is in progress, or the reconnect setting
  permits it (values 1 or 3 in the Lua condition; see §4.3 for the
  reconnect setting).
- The proxy assigns each device two numbers: a protocol id (the wire id)
  and a classId, assigned sequentially from 1
  (`ULUsbProxy.lua:443-445`). The classId **is** the player number: the
  displayed name is derived from the stored profile plus the classId
  (`UTActivity.lua:168`), and the rankings announcer callout is addressed
  by classId (`FinalRankings.lua:89`). Capture 45 confirms the
  relationship on the wire: guns acknowledged with protocol id one greater
  than classId (id 04 with classId 03; id 05 with classId 04), i.e. id
  equals classId plus 1, with id 01 reserved for the UbiConnect itself.
  A host that copies one gun's acknowledgement template and substitutes
  only the id will give every gun classId 01, and every gun display will
  show player 1 — this exact failure was observed and fixed during the
  live FreeFrag work (see §6).
- After acknowledgement, the host requests the device revision and the
  stored profile. The gun validates the device reference carried in the
  revision request: live replay proved that a revision request carrying a
  stale reference goes unanswered while the same request carrying the
  live reference is answered at once (master document §3, items 30/36).
  The host must therefore use the reference the gun actually presented,
  never a reference copied from an old capture.

### 2.3 PlayersManagement

This stage detects which guns are present. The `GunDetect` UI sound plays
when a gun is found. Player-count limits in 1.1.354 are a minimum of 2
and a maximum of 8, except Tag'n Shoot, which accepts a single player.
The video walkthrough shows a two-column slot grid with per-player rows
carrying a team tab, avatar, gun-number badge, name, vest indicator, and
an add button; the 1.1.354 Lua flow is the same in outline (detection,
then setup). Profile editing from this area writes the profile to the
gun immediately on confirmation — see
[08-profile-write.md](08-profile-write.md).

### 2.4 PlayersSetup

PlayersSetup is where profiles, avatars, and teams are assigned. In Duel,
the mode's intro announcer lines play here. Two wire behaviours belong to
this stage:

- **Configuration send.** Once setup is done, the host sends each gun its
  game configuration (`0x92`) built from the mode settings
  (`UTActivity.State.PlayersSetup.lua:254`). The configuration carries
  volume, beam power, and a count followed by 16-bit big-endian values
  (health, ammunition, clips, swap, respawn, team, friendly fire,
  assistance, medkit, depending on mode). One-setting capture experiments
  confirmed the layout byte-exactly. Duration and launch mode are
  **nowhere** in the configuration — they are host-side only (captures 41
  and 42). Tag'n Shoot sends only two values (number of bases, assistance).
  FreeFrag sends an empty configuration (count zero).
- **Harness polling.** After configuration, while waiting for game start,
  the host polls for vest/harness presence every 250 ms
  (`PlayersSetup.lua:200,234`). The reply's first argument sets a
  harness-connected flag (1 means the vest/harness is connected). The
  connected case is CONFIRMED in captures 16, 17, 46, and 47; the
  disconnected case (argument 0) was never captured and is UNVERIFIED.

### 2.5 Bytecode (upload) stage

The host programs each gun with the mode's gun-side program plus its
configuration, over the UbiConnect. The video walkthrough shows this as a
loading screen with a progress bar and a warning not to switch the guns
off; the 1.1.354 flow has the same stage. The original upload timing uses
a fixed 250,000 µs interval (see
[13-community-mods.md](13-community-mods.md) for the tkdrob scaling
change, which is not original behaviour). Upload traffic uses the flash
commands (`0x86`/`0x81`/`0x82`/`0x83`) at the bytecode address; the
upload is complete only when all chunks for a gun have been sent. A device
removed mid-load returns to Players Management in the tkdrob bundle; the
1.1.354 behaviour on mid-load removal is not established in the sources.

### 2.6 Matchmaking, BeginMatch, BeginRound

Matchmaking covers the countdown. Launch mode decides its length:
automatic launch uses a 10-second countdown; manual launch uses 5 seconds
(players press the trigger when ready). The host sends the countdown value
to the guns (`0x93`, value 10 observed) and the announcer plays a ready
line followed by the countdown numbers. Music for in-game play starts at
this stage in most modes. BeginRound then dismisses the countdown display
and the round starts.

There is **no countdown-complete message** from the guns (CONFIRMED by
capture analysis during the live FreeFrag work): guns echo the countdown
in about 11 ms and then count down locally in silence, with the first
status poll 10.1–10.4 s after the countdown. The approach proven in the
live FreeFrag loop is to wait about 10 seconds, sending one broadcast
ping at about 5 seconds, then begin polling. Any
implementation that waits for a message to signal countdown completion
will mistake the echo for completion or time out.

### 2.7 RoundLoop

RoundLoop is the game itself. The host polls each gun in turn with the
status poll (`0x95`), which also carries downlink gameplay data to the
gun. Polling cadence in captures is about 0.311 s per gun during a game.
The host derives everything — shots, hits, health, ammunition, clips,
shooter identities — from the polled status reports; guns send no
unsolicited gameplay events. A poll can go unanswered (observed in
capture 16); the host retries on the next cycle.

Host-side RoundLoop duties, paraphrased from the per-mode RoundLoop
modules:

- **Scoring.** In the frag modes, a hit scores 1 point and a frag
  (victim reaching zero lifepoints) scores 3. Accuracy is computed as 100
  times hits-plus-deaths divided by shots in frag modes, and 100 times
  hits divided by shots in FreeFrag. Per-player statistics tracked are
  score, ranking, accuracy, shots, hits, deaths, respawns, ammo-pack
  scans, medkit scans, and who-hit / who-fragged whom.
- **Winner determination trap.** Hits *received* by a gun are not hits
  *landed* by it. The correct winner is the player whose opponents
  received the most hits from them. The live FreeFrag work caught the
  inverted version of this bug (crowning the most-hit gun) and fixed it
  with a live uneven-score run. Ties send the losing colour to both guns
  (see §2.8).
- **Kill feed.** The host maintains a live event feed (localisation keys
  `ingame001–021`): lead changes, base tags, ammo-pack tags, misses,
  UbiConnect hits, hits, reloads, frags, health recovery, near-elimination,
  return to game, elimination, team lead changes, and teammate hit/frag
  events.
- **Timed announcements.** The announcer plays remaining-time lines at
  playtime minus 60 s, minus 30 s, and minus 15 s, plus random filler
  lines at minus 57 s, minus 27 s, and minus 12 s. See
  [11-asset-inventory.md](11-asset-inventory.md) for the file mapping.
- **Vest watch.** The host watches each gun's own clock field in the
  status report; a clock stalled for more than 5 seconds means the vest
  is considered disconnected (see §4.4).
- **Rejoin clearing.** A rejoined gun's rejoin flag is cleared once the
  gun reports a hit count greater than zero (see §4.3).
- **Mode specifics.** Duel runs 5 rounds with a random 5–8 s wait before
  each "go" signal; shooting before the signal loses the point to the
  opponent; if both players hit, or both run out of ammunition, the round
  is a draw. Last Man Standing has no timer and ends when one player
  remains; its scoring counts lifepoints plus players while alive. Tag'n
  Shoot tracks base-tag stages in order on the gun and scores UbiConnect
  hits via command `0xC3` — a path that is Lua-only and UNVERIFIED on
  real hardware (see [14-open-questions.md](14-open-questions.md)).

The in-game screen, per the video walkthrough, is a leaderboard plus
timer plus event feed and nothing else: a left panel with the mode title,
a large timer, and the events list, and a right panel with team rows and
per-player rows showing avatar, gun-number badge, name, and hits / frags
/ health columns. Lead changes flash a full-screen orange event banner.

### 2.8 EndRound, rankings, end

When the timer ends (or the last player stands, or Duel's rounds finish),
the host plays end-of-match announcer lines and sends each gun a
game-over message (`0x94`) carrying the result colour (1 = won/green,
2 = lost/red) and the game-over sound code. The gun flash shows the
result. Colour semantics are CONFIRMED in capture 19 only; FreeFrag sent
the winning colour to both guns in capture 05, consistent with a mode
with no winner/loser distinction in the same sense. The host must send
each gun its correct final colour exactly once — the Efeion Java
project's habit of sending one colour to all guns first and then the
correct colours is a sequencing bug, not original behaviour (see
[13-community-mods.md](13-community-mods.md)). A clear form of the
game-over message also exists in Lua for resetting gun state.

Rankings play the `Ranking` UI sound. The winner announcement uses a
single-winner line, or a shared-win line on a tie (the tie reading is
INFERRED, not fully pinned), followed by player-number callouts computed
from classId, and for TeamFrag the winning-team lines. The original
1.1.354 Lua has no rematch button and no saved match history; the match
returns to the menu. The 1.2.439 video shows a Replay button on the
rankings screen — a version difference, recorded here so the two builds
are not conflated.

## 3. The announcer (Game Master) engine

The announcer engine (`UTGameMaster.lua`) has two entry points,
paraphrased: an immediate-play operation that plays one line at once,
optionally with a callback (used by Duel for phase changes), and a
registration operation that schedules lines with a set of alternate
paths, selection probabilities, a priority, a time offset, and an overall
play probability. Priority levels are: 0 for timed/ambient lines (with an
offset in seconds and a tolerance window), 1 for events, 2 for important
events, 3 for urgent lines, and 4 for activity/mode introductions.
Alternate lines are picked randomly according to their probabilities, and
the overall probability decides whether the line plays at all. The
game-over sound reference sent to guns is a gun-bank code, not a host
audio file (see [09-gun-audio.md](09-gun-audio.md)). The event-to-file
mapping is in [11-asset-inventory.md](11-asset-inventory.md).

## 4. Pinger and connection supervision

The sources do not define a single named set of "Pinger modes". What they
document is a set of distinct polling and supervision behaviours, listed
here separately so they are not merged into an invented enum.

### 4.1 The `0x05` ping

The Pinger module (`ULUsbDevice.Pinger.lua:29,70,136-143`) sends a
3-byte broadcast ping. The Lua rule is: at most once per second, and only
when a device has been silent for at least 5 seconds (timeout values
5,000,000 / 10,000,000 / 1,000,000 µs for the low threshold, high
threshold, and interval). Measured cadence in captures differs by phase:
about 6 seconds while idle (capture 13, gaps 5.99–6.24 s), and once per
poll cycle (about 0.311 s) during a game. The Lua rule and the measured
cadences are not in conflict — the Lua states the rate limit and silence
threshold; the captures show how often the condition actually fires in
each phase. Devices acknowledge the ping with an argument of 1. The
official ping's leading byte differs from the Efeion Java project's
version; the official form is the captured one.

### 4.2 Unregister on silence

A device silent for at least 80 seconds is unregistered
(`ULUsbDevice.Pinger.lua:105-121`, threshold 80,000,000 µs) when the
unregister setting is enabled or the game is in progress. The tkdrob
bundle doubles the Pinger intervals during bytecode upload (reset values
4 s / 8 s / 0.5 s against original 2 s / 4 s / 0.5 s,
`UTActivity.State.Bytecode.lua:85-86`); the doubled values are tkdrob
behaviour, not original.

### 4.3 Rejoin

A gun that drops mid-game can rejoin the running match. When a whitelisted
device reconnects while the proxy is locked and in-game, and the
reconnect setting permits it, the proxy marks the device as blocked from
game-data handling pending rejoin, sets its rejoin flag, and clears its
timeout state (`ULUsbProxy.lua:317-323`). The RoundLoop clears the rejoin
flag once the gun reports a hit count greater than zero. The reconnect
setting takes values 0/1/2 (`GameSettings.lua:40`); value 3 is a transient
state reset to 2 after a rejoin (`ULUsbProxy.lua:301-303`). Whether
rejoin existed in unmodified 1.1.354 or arrived with the community
lineage is not settled by the sources — the mechanism is documented in
the tkdrob bundle's Lua, and the master document's summary presents it
alongside tkdrob-derived behaviour. Treat rejoin as INFERRED for the
original build.

### 4.4 Vest-disconnect supervision

The vest-disconnect setting (`GameSettings.vestdisconnect`, values 0/1/2,
default 1, `UTGame.lua:100`) governs the anti-cheat response to a vest
being disconnected. The host watches the gun's own clock field in the
status report; if the clock stalls for more than 5 seconds, the vest is
considered disconnected and the host sets the gun's downlink state to the
trigger-disabled value (7), disabling the trigger. A manual mode exists
in which the leaderboard flags players with vest issues and clicking a
profile icon toggles that player's trigger off and on. The sources name
an automatic mode and a manual mode but do not cleanly map all three
setting values (0, 1, 2) to behaviours — that mapping is UNVERIFIED (see
[14-open-questions.md](14-open-questions.md)). Excluded modes, per the
tkdrob changelog, are FreeFrag, Infection, Last of the Species, Wild West
Duel, Tag'n Shoot, and The Martian Wolf. A test setting bypasses the
protection. The downlink state values are: 0 normal (trigger live), 6
dual-wield secondary mirror (paired gun follows the primary), 7 trigger
disabled. A separate setting, Prevent Launch (default 1), blocks starting
a match while a gun is disconnected and is auto-enabled when unregister
is turned on.

### 4.5 Out-of-range behaviour — conflicting sources

The printed manual says a gun has 30 seconds to return to range before
being kicked; the in-game text says 10 seconds. The Lua device-pinger
values are in undocumented units and do not settle it. This is an open
question (see [14-open-questions.md](14-open-questions.md)); do not pick
a number and present it as original behaviour.

## 5. Settings the host sends to guns

The configuration values the host builds per player (the "heap" in each
mode's main Lua, e.g. `UAFreeForAll.lua:267-297`) are the authoritative
checklist of what gets configured on a gun: lifepoints, ammunition,
clips, beam power, gun volume, swap, respawn time, medkit, and respawn
mode, plus per-mode extras (invulnerability times, shield, capture goal,
end-game timer, and others). The mode settings catalogue (playtime,
launch mode, beam power 1–5, ammunition per clip, clips, health points,
swap, number of teams, medkit/ammo-pack availability, respawn mode,
respawn time, friendly fire, assistance, number of bases) is documented
in full in the master document §5.2. Two settings notes matter for
implementers:

- Beam power (1–5, default 3) is sent in the configuration and confirmed
  byte-exact in captures, but the actual infrared behaviour behind the
  scale is not in the files and is UNVERIFIED.
- Respawn Mode is present in the settings data with value 0 but commented
  out of the options UI; its possible values and effect are unknown. Do
  not resurrect it without understanding it.

Gun-side programs declare three variable classes: CONFIG variables
uploaded at game start, RECEIVE variables pushed from the host during
play (for example wolf identity and bonus values in Infection), and SEND
variables read back in status reports (for example hit counts and
scores). The counts of CONFIG/SEND/RECEIVE variables must match exactly
between host and gun program — a constraint recorded from the btasm work
and consistent with the per-mode declarations.

## 6. Quirks and bugs found

Quirks in the original host behaviour and traps found during verification.
Each entry states its grade.

- **No countdown-complete message (CONFIRMED).** See §2.6. Host waits;
  guns count down locally.
- **Hit-id slots are not cleared at game start (CONFIRMED).** Status
  reports at the start of a game already carry shooter ids from the
  previous game. The host must take a baseline at game start and work
  from deltas. Relatedly, first-poll counters are already nonzero for the
  same reason.
- **Echoes are not replies (CONFIRMED).** Every host message is echoed
  back, with the target id substituted for the broadcast id. Never treat
  the first report after a send as the reply. A poll can also go
  unanswered; retry next cycle. The profile-write form of this trap is
  detailed in [08-profile-write.md](08-profile-write.md).
- **Stale tails (CONFIRMED).** Only the leading bytes of a 64-byte HID
  report are meaningful; trailing bytes are stale buffer residue. Never
  compare full reports.
- **Duration and launch mode are host-side only (CONFIRMED).** They
  appear nowhere in the gun configuration.
- **Player-number callout mismatch (INFERRED quirk).** The final-rankings
  callout family is computed from classId starting at file 96, but the
  file at the base of that family speaks a race-related line, not a
  player number. The mismatch is unresolved in the sources; the
  player-number callouts proper start one file later. See
  [11-asset-inventory.md](11-asset-inventory.md) and
  [14-open-questions.md](14-open-questions.md).
- **Tie announcement reading is inferred.** The shared-win line's use for
  ties is not fully pinned in the Lua read.
- **Health 5-vs-4 was carried-over state, not a setting (CONFIRMED,
  resolved).** A capture whose target started at 4 health was showing
  damage from an aborted earlier session, not a different configuration.
- **ClassId substitution (CONFIRMED by live fix).** Pairing
  acknowledgements and the revision request must carry both the protocol
  id and the classId, substituted per gun. Copying one gun's template
  gives every gun classId 01 and every display shows player 1.
- **Dangling sound references exist in the tkdrob bundle** (announcer
  files and a gun code referenced but present in no bank or install).
  They are mod bugs, not original behaviour — see
  [13-community-mods.md](13-community-mods.md). The original's own
  ungrounded announcer files (no literal Lua reference, no computed-name
  explanation) are listed in [11-asset-inventory.md](11-asset-inventory.md).
- **Dual-wield merging (INFERRED, tkdrob bundle).** Two guns with the
  same profile name on the same team merge into one player; the secondary
  gun mirrors the primary's trigger state via downlink value 6. The
  secondary-mirror behaviour against captures is UNVERIFIED.
- **Gun-default team and icon-prefix team rules (INFERRED, tkdrob
  bundle).** Each gun can carry a stored default team, and in the tkdrob
  bundle an icon filename starting with a digit and underscore sets the
  profile team from that digit (`ULUsbDevice.lua:268-274`). These are
  community-bundle mechanisms; the original 1.1.354 team derivation from
  a profile is not established in the sources.

## 7. Friendly fire — the no-score rule

Friendly fire is a TeamFrag-only setting (`teamFrag`), default off. The
rule, as documented in the settings catalogue and the mode description,
is:

- With friendly fire **off** (the default), same-team hits decrement the
  victim's lifepoints in the gun-side program but **score nothing** for
  the shooter. Only hits where shooter and victim are on different teams
  score (hit 1 point, frag 3 points).
- The setting controls whether same-team hits count; the scoring
  consequence of turning it on is therefore that same-team hits do count.
  That positive reading is INFERRED from the setting's semantics — the
  sources document the off-state rule explicitly and the on-state only
  through the setting's purpose.
- The host still surfaces teammate events in the kill feed (teammate hit
  and teammate frag feed entries exist), and two announcer lines are
  registered for a teammate-frag event at priority 2 — but the exact
  trigger for those lines is not fully pinned in the sources.
- In the tkdrob bundle, games with 5–6 teams force friendly fire on,
  because the gun firmware does not address teams 5 and 6. That forcing
  is a mod workaround for an unsupported configuration, not original
  behaviour, and must not be copied as a general rule (see
  [13-community-mods.md](13-community-mods.md)).

## Sources

- `BATTLE_TAG_MASTER.md` §§4.3–4.4, 5.1–5.4 (state machines, flows,
  settings, announcer engine, scoring, friendly fire), §3 items 30–42
  (live verification, echo rule, classId fix, winner fix).
- `TKDROB_RESEARCH.md` §§3.3–3.4, 5.2 (connection state machine,
  Pinger, vest handling, downlink values, CONFIG/RECEIVE/SEND).
- `SPEEDUP_RESEARCH_2026-11-10.md` §3 (Lua command table and send sites).
- `GHIDRA_PASS_2026-11-10.md` Q1–Q3 (HID packing, native transport,
  init sequence).
- `GUI_SCREENSHOTS.md` §10 (video walkthrough: window template, flow,
  in-game layout, version finding 1.2.439).
- `sniffing-results/protocol_map.json` (capture confirmation for
  pairing, configuration, harness polling, countdown, game-over, polling
  cadence).
