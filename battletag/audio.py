"""UTGameMaster announcer, UI SFX, background music, and gun sound engine."""

from __future__ import annotations

import os
import pathlib
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _default_user_assets_dir() -> pathlib.Path:
    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library" / "Application Support" / "BattlePew" / "assets"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(pathlib.Path.home() / "AppData" / "Local")
        return pathlib.Path(base) / "BattlePew" / "assets"
    xdg = os.environ.get("XDG_DATA_HOME") or str(pathlib.Path.home() / ".local" / "share")
    return pathlib.Path(xdg) / "BattlePew" / "assets"


USER_ASSETS_DIR = _default_user_assets_dir()


def _has_audio_tree(base: pathlib.Path) -> bool:
    ann = base / "game" / "Program Files" / "Ubisoft" / "BattleTag" / "System" / "Locale" / "En" / "Audio" / "GameMaster"
    ui = base / "game" / "Program Files" / "Ubisoft" / "BattleTag" / "Data" / "Audio" / "Ui"
    mus = base / "music-wav"
    return ann.is_dir() and ui.is_dir() and mus.is_dir() and any(ann.glob("*.wav"))


def resolve_handoff_root() -> pathlib.Path:
    env_dir = os.environ.get("BATTLETAG_ASSETS_DIR", "").strip()
    if env_dir:
        p = pathlib.Path(env_dir).expanduser()
        if _has_audio_tree(p):
            return p
    bundled = ROOT / "battle-tag-antigravity-handoff"
    if _has_audio_tree(bundled):
        return bundled
    if _has_audio_tree(USER_ASSETS_DIR):
        return USER_ASSETS_DIR
    return bundled


HANDOFF = resolve_handoff_root()
# Real layout of the extracted game tree (see ASSET_INVENTORY.md): UI sounds
# live under Data/Audio/Ui and the English announcer under
# System/Locale/En/Audio/GameMaster. (An earlier revision pointed these at
# a Data/Sound tree that does not exist, which silently suppressed every
# UI/music cue: play_ui/play_music return None when the file is missing.)
GAME_TREE = HANDOFF / "game" / "Program Files" / "Ubisoft" / "BattleTag"
ANNOUNCER_DIR = GAME_TREE / "System" / "Locale" / "En" / "Audio" / "GameMaster"
UI_SFX_DIR = GAME_TREE / "Data" / "Audio" / "Ui"
MUSIC_DIR = HANDOFF / "music-wav"
GUN_SOUNDS_DIR = HANDOFF / "gun-sounds"


def refresh_audio_paths() -> None:
    """Re-resolve audio directories (e.g. after runtime asset extraction)."""
    global HANDOFF, GAME_TREE, ANNOUNCER_DIR, UI_SFX_DIR, MUSIC_DIR, GUN_SOUNDS_DIR
    HANDOFF = resolve_handoff_root()
    GAME_TREE = HANDOFF / "game" / "Program Files" / "Ubisoft" / "BattleTag"
    ANNOUNCER_DIR = GAME_TREE / "System" / "Locale" / "En" / "Audio" / "GameMaster"
    UI_SFX_DIR = GAME_TREE / "Data" / "Audio" / "Ui"
    MUSIC_DIR = HANDOFF / "music-wav"
    GUN_SOUNDS_DIR = HANDOFF / "gun-sounds"


def resolve_static_assets_dir() -> pathlib.Path:
    """Return directory containing extracted/bundled fonts and textures."""
    bundled_static = ROOT / "battletag" / "static" / "assets"
    if (bundled_static / "fonts" / "UbiTTB.ttf").is_file():
        return bundled_static
    env_dir = os.environ.get("BATTLETAG_ASSETS_DIR", "").strip()
    if env_dir:
        cand = pathlib.Path(env_dir).expanduser() / "static-assets"
        if (cand / "fonts" / "UbiTTB.ttf").is_file():
            return cand
    return USER_ASSETS_DIR / "static-assets"


