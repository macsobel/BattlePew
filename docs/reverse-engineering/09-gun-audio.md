# 09 — Gun Audio (T-Blaster Sound Banks)

The T-Blaster plays its own sounds from sound banks shipped in the game
install. The host never streams audio to the gun: game logic on the gun
selects sounds by short code (for example `SD16`), and the spoken prompts
and effects live in the gun's banks. This document describes the bank file
format, the audio codec and its decode parameters, the code-to-sound map,
and the status of the live ear-check that would settle the sample rate.

Grading used in this document: **CONFIRMED** means verified in USB captures
and/or live replay; **INFERRED** means derived from file analysis or Lua
only, or a single observation; **UNVERIFIED** means an open question.
Evidence is cited per claim. Note that no gun-audio claim in this document
rests on a capture: the evidence is file parsing, codec trials, and
machine transcription. The decisive hardware comparison (the ear-check)
has not run.

Sources: `GUN_BANK_DECODING.md` (primary), `BATTLE_TAG_MASTER.md` §5.7,
`ASSET_INVENTORY.md`, `EAR_CHECK_PROMPT.md`,
`sniffing-results/protocol_map.json` (`GAME_END`, game-over sting).

## 1. Bank locations and inventory

Banks live under `System/Revision/0x02000020/Banks/` in the extracted
install, one subdirectory per bank family. The tkdrob bundle adds further
bank variants under the same layout (see §6).

| Bank | Entries | Content | Entry sizes |
|---|---|---|---|
| DSN1 | SC01–SC10 (10) | Short sound effects | 200–7,426 bytes each |
| DSN2 | SW12, SW22, SW42, SW56 (4) | Sound effects, role unknown | 800–3,836 bytes each |
| DSN3 | SD01–SD08, SD16 (9) | Spoken voice prompts | 2,235–9,951 bytes each |
| DSN4 | SD17, SD09–SD15 (8) | Spoken voice prompts | 5,241–10,933 bytes each |
| DANI | 13 animation entries | Animation programs, **not audio** (see §7) | 407 bytes total |

Total original audio entries: **31** (10 + 4 + 9 + 8). Bank file sizes in
the install range from about 7 KB to about 55 KB depending on bank and
variant (DSN1 27,241 bytes per variant; DSN2 7,499 bytes;
DSN3 43,486–48,220 bytes; DSN4 50,503–55,375 bytes).

### Variants

- **EN01 vs EN02: decoded audio is sample-identical** (verified
  sample-for-sample on SC01, SC04, SD06, SD17, and SW42). The files differ
  only in the header locale bytes and the MD5 trailer. EN02 is a second
  bank slot carrying the same sounds; no separate decoded files were
  produced for it.
- **FR01/FR02:** DSN1/DSN2 payloads are identical to the English ones
  (effects are language-independent; checksums confirm FR01 equals EN01
  for DSN1, DSN2, and DANI). DSN3/DSN4 FR01 differ from English — French
  voice recordings with different lengths (for example, French SD01 is
  6,818 samples against English 9,172).
- **tkdrob-only banks:** DSN5-EN02 (SG01–SG07), DSN3-EN03 (adds SD19),
  DSN4-EN03 (adds SD18). See §6.

## 2. Bank container format

**CONFIRMED** by parsing validation: every bank file (DSN and DANI alike)
uses the same container. The parser validated entry chaining, the trailer
offset field, the trailer bytes, and the checksum on the original banks.

| Offset | Size | Content |
|---|---|---|
| 0–3 | 4 | Magic: `DSN1`, `DSN2`, `DSN3`, `DSN4`, `DSN5` (tkdrob only), or `DANI` |
| 4 | 1 | Entry count (for example `0x0A` = 10 for DSN1) |
| 5–8 | 4 | Locale/variant: `EN01`, `EN02`, `FR01`, `FR02` (`EN03` in tkdrob) |
| 9–10 | 2 | Unsigned 16-bit little-endian offset of the trailer, equal to the end of the last entry's data |
| 11… | 8 × count | Entry table: one 8-byte entry per sound — 4-character name, unsigned 16-bit little-endian start offset, unsigned 16-bit little-endian length |
| … | — | Entry payload data, entries chained contiguously: each entry's start equals the previous entry's start plus its length |
| Last 20 | 20 | Trailer: 4 bytes `22 04 19 86`, then a 16-byte MD5 checksum |

