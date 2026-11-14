# 08 — Profile Write (Gun-Stored Player Profile)

Status: **INFERRED** overall. The write sequence was reconstructed from the
original Lua in 2026-11-12 and has **never been observed on the wire**: no
capture in the set covers a profile edit, and every captured `0x82` frame
targets the bytecode address, not the profile address. The matching read
path (`0x84`) is capture-confirmed, which corroborates the block layout but
does not prove the write bytes. Treat every write byte below as
Lua-derived until a profile-edit capture exists. See
[14-open-questions.md](14-open-questions.md) for the settling experiment.

Grading used in this document: **CONFIRMED** means verified in USB captures
and/or live replay; **INFERRED** means Lua source only, or a single
observation; **UNVERIFIED** means an open question. Evidence is cited per
claim.

Sources: `sniffing-results/protocol_map.json`, entries
`0x83_profile_write` and `0x84_profile_request`; Lua
`UTGame.Ui.Associate.lua` (Player Management profile editor),
`Profile.lua:45`, `ULUsbDevice.lua:229-255` (profile reply decode),
`FlashMemoryManager.lua` (flash commands). The task brief's known
corrections are applied: profile write uses address `0x00000000`;
bytecode flash uses address `0x4000`.

## 1. What a profile is

Each gun stores one player profile in its own flash: a player name and an
avatar icon filename. The host reads it back during pairing with the `0x84`
request and displays the stored name and icon for that gun. The profile is
gun-resident state, independent of any game mode: it survives game
sessions and is only changed by an explicit edit.

**INFERRED** (Lua, `UTGame.Ui.Associate.lua`): editing a profile in the
Player Management screen and confirming flashes the edited profile to the
gun **immediately**, at edit time — not later at mode-upload time. The
protocol map notes that the tkdrob bundle keeps the identical sequence;
that is a statement about the mod's Lua, not independent wire evidence.

## 2. Write sequence

**INFERRED** (Lua only). The sequence is **unicast to one gun id** (the
target gun's protocol id, not the `0xFF` broadcast used by the bytecode
upload). Each message is re-sent every 250 ms until a reply carrying the
same command arrives. The five steps, in order:

| Step | Message (meaningful bytes) | Purpose |
|---|---|---|
| 1. Unlock | `01 <id> 86 00` | Unlock flash for writing |
| 2. Erase | `04 <id> 81 00 00 00 00` | Erase the page at address `0x00000000` |
| 3. Write-init | `06 <id> 82 00 00 00 00 00 <N>` | Announce a write at address `0x00000000` of `N` bytes, where `N` is the block length |
| 4. Chunk | `<3+N> <id> 83 00 00 <N> <block>` | Single chunk carrying the whole profile block at offset `0x0000` |
| 5. Lock | `01 <id> 86 01` | Lock flash again |

Notes:

- The framing follows the general host-message shape: a leading size byte,
  then the target id, then the command byte, then arguments. In step 3 the
  trailing two bytes are the block length as a 16-bit big-endian value
  (`0x00`, `N`), matching the `0x82` construction used for bytecode
  uploads. In step 4 the leading byte is `3 + N` and the length byte after
  the two offset bytes is `N` itself.
- The address in steps 2–3 is `0x00000000`. **Do not confuse this with the
  bytecode upload**, whose `0x81`/`0x82` address is `0x00004000`
  (**CONFIRMED**, capture 16 frames 61 and 67, re-parsed 2026-11-12; earlier
  readings of the bytecode address were parse errors and are superseded).
- The profile write is a **single chunk**. The bytecode upload, by contrast,
  is many 58-byte chunks. A profile block is short enough (tens of bytes)
  that one chunk suffices; no multi-chunk profile write is documented.
- The 250 ms retry rule and the "reply carries the same command" acceptance
  rule come from the protocol map's sequence description. The exact bytes
  of a genuine reply, as opposed to an echo, are **UNVERIFIED** — see §6.

## 3. Profile block format

The block written in step 4 has two parts: a 17-byte table of contents
(TOC), then the record. Total block length `N = 17 + record length`.

### 3.1 The 17-byte TOC

**INFERRED** (Lua-derived block description in the protocol map). Offsets
are within the block, starting at 0.