def audio_assets_ready() -> bool:
    """True when extracted audio, fonts, and textures are all present on disk."""
    static_dir = resolve_static_assets_dir()
    return (
        ANNOUNCER_DIR.is_dir()
        and UI_SFX_DIR.is_dir()
        and MUSIC_DIR.is_dir()
        and any(ANNOUNCER_DIR.glob("*.wav"))
        and ((MUSIC_DIR / "musicmenu.wav").is_file() or (MUSIC_DIR / "musicmenu.ogg").is_file())
        and (static_dir / "fonts" / "UbiTTB.ttf").is_file()
        and (static_dir / "textures" / "Avatars" / "256x" / "Dragon.png").is_file()
    )


@dataclass
class AudioCue:
    """Single audio event emitted by the GameMaster engine."""

    category: str  # "announcer" | "ui" | "music" | "gun"
    url: str
    filename: str
    priority: int = 3
    subtitle: str = ""
    loop: bool = False
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "url": self.url,
            "filename": self.filename,
            "priority": self.priority,
            "subtitle": self.subtitle,
            "loop": self.loop,
            "timestamp": self.timestamp,
            # Frontend-compatible aliases: the Arena Master page reads
            # `sound_file` / `channel` / `volume` (see handleAudioCue). Without
            # these the browser silently drops every cue and no announcer,
            # music, or UI sound ever plays.
            "sound_file": self.url,
            "channel": self.category,
            "volume": 0.9,
        }


# English announcer pools and subtitles (verified against §6.1 of BATTLE_TAG_MASTER.md)
# Announcer pools, mapped to the EXTRACTED files by spoken content.
# Authority: transcripts/announcer_transcripts.json (161 EN files, all
# transcribed). An earlier revision referenced invented names (COMMENT_*,
# FRAG_TEAM_* without the DLG_GM_ prefix, LAST_MAN_*, WESTERN_DUEL_*,
# TAG_SHOOT_*) and GLOBAL_ ranges whose spoken lines did not match the
# pool's intent (e.g. game_end pointed at "You're hit!" lines and the time
# warnings at ammo lines); trigger_pool silently dropped every cue because
# none of those files exist. Real layout: DLG_GM_GLOBAL_01..119 (gaps),
# DLG_GM_FRAG_TEAM_01..08, DLG_GM_FRAG_SURVIVAL_01..12,
# DLG_GM_INIT_01..23, DLG_GM_OW_DUEL_01..09.
# Notes: GLOBAL_11 is transcribed "2-0" but sits in the 10..1 countdown run
# (GLOBAL_03..12) and is the "Two!" callout. GLOBAL_96 says "Race over"
# despite its player-callout numbering — never use it as a callout. There
# are no solo "takes the lead", streak, or per-mode intro lines in the set;
# those pools use praise/excitement lines and the subtitle carries the
# specifics. The FRAG_SURVIVAL count lines ("N players still in game")
# need count-aware selection the pool API cannot do, so LMS eliminations
# use the generic elimination lines instead.
def _g(*nums: int) -> list[str]:
    return [f"DLG_GM_GLOBAL_{n:02d}.wav" for n in nums]