Checksum rule: the stored MD5 equals the MD5 of all preceding bytes
**including** the 4 trailer bytes — that is, the MD5 of the file minus its
last 16 bytes. Computing the MD5 over the file minus the last 20 bytes
does not match. This agrees with the tkdrob Animations Guide description
of the footer as a checksum over everything above it.

Two container quirks to preserve:

- **DANI gap.** DANI entries have a 1-byte gap between consecutive entries
  (each start equals the previous end plus 1); the length field there is
  the distance to the next entry minus 1. DANI is animation data, not
  audio (§7).
- **The `DANI\r` header reading.** Some references show a DANI header as
  `DANI` followed by a carriage-return byte. That byte is the entry-count
  field: DANI has 13 entries, and 13 is the carriage-return byte value.
  It is not part of the magic.
- **tkdrob SD19 table slip.** In tkdrob's DSN3-EN03, SD19's table start
  (48,206) is 2 bytes before the true data start (48,208, equal to the end
  of SD16); its length (8,800) is correct, and the trailer field (57,008)
  matches 48,208 + 8,800. The decoded SD19 file was extracted from the
  corrected offset 48,208. Any parser must handle this slip rather than
  trusting the table start blindly for that one entry.

## 3. Codec and decode parameters

### 3.1 Accepted parameters — INFERRED, high confidence

- Codec: **IMA ADPCM, high-nibble-first** (the first 4-bit code of each
  byte is the high nibble). This is the same nibble order as QuickTime IMA
  ADPCM; it is **not** WAV IMA ADPCM, which is low-nibble-first.
- Channels: mono.
- Step table: the standard 89-entry IMA step table (values 7 through
  32,767).
- Index table: −1, −1, −1, −1, +2, +4, +6, +8.
- Predictor and step index reset to 0 at each entry start.
- No per-entry header: entry payload bytes are the ADPCM stream directly.
- Sample rate: **8,000 Hz — high-confidence but UNPROVEN** (see §3.3).
  All durations in this document are stated at 8 kHz labelling.

### 3.2 How the codec was identified

The byte distribution pointed at a nibble codec before any decoder ran:
bytes cluster below `0x40` (32–64%) and at or above `0x80` (29–54%),
with a clear dip in `0x40–0x7F` (6–15%) — consistent with 4-bit delta
codes, not with PCM. Five entries (SC01, SC04, SW42, SD06, SD17) were
then run through six decode hypotheses:

| Hypothesis | Result | Evidence |
|---|---|---|
| Raw PCM unsigned 8-bit, 8 kHz | Rejected | DC offset −7,000…−15,000; zero-crossing rate 0.45–0.61 (white-noise-like); 2–24% samples clipped |
| Raw PCM signed 16-bit, little- and big-endian | Rejected | DC offset −3,400…−8,400; zero-crossing rate 0.29–0.48; jitter 0.6–1.1 (noise, not audio) |
| IMA ADPCM, low-nibble-first | Rejected | Decoder rails: DC +9,000…−18,000; zero-crossing rate 0.01–0.07; RMS near full scale (saturated, not audio) |
| OKI MSM6375 ADPCM, low- and high-nibble-first | Rejected | Low-first saturates (DC +3,000…−8,000, zero-crossing rate ≈ 0.01). High-first RMS implausibly low (630–6,300), DC up to ±8,000 |
| **IMA ADPCM, high-nibble-first** | **Accepted** | DC ≈ 0 on all five test entries (−20…+34); peaks 0.65–0.76 (consistent normalisation); speech-like envelopes; clean machine transcription of full sentences (§4) |

The codec identification is graded **INFERRED** rather than CONFIRMED
because no live gun comparison has run: the evidence is internal (decoder
behaviour plus transcription quality), not a hardware ground truth. The
ear-check (§8) is the experiment that would promote it.

### 3.3 Sample rate: 8,000 Hz is high-confidence but UNPROVEN

Say this plainly in any downstream use: **the 8,000 Hz sample rate has
never been proven against the real gun speaker.** The evidence for it is
strong but indirect:

- **Speaking pace.** SD06 (7 syllables) is 2.49 s at 8 kHz, i.e. 2.8
  syllables per second; SD17 (8 syllables) is 2.73 s, i.e. 2.9
  syllables per second. Both are normal conversational pace. At 11,025 Hz
  they would be 3.9–4.0 syllables per second (rushed); at 16,000 Hz,
  5.6–5.9 syllables per second (implausible for clear prompts).
