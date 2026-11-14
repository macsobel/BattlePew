"""Byte-exact HID protocol framing, config builders, and status decoders for Battle Tag."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REPORT_LEN = 64

# 12 original Battle Tag animal avatars (1-indexed in UTGame.Ui.Associate.Avatars)
AVATAR_NAMES: list[str] = [
    "Bear",
    "Bird",
    "Bull",
    "Cat",
    "Dog",
    "Dragon",
    "Eagle",
    "Lion",
    "Panda",
    "Puma",
    "Shark",
    "Snake",
]

# Team metadata matching §5.5 of BATTLE_TAG_MASTER.md and UATeamFrag.lua (1=Red, 2=Blue, 3=Yellow, 4=Green)
TEAM_INFO: dict[int, dict[str, str]] = {
    1: {
        "id": "1",
        "name": "Red Team",
        "short": "RED",
        "color": "#D41900",
        "light_bg": "rgba(212, 25, 0, 0.12)",
        "slot_tex": "/static/assets/textures/Ui/Components/UIRedSlot_01.png",
        "emblem_tex": "/static/assets/textures/Ui/Leaderboard_RedTeam.png",
        "leader_tex": "/static/assets/textures/Ui/Leaderboard_RedLeader.png",
        "rfid_code": "RF03",
        "rfid_tex": "/static/assets/textures/Ui/Pictograms/64x/RF03.png",
    },
    2: {
        "id": "2",
        "name": "Blue Team",
        "short": "BLUE",
        "color": "#0767C5",
        "light_bg": "rgba(7, 103, 197, 0.12)",
        "slot_tex": "/static/assets/textures/Ui/Components/UIBlueSlot_01.png",
        "emblem_tex": "/static/assets/textures/Ui/Leaderboard_BlueTeam.png",
        "leader_tex": "/static/assets/textures/Ui/Leaderboard_BlueLeader.png",
        "rfid_code": "RF04",
        "rfid_tex": "/static/assets/textures/Ui/Pictograms/64x/RF04.png",
    },
    3: {
        "id": "3",
        "name": "Yellow Team",
        "short": "YELLOW",
        "color": "#FFB432",
        "light_bg": "rgba(255, 180, 50, 0.16)",
        "slot_tex": "/static/assets/textures/Ui/Components/UIYellowSlot_01.png",
        "emblem_tex": "/static/assets/textures/Ui/Leaderboard_YellowTeam.png",
        "leader_tex": "/static/assets/textures/Ui/Leaderboard_YellowLeader.png",
        "rfid_code": "RF05",
        "rfid_tex": "/static/assets/textures/Ui/Pictograms/64x/RF05.png",
    },
    4: {
        "id": "4",
        "name": "Green Team",
        "short": "GREEN",
        "color": "#00AB03",
        "light_bg": "rgba(0, 171, 3, 0.12)",
        "slot_tex": "/static/assets/textures/Ui/Components/UIGreenSlot_01.png",
        "emblem_tex": "/static/assets/textures/Ui/Leaderboard_GreenTeam.png",
        "leader_tex": "/static/assets/textures/Ui/Leaderboard_GreenLeader.png",
        "rfid_code": "RF06",
        "rfid_tex": "/static/assets/textures/Ui/Pictograms/64x/RF06.png",
    },
}

# Physical RFID cards and T-Bases (RF01-RF09)
RFID_CARDS: dict[str, dict[str, str]] = {
    "RF01": {"code": "RF01", "name": "Ammo Pack 1", "type": "ammo", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF01.png"},
    "RF02": {"code": "RF02", "name": "Ammo Pack 2", "type": "ammo", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF02.png"},
    "RF03": {"code": "RF03", "name": "T-Base 1 (Red)", "type": "base_1", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF03.png"},
    "RF04": {"code": "RF04", "name": "T-Base 2 (Blue)", "type": "base_2", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF04.png"},
    "RF05": {"code": "RF05", "name": "T-Base 3 (Yellow)", "type": "base_3", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF05.png"},
    "RF06": {"code": "RF06", "name": "T-Base 4 (Green)", "type": "base_4", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF06.png"},
    "RF07": {"code": "RF07", "name": "Medkit", "type": "medkit", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF07.png"},
    "RF08": {"code": "RF08", "name": "Finish / Action", "type": "action", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF08.png"},
    "RF09": {"code": "RF09", "name": "Bonus Card", "type": "bonus", "icon": "/static/assets/textures/Ui/Pictograms/64x/RF09.png"},
}


def pad64(raw: bytes) -> bytes:
    """Zero-pad or truncate a payload to a 64-byte USB HID report."""
    if len(raw) > REPORT_LEN:
        raise ValueError(f"Report too long ({len(raw)} > {REPORT_LEN})")
    return raw + bytes(REPORT_LEN - len(raw))


def build_ping_packet() -> bytes:
    """Host keep-alive ping: 00 FF 05."""
    return bytes([0x00, 0xFF, 0x05])


def build_vest_poll_packet() -> bytes:
    """Host vest connection poll: 00 FF C1."""
    return bytes([0x00, 0xFF, 0xC1])


def build_config_packet(
    gun_id: int,
    mode_name: str,
    settings: dict[str, Any],
    team_id: int = 1,
    volume: int = 0,
    beam_power: int = 3,
) -> bytes:
    """Build the 0x92 per-gun game configuration packet.

    Format from UTActivity.State.PlayersSetup.lua:
      [3 + 2*n, gun_id, 0x92, volume, beamPower, n, ...n BE16 words]
    """
    values: list[int] = []
    if mode_name in ("FreeFrag", "OldFashionDuel"):
        values = []
    elif mode_name == "TeamFrag":
        values = [
            int(settings.get("lifePoints", 5)),
            int(settings.get("ammunitions", 9)),
            int(settings.get("clips", 5)),
            int(settings.get("swap", 0)),
            int(settings.get("respawnTime", 0)),
            int(team_id),
            int(settings.get("teamFrag", 0)),
            int(settings.get("assist", 1)),
            int(settings.get("medkit", 0)),
        ]
    elif mode_name == "FreeForAll":
        values = [
            int(settings.get("lifePoints", 5)),
            int(settings.get("ammunitions", 9)),
            int(settings.get("clips", 5)),
            int(settings.get("swap", 0)),
            int(settings.get("respawnTime", 0)),
            int(settings.get("assist", 1)),
            int(settings.get("medkit", 0)),
        ]
    elif mode_name == "LastManStanding":
        values = [
            int(settings.get("lifePoints", 5)),
            int(settings.get("ammunitions", 9)),
            int(settings.get("clips", 5)),
            int(settings.get("swap", 0)),
            int(settings.get("assist", 1)),
            int(settings.get("medkit", 0)),
        ]
    elif mode_name == "StarterFrag":
        values = [
            int(settings.get("assist", 1)),
            int(settings.get("ammunitions", 12)),
            int(settings.get("clips", 3)),
        ]
    elif mode_name == "TagThenShoot":
        values = [
            int(settings.get("numberOfBase", 4)),
            int(settings.get("assist", 1)),
        ]
    else:
        raise ValueError(f"Unsupported mode for 0x92 config: {mode_name}")

    n = len(values)
    header = bytes([3 + 2 * n, gun_id & 0xFF, 0x92, volume & 0xFF, beam_power & 0xFF, n])
    words = b"".join((v & 0xFFFF).to_bytes(2, "big") for v in values)
    return header + words


def build_countdown_packet(gun_id: int, seconds: int = 10) -> bytes:
    """Build 0x93 countdown start command (`01 <id> 93 <seconds>`)."""
    return bytes([0x01, gun_id & 0xFF, 0x93, seconds & 0xFF])


def build_poll_packet(
    gun_id: int,
    mode_name: str = "FreeFrag",
    downlink_word: int | None = None,
) -> bytes:
    """Build 0x95 status poll command.

    - Standard modes (`FreeFrag`, `TeamFrag`, `FreeForAll`, `LastManStanding`, `StarterFrag`):
      `01 <id> 95 00`
    - `OldFashionDuel` / `TagThenShoot`:
      `03 <id> 95 01 <downlink_word BE16>`
    """
    if mode_name in ("OldFashionDuel", "TagThenShoot") or downlink_word is not None:
        val = int(downlink_word or 0) & 0xFFFF
        return bytes([0x03, gun_id & 0xFF, 0x95, 0x01]) + val.to_bytes(2, "big")
    return bytes([0x01, gun_id & 0xFF, 0x95, 0x00])


def build_gameover_packet(
    gun_id: int,
    won: bool,
    mode_name: str = "FreeFrag",
) -> bytes:
    """Build 0x94 game-over packet (`06 <id> 94 01 <colour> <ASCII sound>`).

    - `colour`: `0x01` (green LED = outright winner), `0x02` (red LED = loser or tie).
    - `sound`: `SD07` (`53 44 30 37`) for OldFashionDuel & TagThenShoot, `SD16` (`53 44 31 36`) for all other modes.
    """
    colour = 0x01 if won else 0x02
    sound = b"SD07" if mode_name in ("OldFashionDuel", "TagThenShoot") else b"SD16"
    return bytes([0x06, gun_id & 0xFF, 0x94, 0x01, colour]) + sound


def build_gameover_clear_packet(gun_id: int) -> bytes:
    """Build the 0x94 "clear game-over" packet (`06 <id> 94 00 00 00 00 00 00`).

    Capture 14 (aborted restart): after a game-over (`06 id 94 01 <colour> SD16`)
    the original sends each gun `06 id 94 00 00 00 00 00 00` before starting a new
    upload. Colour 0 releases the winner/loser LED blink so the gun leaves its
    end-of-round state.
    """
    return bytes([0x06, gun_id & 0xFF, 0x94, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])


def decode_0x84_profile(reply: bytes) -> dict[str, Any]:
    """Decode gun's 0x84 EEPROM profile response.

    Supports both the real hardware ASCII format seen in captures/logs:
      `35 <gid> 84 00 00 00 11 30 <name_len> <ASCII name> <team_byte> <avatar.tga> 00 ...`
    and legacy UTF-16BE fallback.
    """
    if len(reply) < 14 or reply[2] != 0x84:
        return {"name": "", "icon_index": 1, "avatar": AVATAR_NAMES[0]}

    # Check real hardware format: reply[6:8] == 0x11 0x30 and reply[8] is name_len (1..20)
    name_len = reply[8]
    if reply[6] == 0x11 and reply[7] == 0x30 and 1 <= name_len <= 20 and 10 + name_len <= len(reply):
        raw_name = reply[9 : 9 + name_len]
        if all(32 <= b < 127 for b in raw_name):
            name = raw_name.decode("ascii", errors="ignore").strip()
            icon_len = reply[9 + name_len]
            matched_avatar: str | None = None
            if 1 <= icon_len <= 20 and 10 + name_len + icon_len <= len(reply):
                stem = reply[10 + name_len : 10 + name_len + icon_len].decode("ascii", errors="ignore").strip()
                if stem.lower().endswith(".tga"):
                    stem = stem[:-4]
                matched_avatar = next(
                    (a for a in AVATAR_NAMES if a.lower() == stem.lower()),
                    None,
                )
            if matched_avatar is None:
                rest_str = reply[10 + name_len :].decode("ascii", errors="ignore").lower()
                matched_avatar = next(
                    (a for a in AVATAR_NAMES if rest_str.startswith(f"{a.lower()}.tga")),
                    None,
                )
            idx = (
                AVATAR_NAMES.index(matched_avatar)
                if matched_avatar
                else ((icon_len - 1) % len(AVATAR_NAMES) if 1 <= icon_len <= 12 else icon_len % len(AVATAR_NAMES))
            )
            return {
                "name": name,
                "icon_index": idx + 1,
                "avatar": matched_avatar or AVATAR_NAMES[idx],
            }

    icon_raw = reply[13]
    idx = (icon_raw - 1) % len(AVATAR_NAMES) if 1 <= icon_raw <= 12 else icon_raw % len(AVATAR_NAMES)
    avatar = AVATAR_NAMES[idx]
    name_bytes = bytes(reply[14 : 3 + reply[0]])
    try:
        name = name_bytes.decode("utf-16-be", errors="replace").rstrip("\x00").strip()
    except Exception:
        name = ""
    return {
        "name": name,
        "icon_index": idx + 1,
        "avatar": avatar,
    }


# Profile flash block (UTGame.Ui.Associate:Initialize()). The original game
# writes the gun's stored profile from the Player Management + button: a
# 17-byte TOC followed by the name/icon record. The record starts at block
# offset 17 (0x11) — exactly the address the 0x84 read requests.
MAX_PROFILE_NAME_LEN = 14  # matches the roster name input; the wire format
# would fit more, but the original UI's own limit is unverified and the
# display truncates at 14 everywhere else.


def profile_icon_filename(avatar: str) -> str:
    """Canonical avatar display name -> stored icon filename (\"Dog\" -> \"dog.tga\")."""
    matched = next((a for a in AVATAR_NAMES if a.lower() == avatar.strip().lower()), None)
    if matched is None:
        raise ValueError(f"Unknown avatar: {avatar!r}")
    return f"{matched.lower()}.tga"


def build_profile_block(name: str, avatar: str) -> bytes:
    """Build the flash block the Associate editor writes to address 0.

    Layout (Lua table order, 1-based indices in the comment):
      TOC (17 bytes): "PRFL", number 0x01, revision 00 00 00 01, "DATA",
        offset 0x0010 (LE), data length LE16.
      Record: name-length byte, name bytes, icon-stem-length byte
        (icon filename WITHOUT ".tga"), then the FULL icon filename
        bytes including ".tga" (the 0x84 reader reads stem-length bytes
        and appends ".tga" itself; the stored tail is redundant).
    Data length = 2 + len(name) + len(icon_filename) - 4.
    """
    clean = name.strip()
    if not clean:
        raise ValueError("Profile name must not be empty.")
    if len(clean) > MAX_PROFILE_NAME_LEN:
        raise ValueError(f"Profile name must be at most {MAX_PROFILE_NAME_LEN} characters.")
    if not all(32 <= ord(ch) < 127 for ch in clean):
        raise ValueError("Profile name must be printable ASCII.")
    icon_file = profile_icon_filename(avatar)
    name_bytes = clean.encode("ascii")
    icon_bytes = icon_file.encode("ascii")
    data_len = 2 + len(name_bytes) + len(icon_bytes) - 4
    toc = bytes(
        [
            0x50, 0x52, 0x46, 0x4C,  # "PRFL"
            0x01,  # number
            0x00, 0x00, 0x00, 0x01,  # revision
            0x44, 0x41, 0x54, 0x41,  # "DATA"
            0x10, 0x00,  # offset 0x0010 (literal; the record sits at 0x11)
            data_len & 0xFF,
            (data_len >> 8) & 0xFF,
        ]
    )
    record = bytes([len(name_bytes)]) + name_bytes + bytes([len(icon_bytes) - 4]) + icon_bytes
    return toc + record


@dataclass
class DecodedStatus:
    """Decoded universal 0x95 gun status report."""

    gun_id: int
    word_count: int
    send_words: list[int]
    hit_ids: list[int]
    rfid_tail: bytes = b""
    # Mode-interpreted convenience fields:
    life_points: int | None = None
    ammo: int | None = None
    clips: int | None = None
    shots_fired: int | None = None
    hits_received: int | None = None
    phase_flag: int | None = None


def decode_0x95_status(reply: bytes, mode_name: str = "FreeFrag") -> DecodedStatus | None:
    """Decode a 0x95 status response across all 7 Tier 1 modes.

    Universal layout verified across captures & Lua bytecode:
      reply[0] = payload size (6 + 2 * num_send_vars: 0x0A for 2 vars, 0x0C for 3 vars, 0x0E for 4 vars)
      reply[1] = gun_id
      reply[2] = 0x95
      reply[3] = var_bytes_plus_one (2 * num_send_vars + 1: 0x05 for 2 vars, 0x07 for 3 vars, 0x09 for 4 vars)
      words[0..num_send_vars-1] = BE16 at reply[4 + 2*i : 6 + 2*i]
      hit_ids[0..4] = 5 bytes at reply[4 + 2*num_send_vars : 9 + 2*num_send_vars]
    """
    if len(reply) < 13 or reply[2] != 0x95:
        return None
    size_byte = reply[0]
    word_count = reply[3]
    if size_byte >= 8:
        num_vars = (size_byte - 6) // 2
    elif word_count >= 5:
        num_vars = (word_count - 1) // 2
    else:
        return None
    min_len = 9 + 2 * num_vars
    if len(reply) < min_len:
        return None

    words = [
        int.from_bytes(reply[4 + 2 * i : 6 + 2 * i], "big")
        for i in range(num_vars)
    ]
    hit_start = 4 + 2 * num_vars
    hit_ids = list(reply[hit_start : hit_start + 5])
    rfid_tail = bytes(reply[hit_start + 5 : 3 + reply[0]])

    status = DecodedStatus(
        gun_id=reply[1],
        word_count=word_count,
        send_words=words,
        hit_ids=hit_ids,
        rfid_tail=rfid_tail,
    )

    if mode_name == "FreeFrag" and len(words) >= 2:
        status.hits_received = words[0]
        status.shots_fired = words[1]
    elif mode_name == "TeamFrag":
        if len(words) >= 4:
            status.life_points = words[0]
            status.ammo = words[1]
            status.clips = words[2]
            status.shots_fired = words[3]
        elif len(words) == 3:
            status.life_points = words[0]
            status.ammo = words[1]
            status.clips = words[2]
    elif mode_name in ("FreeForAll", "LastManStanding") and len(words) >= 3:
        status.life_points = words[0]
        status.ammo = words[1]
        status.clips = words[2]
    elif mode_name == "StarterFrag" and len(words) >= 3:
        status.hits_received = words[0]
        status.ammo = words[1]
        status.clips = words[2]
    elif mode_name == "OldFashionDuel" and len(words) >= 2:
        status.ammo = words[0]
        status.hits_received = words[1]
    elif mode_name == "TagThenShoot" and len(words) >= 2:
        status.hits_received = words[0]  # In TagThenShoot word[0] is hits scored on base / tags
        status.phase_flag = words[1]

    return status


def count_new_shooters(prev_slots: list[int] | None, curr_slots: list[int], delta_hits: int) -> list[int]:
    """Extract the shooter IDs responsible for `delta_hits` new hits from the 5-slot shift register.

    In the gun firmware, each incoming IR hit shifts `hit_ids[0..4]` right by 1 and writes
    the shooter's `gun_id` into `curr_slots[0]`.
    """
    if delta_hits <= 0:
        return []
    count = min(delta_hits, len(curr_slots))
    shooters = [gid for gid in curr_slots[:count] if gid not in (0x00, 0xFF)]
    return shooters