ANNOUNCER_POOLS: dict[str, dict[str, Any]] = {
    "countdown_intro": {
        "files": _g(1, 2, 71),
        "priority": 0,
        "proba": 1.0,
        "subtitle": "Prepare for battle! Countdown starting!",
    },
    "game_start": {
        "files": _g(13, 14, 15),
        "priority": 0,
        "proba": 1.0,
        "subtitle": "Match started — Go! Go! Go!",
    },
    "game_end": {
        "files": _g(50, 51, 52),
        "priority": 0,
        "proba": 1.0,
        "subtitle": "Game Over! Return to UbiConnect.",
    },
    "time_60": {
        "files": _g(45),
        "priority": 1,
        "proba": 1.0,
        "subtitle": "One minute remaining!",
    },
    "time_30": {
        "files": _g(46),
        "priority": 1,
        "proba": 1.0,
        "subtitle": "30 seconds remaining!",
    },
    "time_15": {
        "files": _g(47),
        "priority": 1,
        "proba": 1.0,
        "subtitle": "15 seconds remaining!",
    },
    "hurry_up": {
        "files": _g(48, 49, 85, 86, 87),
        "priority": 1,
        "proba": 0.45,
        "subtitle": "Hurry up! Time is running out!",
    },
    "impatient": {
        "files": [
            "DLG_GM_FRAG_SURVIVAL_07.wav",
            *_g(14, 48, 49, 85, 86, 92),
        ],
        "priority": 2,
        "proba": 1.0,
        "subtitle": "Come on! Hurry up!",
    },
    "lead_change_ffa": {
        "files": _g(26, 27, 28),
        "priority": 2,
        "proba": 0.75,
        "subtitle": "We have a new leader!",
    },
    "lead_red": {
        "files": ["DLG_GM_FRAG_TEAM_01.wav"],
        "priority": 2,
        "proba": 0.9,
        "subtitle": "Red Team takes the lead!",
    },
    "lead_blue": {
        "files": ["DLG_GM_FRAG_TEAM_02.wav"],
        "priority": 2,
        "proba": 0.9,
        "subtitle": "Blue Team takes the lead!",
    },
    "lead_yellow": {
        "files": ["DLG_GM_FRAG_TEAM_03.wav"],
        "priority": 2,
        "proba": 0.9,
        "subtitle": "Yellow Team takes the lead!",
    },
    "lead_green": {
        "files": ["DLG_GM_FRAG_TEAM_04.wav"],
        "priority": 2,
        "proba": 0.9,
        "subtitle": "Green Team takes the lead!",
    },
    "friendly_fire": {
        "files": _g(41, 42, 43, 44),
        "priority": 2,
        "proba": 0.85,
        "subtitle": "Watch your fire! Friendly hit!",
    },
    "hit_scored": {
        "files": _g(25, 26, 27, 28, 88),
        "priority": 4,
        "proba": 0.5,
        "subtitle": "Direct hit!",
    },
    "frag_scored": {
        "files": _g(38, 39),
        "priority": 3,
        "proba": 0.7,
        "subtitle": "Player tagged out!",
    },
    "duel_round_win": {
        "files": _g(119),
        "priority": 3,
        "proba": 1.0,
        "subtitle": "Wins the round!",
    },
    "lap_complete": {
        "files": _g(93, 95),
        "priority": 3,
        "proba": 0.85,
        "subtitle": "Lap complete!",
    },
    "streak_3": {
        "files": _g(26, 27, 28),
        "priority": 3,
        "proba": 0.9,
        "subtitle": "3-hit streak!",
    },
    "streak_5": {
        "files": _g(24, 26, 88),
        "priority": 2,
        "proba": 1.0,
        "subtitle": "Unstoppable! 5-hit streak!",
    },
    "reload_reminder": {
        "files": _g(21, 22, 23, 76),
        "priority": 4,
        "proba": 0.4,
        "subtitle": "Low ammo — scan an Ammo pack!",
    },
    "medkit_used": {
        "files": _g(16, 17, 18, 19, 77),
        "priority": 3,
        "proba": 0.65,
        "subtitle": "Medkit scanned — life restored!",
    },
    "lms_eliminated": {
        "files": _g(38, 39),
        "priority": 1,
        "proba": 1.0,
        "subtitle": "Player eliminated!",
    },
    "duel_prepare": {
        "files": [f"DLG_GM_OW_DUEL_0{i}.wav" for i in (1, 3, 4, 5)],
        "priority": 0,
        "proba": 1.0,
        "subtitle": "Duelists ready... wait for the signal!",
    },
    "duel_draw": {
        "files": ["DLG_GM_OW_DUEL_06.wav", "DLG_GM_OW_DUEL_07.wav"],
        "priority": 0,
        "proba": 1.0,
        "subtitle": "DRAW! Fire now!",
    },
    "duel_cheat": {
        "files": ["DLG_GM_OW_DUEL_08.wav", "DLG_GM_OW_DUEL_09.wav"],
        "priority": 0,
        "proba": 1.0,
        "subtitle": "Fired too early! Round forfeited!",
    },
    "tagnshoot_base": {
        "files": _g(78, 79, 80),
        "priority": 2,
        "proba": 0.85,
        "subtitle": "Base scanned! Now shoot the UbiConnect!",
    },
    "idle_banter": {
        "files": [
            "DLG_GM_GLOBAL_32.wav",
            "DLG_GM_GLOBAL_33.wav",
            "DLG_GM_GLOBAL_34.wav",
            "DLG_GM_GLOBAL_35.wav",
            "DLG_GM_FRAG_SURVIVAL_07.wav",
            "DLG_GM_FRAG_SURVIVAL_08.wav",
            "DLG_GM_FRAG_SURVIVAL_09.wav",
        ],
        "priority": 3,
        "proba": 1.0,
        "subtitle": "Come on, players! Keep moving and fire!",
    },
    "miss_taunt": {
        "files": [
            "DLG_GM_GLOBAL_43.wav",
            "DLG_GM_GLOBAL_44.wav",
            "DLG_GM_GLOBAL_91.wav",
            "DLG_GM_FRAG_SURVIVAL_10.wav",
        ],
        "priority": 3,
        "proba": 1.0,
        "subtitle": "Need some help aiming?",
    },
}

