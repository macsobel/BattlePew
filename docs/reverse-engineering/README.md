# Reverse-engineering documentation: Ubisoft Battle Tag (2010) T-Blaster system

These documents record the reverse-engineering of the original Ubisoft
Battle Tag laser-tag system (host game version **1.1.354**) carried out in
November 2026. They describe the wire protocol, host behavior, and device
behavior in enough detail that a human or an AI implementer can write
compatible host software without repeating the USB sniffing work.

They describe the **original system only**. They are implementation-neutral:
they do not describe BattlePew's code, except for brief pointers to repo
data files where captured payload bytes already live. Where BattlePew's
behavior deliberately differs from the original, that is noted as an
implementer choice, not presented as original behavior.

## How to read these docs

Start with the overview and the transport, then follow the lifecycle of a
game session in order: pairing, upload, config, match loop, modes. The
remaining docs cover the surrounding systems (profiles, gun audio, host
software, assets), the evidence trail (capture index), the community-mod
context, and everything still unknown.

Every behavioral claim is graded. The grading legend below applies to all
documents in this folder. Do not treat an INFERRED or UNVERIFIED claim as
settled — several of the traps in this system come from exactly that
mistake (see [13-community-mods.md](13-community-mods.md)).

## Confidence-grading legend

| Label | Meaning |
|---|---|
| **CONFIRMED** | Verified in USB captures and/or live replay on real hardware. The doc cites the capture number, frame, or replay result. |
| **INFERRED** | Supported by the original Lua source only, or by a single observation. Plausible, but not wire-verified. The doc cites the Lua file and function. |
| **UNVERIFIED** | An open question. The doc states what is known, what is not, and (in [14-open-questions.md](14-open-questions.md)) the experiment that would settle it. |

Two grading rules used throughout:

- **Captures beat code.** Where installer Lua and captured wire bytes
  disagree, the capture wins for what the 1.1.354 system actually does.
  The known cases (TeamFrag and TagThenShoot payloads) are documented in
  [04-bytecode-upload.md](04-bytecode-upload.md) and
  [07-mode-behavior.md](07-mode-behavior.md).
- **The protocol map is the byte-level authority.** Frame formats were
  cross-checked against the 14/14 protocol map built from the captures.
  Where an earlier research note and the map disagree, the map wins and
  the conflict is recorded in
  [12-capture-index-and-verification.md](12-capture-index-and-verification.md)
  §5.

## Method summary

Three evidence sources, in order of authority for wire behavior:

1. **USB sniffing (USBPcap, Windows).** USB sniffing does not work on
   macOS, so all captures were taken on a Windows machine running the
   original game, then re-parsed and cross-checked. The capture inventory
   — what each numbered capture proved — is in
   [12-capture-index-and-verification.md](12-capture-index-and-verification.md).
2. **Original Lua source.** The original game ships its Lua source in
   plaintext inside the installer. Host logic in these docs is
   *paraphrased* from that source, with file and function citations so a
   reader with the installer can verify each claim. No original code is
   reproduced here.
3. **Live replay verification.** Captured sequences were replayed against
   real T-Blasters on both Windows and Mac (via hidapi) to confirm that
   the decoded protocol actually drives the hardware. Replay results are
   recorded in [12-capture-index-and-verification.md](12-capture-index-and-verification.md).

## Document map

| Doc | Contents |
|---|---|
| [01-overview-and-method.md](01-overview-and-method.md) | System components (T-Blaster, vest, UbiConnect, RFID boxes, host), how the RE was done, baseline version 1.1.354. |
| [02-usb-transport.md](02-usb-transport.md) | HID identity, report framing, 65-byte writes, hidapi on macOS, the echo rule, reader discipline. |
| [03-pairing-registration.md](03-pairing-registration.md) | Announcement, pair start/stop, ack A/B, revision, profile read, id vs classId, refs, whitelist, Pinger, lobby-only joining, silence semantics, rejoin rules. |
| [04-bytecode-upload.md](04-bytecode-upload.md) | The 250 ms upload timer, all-guns ACK gating, the 0x83 next-offset rule, frame formats, TOC/trailer, chunking, per-mode sizes, timing oracle, traps. |
| [05-config-and-settings.md](05-config-and-settings.md) | 0x92 config framing, team baked into config, volume/beam-power scales, per-mode defaults, player bounds. |
| [06-match-loop.md](06-match-loop.md) | 0x95 polling, status byte maps, hit shift register, countdown, game-over frames, RFID mechanisms, manual launch, vest polling, TagThenShoot + 0xC3 caveat. |
| [07-mode-behavior.md](07-mode-behavior.md) | Per-mode behavior for the 7 modes + Introduction tutorial: win conditions, scoring, timers, quirks; capture-vs-Lua divergences. |
| [08-profile-write.md](08-profile-write.md) | Profile flash sequence, 17-byte TOC, record format, worked example (placeholders), echo-acceptance pitfall, avatar list. |
| [09-gun-audio.md](09-gun-audio.md) | Sound-bank format, IMA ADPCM decode parameters, SD01–SD17 map, ear-check status. |
| [10-host-software-behavior.md](10-host-software-behavior.md) | Host state machines and UI flows paraphrased from Lua, Pinger supervision, quirks/bugs, friendly-fire rule. |
| [11-asset-inventory.md](11-asset-inventory.md) | Counts/formats/locations of WAV/TGA/Lua assets, announcer event mapping, measured palette, what is not included and why. |
| [12-capture-index-and-verification.md](12-capture-index-and-verification.md) | Capture inventory, replay verification results, source-conflict log, how to re-derive. |
| [13-community-mods.md](13-community-mods.md) | tkdrob mod summary + warnings (what not to copy), Efeion Java notes, version-provenance warnings. |
| [14-open-questions.md](14-open-questions.md) | Everything unverified, each with the experiment that would settle it. |
| [glossary.md](glossary.md) | Domain terms: UbiConnect, T-Blaster, vest, T-Base, medbox, classId, id, ref, bytecode, SD codes, and more. |

## Scope and provenance

- These docs describe **independently observed wire behavior and
  paraphrased logic, for interoperability**. They exist so compatible
  software can be written without redoing the sniffing.
- **No original code or assets are reproduced.** No Lua source is quoted
  (logic is paraphrased with file/function citations); no audio files,
  images, screenshots, or capture files are included; no announcer
  transcripts are reproduced (only an event-to-filename mapping with
  short functional labels). The original game assets and code remain
  Ubisoft's.
- **Payload bytes** in the repo's data files (e.g.
  `battletag/data/mode_sequences.json`) were derived from USB captures of
  the original system running, not copied from the installer's Lua tables
  — which matters, because the capture bytes diverge from the Lua tables
  for two modes (see [04-bytecode-upload.md](04-bytecode-upload.md)).
- **No real device identifiers appear anywhere in these docs.** Radio
  references are shown only as clearly-labeled placeholders. Worked
  examples use invented placeholder values marked EXAMPLE.
- Community-mod behavior (tkdrob, Efeion, btasm/btbase) is described only
  to warn implementers what **not** to copy into a 1.1.354-compatible
  implementation. No mod code is reproduced.
- The baseline is game version **1.1.354**. Behavior observed only in
  other versions (notably 1.2.439) is flagged as such wherever it appears
  and is not presented as 1.1.354 behavior.