- **Clean transcription at 8 kHz.** Full sentences are recognised cleanly
  from the 8 kHz decodes. A wrong rate would pitch-shift the speech and
  degrade recognition.
- **Hardware context.** An 8051-based toy speaker playing 4-bit ADPCM at
  8 kHz is the standard design point, and effect durations are sensible
  at 8 kHz (the shortest entry, SC06, is a 0.05 s blip; SC02 is a 0.20 s
  beep).
- **Inconclusive cross-check, reported honestly.** Comparing the gun's
  SD01 against the PC announcer's corresponding callout by fundamental
  frequency gave 258 Hz against 400 Hz at 8 kHz labelling, with an
  autocorrelation of only 0.36 (octave error likely; the two voices may
  also be different speakers or differently processed). This neither
  proves nor overturns the 8 kHz conclusion.

If the rate is wrong, every gun sound in a replacement will be pitched
wrong. The decisive test is the ear-check (§8): play a decoded file next
to the real gun speaker while triggering the same sound. Until that runs,
8,000 Hz stays labelled high-confidence, UNPROVEN.

## 4. SD01–SD17 map (spoken prompts)

Transcriptions are machine-generated (Whisper small, local, from the
8 kHz decodes). `[?]` marks an uncertain hearing; the reasoning is in the
decoding report. Durations are at 8 kHz labelling. The "likely SND name"
column maps each bank code to the gun ByteCode sound name it is believed
to serve; the name-to-code mapping itself lives in the native engine, not
in any file read, so these mappings rest on transcript content plus the
ByteCode sound-name lists, not on a mapping table.

### DSN3 (9 entries)

| Code | Duration | Functional content | Likely SND name / role |
|---|---|---|---|
| SD01 | 1.15 s | Reload prompt | RELOAD / EMPTY prompt — spoken when ammo is out |
| SD02 | 1.18 s | Back-in-game prompt | ASSIST_BACKINGAME (respawn) |
| SD03 | 1.85 s | Out/eliminated prompt | DEAD |
| SD04 | 1.50 s | Connection-lost warning | UbiConnect/vest disconnect warning |
| SD05 | 1.63 s | Low-battery warning | Low-battery warning |
| SD06 | 2.49 s | Vest-connection prompt | Vest-not-connected prompt (setup/pairing) |
| SD07 | 0.79 s | Finished prompt [?] | Game-over sting for Duel / Tag'n Shoot (override, see §5) |
| SD08 | 0.56 s | Go prompt | START (duel "go" / match start) |
| SD16 | 0.88 s | Game-over prompt | Default game-over sting (see §5) |

### DSN4 (8 entries)

| Code | Duration | Functional content | Likely SND name / role |
|---|---|---|---|
| SD09 | 1.59 s | Scan T-Base 1 prompt | ASSIST_BASE1 |
| SD10 | 1.76 s | Scan T-Base 2 prompt [?] | ASSIST_BASE2 — pattern from SD09/SD11/SD12 makes the reading near-certain |
| SD11 | 1.61 s | Scan T-Base 3 prompt | ASSIST_BASE3 |
| SD12 | 1.65 s | Scan T-Base 4 prompt | ASSIST_BASE4 |
| SD13 | 1.59 s | Scan ammo pack prompt | ASSIST_SCANAMMO |
| SD14 | 1.31 s | Scan medkit prompt | ASSIST_SCANLIFE |
| SD15 | 1.59 s | Shoot-the-UbiConnect prompt [?] | ASSIST_UBICONNECT — fits the ASSIST set and Tag'n Shoot exactly |
| SD17 | 2.73 s | Trigger-to-start prompt | Manual-launch prompt (press trigger when ready) |

### French prompts

All 17 French DSN3/DSN4 entries were decoded. Only SD01 and SD03 were
spot-transcribed (French equivalents of the reload and out prompts); the
rest are code-named and await French transcription if needed. French
recordings occupy the same code slots as English.

## 5. The game-over sting (SD16 / SD07)