| Offset | Length | Content | Value / meaning |
|---|---|---|---|
| 0–3 | 4 | Magic | ASCII `PRFL` |
| 4 | 1 | Number | `01` (semantics of this byte are UNVERIFIED — see §7) |
| 5–8 | 4 | Revision | `00 00 00 01` |
| 9–12 | 4 | Magic | ASCII `DATA` |
| 13–14 | 2 | Offset | `10 00`, i.e. `0x0010` little-endian (semantics UNVERIFIED — see §7) |
| 15–16 | 2 | Data length | 16-bit little-endian value computed by the formula in §3.3 |

### 3.2 The record

The record starts at block offset `0x11` (17 decimal) — the same offset the
`0x84` read requests, which is how the read and write views of the block
agree (**CONFIRMED** for the read offset: request `05 <id> 84 00 00 00 11
30` observed in capture 02 frame 175 and capture 45 frames 157/207).

| Field | Format |
|---|---|
| Name length | 1 byte: length of the player name in bytes |
| Name | The player name, as raw bytes, no terminator documented |
| Icon-stem length | 1 byte: length of the icon filename **without** the `.tga` suffix |
| Icon filename | The full icon filename **including** `.tga` |

Record length is therefore `2 + len(name) + len(icon filename)`.

### 3.3 Data-length formula

The TOC data-length field is **not** the record length and **not** the block
length. It is computed as:

`data length = 2 + len(name) + len(icon filename) − 4`

which equals the record length minus 4. The formula is stated in the
protocol map's block description; what the value counts internally is
UNVERIFIED. Implementations must reproduce the formula exactly as stated
rather than substituting a "more sensible" length.

### 3.4 Read-back verification

**CONFIRMED** (captures 02, 03, 45; Lua `ULUsbDevice.lua:229-255`): the
`0x84` request reads the profile back, and the reply is decoded as a name
length, a name, an icon length, and an icon, at address `0x00000011`. The
protocol map records that captured replies decode to the stored name and
icon for the gun in question. A profile write implementation should verify
itself by issuing the `0x84` read after the lock step and comparing the
decoded name and icon with what was written. Do not hardcode any icon
value: the Efeion Java project hardcoded one icon filename and is wrong —
real replies carry the per-profile icon (see
[13-community-mods.md](13-community-mods.md)).

## 4. Worked example — PLACEHOLDER VALUES ONLY

Everything in this section is a **constructed example**. The name, icon,
and gun id are invented placeholder values, marked EXAMPLE, chosen only to
exercise the format arithmetic. This byte string was **never observed on
the wire** and does not correspond to any real gun or stored profile. Do
not replay it as if it were captured traffic, and do not copy any real
device reference into an implementation from the research files.

EXAMPLE inputs:

- EXAMPLE gun id: `0x02` (placeholder target id)
- EXAMPLE name: `Player` (6 bytes)
- EXAMPLE icon: `lion.tga` (8 bytes, including `.tga`); EXAMPLE icon stem:
  `lion` (4 bytes)

EXAMPLE arithmetic:

- Record length = `2 + 6 + 8 = 16` bytes.
- TOC data length = `2 + 6 + 8 − 4 = 12` (`0x000C` little-endian: `0C 00`).
- Block length `N = 17 + 16 = 33` (`0x21`).
- Chunk leading byte = `3 + 33 = 36` (`0x24`).

EXAMPLE TOC (17 bytes): `50 52 4C 4C 01 00 00 00 01 44 41 54 41 10 00 0C
00`, i.e. `PRFL`, `01`, revision `00 00 00 01`, `DATA`, offset `10 00`,
data length `0C 00`.

EXAMPLE record (16 bytes): `06 50 6C 61 79 65 72 04 6C 69 6F 6E 2E 74 67
61`, i.e. name length `06`, `Player`, stem length `04`, `lion.tga`.

EXAMPLE step-3 write-init: `06 02 82 00 00 00 00 00 21`.

EXAMPLE step-4 chunk (39 bytes: 6 header bytes + 33 block bytes): `24 02
83 00 00 21 50 52 4C 4C 01 00 00 00 01 44 41 54 41 10 00 0C 00 06 50 6C
61 79 65 72 04 6C 69 6F 6E 2E 74 67 61`.

EXAMPLE steps 1, 2, and 5 use the target id `02` in the templates of §2:
unlock `01 02 86 00`, erase `04 02 81 00 00 00 00`, lock `01 02 86 01`.