# No per-mode intro lines exist in the extracted audio (no spoken mode
# names anywhere in the 161 EN files), so mode selection stays silent.
# play_mode_intro returns None for every mode; the map stays as the
# documented place to add lines if any are ever sourced.
MODE_INTRO_VOICE: dict[str, tuple[str, str]] = {}


class GameMasterAudio:
    """Manages priority-based announcer callouts, UI sounds, and music tracks."""

    def __init__(self, emit_cb: Callable[[AudioCue], None] | None = None, local_play: bool = False) -> None:
        self.emit_cb = emit_cb
        self.local_play = local_play
        self.current_music: str = "musicmenu.wav"
        self.active_priority: int = 99
        self.active_until: float = 0.0
        self.fired_offsets: set[int] = set()
        self.recent_cues: list[AudioCue] = []

    def reset_match_timers(self) -> None:
        self.fired_offsets.clear()

    def _emit(self, cue: AudioCue) -> AudioCue:
        self.recent_cues.append(cue)
        if len(self.recent_cues) > 30:
            self.recent_cues = self.recent_cues[-30:]
        if self.emit_cb:
            self.emit_cb(cue)
        if self.local_play and not cue.loop:
            self._play_local_nonblocking(cue)
        return cue

    def _play_local_nonblocking(self, cue: AudioCue) -> None:
        file_map = {
            "announcer": ANNOUNCER_DIR / cue.filename,
            "ui": UI_SFX_DIR / cue.filename,
            "music": MUSIC_DIR / cue.filename,
            "gun": GUN_SOUNDS_DIR / cue.filename,
        }
        path = file_map.get(cue.category)
        if path and path.exists():
            try:
                subprocess.Popen(
                    ["afplay", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

    def play_ui(self, filename: str, subtitle: str = "") -> AudioCue | None:
        if not (UI_SFX_DIR / filename).exists():
            return None
        cue = AudioCue(
            category="ui",
            url=f"/audio/ui/{filename}",
            filename=filename,
            priority=1,
            subtitle=subtitle,
        )
        return self._emit(cue)

    def play_music(self, track: str) -> AudioCue | None:
        """Start or switch background music (`musicmenu.wav`, `musicingame.wav`, `musicintroduction.wav`)."""
        if not track.endswith((".wav", ".ogg")):
            track = f"{track}.wav"
        if not (MUSIC_DIR / track).exists():
            alt = track.removesuffix(".wav") + ".ogg" if track.endswith(".wav") else track
            if (MUSIC_DIR / alt).exists():
                track = alt
            else:
                return None
        self.current_music = track
        cue = AudioCue(
            category="music",
            url=f"/audio/music/{track}",
            filename=track,
            priority=0,
            subtitle=f"Music: {track}",
            loop=True,
        )
        return self._emit(cue)

    def play_mode_intro(self, mode_name: str) -> AudioCue | None:
        entry = MODE_INTRO_VOICE.get(mode_name)
        if not entry:
            return None
        wav_name, label = entry
        if not (ANNOUNCER_DIR / wav_name).exists():
            return None
        now = time.time()
        self.active_priority = 0
        self.active_until = now + 2.2
        return self._emit(
            AudioCue(
                category="announcer",
                url=f"/audio/announcer/{wav_name}",
                filename=wav_name,
                priority=0,
                subtitle=f"Mode selected: {label}",
            )
        )

    def play_countdown_number(self, n: int) -> AudioCue | None:
        if not (1 <= n <= 10):
            return None
        # Countdown numbers are GLOBAL_03..12 (10..1) in the extracted set.
        wav_name = f"DLG_GM_GLOBAL_{13 - n:02d}.wav"
        if not (ANNOUNCER_DIR / wav_name).exists():
            return None
        return self._emit(
            AudioCue(
                category="announcer",
                url=f"/audio/announcer/{wav_name}",
                filename=wav_name,
                priority=0,
                subtitle=f"{n}...",
            )
        )

    def play_announcer_file(
        self, wav_name: str, subtitle: str, priority: int = 0, duration: float = 2.0
    ) -> AudioCue | None:
        """Emit an arbitrary announcer `.wav` from `ANNOUNCER_DIR`."""
        if not wav_name.endswith(".wav"):
            wav_name = f"{wav_name}.wav"
        if not (ANNOUNCER_DIR / wav_name).exists():
            return None
        self.active_priority = priority
        self.active_until = time.time() + duration
        return self._emit(
            AudioCue(
                category="announcer",
                url=f"/audio/announcer/{wav_name}",
                filename=wav_name,
                priority=priority,
                subtitle=subtitle,
            )
        )

    def trigger_pool(self, pool_key: str, custom_subtitle: str | None = None, force: bool = False) -> AudioCue | None:
        """Trigger a `UTGameMaster` announcer pool respecting priority 0..4 and `proba`."""
        pool = ANNOUNCER_POOLS.get(pool_key)
        if not pool:
            return None
        priority = int(pool.get("priority", 3))
        proba = float(pool.get("proba", 1.0))
        now = time.time()

        # Priority arbitration: lower integer = higher priority; block equal or
        # lower priority cues while an announcer line is still active.
        if not force and now < self.active_until and priority >= self.active_priority:
            return None

        if not force and proba < 1.0 and random.random() > proba:
            return None

        candidates = [f for f in pool["files"] if (ANNOUNCER_DIR / f).exists()]
        if not candidates:
            return None
        chosen = random.choice(candidates)
        self.active_priority = priority
        self.active_until = now + (2.4 if priority <= 2 else 1.5)
        return self._emit(
            AudioCue(
                category="announcer",
                url=f"/audio/announcer/{chosen}",
                filename=chosen,
                priority=priority,
                subtitle=custom_subtitle or pool.get("subtitle", ""),
            )
        )

    def check_time_remaining(self, remaining_sec: float) -> list[AudioCue]:
        """Fire UTGameMaster negative-offset time warnings (-60, -57, -30, -27, -15, -12, 10..1)."""
        cues: list[AudioCue] = []
        rem_int = int(remaining_sec)
        schedule = [
            (60, "time_60", True),
            (57, "hurry_up", False),
            (30, "time_30", True),
            (27, "hurry_up", False),
            (15, "time_15", True),
            (12, "hurry_up", False),
        ]
        for sec_mark, pool_key, force in schedule:
            if rem_int == sec_mark and sec_mark not in self.fired_offsets:
                self.fired_offsets.add(sec_mark)
                c = self.trigger_pool(pool_key, force=force)
                if c:
                    cues.append(c)

        if 1 <= rem_int <= 10 and rem_int not in self.fired_offsets:
            self.fired_offsets.add(rem_int)
            c = self.play_countdown_number(rem_int)
            if c:
                cues.append(c)
        return cues