**CONFIRMED** by Lua plus captures. The host's game-over message (`0x94`)
carries a 4-byte ASCII bank code as its sound reference. The default code
is `SD16`, defined as a byte array in `UTActivity.lua:44` and sent in the
`0x94` message at end-of-match (`UTActivity.State.EndMatch.lua:123`;
`FinalRankings.lua:172`). Duel and Tag'n Shoot override it to `SD07`
(`UAOldFashionDuel.lua:43`, `UATagThenShoot.lua:42`, both files carrying a
comment identifying the SD07 sound). Captures confirm the wire forms: the
SD16 form in captures 05, 19, and 42; the SD07 form in Tag'n Shoot
captures 25, 26, and 28. See
[10-host-software-behavior.md](10-host-software-behavior.md) for the
end-of-match flow.

## 6. SC / SW effects and tkdrob-only codes

### SC01–SC10 and SW12/SW22/SW42/SW56 — mostly unmapped

Machine transcription returns empty or hallucinated single words on
DSN1/DSN2 entries, as expected for sound effects. Their envelopes confirm
effect character: SC01 is a 0.75 s burst decaying to silence; SC02 a flat
0.20 s beep; SC06 a 0.05 s blip; SC09 a sustained 1.00 s tone decaying at
the end; SW42 a 0.96 s decaying sweep. Only two mappings have file
evidence:

| Code | Mapping | Evidence and grade |
|---|---|---|
| SC01 | ≈ SCAN_GOOD (base-scan confirm chirp) | **INFERRED, strong.** In tkdrob ByteCode, the spots that play SC01 on a base scan are the same spots where the original ByteCode plays SCAN_GOOD (context substitution, Capture / ClassicCapture). |
| SC06 | ≈ BIP (short beep) | **INFERRED, plausible, not proven.** Duration-based: 200 bytes / 0.05 s, by far the shortest entry — a single blip. |
| SC02–SC05, SC07–SC10 | Unmapped | No file evidence pins them. Candidate sound names remaining: SHOOT, HURT, RELOAD, RELOAD_CLIP, EMPTY, SCAN_BAD, DEAD, RESPAWN, OK, START. The count coincidence (10 DSN1 entries, about 10 effect-like names) is suggestive only and is **not** a mapping: several names may instead resolve to SD voice entries (for example START to SD08, DEAD to SD03, RELOAD to SD01). Do not assign SC02–SC10 to names without a live test. |
| SW12, SW22, SW42, SW56 | Unknown role | No Lua/ByteCode reference found. Possibly dashboard-swap sounds (the "SW" prefix is suggestive only). Unmapped. |
| SC11 | **Dangling — do not use** | Referenced in tkdrob ByteCode but present in no bank (all original and tkdrob banks checked). Same class of bug as tkdrob's dangling announcer references. |

### tkdrob-only voice entries

These exist only in the tkdrob bundle's banks (DSN5-EN02, DSN3-EN03,
DSN4-EN03) and require a gun-bank flash to reach a gun — a path the
project does not use. They are documented here for completeness; see
[13-community-mods.md](13-community-mods.md) for the flashing warnings.

| Code | Bank | Duration | Functional content | Used by |
|---|---|---|---|---|
| SD18 | DSN4-EN03 | 2.16 s | Flag-captured prompt | Capture / CaptureMax |
| SD19 | DSN3-EN03 | 2.20 s | Field-captured prompt [?] | No ByteCode reference found — unreferenced spare |
| SG01 | DSN5-EN02 | 2.68 s | Liquidator warning prompt | Liquidator |
| SG02 | DSN5-EN02 | 2.49 s | Energy-stolen prompt | Rustlers (energy steal) |
| SG03 | DSN5-EN02 | 2.51 s | No-energy-left prompt | Capture (energy mode) |
| SG04 | DSN5-EN02 | 1.21 s | Wolf prompt | TheWolf / Last of the Species |
| SG05 | DSN5-EN02 | 2.72 s | Infected prompt [?] | Infection |
| SG06 | DSN5-EN02 | 1.70 s | Zombie prompt | Infection |
| SG07 | DSN5-EN02 | 1.85 s | Still-in-play prompt | Capture |

Do not use SD19 (unreferenced spare) as a game sound without a live test.

## 7. DANI banks — animation data, not audio