## 5. The 12 writable avatars

The protocol map states that only 12 icons are writable. The 12 animal
avatars, named in the master document's styling reference, are:

| # | Avatar | # | Avatar |
|---|---|---|---|
| 1 | Bear | 7 | Eagle |
| 2 | Bird | 8 | Lion |
| 3 | Bull | 9 | Panda |
| 4 | Cat | 10 | Puma |
| 5 | Dog | 11 | Shark |
| 6 | Dragon | 12 | Snake |

The profile record stores the icon as a filename including the `.tga`
suffix, with a separate stem-length field excluding the suffix (§3.2). The
filename pattern (lowercase animal name plus `.tga`) is **INFERRED**: only
a few filename forms are directly evidenced in the protocol map's `0x84`
reply decodes, and the full set of 12 filenames was not enumerated in the
sources. Verify the exact filename for any avatar by reading back a gun
known to carry it with the `0x84` request before writing it. "Eliminated"
(`_Out`) avatar variants exist as UI textures for the host display; no
source states that they are writable profile icons — do not write them as
profile icons.

Whether the gun validates the icon against the 12, and what happens if an
unknown filename is written, is UNVERIFIED.

## 6. The echo-acceptance pitfall — read before implementing

**CONFIRMED** echo rule (protocol map merge notes; capture evidence
including game-over echoes in capture 19 frames 5757/5759; live-replay
experience recorded in the master document): every host message is echoed
back as an inbound report. Guns echo host messages with the target id
substituted for the broadcast id. The first report that arrives after a
send is therefore routinely the echo of what was just sent, not a reply
to it. The general rule, established during the FreeFrag live work, is:
never treat the first report after a send as the reply.

The profile write is where this trap bites hardest, because the retry rule
in §2 is phrased as "re-send until a reply carrying the same command
arrives" — and an echo carries the same command too. An implementation
that accepts any same-command report as the acknowledgement for the `0x83`
chunk step will accept the gun's echo of the chunk it just sent and
declare the step complete without any evidence that the gun processed the
write. The correct acceptance criterion is a reply showing the write
offset advanced past the sent chunk — i.e. an acknowledgement that
reflects progress, not a repetition of the sent bytes.

Grade this carefully: the echo rule itself is **CONFIRMED**; the
offset-advanced acceptance criterion for the profile write is **INFERRED**
(it is the preserved trap from the profile-write work, and the exact
acknowledgement bytes are **UNVERIFIED** because no profile write was ever
captured). Do not invent acknowledgement bytes. The settling experiment —
a profile edit under USBPcap, comparing echoes with genuine replies — is
specified in [14-open-questions.md](14-open-questions.md).

Two related traps, both **CONFIRMED** from the Ghidra pass and captures:

- Only the leading bytes of a 64-byte HID report are meaningful; the tail
  is stale buffer residue. Never compare full 64-byte reports for
  equality, and never treat tail bytes as part of a reply.
- A host message can go unanswered (observed for polls in capture 16 and
  in live runs). The 250 ms retry exists for a reason; a single unanswered
  attempt is normal, not an error.

## 7. What is not known

The following are UNVERIFIED and are carried in full in
[14-open-questions.md](14-open-questions.md): the exact acknowledgement
bytes for each step (especially the `0x83` chunk); the semantics of the
TOC number byte and offset field; the maximum name length and allowed
characters; the full set of valid icon filenames and the gun's behaviour
on an invalid one; the erased page size at address 0; what happens if the
sequence is interrupted between erase and lock; whether the lock/unlock
argument values mean anything beyond the two observed values; and how (or
whether) the host derives a team from a profile in the original game —
the icon-prefix team mechanism is documented only in the tkdrob bundle
(see [13-community-mods.md](13-community-mods.md)).

## Sources

- `sniffing-results/protocol_map.json`: `0x83_profile_write`,
  `0x84_profile_request`, `0x81_flash_read`, `0x82_flash_write_header`,
  `0x86_flash`, merge notes (echo rule, stale tail).
- `BATTLE_TAG_MASTER.md` §3 (live replay and echo-rule findings), §5.5
  (12 avatar names).
- `SPEEDUP_RESEARCH_2026-11-10.md` §3 (Lua command table, flash commands,
  `0x84` profile request).
- `GHIDRA_PASS_2026-11-10.md` Q1 (HID packing, stale tail).