**CONFIRMED** (container parse plus the tkdrob Animations Guide): DANI
banks hold 13 animation programs (407 bytes total), not audio. Entry names:
ARAM, AMED, AGB1–AGB4, AVUC, ABOO, AGOV, AOUT, AEND, AUBI, ASHT. They match
ByteCode animation names (ARAM = ammo reload animation, AMED = medikit
animation). The tkdrob guide documents further animation codes used in
edited banks (including AGB5–AGB6, AGBF, AGBH, AGAV, ADON, ASWI, ASWF,
ASWL, AHUR), but those are not entries in the original DANI bank. The
animation programs control LEDs and the gauge on the gun; no host command
for LEDs exists (see [10-host-software-behavior.md](10-host-software-behavior.md)
and the LED/rumble closure in the protocol map). DANI decoding is a
separate task from audio and is not needed for sound reproduction.

Firmware blobs (`0x00010000.bin`, 16,652 bytes and 30,593 bytes in the two
Revision folders) contain no readable strings and are almost certainly
8051 firmware images. They were not decoded and are not needed for the
sounds. Do not pursue them for audio purposes.

## 8. Ear-check status — NOT YET RUN

The ear-check is the experiment that settles the 8,000 Hz rate and the
remaining SC-to-sound mappings. As of the sources, it is **prepared but
not run**: the package (`ear_check.py` plus `EAR_CHECK_PROMPT.md`, with
the 6 reference files) was built and its self-test verified on simulated
data (it recovers 8,000 / 11,025 / 16,000 Hz exactly), and it awaits a
session on the Windows machine with the real hardware. No ear-check
results exist in the sources. Do not cite the rate as proven.

The prepared procedure, for the implementer who runs it:

- Two independent measurements per sound: a **duration ratio** (recorded
  length divided by decoded length, equal to 8,000 divided by the true
  rate) and a **spectrum scale** (the frequency-scale factor that best
  aligns the two recordings' spectra, equal to the true rate divided by
  8,000). Agreement near 8,000 Hz confirms the rate; agreement elsewhere
  establishes that rate instead.
- Six sounds, each triggered on the real gun while the microphone
  records: SD06 (gun on, no vest connected), SD01 (fire until empty, then
  one more trigger pull), SD13 (empty-magazine ammo-pack prompt, the
  following prompt after SD01), SD02 (respawn after a team-base scan),
  SD16 (end a game), SC01 (scan any accepted RFID box).
- The operator's own hearing verdict (right / too high / too low / skip)
  is recorded per sound alongside the measurements; even 2–3 sounds
  decide the rate, and partial results are useful.
- Safety scope of the prepared session: playback plus microphone only.
  No writes to the UbiConnect or guns, no replay, no firmware flows, no
  game-file changes.

The full experiment specification, including the SC-mapping live test
(trigger each ByteCode sound name in turn and note which code the gun
plays, checking the SC01 and SC06 predictions first), is carried in
[14-open-questions.md](14-open-questions.md).

## 9. What the replacement needs from this

- The ByteCode sound names (SHOOT, HURT, RELOAD, RELOAD_CLIP, EMPTY,
  SCAN_GOOD, SCAN_BAD, DEAD, RESPAWN, START, OK, BIP, and the ASSIST_*
  set) are the contract between game logic and gun sounds. The host
  reproduces these cues from decoded audio; no re-recording is needed for
  the 31 original English entries, which were decoded to playable files
  during the research (31 English, 17 French, 9 tkdrob-only — 57 files
  in total, verified as 8,000 Hz mono signed 16-bit WAV).
- The gun also has a vibration motor (driven on hits in ByteCode) and LEDs
  (flash colours driven by the gun-side program). Both are gun-side,
  bytecode-driven behaviour, not host commands.
- Until the ear-check runs, label every decoded duration and every
  playback rate as "at 8 kHz labelling" and keep the rate configurable.

## Sources

- `GUN_BANK_DECODING.md` §§1–7 and appendix (container, codec trials,
  transcriptions, mapping, DANI, inventory, next steps).
- `BATTLE_TAG_MASTER.md` §5.7 (sound map, bank summary, 8 kHz status),
  §3 items 16 and 24 (decode done; ear-check queued).
- `ASSET_INVENTORY.md` (bank locations and sizes).
- `EAR_CHECK_PROMPT.md` (ear-check procedure, 6 sounds, measurements).
- `sniffing-results/protocol_map.json` (`GAME_END`: SD16/SD07 wire forms).
