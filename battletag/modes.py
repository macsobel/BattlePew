"""Game Mode definitions, scoring rules, and ArenaEngine state machine."""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from battletag.audio import AudioCue, GameMasterAudio, audio_assets_ready
from battletag.hardware import RealHIDTransport, SimulatorTransport, detect_ubiconnect_readonly
from battletag.protocol import (
    AVATAR_NAMES,
    DecodedStatus,
    RFID_CARDS,
    TEAM_INFO,
    count_new_shooters,
)

# Original Main.loc kill-feed templates (ingame001-021)
KILL_VERBS = [
    "tagged",
    "blasted",
    "zapped",
    "ambushed",
    "outgunned",
    "sniped",
    "locked onto",
]

FRAG_VERBS = [
    "eliminated",
    "knocked out",
    "fragged",
    "neutralized",
    "dominated",
]

# All 7 Tier 1 modes in implementation order: FreeFrag -> TeamFrag -> rest
MODE_CATALOG: dict[str, dict[str, Any]] = {
    "FreeFrag": {
        "id": "FreeFrag",
        "title": "Free Frag",
        "subtitle": "Instant Action · Infinite Ammo · No Reloads",
        "tier": 1,
        "badge": "Arcade Action",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": False,
        "accessories": [],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/FreeFrag.png",
        "banner": "/static/assets/textures/Packages/FreeFrag/Ui/Loading_FreeFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_FreeFrag.png",
        "description": "Pure laser tag action with zero setup cards required. Infinite life and infinite ammo — every hit landed scores +1 point. Highest score when the timer hits 0:00 wins!",
        "bullets": [
            "Infinite life & infinite ammo",
            "No setup boxes required",
            "+1 pt per hit · Highest score wins",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "scoreLimit": 0,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "scoreLimit", "label": "Hit Limit (0 = Timed)", "choices": [0, 10, 20, 30, 50]},
        ],
    },
    "TeamFrag": {
        "id": "TeamFrag",
        "title": "Team Frag",
        "subtitle": "2–4 Teams · Life Points · T-Base Respawn",
        "tier": 1,
        "badge": "Team Battle",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": True,
        "needs_accessories": True,
        "accessories": ["ammo", "tbase", "medkit"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/TeamFrag.png",
        "banner": "/static/assets/textures/Packages/TeamFrag/Ui/Loading_TeamFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_TeamFrag.png",
        "description": "Squad up into Red, Blue, Yellow, or Green teams! Every hit scores +1 point and depletes enemy Life Points; knocking a player to 0 HP scores a +3 Frag bonus. Eliminated players scan their team's T-Base (RF03–RF06) to respawn!",
        "bullets": [
            "Red, Blue, Yellow & Green teams",
            "+1 pt per hit · +3 pt Frag bonus",
            "Scan team T-Base to respawn",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammunitions": 9,
            "clips": 5,
            "swap": 0,
            "respawnTime": 0,
            "teamFrag": 0,
            "assist": 1,
            "medkit": 1,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Life Points", "choices": [3, 5, 7, 9]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 3, 5, 6]},
            {"key": "teamFrag", "label": "Friendly Fire", "choices": [0, 1]},
            {"key": "medkit", "label": "Medkit Boxes", "choices": [0, 1]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
            {"key": "respawnTime", "label": "Respawn Delay", "choices": [0, 3, 5, 10]},
        ],
    },
    "FreeForAll": {
        "id": "FreeForAll",
        "title": "Free For All",
        "subtitle": "Every Player for Themselves · Life + Clips + Respawn",
        "tier": 1,
        "badge": "Free For All",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["ammo", "tbase", "medkit"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/FreeForAll.png",
        "banner": "/static/assets/textures/Packages/FreeForAll/Ui/Loading_FreeForAll.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_FreeForAll.png",
        "description": "Every player for themselves! 2–8 players fight solo with full Life Points, Ammo clips, and T-Base respawn. Score +1 per hit landed and +3 bonus per Frag.",
        "bullets": [
            "Every player for themselves",
            "+1 pt per hit · +3 pt Frag bonus",
            "Limited life & ammo · T-Base respawn",
        ],
        "defaults": {
            "durationSeconds": 180,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammunitions": 9,
            "clips": 3,
            "swap": 0,
            "respawnTime": 0,
            "assist": 1,
            "medkit": 1,
        },
        "options": [
            {"key": "durationSeconds", "label": "Match Duration", "choices": [120, 180, 300, 600, 900, 1200]},
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Life Points", "choices": [3, 5, 7, 9]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 2, 3, 4, 5, 6]},
            {"key": "medkit", "label": "Medkit Boxes", "choices": [0, 1]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
            {"key": "respawnTime", "label": "Respawn Delay", "choices": [0, 3, 5, 10]},
        ],
    },
    "LastManStanding": {
        "id": "LastManStanding",
        "title": "Last Man Standing",
        "subtitle": "Survival Elimination · No Respawns · Final Survivor Wins",
        "tier": 1,
        "badge": "Elimination",
        # The original has no timer: the match ends when one player is
        # left alive. No duration setting exists for this mode.
        "timed": False,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": False,
        "accessories": ["ammo"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/LastManStanding.png",
        "banner": "/static/assets/textures/Packages/LastManStanding/Ui/Loading_LastManStanding.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_LastManStanding.png",
        "description": "High-stakes survival! Players have limited Life Points and NO T-Base respawns. Once your HP hits 0, you are eliminated — the last player standing takes victory!",
        "bullets": [
            "Limited Life Points & ammo",
            "No T-Base respawns",
            "Last player standing wins",
        ],
        "defaults": {
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "lifePoints": 5,
            "ammunitions": 9,
            "clips": 3,
            "swap": 0,
            "assist": 1,
            "medkit": 0,
        },
        "options": [
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "lifePoints", "label": "Starting Lives", "choices": [3, 5, 7, 9]},
            {"key": "ammunitions", "label": "Shots per Clip", "choices": [6, 9, 12, 20]},
            {"key": "clips", "label": "Clips (magazines)", "choices": [1, 2, 3, 4, 5]},
            {"key": "swap", "label": "Swap Boxes", "choices": [0, 1]},
        ],
    },
    "OldFashionDuel": {
        "id": "OldFashionDuel",
        "title": "Wild West Duel",
        "subtitle": "2-Player Showdown · 5 Rounds · 6 Bullets per Round",
        "tier": 1,
        "badge": "Quickdraw",
        # The original runs purely on rounds with no timer at all.
        "timed": False,
        "min_players": 2,
        "max_players": 2,
        "teams": False,
        "needs_accessories": False,
        "accessories": [],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/OldFashionDuel.png",
        "banner": "/static/assets/textures/Packages/OldFashionDuel/Ui/Loading_OldFashionDuel.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_OldFashionDuel.png",
        "description": "Classic 2-player showdown across 3, 5, or 7 tense rounds! Player 1 pulls the trigger to start the match. Point your T-Blaster down during the 5–8s standoff; when the announcer yells FIRE, shoot first — you have 6 bullets per round. Firing before the signal gives the round point to your opponent!",
        "bullets": [
            "1v1 quickdraw · Player 1 pulls trigger to start",
            "6 bullets per round · First hit after FIRE wins +1 pt",
            "Firing before FIRE awards the point to your opponent",
        ],
        "defaults": {
            "countdownSeconds": 5,
            "rounds": 5,
        },
        "options": [
            {"key": "rounds", "label": "Number of Rounds", "choices": [3, 5, 7]},
        ],
    },
    "StarterFrag": {
        "id": "StarterFrag",
        "title": "Starter Frag",
        "subtitle": "Infinite Life · Reloads with Ammo Cards",
        "tier": 1,
        "badge": "Card Reload",
        "timed": True,
        "min_players": 2,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["ammo"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/StarterFrag.png",
        "banner": "/static/assets/textures/Packages/StarterFrag/Ui/Loading_StarterFrag.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_StarterFrag.png",
        "description": "Bridges Free Frag and full tactical modes: players have infinite Life Points, but limited ammunition that must be reloaded by scanning physical Ammo Boxes!",
        "bullets": [
            "Infinite Life Points",
            "Limited ammo · Scan Ammo Box to reload",
            "+1 pt per hit · Highest score wins",
        ],
        # The original fixes all three settings (playtime 5 min, ammo
        # 12, clips 3) with no UI options; only the launch mode varies.
        "defaults": {
            "durationSeconds": 300,
            "countdownSeconds": 10,
            "gameLaunch": 0,
            "ammunitions": 12,
            "clips": 3,
            "assist": 1,
        },
        "options": [
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
        ],
    },
    "TagThenShoot": {
        "id": "TagThenShoot",
        "title": "Tag'n Shoot",
        "subtitle": "Timed turns: run the course, beat the clock",
        "tier": 1,
        "badge": "Solo Course",
        "timed": True,
        "min_players": 1,
        "max_players": 8,
        "teams": False,
        "needs_accessories": True,
        "accessories": ["tbase"],
        "icon": "/static/assets/textures/Ui/Pictograms/96x/TagNShoot.png",
        "banner": "/static/assets/textures/Packages/TagThenShoot/Ui/Loading_TagNShoot.png",
        "ranking_bg": "/static/assets/textures/Ui/Ranking_Bg_TagNShoot.png",
        "description": "Solo timed turns! Each player runs the course in turn — pull the trigger to start (Manual) or wait for the countdown (Auto) — scans the T-Bases in order, and shoots the UbiConnect tower after each one. Best finished time wins across the rounds; points still count if the 60-second cap runs out!",
        "bullets": [
            "Each player runs the course solo, in turn",
            "Best finished time wins (60 s cap per run)",
            "Every racer runs each round, in race order",
        ],
        "defaults": {
            "durationSeconds": 60,
            "countdownSeconds": 3,
            "gameLaunch": 1,
            "numberOfBase": 4,
            "rounds": 1,
            "assist": 1,
        },
        "options": [
            # Fixed 60 s in the original: no duration option. Bases are
            # 2, or 4 with the medkit pack. Rounds are full cycles: every
            # racer runs once per round; best finished time wins.
            {"key": "gameLaunch", "label": "Game Launch", "choices": [0, 1]},
            {"key": "numberOfBase", "label": "T-Bases Required", "choices": [2, 4]},
            {"key": "rounds", "label": "Number of Rounds", "choices": [1, 2, 3]},
        ],
    },
}

# Tier 2 Modes (§8) listed for roadmap visibility after Tier 1
TIER2_PREVIEW: list[dict[str, str]] = [
    {"id": "Survivor", "title": "Survivor (Infection)", "origin": "Cut Ubisoft Mode (UASurvivor.lua)", "summary": "Asymmetric zombie/survivor infection where tagged survivors switch teams."},
    {"id": "TakeAndHold", "title": "Take & Hold (King of the Hill)", "origin": "Cut Ubisoft Mode (UATakeAndHold.lua)", "summary": "Scan the central T-Base to claim it; holding team ticks +1 pt/sec."},
    {"id": "CaptureTheFlag", "title": "Capture The Flag", "origin": "Cut Ubisoft Mode", "summary": "Scan enemy T-Base to steal flag and return to your home T-Base."},
    {"id": "VIP", "title": "VIP Escort", "origin": "Host Rule Mode", "summary": "One player per squad has 1 HP and no ammo; VIP tags count 3×."},
    {"id": "Juggernaut", "title": "Juggernaut (1 vs All)", "origin": "Host Rule Mode", "summary": "One boss with 20 HP vs hunters with 3 HP; killer becomes the new Juggernaut."},
    {"id": "Vampire", "title": "Vampire / Life Drain", "origin": "Host Rule Mode", "summary": "Health ticks down every 15s unless you land hits on opponents."},
]


@dataclass
class PlayerState:
    """Live state of a player/gun in the arena."""

    gun_num: int
    gun_id: int
    ref_hex: str
    name: str
    avatar: str
    team_id: int = 1
    paired: bool = True
    vest_ok: bool = True
    # False once the T-Blaster itself stops answering (powered off / out of
    # RF range) - distinct from vest_ok, which is the vest reporting in.
    gun_online: bool = True
    # Match stats
    score: int = 0
    hits_landed: int = 0
    hits_received: int = 0
    frags: int = 0
    deaths: int = 0
    # TagThenShoot splits its score into two disjoint channels: T-Base
    # tags (status word 0) and UbiConnect tower hits (0xC3 reports).
    # `frags` still mirrors the tower-hit total in that mode because the
    # poll downlink sends the running tower-hit count from it.
    tags: int = 0
    tower_hits: int = 0
    # Timed TagThenShoot turns, one entry per completed-or-forfeited
    # run: {"round": 1-based round, "time": seconds | None,
    # "completed": bool, "points": int, "tags": int, "tower_hits": int}.
    # `tags`/`tower_hits` above are the CURRENT run's live counters
    # (reset at each run start); `score` is the TOTAL across runs.
    # Ranking, winner LEDs, and every display derive best time and
    # totals from this history — never from a single-run field.
    run_history: list[dict[str, Any]] = field(default_factory=list)
    shots_fired: int = 0
    streak: int = 0
    best_streak: int = 0
    life_points: int = 5
    max_life: int = 5
    ammo: int = 9
    max_ammo: int = 9
    clips: int = 5
    max_clips: int = 5
    status_badge: str = "READY"  # READY | ACTIVE | OUT (SCAN BASE) | ELIMINATED | WINNER
    last_tagged_by: str = ""
    last_tagged_by_time: float = 0.0
    last_tagged_target: str = ""
    last_rfid: str = ""
    # Last time a 0x95 status report arrived from this gun (0 = none yet
    # this match). The match screen flags a gun whose reports stop
    # arriving mid-match as having lost signal.
    last_status_ts: float = 0.0
    # Last time ANY report arrived from this gun: a 0x95 poll reply, a
    # C1 vest reply, or the pairing handshake. Drives the lobby
    # quiet-timeout; unlike last_status_ts it is not reset per match.
    last_seen_ts: float = 0.0
    # Internal baseline for diffing 0x95 packets
    baseline_set: bool = False
    prev_hits_rec: int = 0
    prev_shots: int = 0
    prev_life: int = 5
    prev_ammo: int = 9
    prev_clips: int = 5
    prev_phase_flag: int = 1
    prev_slots: list[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])
    miss_streak: int = 0
    hits_dealt_to: dict[int, int] = field(default_factory=dict)
    hits_taken_from: dict[int, int] = field(default_factory=dict)
    _empty_ammo_warned: bool = False
    _last_empty_warn_ts: float = 0.0
    # True once the low-ammo ("Reload!") line has fired for the current
    # clip; cleared when ammo rises above 3 so it fires once per descent.
    _low_ammo_warned: bool = False

    @property
    def display_name(self) -> str:
        return (self.name or "").upper()

    @property
    def accuracy(self) -> float:
        if self.shots_fired <= 0:
            return 0.0
        return round(min(100.0, (self.hits_landed / self.shots_fired) * 100.0), 1)

    @property
    def best_run(self) -> dict[str, Any] | None:
        """The fastest finished run in run_history, or None (no finish)."""
        finished = [r for r in self.run_history if r.get("completed") and r.get("time") is not None]
        if not finished:
            return None
        return min(finished, key=lambda r: r["time"])

    @property
    def best_run_time(self) -> float | None:
        best = self.best_run
        return None if best is None else float(best["time"])

    @property
    def best_single_run_points(self) -> int:
        """Most points scored in any single recorded run (0 if none)."""
        return max((int(r.get("points", 0)) for r in self.run_history), default=0)

    def run_for_round(self, round_index: int) -> dict[str, Any] | None:
        """The recorded run for 1-based `round_index`, or None."""
        for r in self.run_history:
            if int(r.get("round", 0)) == round_index:
                return r
        return None

    def to_dict(self) -> dict[str, Any]:
        team = TEAM_INFO.get(self.team_id, TEAM_INFO[1])
        return {
            "gun_num": self.gun_num,
            "gun_id": self.gun_id,
            "ref_hex": self.ref_hex,
            "name": self.name,
            "display_name": self.display_name,
            "avatar": self.avatar,
            "avatar_url": f"/static/assets/textures/Avatars/256x/{self.avatar}.png",
            "avatar_80_url": f"/static/assets/textures/Avatars/80x/{self.avatar}.png",
            "avatar_out_url": f"/static/assets/textures/Avatars/80x/{self.avatar}_Out.png",
            "team_id": self.team_id,
            "team_name": team["name"],
            "team_short": team["short"],
            "team_color": team["color"],
            "team_slot_tex": team["slot_tex"],
            "paired": self.paired,
            "vest_ok": self.vest_ok,
            "gun_online": self.gun_online,
            "score": self.score,
            "hits_landed": self.hits_landed,
            "hits_received": self.hits_received,
            "frags": self.frags,
            "deaths": self.deaths,
            "tags": self.tags,
            "tower_hits": self.tower_hits,
            "run_history": [dict(r) for r in self.run_history],
            "best_run_time": self.best_run_time,
            "best_run": dict(self.best_run) if self.best_run is not None else None,
            "shots_fired": self.shots_fired,
            "accuracy": self.accuracy,
            "streak": self.streak,
            "best_streak": self.best_streak,
            "life_points": self.life_points,
            "max_life": self.max_life,
            "ammo": self.ammo,
            "max_ammo": self.max_ammo,
            "clips": self.clips,
            "max_clips": self.max_clips,
            "status_badge": self.status_badge,
            "last_tagged_by": self.last_tagged_by,
            "last_tagged_by_ago": round(time.time() - self.last_tagged_by_time, 1) if self.last_tagged_by_time else None,
            "last_tagged_target": self.last_tagged_target,
            "last_rfid": self.last_rfid,
            "hits_dealt_to": {str(k): v for k, v in self.hits_dealt_to.items()},
            "hits_taken_from": {str(k): v for k, v in self.hits_taken_from.items()},
            # True once a gun that answered this match goes quiet for
            # SIGNAL_LOST_TIMEOUT_S (the poll loop answers every cycle,
            # so 3 s is ~60 missed polls on hardware). Only meaningful
            # mid-match; the match screen gates the warning to the
            # PLAYING phase.
            "signal_lost": self.last_status_ts > 0 and (time.time() - self.last_status_ts) > SIGNAL_LOST_TIMEOUT_S,
        }


@dataclass
class EventEntry:
    """Kill-feed / arena event entry matching the E3 split-screen Events log."""

    id: int
    timestamp: float
    match_clock: str
    kind: str  # "hit" | "frag" | "respawn" | "medkit" | "ammo" | "lead" | "system" | "duel"
    text: str
    actor_name: str = ""
    actor_team: int = 1
    actor_gun_num: int = 0  # player number of the actor (0 = none/system)
    target_name: str = ""
    target_team: int = 2
    target_gun_num: int = 0  # player number of the target (0 = none/system)
    icon_url: str = "/static/assets/textures/Ui/Pictograms/64x/Hud_1.png"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "match_clock": self.match_clock,
            "kind": self.kind,
            "text": self.text,
            "actor_name": self.actor_name,
            "actor_team": self.actor_team,
            "actor_gun_num": self.actor_gun_num,
            "target_name": self.target_name,
            "target_team": self.target_team,
            "target_gun_num": self.target_gun_num,
            "icon_url": self.icon_url,
        }


# Global settings: apply to every mode until changed (see REPO_REVIEW §8).
# beam_power and gun_volume are read at 0x92 config time — the original reads
# gun volume from global game settings, and beam power describes the venue,
# not the mode. The audio volumes use the original 0-8 scale and are applied
# by the browser when it plays each cue.
# gun_volume uses the original's inverted scale: 16 = Off, 4 = Low, 1 = Medium,
# 0 = High (UTGame.Ui.Settings.Sound.lua).
DEFAULT_GLOBAL_SETTINGS: dict[str, int] = {
    "beam_power": 3,
    "gun_volume": 0,
    "music_volume": 5,
    "sfx_volume": 2,
    "announcer_volume": 5,
}

_GLOBAL_SETTING_RANGES: dict[str, tuple[int, int]] = {
    "beam_power": (1, 5),
    "gun_volume": (0, 16),
    "music_volume": (0, 8),
    "sfx_volume": (0, 8),
    "announcer_volume": (0, 8),
}

def get_user_data_dir(app_name: str = "BattlePew") -> Path:
    """Return the platform-appropriate user application data/config directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / app_name
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / app_name


DEFAULT_GLOBAL_SETTINGS_PATH: Path = get_user_data_dir() / "global_settings.json"


# Consecutive silent C1 sweeps before a gun counts as vest-less in the lobby.
# One missed reply is normal RF behavior (a poll can go unanswered and be
# answered on the next cycle), so a single silence never flips the display.
VEST_SILENT_SWEEPS_TO_DISCONNECT = 2
# During a live match, guns are busy streaming 0x95/0x97 traffic and players
# move around, so transient C1 00 or missed C1 replies are much more common.
# Require 4 consecutive misses (~20 s at 5 s sweeps) before warning mid-game.
VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT = 4
# A gun that was acked never re-announces on its own, so dropping a gun that
# merely hit an RF lapse would strand it until a power cycle. Only a long
# lobby silence (~20 s of 2 s sweeps) is treated as "powered off"; before that
# the slot is kept and shown as offline. Never removed outside the LOBBY.
# This sweep-count removal is the backstop behind the 4 s quiet-timeout
# (LOBBY_QUIET_TIMEOUT_S), which normally fires first.
LOBBY_SILENT_SWEEPS_TO_REMOVE = 10
# Lobby quiet-timeout: the original's fast Pinger unregisters a gun after
# ~4 s of silence in setup (ping after 2 s, unregister after 4 s). The
# lobby C1 sweep runs every 2 s, so a live gun is heard from at least
# every ~2.5 s and 4 s leaves a full missed sweep of margin. Guns that
# hit this timeout drop from the roster with a "went quiet" notice; the
# sweep-count removal above stays as the backstop.
LOBBY_QUIET_TIMEOUT_S = 4.0
# Mid-match silence before a gun counts as not answering: polls run every
# cycle (~0.3 s per gun on hardware), so 10 s is ~30 missed polls.
MATCH_DISCONNECT_TIMEOUT_S = 10.0
# A gun that answered this match and then goes quiet for this long is
# flagged signal_lost on the scoreboard.
SIGNAL_LOST_TIMEOUT_S = 3.0
# Duel: seconds after the "Fire!" cue starts before shots count as legal
# (covers browser audio start latency and the gun's poll interval).
DUEL_FIRE_GRACE_S = 0.35


class ArenaEngine:
    """Central Battle Tag match coordinator and state machine."""

    def __init__(
        self,
        notify_cb: Callable[[dict[str, Any]], None] | None = None,
        settings_path: Path | None = DEFAULT_GLOBAL_SETTINGS_PATH,
    ) -> None:
        self.notify_cb = notify_cb
        self._settings_path = settings_path
        self.use_simulator: bool = True
        self.hardware_write_confirmed: bool = False
        self.sim = SimulatorTransport(num_guns=2)
        self.hw: RealHIDTransport | None = None
        self.audio = GameMasterAudio(emit_cb=self._on_audio_cue, local_play=False)

        self.mode_name: str = "FreeFrag"
        self.settings: dict[str, Any] = dict(MODE_CATALOG["FreeFrag"]["defaults"])
        self.global_settings: dict[str, int] = self._load_global_settings()
        self.phase: str = "LOBBY"  # LOBBY | PAIRING | VEST_CHECK | UPLOADING | COUNTDOWN | PLAYING | GAME_OVER
        self.bytecode_ready: bool = False
        self.auto_pair_active: bool = True
        # The Arena Master wizard step the main screen is showing (1 = Pair
        # Guns, 2 = Mode & Settings), reported by the frontend. Names and
        # avatars are editable only on step 1 — the pairing page — from
        # either interface, mirroring the original's Player Management
        # editor; going back to step 1 re-opens editing.
        self.wizard_step: int = 1
        # Per-gun profile-write progress for the roster/HUD: gun_num ->
        # {"state": queued|writing|saved|failed, "detail": str}. Updated by
        # the profile-write worker; the UIs render from the snapshot.
        self.profile_write_status: dict[int, dict[str, Any]] = {}
        self.upload_progress: dict[str, int] = {"current": 0, "total": 3}
        self.last_upload_failed_guns: list[int] = []
        # Gun ids whose 0x92 config was never acknowledged after a
        # successful bytecode upload. The host retries just the config
        # for these guns, or removes one and continues with the rest —
        # a single silent gun never nukes the whole launch.
        self.config_failed_guns: list[int] = []
        # True once the current mode's bytecode upload succeeded for the
        # current roster, even if 0x92 config is still pending for some
        # guns. Cleared whenever the roster changes.
        self.bytecode_uploaded: bool = False
        # Ref -> player number remembered across quiet-drops, host
        # Removes, and UbiConnect-loss re-pairing. Shared with the
        # hardware transport so the memory survives transport
        # recreation; see RealHIDTransport.choose_player_number.
        self.ref_number_memory: dict[str, int] = {}
        # Refs the host removed on the pairing page. Shared with the
        # hardware transport for the same reason: a Remove survives a
        # UbiConnect replug; only a fresh pairing scan clears it.
        self.ignored_refs: set[str] = set()
        # Pairing/liveness notes for the pairing page ("went quiet",
        # host Removes, UbiConnect transitions). Separate from the
        # match feed: lobby happenings stay visible in setup without
        # leaking into the next match's kill-feed.
        self.lobby_notices: list[dict[str, Any]] = []
        # Gun numbers whose name/avatar the user explicitly set (roster or
        # phone HUD). Only explicit choices override the gun's stored profile
        # when hardware re-pairs; simulator defaults never win over it.
        self._explicit_profiles: set[int] = set()

        self.players: dict[int, PlayerState] = {}
        self.events: list[EventEntry] = []
        self._event_seq: int = 0
        # Consecutive C1 sweeps each gun id has stayed silent for. Used by
        # apply_vest_states so a single dropped reply never marks a vest
        # disconnected; reset whenever the gun answers a sweep.
        self._vest_miss_counts: dict[int, int] = {}
        self.match_start_ts: float = 0.0
        # Set when a match ends so the elapsed clock on timerless modes
        # freezes at the final time instead of keep counting in GAME_OVER.
        self.match_end_ts: float = 0.0
        self.match_duration_s: int = int(self.settings.get("durationSeconds", 300))
        self.countdown_remaining: int = 0
        self.current_leader_key: str = ""
        self.duel_state: dict[str, Any] = {
            "round": 0,
            "max_rounds": 5,
            "phase_code": 2,  # 2 = between rounds (reload 6 bullets), 0/1 = active standoff/draw
            "subphase": "INTRO",  # INTRO | WAIT | DRAW | ROUND_END | WAIT_TRIGGER | COMPLETE
            "can_shoot": False,
            "next_transition_at": 0.0,
            "round_scores": {},
            # Per-round results, oldest first: {"round": n,
            # "winner_gun_num": g | None, "fault_gun_num": g | None}.
            "round_results": [],
            "last_result_text": "",
            "last_winner_gun_num": None,
            "trigger_armed": False,
        }
        self._duel_round_triggered: bool = False
        # Deferred actions the match loop fires when their time comes:
        # sequenced announcer followups (a second line that must start
        # after the first finishes) and the duel can_shoot flip, which
        # the original sets on completion of the DRAW line. Each entry
        # is {"fire_at": float, "kind": str, ...}; see
        # _schedule_audio_followup / tick_pending_audio. The browser
        # reports no playback-completion events, so every delay here is
        # the host's modeled line length, not a true completion callback.
        self._pending_audio: list[dict[str, Any]] = []
        self.expected_hardware_guns: int = 2
        self.match_history: list[dict[str, Any]] = []
        self.score_history: list[dict[str, Any]] = []
        self.last_action_ts: float = 0.0
        self._last_idle_banter_ts: float = 0.0
        self._last_miss_taunt_ts: float = 0.0
        self._last_reload_reminder_ts: float = 0.0
        self._manual_launch_triggered: bool = False
        # Timed TagThenShoot turns: set when the player whose turn it is
        # pulls the trigger (API endpoint in the simulator, hardware
        # trigger poll on real guns); consumed by tick_race_turns when
        # the wait releases. Separate from _manual_launch_triggered,
        # which releases the match-level wait other modes use.
        self._turn_triggered: bool = False
        # Timed-turn machine state (TagThenShoot only; {} otherwise).
        # Shape is built by reset_stats_for_new_match; see
        # tick_race_turns for the ROUND_INTRO -> WAIT_TRIGGER/COUNTDOWN
        # -> RUNNING -> TURN_END -> DONE machine, looped per round.
        self.race_state: dict[str, Any] = {}
        # Race roster/order, editable only in setup (LOBBY) for
        # TagThenShoot: gun numbers in race order + benched gun
        # numbers. Default: every paired gun, gun-number order, all
        # in. New guns append to the end of the order; removed guns
        # drop out of both lists (sync_race_roster). Frozen into
        # race_state.turn_order at match start; benching never
        # unpairs a gun.
        self.race_roster: dict[str, list[int]] = {"order": [], "bench": []}
        # Best finished run per (gun id, base count) across matches in
        # this server run: {(gun_id, bases): {"time": seconds,
        # "name": display name}}. Keyed by bases so a 2-base time can
        # never overwrite a 4-base best. In-memory only.
        self.race_session_best: dict[tuple[int, int], dict[str, Any]] = {}
        self.results_revealed: bool = False
        self._impatient_timestamps: dict[int, list[float]] = {}
        self._impatient_last_global_ts: float = 0.0
        self._sync_players_from_sim()

    def _on_audio_cue(self, cue: AudioCue) -> None:
        if self.notify_cb:
            self.notify_cb({"type": "audio", "cue": cue.to_dict()})

    def _sync_players_from_sim(self) -> None:
        existing_profiles = {
            p.gun_num: (p.name, p.avatar, p.team_id) for p in self.players.values()
        }
        self.players.clear()
        for gid, sg in sorted(self.sim.guns.items(), key=lambda item: item[1].gun_num):
            saved = existing_profiles.get(sg.gun_num)
            name = saved[0] if saved else sg.name
            avatar = saved[1] if saved else sg.avatar
            team_id = saved[2] if saved else sg.team_id
            sg.name = name
            sg.avatar = avatar
            sg.team_id = team_id
            self.players[gid] = PlayerState(
                gun_num=sg.gun_num,
                gun_id=gid,
                ref_hex=sg.ref_hex,
                name=name,
                avatar=avatar,
                team_id=team_id,
                paired=True,
                vest_ok=sg.vest_connected,
            )

    def set_num_simulated_guns(self, count: int) -> None:
        self.expected_hardware_guns = max(1, min(8, int(count)))
        self.sim.setup_number_of_guns(self.expected_hardware_guns)
        self._sync_players_from_sim()
        self.sync_race_roster()
        self.audio.play_ui("GunDetect.wav", subtitle=f"{self.expected_hardware_guns} T-Blasters configured")

    def sync_race_roster(self) -> dict[str, list[int]]:
        """Reconcile the race roster with the currently paired guns.

        Guns that left drop out of both lists; newly paired guns
        append to the end of the race order (gun-number order among
        themselves) so an existing order is never disturbed. Called
        lazily from the snapshot too, so every roster mutation site
        (pairing, quiet-drops, host Removes, unpair-all) stays
        consistent without each one remembering to call it.
        """
        paired = sorted(p.gun_num for p in self.players.values())
        paired_set = set(paired)
        order = [n for n in self.race_roster.get("order", []) if n in paired_set]
        bench = [n for n in self.race_roster.get("bench", []) if n in paired_set]
        seen = set(order) | set(bench)
        order.extend(n for n in paired if n not in seen)
        self.race_roster = {"order": order, "bench": bench}
        return {"order": list(order), "bench": list(bench)}

    def race_roster_snapshot(self) -> dict[str, list[int]]:
        """The roster as the UIs render it (always reconciled)."""
        return self.sync_race_roster()

    def _roster_edit_allowed(self) -> str | None:
        """None when a roster edit is allowed, else the rejection reason."""
        if self.mode_name != "TagThenShoot":
            return "Race roster only applies to Tag'n Shoot."
        if self.phase != "LOBBY":
            return "Race roster can only change before the match starts."
        return None

    def set_race_roster(
        self, order: list[int], bench: list[int]
    ) -> tuple[bool, str]:
        """Replace the full roster (TV drag-and-drop / X / re-add).

        `order` + `bench` must partition the paired gun numbers with
        no duplicates, and at least one racer must remain.
        """
        reason = self._roster_edit_allowed()
        if reason is not None:
            return False, reason
        try:
            order_nums = [int(n) for n in order]
            bench_nums = [int(n) for n in bench]
        except (TypeError, ValueError):
            return False, "Roster entries must be player numbers."
        paired = sorted(p.gun_num for p in self.players.values())
        if sorted(order_nums + bench_nums) != paired:
            return False, "Roster must list every paired player exactly once, in the race or on the bench."
        if len(set(order_nums)) != len(order_nums) or len(set(bench_nums)) != len(bench_nums):
            return False, "Roster must not repeat a player."
        if not order_nums:
            return False, "At least one player must race."
        self.race_roster = {"order": order_nums, "bench": bench_nums}
        return True, ""

    def toggle_race_roster(self, gun_num: int, in_race: bool) -> tuple[bool, str]:
        """Opt one player in or out (phone HUD). Opting in appends to
        the END of the race order and never reorders anyone else;
        opting out moves the player to the bench. Cannot bench the
        last racer."""
        reason = self._roster_edit_allowed()
        if reason is not None:
            return False, reason
        self.sync_race_roster()
        if not any(p.gun_num == gun_num for p in self.players.values()):
            return False, f"Player {gun_num} is not paired."
        order = list(self.race_roster["order"])
        bench = list(self.race_roster["bench"])
        if in_race:
            if gun_num in order:
                return True, ""
            bench = [n for n in bench if n != gun_num]
            order.append(gun_num)
        else:
            if gun_num in bench:
                return True, ""
            if len(order) <= 1:
                return False, "At least one player must race."
            order = [n for n in order if n != gun_num]
            bench.append(gun_num)
        self.race_roster = {"order": order, "bench": bench}
        return True, ""

    def race_roster_players(self) -> list[PlayerState]:
        """Paired players in race order (rostered racers only)."""
        self.sync_race_roster()
        by_num = {p.gun_num: p for p in self.players.values()}
        return [by_num[n] for n in self.race_roster["order"] if n in by_num]

    def race_rosters_gun_ids(self) -> list[int]:
        """Gun ids in race order (rostered racers only)."""
        return [p.gun_id for p in self.race_roster_players()]

    def add_lobby_notice(self, text: str) -> None:
        """Note a pairing/liveness event for the pairing page.

        Newest first, capped at 5. Unlike engine.events, notices are
        never cleared by a match start — they describe the lobby, not
        the match.
        """
        self.lobby_notices.insert(0, {"text": text, "timestamp": time.time()})
        self.lobby_notices = self.lobby_notices[:5]

    def sync_paired_hardware_guns(self, paired_list: list[dict[str, Any]]) -> None:
        """Populate `self.players` with 1..8 physically paired T-Blasters.

        Name and avatar come from the gun's stored profile unless the user
        explicitly set them (roster or phone HUD) — a simulator default must
        never stick over the real stored profile on re-pair. Team assignment
        is always the user's choice, so a previously assigned team is kept.
        Every synced gun was just heard from (pair handshake), so its
        last-seen clock starts now, and its ref->number mapping is
        remembered for a future return. A changed roster invalidates
        any completed upload/config state: the guns must be re-primed.
        """
        saved_by_num = {
            p.gun_num: (p.name, p.avatar, p.team_id) for p in self.players.values()
        }
        self.players.clear()
        self.bytecode_uploaded = False
        self.config_failed_guns = []
        now = time.time()
        for ginfo in sorted(paired_list, key=lambda g: g["gun_num"]):
            gnum = int(ginfo["gun_num"])
            gid = int(ginfo["gun_id"])
            saved = saved_by_num.get(gnum)
            explicit = gnum in self._explicit_profiles
            default_team = ((gnum - 1) % 2) + 1
            name = saved[0] if (saved and explicit) else (ginfo.get("name") or f"Player {gnum}")
            avatar = saved[1] if (saved and explicit) else (ginfo.get("avatar") or AVATAR_NAMES[(gnum - 1) % len(AVATAR_NAMES)])
            team_id = saved[2] if saved else default_team
            ref_hex = ginfo.get("ref_hex", "")
            if ref_hex:
                self.ref_number_memory[ref_hex] = gnum
            self.players[gid] = PlayerState(
                gun_num=gnum,
                gun_id=gid,
                ref_hex=ref_hex,
                name=name,
                avatar=avatar,
                team_id=team_id,
                paired=True,
                vest_ok=True,
                last_seen_ts=now,
            )

    def apply_vest_states(self, states: dict[int, bool]) -> list[tuple[PlayerState, bool]]:
        """Fold one C1 vest sweep into player state.

        In the lobby, an explicit C1 reply sets vest_ok immediately and a
        silent gun is marked vest-less after VEST_SILENT_SWEEPS_TO_DISCONNECT
        consecutive silent sweeps. During an active match (phase != 'LOBBY'),
        both C1 00 replies and silent sweeps require
        VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT consecutive misses before
        flipping vest_ok to False, so transient mid-game glitches do not
        flash vest warnings. Any C1 01 reply restores vest_ok immediately.
        """
        in_match = self.phase != "LOBBY"
        miss_threshold = (
            VEST_MATCH_MISS_SWEEPS_TO_DISCONNECT if in_match else VEST_SILENT_SWEEPS_TO_DISCONNECT
        )
        changed: list[tuple[PlayerState, bool]] = []
        now = time.time()
        for gid, player in self.players.items():
            if gid in states:
                # A C1 reply is a report from the gun: it was heard from.
                player.last_seen_ts = now
                player.gun_online = True
                if states[gid]:
                    self._vest_miss_counts[gid] = 0
                    new_ok = True
                else:
                    if in_match:
                        self._vest_miss_counts[gid] = self._vest_miss_counts.get(gid, 0) + 1
                        if self._vest_miss_counts[gid] < miss_threshold:
                            continue
                    else:
                        self._vest_miss_counts[gid] = 0
                    new_ok = False
            else:
                self._vest_miss_counts[gid] = self._vest_miss_counts.get(gid, 0) + 1
                if self._vest_miss_counts[gid] < miss_threshold:
                    continue
                player.gun_online = False
                new_ok = False
            if new_ok != player.vest_ok:
                player.vest_ok = new_ok
                changed.append((player, new_ok))
        for gid in list(self._vest_miss_counts):
            if gid not in self.players:
                del self._vest_miss_counts[gid]
        return changed

    def remove_quiet_lobby_players(
        self,
        now: float | None = None,
        timeout_s: float = LOBBY_QUIET_TIMEOUT_S,
    ) -> list[PlayerState]:
        """Drop lobby players unheard from for longer than `timeout_s`.

        Mirrors the original's fast Pinger: in setup a gun silent >4 s
        is treated as gone. Only the quiet guns leave — other players
        keep their numbers — and the ref->number memory remembers the
        dropped guns so a returning gun reclaims its number. Never
        removes outside LOBBY, and never removes a gun never heard
        from (last_seen 0): there is no silence to measure. Each
        removal leaves a "went quiet" lobby notice. The caller unbinds
        the transport-side pairing for each removed gun.
        """
        if self.phase != "LOBBY":
            return []
        now = time.time() if now is None else now
        removed: list[PlayerState] = []
        for gid in list(self.players):
            player = self.players[gid]
            if player.last_seen_ts <= 0:
                continue
            if now - player.last_seen_ts > timeout_s:
                removed.append(self.players.pop(gid))
                self._vest_miss_counts.pop(gid, None)
                self.add_lobby_notice(
                    f"Player {player.gun_num} ({player.display_name}) went quiet "
                    "and was removed from the roster."
                )
        return removed

    def remove_player(self, gun_num: int) -> PlayerState | None:
        """Host Remove: drop one player before a match, nothing else.

        LOBBY only — the pairing page is a setup screen, and a match
        never loses a player to a host action. Removes the player,
        clears vest-miss counts and any stale profile-write status,
        and leaves every other player (and their numbers) untouched.
        The transport-side unbind + ignore-listing is the caller's job
        (app layer); the ref->number memory keeps the number for the
        gun's return. Remaining guns keep their uploaded code/config —
        removal never forces a re-upload — and if the removed gun was
        the last one holding up a config retry, the rest are ready.
        Returns the removed player, or None when not allowed/found.
        """
        if self.phase != "LOBBY":
            return None
        gid = next((g for g, p in self.players.items() if p.gun_num == gun_num), None)
        if gid is None:
            return None
        player = self.players.pop(gid)
        self._vest_miss_counts.pop(gid, None)
        self.profile_write_status.pop(gun_num, None)
        if gid in self.config_failed_guns:
            self.config_failed_guns.remove(gid)
            if not self.config_failed_guns and self.bytecode_uploaded:
                self.bytecode_ready = True
        self.add_lobby_notice(
            f"Player {player.gun_num} ({player.display_name}) was removed by "
            "the host. Run a pairing scan to add the gun back."
        )
        return player

    def remove_silent_lobby_players(
        self,
        max_silent_sweeps: int = LOBBY_SILENT_SWEEPS_TO_REMOVE,
    ) -> list[PlayerState]:
        """Remove T-Blasters that have stayed silent across consecutive lobby sweeps.

        Balances temporary RF drops (tolerating up to max_silent_sweeps - 1
        missed C1 sweeps) against powered-off T-Blasters lingering on the lobby
        roster. Never removes players outside the LOBBY phase.
        """
        if self.phase != "LOBBY":
            return []
        removed: list[PlayerState] = []
        for gid in list(self.players):
            if self._vest_miss_counts.get(gid, 0) >= max_silent_sweeps:
                player = self.players.pop(gid)
                self._vest_miss_counts.pop(gid, None)
                removed.append(player)
        return removed

    def select_mode(self, mode_name: str, custom_settings: dict[str, Any] | None = None) -> None:
        if mode_name not in MODE_CATALOG:
            raise ValueError(f"Unknown mode: {mode_name}")
        self.mode_name = mode_name
        self.settings = dict(MODE_CATALOG[mode_name]["defaults"])
        if custom_settings:
            self.settings.update(custom_settings)
        self.match_duration_s = int(self.settings.get("durationSeconds", 300))
        self.bytecode_ready = False
        self.bytecode_uploaded = False
        self.config_failed_guns = []
        self.audio.play_ui("Validation.wav")
        self.audio.play_mode_intro(mode_name)

    def update_settings(self, patch: dict[str, Any]) -> None:
        for k, v in patch.items():
            self.settings[k] = int(v) if isinstance(v, (int, float, str)) and str(v).isdigit() else v
        self.match_duration_s = int(self.settings.get("durationSeconds", 300))
        self.bytecode_ready = False
        self.bytecode_uploaded = False
        self.config_failed_guns = []
        self.audio.play_ui("ChangeValue.wav")

    def _load_global_settings(self) -> dict[str, int]:
        """Load persisted global settings from disk, falling back to defaults."""
        loaded = dict(DEFAULT_GLOBAL_SETTINGS)
        if self._settings_path is None or not self._settings_path.exists():
            return loaded
        try:
            raw = json.loads(self._settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return loaded
        if not isinstance(raw, dict):
            return loaded
        for key, value in raw.items():
            if key not in DEFAULT_GLOBAL_SETTINGS:
                continue
            try:
                lo, hi = _GLOBAL_SETTING_RANGES[key]
                loaded[key] = max(lo, min(hi, int(value)))
            except (TypeError, ValueError):
                continue
        return loaded

    def _save_global_settings(self) -> None:
        """Persist global settings to disk so they survive server restarts."""
        if self._settings_path is None:
            return
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._settings_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(self.global_settings, indent=2) + "\n", encoding="utf-8")
            tmp_path.replace(self._settings_path)
        except OSError:
            pass

    def update_global_settings(self, patch: dict[str, Any]) -> dict[str, int]:
        """Apply global-setting changes, clamped to each setting's valid range.

        Beam power and gun volume are baked into the 0x92 config sent at
        upload time, so changing either one forces a fresh upload + config
        before the next match. Audio volumes apply immediately (the browser
        scales each cue) and do not invalidate the upload.
        """
        for key, value in patch.items():
            if key not in DEFAULT_GLOBAL_SETTINGS:
                continue
            lo, hi = _GLOBAL_SETTING_RANGES[key]
            self.global_settings[key] = max(lo, min(hi, int(value)))
        if any(k in patch for k in ("beam_power", "gun_volume")):
            self.bytecode_ready = False
            self.bytecode_uploaded = False
            self.config_failed_guns = []
        self._save_global_settings()
        self.audio.play_ui("ChangeValue.wav")
        return dict(self.global_settings)

    IMPATIENT_MAX_PRESSES = 10
    IMPATIENT_WINDOW_S = 120.0
    IMPATIENT_GLOBAL_COOLDOWN_S = 2.5

    def get_impatient_status(self, now: float | None = None) -> dict[str, dict[str, int]]:
        """Return per-player remaining impatient taps and cooldown reset seconds."""
        t = time.time() if now is None else now
        out: dict[str, dict[str, int]] = {}
        for p in self.players.values():
            ts = [x for x in self._impatient_timestamps.get(p.gun_num, []) if t - x < self.IMPATIENT_WINDOW_S]
            self._impatient_timestamps[p.gun_num] = ts
            used = len(ts)
            remaining = max(0, self.IMPATIENT_MAX_PRESSES - used)
            reset_in = int(math.ceil(self.IMPATIENT_WINDOW_S - (t - ts[0]))) if (used >= self.IMPATIENT_MAX_PRESSES and ts) else 0
            out[str(p.gun_num)] = {
                "remaining": remaining,
                "limit": self.IMPATIENT_MAX_PRESSES,
                "reset_in": max(0, reset_in),
            }
        return out

    def trigger_impatient(self, gun_num: int, now: float | None = None) -> dict[str, Any]:
        """Play a pre-game jeer on behalf of an impatient player, with rate limiting."""
        t = time.time() if now is None else now
        player = next((p for p in self.players.values() if p.gun_num == gun_num), None)
        if player is None:
            return {"ok": False, "reason": "unknown_player", "status_code": 404}
        if self.phase not in ("LOBBY", "WAIT_TRIGGER"):
            return {"ok": False, "reason": "invalid_phase", "status_code": 400}

        ts = [x for x in self._impatient_timestamps.get(gun_num, []) if t - x < self.IMPATIENT_WINDOW_S]
        self._impatient_timestamps[gun_num] = ts
        if len(ts) >= self.IMPATIENT_MAX_PRESSES:
            retry_after = max(1, int(math.ceil(self.IMPATIENT_WINDOW_S - (t - ts[0]))))
            return {
                "ok": False,
                "reason": "rate_limit",
                "retry_after": retry_after,
                "remaining": 0,
                "status_code": 429,
            }

        since_global = t - self._impatient_last_global_ts
        if since_global < self.IMPATIENT_GLOBAL_COOLDOWN_S:
            retry_after = max(1, int(math.ceil(self.IMPATIENT_GLOBAL_COOLDOWN_S - since_global)))
            return {
                "ok": False,
                "reason": "global_cooldown",
                "retry_after": retry_after,
                "remaining": self.IMPATIENT_MAX_PRESSES - len(ts),
                "status_code": 429,
            }

        ts.append(t)
        self._impatient_last_global_ts = t
        self.audio.trigger_pool("impatient", force=True)
        return {
            "ok": True,
            "gun_num": player.gun_num,
            "name": player.display_name,
            "avatar": player.avatar,
            "remaining": self.IMPATIENT_MAX_PRESSES - len(ts),
        }

    def set_wizard_step(self, step: int) -> int:
        """Record the wizard step the main screen is showing (1 or 2)."""
        self.wizard_step = 1 if int(step) < 1 else (2 if int(step) > 2 else int(step))
        return self.wizard_step

    def profiles_editable(self) -> bool:
        """Whether names/avatars may change right now, from either interface.

        Only in setup phases (LOBBY / GAME_OVER) AND while the Arena Master
        is on the pairing page (wizard step 1). Once the user moves past
        pairing, editing stays closed until they come back to step 1 —
        the same shape as the team lock after game selection, one screen
        earlier, because a name/avatar save writes to the gun's flash.
        """
        return self.phase in ("LOBBY", "GAME_OVER") and self.wizard_step == 1

    def profiles_locked_reason(self) -> str:
        """Human-readable reason profiles are not editable ('' when editable)."""
        if self.phase in ("UPLOADING", "COUNTDOWN", "PLAYING"):
            return "Profiles are locked while a match is running."
        if self.wizard_step != 1:
            return "Names and avatars can only be changed from the pairing screen."
        return ""

    def update_player_profile(
        self,
        gun_num: int,
        name: str | None = None,
        avatar: str | None = None,
        team_id: int | None = None,
    ) -> PlayerState | None:
        """Apply a name/avatar/team change, enforcing the phase locks.

        This is the display-update primitive. Name/avatar changes reach it
        from the profile-write worker AFTER the new values have been
        written to the gun's flash (a save in either interface writes to
        the gun; the display follows the gun, never the other way round).
        The /api/profile endpoint itself accepts team changes only.
        The team IS baked into the gun's 0x92 config, so:
        - During UPLOADING / COUNTDOWN / PLAYING every profile change is
          rejected (ValueError) so the scoreboard cannot change mid-game.
        - In setup (LOBBY / GAME_OVER) name/avatar are freely editable.
        - A team change in setup is allowed but invalidates the upload: the
          guns still hold the old team in their config, so the next match
          must re-upload and re-configure before it starts.
        """
        if self.phase in ("UPLOADING", "COUNTDOWN", "PLAYING"):
            raise ValueError("Profiles are locked while a match is running.")
        target = next((p for p in self.players.values() if p.gun_num == gun_num), None)
        if not target:
            return None
        if name is not None:
            clean = name.strip()[:14]
            if clean:
                target.name = clean
                self._explicit_profiles.add(gun_num)
        if avatar is not None and avatar in AVATAR_NAMES:
            target.avatar = avatar
            self._explicit_profiles.add(gun_num)
        if team_id is not None and int(team_id) in TEAM_INFO:
            if int(team_id) != target.team_id:
                self.bytecode_ready = False
                self.bytecode_uploaded = False
                self.config_failed_guns = []
            target.team_id = int(team_id)
        if target.gun_id in self.sim.guns:
            sg = self.sim.guns[target.gun_id]
            sg.name = target.name
            sg.avatar = target.avatar
            sg.team_id = target.team_id
        self.audio.play_ui("Validation.wav")
        return target

    def mark_profile_stored(self, gun_num: int) -> None:
        """Drop the host-side override after a successful gun profile write.

        The gun now stores exactly what the roster shows, so a future
        re-pairing reads the new profile back from the gun itself — the
        explicit override is no longer needed to protect it.
        """
        self._explicit_profiles.discard(gun_num)

    def _is_timed_mode(self) -> bool:
        """Whether the current mode runs against a match clock.

        OldFashionDuel and LastManStanding are timerless in the
        original (rounds / last-alive endings); every other mode is
        timed. Timerless modes show elapsed time, get no time
        warnings, and never end on the clock.
        """
        return bool(MODE_CATALOG.get(self.mode_name, {}).get("timed", True))

    def get_elapsed_seconds(self) -> float:
        if self.match_start_ts <= 0:
            return 0.0
        end = self.match_end_ts if (self.phase == "GAME_OVER" and self.match_end_ts > 0) else time.time()
        return max(0.0, end - self.match_start_ts)

    def format_clock(self) -> str:
        if not self._is_timed_mode():
            elapsed = self.get_elapsed_seconds()
            mins = int(elapsed) // 60
            secs = int(elapsed) % 60
            return f"{mins:02d}:{secs:02d}"
        rem = self.get_remaining_seconds()
        if rem is None:
            return "00:00"
        mins = int(rem) // 60
        secs = int(rem) % 60
        return f"{mins:02d}:{secs:02d}"

    def get_remaining_seconds(self) -> float | None:
        """Seconds left on the match clock, or None for timerless modes."""
        if not self._is_timed_mode():
            return None
        if self.mode_name == "TagThenShoot":
            # Timed turns: the clock is the active run's cap, not the
            # match. Outside a live run it shows the full cap, so the
            # 60 s warning stays skipped and the match never ends on
            # this clock — the turn machine owns the cap.
            rs = self.race_state
            if self.phase == "PLAYING" and rs and rs.get("turn_phase") == "RUNNING":
                return max(0.0, rs.get("turn_ends_at", 0.0) - time.time())
            if self.phase == "GAME_OVER":
                return 0.0
            return float(self.match_duration_s)
        if self.phase == "PLAYING":
            elapsed = time.time() - self.match_start_ts
            return max(0.0, self.match_duration_s - elapsed)
        if self.phase == "GAME_OVER":
            return 0.0
        return float(self.match_duration_s)

    def _schedule_audio_followup(self, delay_s: float, kind: str, **payload: Any) -> None:
        """Queue a deferred action for tick_pending_audio to fire.

        Kinds: "pool" (payload: pool_key, subtitle, force),
        "file" (payload: filename, subtitle, priority, duration),
        "duel_can_shoot" (no payload). Delays are modeled line
        lengths — see _pending_audio.

        Race followups may carry "race_run" (the race turn_index at
        queue time): tick_pending_audio drops them unless that run
        is still RUNNING, so a prompt queued for a run that capped
        or was skipped never sounds into the next run.
        """
        entry: dict[str, Any] = {"fire_at": time.time() + max(0.0, delay_s), "kind": kind}
        entry.update(payload)
        self._pending_audio.append(entry)

    def tick_pending_audio(self, now: float | None = None) -> None:
        """Fire due deferred actions. Called once per match-loop cycle."""
        if not self._pending_audio:
            return
        now = time.time() if now is None else now
        due, rest = [], []
        for entry in self._pending_audio:
            (due if entry["fire_at"] <= now else rest).append(entry)
        self._pending_audio = rest
        if self.phase != "PLAYING":
            return
        for entry in due:
            kind = entry["kind"]
            if "race_run" in entry and not self._race_followup_still_live(entry):
                continue
            if kind == "pool":
                self.audio.trigger_pool(
                    entry["pool_key"],
                    custom_subtitle=entry.get("subtitle"),
                    force=bool(entry.get("force", False)),
                )
            elif kind == "file":
                self.audio.play_announcer_file(
                    entry["filename"],
                    entry.get("subtitle", ""),
                    priority=int(entry.get("priority", 0)),
                    duration=float(entry.get("duration", 2.0)),
                )
            elif kind == "duel_can_shoot":
                # Only arm shooting while the round is still in DRAW;
                # a hit that already resolved the round must not
                # re-arm it.
                if self.duel_state.get("subphase") == "DRAW":
                    self.duel_state["can_shoot"] = True

    def _race_followup_still_live(self, entry: dict[str, Any]) -> bool:
        """True when a run-guarded race followup may still fire.

        Next-target prompts are queued during a run but fire ~1-2 s
        later; if the run has since capped, been skipped, or ended,
        the prompt belongs to a dead run and must not sound into the
        next run's sequence. The guard keys on the run's turn_index,
        which advances on every turn change, plus the RUNNING phase.
        """
        if self.mode_name != "TagThenShoot":
            return False
        rs = self.race_state
        return bool(rs) and rs.get("turn_phase") == "RUNNING" and rs.get("turn_index") == entry.get("race_run")

    def _record_duel_round_result(self, winner: PlayerState | None) -> None:
        """Record one finished duel round for the round display.

        `winner` is the round-winning player, or None for a draw.
        Totals in round_scores and the per-round history in
        round_results stay in step.
        """
        rnd = int(self.duel_state.get("round", 0))
        if winner is not None:
            scores = self.duel_state.setdefault("round_scores", {})
            scores[winner.gun_id] = scores.get(winner.gun_id, 0) + 1
        self.duel_state.setdefault("round_results", []).append(
            {"round": rnd, "winner_gun_num": winner.gun_num if winner else None}
        )

    def add_event(
        self,
        kind: str,
        text: str,
        actor: PlayerState | None = None,
        target: PlayerState | None = None,
        icon_url: str | None = None,
    ) -> EventEntry:
        # Guarantee all player names appearing in event texts are shown in ALL CAPS
        for p in sorted(self.players.values(), key=lambda pl: len(pl.name or ""), reverse=True):
            if p.name and p.name.strip():
                text = re.sub(re.escape(p.name.strip()), p.name.strip().upper(), text, flags=re.IGNORECASE)

        if icon_url is None:
            if actor is not None and 1 <= actor.gun_num <= 19:
                icon_url = f"/static/assets/textures/Ui/Pictograms/64x/Hud_{actor.gun_num}.png"
            else:
                icon_url = "/static/assets/textures/Ui/Pictograms/64x/Hud_1.png"
        self._event_seq += 1
        entry = EventEntry(
            id=self._event_seq,
            timestamp=time.time(),
            match_clock=self.format_clock(),
            kind=kind,
            text=text,
            actor_name=actor.name if actor else "",
            actor_team=actor.team_id if actor else 1,
            actor_gun_num=actor.gun_num if actor else 0,
            target_name=target.name if target else "",
            target_team=target.team_id if target else 2,
            target_gun_num=target.gun_num if target else 0,
            icon_url=icon_url,
        )
        self.events.insert(0, entry)
        if len(self.events) > 60:
            self.events = self.events[:60]
        return entry

    def reset_stats_for_new_match(self) -> None:
        self.events.clear()
        self._event_seq = 0
        self.current_leader_key = ""
        now = time.time()
        self.last_action_ts = now
        self._last_idle_banter_ts = now
        self._last_miss_taunt_ts = 0.0
        self._last_reload_reminder_ts = 0.0
        self._manual_launch_triggered = False
        self._turn_triggered = False
        self.results_revealed = False
        self.match_end_ts = 0.0
        self._pending_audio = []
        self.audio.reset_match_timers()
        team_map = {gid: p.team_id for gid, p in self.players.items()}
        self.sim.reset_for_mode(self.mode_name, self.settings, team_map=team_map)
        # The 0xC3 tower-hit counter on the hardware transport is
        # cumulative across the transport's lifetime; clear it every
        # match or the first poll of the next TagThenShoot match
        # credits phantom hits equal to the previous match's total.
        if self.hw is not None:
            self.hw.ubiconnect_hits.clear()

        max_life = int(self.settings.get("lifePoints", 5))
        max_ammo = 6 if self.mode_name == "OldFashionDuel" else int(self.settings.get("ammunitions", 9))
        max_clips = 0 if self.mode_name == "OldFashionDuel" else int(self.settings.get("clips", 3))
        num_players = len(self.players)

        for p in self.players.values():
            # LastManStanding's original survival formula sets an alive
            # player's score to life_points + num_players; initialize it
            # at match start so an untouched survivor does not stay at 0.
            p.score = (max_life + num_players) if self.mode_name == "LastManStanding" else 0
            p.hits_landed = 0
            p.hits_received = 0
            p.frags = 0
            p.deaths = 0
            p.tags = 0
            p.tower_hits = 0
            p.run_history = []
            p.shots_fired = 0
            p.streak = 0
            p.best_streak = 0
            p.hits_dealt_to.clear()
            p.hits_taken_from.clear()
            p.life_points = max_life
            p.max_life = max_life
            p.ammo = max_ammo
            p.max_ammo = max_ammo
            p.clips = max_clips
            p.max_clips = max_clips
            p.status_badge = "ACTIVE"
            p.miss_streak = 0
            p._empty_ammo_warned = False
            p._last_empty_warn_ts = 0.0
            p._low_ammo_warned = False
            p.last_tagged_by = ""
            p.last_tagged_by_time = 0.0
            p.last_tagged_target = ""
            p.last_rfid = ""
            p.last_status_ts = 0.0
            p.baseline_set = False
            p.prev_slots = [0, 0, 0, 0, 0]

        self.score_history = [
            {"t": 0.0, "scores": {str(p.gun_num): p.score for p in self.players.values()}}
        ]

        if self.mode_name == "OldFashionDuel":
            self._duel_round_triggered = False
            self.duel_state = {
                "round": 0,
                "max_rounds": int(self.settings.get("rounds", 5)),
                "phase_code": 2,
                "subphase": "INTRO",
                "can_shoot": False,
                "next_transition_at": time.time() + 3.0,
                "round_scores": {gid: 0 for gid in self.players},
                "round_results": [],
                "last_result_text": "",
                "last_winner_gun_num": None,
                "trigger_armed": False,
            }

        if self.mode_name == "TagThenShoot":
            # The roster freezes at match start: turn_order is the
            # snapshot, in race order, repeated once per round. Benched
            # players never appear in it (they still poll and stay in
            # state.players, but the game displays hide them entirely).
            order = self.race_rosters_gun_ids()
            first = order[0] if order else None
            max_rounds = max(1, min(3, int(self.settings.get("rounds", 1))))
            launch_mode = "auto" if int(self.settings.get("gameLaunch", 1)) == 0 else "manual"
            self.race_state = {
                "turn_order": order,
                "turn_index": 0,
                "round_index": 1,
                "max_rounds": max_rounds,
                "launch_mode": launch_mode,
                "turn_phase": "ROUND_INTRO" if order else "DONE",
                "active_gun_id": first,
                # False until a trigger poll observes the trigger
                # released, so a trigger held over from the previous
                # turn cannot auto-release the next wait.
                "trigger_armed": False,
                "countdown_seconds": 0,
                "countdown_remaining": 0,
                "countdown_ends_at": 0.0,
                "countdown_sent": True,
                "next_number": 0,
                "next_number_at": 0.0,
                # ROUND_INTRO: the round announcement plays on the
                # first tick, then the intro ends at intro_ends_at
                # (modeled line length + 0.25 s) so the line never
                # overlaps the countdown numbers.
                "intro_played": False,
                "intro_ends_at": 0.0,
                # PLAYER_INTRO: the "Player N!" callout plays on the
                # first tick, then the callout ends at callout_ends_at
                # (modeled line length + 0.25 s) so it never overlaps
                # the countdown numbers or the gun's 0x93 countdown.
                "callout_played": False,
                "callout_ends_at": 0.0,
                "turn_started_at": 0.0,
                "turn_ends_at": 0.0,
                "beat_ends_at": 0.0,
                "elapsed": 0.0,
                "turn_cap_s": int(self.settings.get("durationSeconds", 60)),
                "bases": max(1, int(self.settings.get("numberOfBase", 4))),
                # The following racer (UP NEXT tag), derived by
                # race_next_gun_id; None on the final run, in solo
                # races, and once the race is DONE.
                "next_gun_id": None,
            }
            self._refresh_race_next_gun_id()
            if first is not None:
                fp = self.players[first]
                if launch_mode == "auto":
                    self.add_event(
                        "system",
                        f"Round 1 of {max_rounds} — {fp.display_name} is up first, countdown starts in a moment!",
                    )
                else:
                    self.add_event(
                        "system",
                        f"Round 1 of {max_rounds} — {fp.display_name} is up first — pull the trigger to start the run!",
                    )
        else:
            self.race_state = {}

    def _reset_duel_ammo(self) -> None:
        """Reset all duelists' ammo to 6 bullets for the next round."""
        for p in self.players.values():
            p.ammo = 6
            p.prev_ammo = 6
        if self.use_simulator:
            for sg in self.sim.guns.values():
                sg.ammo = 6

    def _begin_duel_round(self, next_rnd: int, max_rnd: int, now: float) -> None:
        """Enter WAIT for round `next_rnd` with fresh ammo and round audio."""
        self.duel_state["round"] = next_rnd
        self.duel_state["phase_code"] = 1
        self.duel_state["subphase"] = "WAIT"
        self.duel_state["can_shoot"] = False
        self.duel_state["last_result_text"] = ""
        self.duel_state["last_winner_gun_num"] = None
        # Round 1 carries the setup intro after the round announcement, so
        # its standoff floor covers both lines (6 s) and the DRAW cue never
        # talks over the intro; later rounds keep the usual 5 s floor.
        wait_floor = 6.0 if next_rnd == 1 else 5.0
        self.duel_state["next_transition_at"] = now + random.uniform(wait_floor, 8.0)
        self._reset_duel_ammo()
        if next_rnd == 1:
            round_file, round_sub, round_dur = ("DLG_GM_GLOBAL_115.wav", "First round!", 1.2)
        elif next_rnd >= max_rnd:
            round_file, round_sub, round_dur = ("DLG_GM_GLOBAL_116.wav", "Final round", 1.4)
        else:
            round_file, round_sub, round_dur = ("DLG_GM_GLOBAL_114.wav", "Next round!", 1.1)
        self.audio.play_ui("Validation.wav")
        self.add_event(
            "duel",
            f"ROUND {next_rnd} OF {max_rnd} — Hold fire and wait for the FIRE! signal!",
        )
        cue = self.audio.play_announcer_file(round_file, round_sub, priority=-1, duration=round_dur)
        delay = (max(0.1, self.audio.active_until - now) + 0.25) if cue is not None else 0.1
        self._schedule_audio_followup(
            delay,
            "pool",
            pool_key="duel_setup" if next_rnd == 1 else "duel_prepare",
            force=True,
        )

    def tick_mode_timers(self) -> None:
        """Advance OldFashionDuel round subphases (`INTRO` -> `WAIT` -> `DRAW` -> `ROUND_END` -> next round `WAIT`)."""
        if self.phase != "PLAYING" or self.mode_name != "OldFashionDuel":
            return
        now = time.time()
        sub = self.duel_state.get("subphase", "INTRO")
        max_rnd = int(self.duel_state.get("max_rounds", 5))
        curr_rnd = int(self.duel_state.get("round", 0))
        if now >= self.duel_state.get("next_transition_at", 0.0):
            if sub in ("INTRO", "ROUND_END"):
                # The match itself starts on a manual trigger pull
                # (WAIT_TRIGGER phase); rounds then follow one another
                # automatically with fresh ammo.
                if curr_rnd + 1 > max_rnd:
                    self.duel_state["subphase"] = "COMPLETE"
                    return
                self._begin_duel_round(curr_rnd + 1, max_rnd, now)
            elif sub == "WAIT":
                self.duel_state["subphase"] = "DRAW"
                self.duel_state["phase_code"] = 1
                # The clear "Fire!" clip (GLOBAL_83, 0.87 s) is the
                # signal. Shooting opens a short grace after the cue
                # starts (browser playback latency) instead of at the
                # end of the line, so a player reacting to the word is
                # never penalized as early.
                self.duel_state["can_shoot"] = False
                self.duel_state["next_transition_at"] = now + 8.0
                self.audio.trigger_pool("duel_draw", force=True)
                self._schedule_audio_followup(DUEL_FIRE_GRACE_S, "duel_can_shoot")
            elif sub == "DRAW":
                # Timeout without a hit -> round draw
                self.duel_state["subphase"] = "ROUND_END"
                self.duel_state["phase_code"] = 2
                self.duel_state["can_shoot"] = False
                self.duel_state["next_transition_at"] = now + 7.0
                self.duel_state["last_winner_gun_num"] = None
                self.duel_state["last_result_text"] = (
                    f"ROUND {self.duel_state['round']}: DRAW — NO POINT AWARDED"
                )
                self._record_duel_round_result(None)
                self.add_event("duel", f"Round {self.duel_state['round']} ended in a draw!")
                self.audio.trigger_pool("duel_drawn", force=True)

    def _race_run_active(self, player: PlayerState) -> bool:
        """True when `player` is the runner of a live timed turn."""
        if self.mode_name != "TagThenShoot":
            return False
        rs = self.race_state
        return bool(rs) and rs.get("turn_phase") == "RUNNING" and rs.get("active_gun_id") == player.gun_id

    # Round announcement lines, shared with the duel (PR #15): the same
    # GLOBAL_114/115/116 files, with the duel's modeled durations.
    # GLOBAL_113 "NEXT GAME!" stays unwired everywhere.
    _RACE_ROUND_LINES: dict[str, tuple[str, str, float]] = {
        "first": ("DLG_GM_GLOBAL_115.wav", "First round!", 1.2),
        "next": ("DLG_GM_GLOBAL_114.wav", "Next round!", 1.1),
        "final": ("DLG_GM_GLOBAL_116.wav", "Final round", 1.4),
    }

    # Player-number callout duration: the duel's GLOBAL_54+gun_num
    # files ("Player 1!".."Player 16!"). Modeled duration covers the
    # longest line in the set (GLOBAL_67 "Player 13", 1.83 s) so the
    # callout window never overlaps the countdown numbers.
    _RACE_PLAYER_CALLOUT_DURATION = 1.9

    def _race_countdown_seconds(self) -> int:
        """Per-run countdown from the launch setting: 10 s in Auto,
        5 s in Manual (after the trigger pull). The old halved-3 s
        countdown is gone; the gun's 0x93 packet takes any value."""
        return 10 if self.race_state.get("launch_mode") == "auto" else 5

    def _enter_race_countdown(self, now: float) -> None:
        """Open COUNTDOWN for the active run. No countdown_intro pool
        here: the intro line would overlap the first spoken number,
        and delaying the numbers would desync them from the gun's
        own 0x93 countdown. The round line and Player callout play
        in their own intro phases before this opens, so the first
        spoken number and the app loop's 0x93 send (countdown_sent)
        start together. Numbers + round line + callout + go only."""
        rs = self.race_state
        cd = self._race_countdown_seconds()
        rs["turn_phase"] = "COUNTDOWN"
        rs["countdown_seconds"] = cd
        rs["countdown_remaining"] = cd
        rs["countdown_ends_at"] = now + cd
        rs["countdown_sent"] = False
        rs["next_number"] = cd
        rs["next_number_at"] = now
        self.countdown_remaining = cd

    def _enter_run_start_phase(self, now: float) -> None:
        """Move the active run to its start phase: a trigger wait in
        Manual, straight into the countdown in Auto."""
        if self.race_state.get("launch_mode") == "auto":
            self._enter_race_countdown(now)
        else:
            rs = self.race_state
            rs["turn_phase"] = "WAIT_TRIGGER"
            rs["trigger_armed"] = False
            self._turn_triggered = False

    def _play_round_announcement(self, now: float) -> None:
        """Play the round line for the current round cycle and set the
        intro window. Round 1 always announces "First round!" (even
        in a 1-round match, as in the duel); the last round of a
        multi-round match announces "Final round"; the rest "Next
        round!". The countdown cannot start until the window elapses,
        so the line never overlaps the countdown numbers."""
        rs = self.race_state
        rnd = int(rs.get("round_index", 1))
        max_rnd = int(rs.get("max_rounds", 1))
        if rnd <= 1:
            key = "first"
        elif rnd >= max_rnd:
            key = "final"
        else:
            key = "next"
        wav, sub, dur = self._RACE_ROUND_LINES[key]
        cue = self.audio.play_announcer_file(wav, sub, priority=-1, duration=dur)
        delay = (max(0.1, self.audio.active_until - now) + 0.25) if cue is not None else 0.1
        rs["intro_played"] = True
        rs["intro_ends_at"] = now + delay

    def _enter_player_intro(self) -> None:
        """Open PLAYER_INTRO for the active run: the Player callout
        plays on the first tick, then the run's start phase opens
        once the callout window ends. Trigger pulls and the 0x93
        countdown both wait: the app loop only polls/sends in
        WAIT_TRIGGER/COUNTDOWN, so nothing can start early."""
        rs = self.race_state
        rs["turn_phase"] = "PLAYER_INTRO"
        rs["callout_played"] = False
        rs["callout_ends_at"] = 0.0

    def _play_player_callout(self, now: float) -> None:
        """Play the active runner's "Player N!" callout and set the
        callout window. The spoken line says only the number (custom
        names cannot be spoken from pre-recorded audio); the subtitle
        carries the display name. Same file formula + guard as the
        duel's round-win callout; out-of-range gun numbers proceed
        silently (the roster caps at 8 guns in practice). The
        countdown cannot start until the window elapses, so the
        callout never overlaps the countdown numbers."""
        rs = self.race_state
        gid = rs.get("active_gun_id")
        player = self.players.get(gid) if gid is not None else None
        if player is not None and 1 <= player.gun_num <= 16:
            wav = f"DLG_GM_GLOBAL_{54 + player.gun_num:02d}.wav"
            sub = f"Player {player.gun_num} ({player.display_name}) is up!"
            cue = self.audio.play_announcer_file(
                wav, sub, priority=-1, duration=self._RACE_PLAYER_CALLOUT_DURATION
            )
            delay = (max(0.1, self.audio.active_until - now) + 0.25) if cue is not None else 0.1
        else:
            delay = 0.1
        rs["callout_played"] = True
        rs["callout_ends_at"] = now + delay

    def _schedule_next_base_prompt(self, now: float, next_base: int, cue: Any) -> None:
        """Queue a "Next: T-Base N!" prompt after the cue just played.

        Direct file play (guaranteed, not a proba pool), sequenced
        after the preceding line via the modeled active_until window
        — the same pattern as the tag handler's confirmation. The
        followup carries the run's turn_index: tick_pending_audio
        drops it if the run has ended before it fires. A prompt is
        also never due before a Next prompt already queued for the
        same run, so prompts always sound in base order even at
        sim speed (on hardware the earlier prompt has long since
        fired, so that clamp is a no-op). No spoken shoot-the-tower
        line exists in the catalog, so that step stays visual plus
        the tower-hit confirmation.
        """
        if cue is not None:
            delay = max(0.1, self.audio.active_until - now) + 0.25
        else:
            delay = 0.1
        run = int(self.race_state.get("turn_index", 0))
        queued = [
            float(entry.get("fire_at", 0.0))
            for entry in self._pending_audio
            if entry.get("kind") == "file"
            and entry.get("race_run") == run
            and str(entry.get("subtitle", "")).startswith("Next:")
        ]
        if queued:
            delay = max(delay, max(queued) + 0.05 - time.time())
        self._schedule_audio_followup(
            delay,
            "file",
            filename=f"DLG_GM_GLOBAL_{72 + next_base - 1:02d}.wav",
            subtitle=f"Next: T-Base {next_base}!",
            priority=2,
            duration=1.5,
            race_run=int(self.race_state.get("turn_index", 0)),
        )

    def tick_race_turns(self, now: float | None = None) -> None:
        """Advance the TagThenShoot timed-turn machine, looped per round.

        ROUND_INTRO (round announcement, no overlap with the
        countdown) -> PLAYER_INTRO ("Player N!" callout, no overlap
        with the countdown) -> WAIT_TRIGGER (Manual only: the active
        player pulls the trigger; mandatory, one pull per run) or
        straight to COUNTDOWN (Auto: 10 s before EVERY run; Manual:
        5 s after the pull) -> RUNNING (one course; the cap ends an
        unfinished run) -> TURN_END (3 s beat) -> the next run, or
        the next round's ROUND_INTRO, or DONE once every racer has
        run every round. Called once per match-loop cycle, next to
        tick_mode_timers; the hardware trigger poll and the per-turn
        countdown command live in the loop, because they need
        blocking HID calls this sync tick cannot make.
        """
        if self.phase != "PLAYING" or self.mode_name != "TagThenShoot":
            return
        rs = self.race_state
        if not rs:
            return
        now = time.time() if now is None else now
        phase = rs.get("turn_phase")
        if phase == "ROUND_INTRO":
            if not rs.get("intro_played"):
                self._play_round_announcement(now)
            elif now >= rs.get("intro_ends_at", 0.0):
                self._enter_player_intro()
        elif phase == "PLAYER_INTRO":
            if not rs.get("callout_played"):
                self._play_player_callout(now)
            elif now >= rs.get("callout_ends_at", 0.0):
                self._enter_run_start_phase(now)
        elif phase == "WAIT_TRIGGER":
            if self._turn_triggered:
                self._turn_triggered = False
                self._enter_race_countdown(now)
        elif phase == "COUNTDOWN":
            if rs.get("next_number", 0) >= 1 and now >= rs.get("next_number_at", 0.0):
                self.audio.play_countdown_number(rs["next_number"])
                self.countdown_remaining = rs["next_number"]
                rs["countdown_remaining"] = rs["next_number"]
                rs["next_number"] -= 1
                rs["next_number_at"] = now + 1.0
            if now >= rs.get("countdown_ends_at", 0.0):
                self._start_race_run(now)
        elif phase == "RUNNING":
            rs["elapsed"] = max(0.0, now - rs.get("turn_started_at", now))
            if now >= rs.get("turn_ends_at", 0.0):
                self._complete_race_turn(completed=False, now=now)
        elif phase == "TURN_END":
            if now >= rs.get("beat_ends_at", 0.0):
                self._advance_race_turn(now)

    def _start_race_run(self, now: float) -> None:
        """Enter RUNNING for the active player's run: fresh per-run
        counters, a reset course, re-armed time warnings. `score`
        is NOT reset: it is the total across all runs this match."""
        rs = self.race_state
        gid = rs.get("active_gun_id")
        player = self.players.get(gid) if gid is not None else None
        rs["turn_phase"] = "RUNNING"
        rs["turn_started_at"] = now
        rs["turn_ends_at"] = now + float(rs.get("turn_cap_s", 60))
        rs["elapsed"] = 0.0
        self.countdown_remaining = 0
        rs["countdown_remaining"] = 0
        if player is not None:
            player.tags = 0
            player.tower_hits = 0
            player.miss_streak = 0
        if self.use_simulator and gid is not None:
            self.sim.reset_race_course(gid)
        self.audio.reset_match_timers()
        # The go signal is the single clear "Go!" (GLOBAL_13, 0.56 s).
        # The game_start pool's other variants do not say go clearly
        # ("GO! If, like, you want to!", "Go stop. No, I was kidding
        # go") — a joke line is the wrong start signal for a timed
        # run. Other modes keep the pool.
        cue = self.audio.play_announcer_file("DLG_GM_GLOBAL_13.wav", "Go!", priority=0, duration=0.6)
        # First-target prompt: "T-Base 1" sequenced after Go, so the
        # runner hears their first target instead of only the screens
        # showing it. Run-guarded: a skip/cap before it fires drops it.
        self._schedule_next_base_prompt(now, 1, cue)
        if player is not None:
            self.add_event("system", f"{player.display_name} started the run — go!")

    def _record_run_result(
        self,
        player: PlayerState,
        completed: bool,
        elapsed: float | None,
        points: int | None = None,
        tags: int | None = None,
        tower_hits: int | None = None,
    ) -> dict[str, Any]:
        """Append one run to the player's history for the current round.

        Defaults take the live counters (points = tags + tower hits),
        which is exactly the run's score since both counters reset at
        run start. Skip-before-start passes explicit zeros so stale
        counters from a previous run never leak into a forfeit.
        """
        entry: dict[str, Any] = {
            "round": int(self.race_state.get("round_index", 1)),
            "time": elapsed if completed else None,
            "completed": completed,
            "points": (player.tags + player.tower_hits) if points is None else points,
            "tags": player.tags if tags is None else tags,
            "tower_hits": player.tower_hits if tower_hits is None else tower_hits,
        }
        player.run_history.append(entry)
        return entry

    def _complete_race_turn(
        self, completed: bool, now: float | None = None, reason: str = "cap"
    ) -> None:
        """Record the active run's result and open the TURN_END beat.

        Called from the ingest paths when the runner finishes the
        course, from the tick when the cap expires, and from skip-run
        (`reason="skip"`). No-op unless a run is live, so a late
        tower report cannot re-complete a turn.
        """
        rs = self.race_state
        if not rs or rs.get("turn_phase") != "RUNNING":
            return
        now = time.time() if now is None else now
        gid = rs.get("active_gun_id")
        player = self.players.get(gid) if gid is not None else None
        elapsed = max(0.0, now - rs.get("turn_started_at", now))
        rs["turn_phase"] = "TURN_END"
        rs["beat_ends_at"] = now + 3.0
        rs["elapsed"] = elapsed
        if player is None:
            return
        # Match-best compares against every finish recorded BEFORE
        # this run (all players, all earlier rounds), so compute it
        # before appending this run to the history.
        prev_best: float | None = None
        for p in self.players.values():
            best = p.best_run_time
            if best is not None and (prev_best is None or best < prev_best):
                prev_best = best
        self._record_run_result(player, completed, elapsed if completed else None)
        if not completed:
            if reason == "skip":
                self.add_event(
                    "system",
                    f"{player.display_name}'s run was skipped — {player.tags + player.tower_hits} points kept.",
                )
            else:
                self.add_event(
                    "system",
                    f"{player.display_name} ran out of time — {player.tags + player.tower_hits} points.",
                )
            return
        is_match_best = prev_best is None or elapsed < prev_best
        bases = int(rs.get("bases", self.settings.get("numberOfBase", 4)))
        prev_session = self.race_session_best.get((gid, bases))
        is_session_best = prev_session is None or elapsed < prev_session["time"]
        if is_session_best:
            self.race_session_best[(gid, bases)] = {"time": elapsed, "name": player.display_name}
        if is_match_best and is_session_best:
            tail = " — fastest so far, and a new session best!"
        elif is_match_best:
            tail = " — fastest so far!"
        elif is_session_best:
            tail = " — a new session best!"
        else:
            tail = ""
        self.add_event(
            "system",
            f"{player.display_name} finished the course in {elapsed:.1f}s!{tail}",
        )
        cue = self.audio.trigger_pool(
            "race_finish",
            custom_subtitle=f"{player.display_name} finished in {elapsed:.1f}s!",
            force=True,
        )
        # Vocal new-best only for a new MATCH best; personal/session
        # bests show on screen, they are not announced.
        if is_match_best:
            if cue is not None:
                delay = max(0.1, self.audio.active_until - now) + 0.25
            else:
                delay = 0.1
            self._schedule_audio_followup(
                delay,
                "pool",
                pool_key="race_new_best",
                subtitle=f"{player.display_name} — new best time!",
                force=True,
            )

    def _advance_race_turn(self, now: float) -> None:
        """Move to the next run: same round's next racer, the next
        round's ROUND_INTRO, or DONE after the last run of the last
        round. Round-major, same roster order every round."""
        rs = self.race_state
        order = rs.get("turn_order", [])
        max_rounds = int(rs.get("max_rounds", 1))
        rs["turn_index"] = int(rs.get("turn_index", 0)) + 1
        if not order or rs["turn_index"] >= len(order) * max_rounds:
            rs["turn_phase"] = "DONE"
            rs["active_gun_id"] = None
            self._refresh_race_next_gun_id()
            return
        gid = order[rs["turn_index"] % len(order)]
        rs["round_index"] = rs["turn_index"] // len(order) + 1
        rs["active_gun_id"] = gid
        rs["elapsed"] = 0.0
        self._turn_triggered = False
        player = self.players.get(gid)
        new_round = rs["turn_index"] % len(order) == 0
        if new_round:
            rs["turn_phase"] = "ROUND_INTRO"
            rs["intro_played"] = False
            rs["intro_ends_at"] = 0.0
            if player is not None:
                self.add_event(
                    "system",
                    f"Round {rs['round_index']} of {max_rounds} — {player.display_name} is up first!",
                )
            self._refresh_race_next_gun_id()
            return
        self._enter_player_intro()
        if player is not None:
            if rs.get("launch_mode") == "auto":
                self.add_event(
                    "system",
                    f"{player.display_name} is up — countdown starting!",
                )
            else:
                self.add_event(
                    "system",
                    f"{player.display_name} is up — pull the trigger to start the run!",
                )
        self._refresh_race_next_gun_id()

    def skip_race_run(self, now: float | None = None) -> tuple[bool, str]:
        """Host skip: forfeit the current run and move on.

        RUNNING: the partial run is recorded unfinished with the
        points scored so far, then the beat is skipped. PLAYER_INTRO
        / WAIT_TRIGGER / COUNTDOWN: the unstarted run is recorded as
        an unfinished 0-point run and the machine advances (the next
        run gets its own Player callout; callouts are played
        synchronously, never queued, so a skipped run's callout
        cannot bleed into the next run). TURN_END: the beat is
        skipped. ROUND_INTRO: the announcement wait is skipped and
        the Player callout still plays. Any other state: rejected.
        """
        if self.phase != "PLAYING" or self.mode_name != "TagThenShoot":
            return False, "No race is running."
        rs = self.race_state
        if not rs:
            return False, "No race is running."
        now = time.time() if now is None else now
        phase = rs.get("turn_phase")
        if phase == "RUNNING":
            self._complete_race_turn(completed=False, now=now, reason="skip")
            rs["beat_ends_at"] = now
            return True, ""
        if phase in ("PLAYER_INTRO", "WAIT_TRIGGER", "COUNTDOWN"):
            gid = rs.get("active_gun_id")
            player = self.players.get(gid) if gid is not None else None
            if player is not None:
                self._record_run_result(player, False, None, points=0, tags=0, tower_hits=0)
                self.add_event("system", f"{player.display_name}'s run was skipped before it started.")
            self._advance_race_turn(now)
            return True, ""
        if phase == "TURN_END":
            self._advance_race_turn(now)
            return True, ""
        if phase == "ROUND_INTRO":
            rs["intro_played"] = True
            self._enter_player_intro()
            return True, ""
        return False, "Nothing to skip right now."

    def finalize_race_on_stop(self, now: float | None = None) -> bool:
        """Snapshot a live run when the match is stopped mid-run, so
        its points are not lost from the history/totals. Returns True
        when a partial run was recorded. Unstarted runs are NOT
        fabricated: they simply never ran."""
        if self.mode_name != "TagThenShoot":
            return False
        rs = self.race_state
        if not rs:
            return False
        now = time.time() if now is None else now
        recorded = False
        if rs.get("turn_phase") == "RUNNING":
            gid = rs.get("active_gun_id")
            player = self.players.get(gid) if gid is not None else None
            if player is not None:
                elapsed = max(0.0, now - rs.get("turn_started_at", now))
                rs["elapsed"] = elapsed
                self._record_run_result(player, False, None)
                recorded = True
        rs["turn_phase"] = "DONE"
        rs["active_gun_id"] = None
        self._refresh_race_next_gun_id()
        return recorded

    def race_next_gun_id(self) -> int | None:
        """The following racer's gun id (UP NEXT tag), or None.

        One derivation, shared by both displays via the
        ``race_state.next_gun_id`` snapshot field: the next run in the
        frozen order, wrapping to the first racer of the next round.
        None when the race is DONE, when the current run is the final
        run of the final round, and when the next run would be the
        same player (a solo race: the card must never carry both UP
        NOW and UP NEXT). Phase-independent on purpose: the tag shows
        consistently across ROUND_INTRO / WAIT_TRIGGER / COUNTDOWN /
        RUNNING / TURN_END, and skip-run advances it through
        _advance_race_turn like any other turn change.
        """
        rs = self.race_state
        if not rs or rs.get("turn_phase") == "DONE":
            return None
        order = rs.get("turn_order", [])
        if len(order) < 1:
            return None
        nxt_index = int(rs.get("turn_index", 0)) + 1
        if nxt_index >= len(order) * int(rs.get("max_rounds", 1)):
            return None
        nxt = order[nxt_index % len(order)]
        if nxt == rs.get("active_gun_id"):
            return None
        return nxt

    def _refresh_race_next_gun_id(self) -> None:
        """Store the derived next racer in race_state (snapshot field)."""
        if self.race_state:
            self.race_state["next_gun_id"] = self.race_next_gun_id()

    def race_racers(self) -> list[PlayerState]:
        """Rostered racers in frozen race order: the match snapshot
        turn_order while a match exists, else the setup roster."""
        rs = self.race_state
        order = rs.get("turn_order") if rs else None
        if order:
            return [self.players[gid] for gid in order if gid in self.players]
        return self.race_roster_players()

    def _race_best_single_points(self, player: PlayerState) -> int:
        """Best single-run points, including the live run if this
        player is running right now (its points are not in history
        yet)."""
        best = player.best_single_run_points
        rs = self.race_state
        if (
            rs
            and rs.get("turn_phase") == "RUNNING"
            and rs.get("active_gun_id") == player.gun_id
        ):
            best = max(best, player.tags + player.tower_hits)
        return best

    def race_rank_key(self, player: PlayerState) -> tuple[int, float, int, int, int]:
        """The ONE race ranking key, shared by sorting, LEDs, winner
        announcement, and both displays' mirrors: finishers by best
        time ascending; any finisher above any non-finisher;
        non-finishers by total points, then best single-run points,
        then gun number. Exact best-time ties share the win — there
        is no tiebreak chain."""
        best = player.best_run_time
        if best is not None:
            return (0, best, 0, 0, player.gun_num)
        return (1, 0.0, -player.score, -self._race_best_single_points(player), player.gun_num)

    def race_ranked_players(self) -> list[PlayerState]:
        """Rostered racers in race rank order (benched excluded)."""
        return sorted(self.race_racers(), key=self.race_rank_key)

    def race_winners(self) -> list[PlayerState]:
        """Shared-win winners: every racer tied on the best finished
        time, or (no finishes) every racer tied on top total points
        AND best single-round points — i.e. the players at place 1
        under race_rank_key, ignoring the gun-number ordering
        tiebreak. Benched players can never win."""
        racers = self.race_racers()
        if not racers:
            return []
        finished = [p for p in racers if p.best_run_time is not None]
        if finished:
            best = min(p.best_run_time for p in finished)
            return [p for p in finished if p.best_run_time == best]
        top = max(p.score for p in racers)
        leaders = [p for p in racers if p.score == top]
        best_single = max(p.best_single_run_points for p in leaders)
        return [p for p in leaders if p.best_single_run_points == best_single]


    def ingest_ubiconnect_hits(self, hits_by_gid: dict[int, int]) -> None:
        """Process 0xC3 IR shots received directly by the UbiConnect tower in TagThenShoot.

        Tower hits are one of the mode's two scoring channels (+1 each);
        the other is T-Base tags from status word 0. `player.frags` is
        kept in step with the tower-hit total because the poll downlink
        sends the running tower-hit count from it.
        """
        if self.mode_name != "TagThenShoot":
            return
        for gid, total_hits in hits_by_gid.items():
            player = self.players.get(gid)
            if not player:
                continue
            if total_hits > player.frags:
                delta = total_hits - player.frags
                player.frags = total_hits
                # Only the active runner scores during a timed turn.
                # The frags mirror above still advances for every gun,
                # so a stray tower shot during someone else's turn
                # never leaks into a later turn as phantom credit.
                if not self._race_run_active(player):
                    continue
                player.tower_hits += delta
                player.score += delta
                player.hits_landed += delta
                player.streak += delta
                player.best_streak = max(player.best_streak, player.streak)
                self.add_event(
                    "frag",
                    f"{player.display_name} shot the UbiConnect tower! (+{delta} pt)",
                    actor=player,
                    icon_url="/static/assets/textures/Ui/Pictograms/64x/RF08.png",
                )
                cue = self.audio.trigger_pool("tower_hit", custom_subtitle=f"{player.display_name} hit the UbiConnect tower!")
                self._check_lead_change()
                # The final tower shot finishes the run: all bases
                # tagged and every tower shot landed. Otherwise the
                # next base name plays as an advance prompt once the
                # tower-hit confirmation finishes (never on the final
                # hit: the finish line plays instead).
                bases = max(1, int(self.settings.get("numberOfBase", 4)))
                if player.tags >= bases and player.tower_hits >= bases:
                    self._complete_race_turn(completed=True)
                elif player.tags < bases:
                    self._schedule_next_base_prompt(time.time(), player.tags + 1, cue)

    def ingest_status_report(self, status: DecodedStatus) -> None:
        """Process a single gun's 0x95 status report and update scores, streaks, and kill-feed."""
        player = self.players.get(status.gun_id)
        if not player:
            return
        now = time.time()
        player.last_status_ts = now
        player.last_seen_ts = now

        # 1. Establish t=0 baseline so residual shift-register values are ignored
        if not player.baseline_set:
            player.baseline_set = True
            player.prev_hits_rec = status.hits_received or 0
            player.prev_shots = status.shots_fired or 0
            player.prev_life = status.life_points if status.life_points is not None else player.max_life
            player.prev_ammo = status.ammo if status.ammo is not None else player.max_ammo
            player.prev_clips = status.clips if status.clips is not None else player.max_clips
            player.prev_phase_flag = status.phase_flag if status.phase_flag is not None else 1
            player.prev_slots = list(status.hit_ids)
            return

        # 2. Update shots fired
        shots_delta = 0
        if status.shots_fired is not None:
            if status.shots_fired >= player.prev_shots:
                shots_delta = status.shots_fired - player.prev_shots
                player.shots_fired += shots_delta
            player.prev_shots = status.shots_fired
        elif status.ammo is not None:
            # Derive shots from ammo drop in modes that don't send nb_shot directly
            if status.clips == player.prev_clips and status.ammo < player.prev_ammo:
                shots_delta = player.prev_ammo - status.ammo
                player.shots_fired += shots_delta

        if shots_delta > 0:
            now = time.time()
            self.last_action_ts = now
            player.miss_streak += shots_delta
            # The original's blind/aim taunt fires after repeated
            # misses; the threshold is 5, matching its blind rule.
            # Gated off in TagThenShoot: tower shots are not misses,
            # and tower hits never reset the streak, so the taunt
            # would fire mid-run (worse across multiple rounds).
            if (
                self.mode_name != "TagThenShoot"
                and player.miss_streak >= 5
                and now - self._last_miss_taunt_ts >= 9.0
            ):
                player.miss_streak = 0
                self._last_miss_taunt_ts = now
                self.audio.trigger_pool(
                    "miss_taunt",
                    custom_subtitle=f"{player.display_name} — need some help aiming?",
                )

        # Update live ammo & clips readout
        if status.clips is not None:
            player.clips = status.clips
            player.prev_clips = status.clips

        ammo_dropped = False
        if status.ammo is not None:
            ammo_dropped = status.ammo < player.prev_ammo
            if (
                self.mode_name != "OldFashionDuel"
                and status.ammo > player.prev_ammo
                and (status.clips or 0) >= player.prev_clips
            ):
                player.last_rfid = "RF01"
                self.add_event(
                    "ammo",
                    f"{player.display_name} scanned an Ammo Card — clips reloaded!",
                    actor=player,
                    icon_url=RFID_CARDS["RF01"]["icon"],
                )
            player.ammo = status.ammo
            player.prev_ammo = status.ammo
            finite_ammo_mode = self.mode_name in ("TeamFrag", "FreeForAll", "StarterFrag", "LastManStanding")
            # The original's low-ammo event fires when ammo reaches
            # exactly 3, once per descent (a separate trigger from the
            # empty-ammo reminder below).
            if finite_ammo_mode and status.ammo == 3 and not player._low_ammo_warned:
                player._low_ammo_warned = True
                self.audio.trigger_pool("reload_reminder")
            elif status.ammo > 3:
                player._low_ammo_warned = False
            if finite_ammo_mode and player.ammo == 0 and player.clips == 0:
                now = time.time()
                if (
                    (not player._empty_ammo_warned or now - player._last_empty_warn_ts >= 25.0)
                    and now - self._last_reload_reminder_ts >= 25.0
                    and now - self.match_start_ts >= 5.0
                ):
                    player._empty_ammo_warned = True
                    player._last_empty_warn_ts = now
                    self._last_reload_reminder_ts = now
                    self.audio.trigger_pool("reload_reminder")
            elif player.ammo > 0 or player.clips > 0:
                player._empty_ammo_warned = False

        # 3. Mode-specific hit & frag resolution
        if self.mode_name in ("FreeFrag", "StarterFrag"):
            curr_hits = status.hits_received or 0
            delta = curr_hits - player.prev_hits_rec
            if delta > 0:
                player.hits_received += delta
                player.streak = 0
                shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                for sgid in shooters:
                    shooter = self.players.get(sgid)
                    if shooter:
                        self._record_hit(shooter, player)
            player.prev_hits_rec = curr_hits

        elif self.mode_name in ("TeamFrag", "FreeForAll", "LastManStanding"):
            curr_life = status.life_points if status.life_points is not None else player.life_points
            if curr_life < player.prev_life:
                delta = player.prev_life - curr_life
                player.hits_received += delta
                player.life_points = curr_life
                player.streak = 0
                shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                # "Medic!" when life drops to exactly 1.
                if curr_life == 1 and player.prev_life > 1:
                    self.audio.trigger_pool("low_life")
                if self.mode_name == "LastManStanding":
                    self._record_lms_hits(shooters, player, curr_life)
                else:
                    for i, sgid in enumerate(shooters):
                        shooter = self.players.get(sgid)
                        if shooter:
                            # The shift register is most-recent-first,
                            # so shooters[0] landed the killing blow.
                            is_frag = (curr_life == 0) and (i == 0)
                            self._record_hit(shooter, player, is_frag=is_frag)
                if curr_life == 0:
                    player.deaths += 1
                    if self.mode_name == "LastManStanding":
                        player.status_badge = "ELIMINATED"
                    else:
                        player.status_badge = "SCAN T-BASE"
            elif curr_life > player.prev_life:
                was_zero = player.prev_life == 0
                player.life_points = curr_life
                player.status_badge = "ACTIVE"
                if was_zero:
                    if self.mode_name == "TeamFrag":
                        team_rfid = TEAM_INFO.get(player.team_id, TEAM_INFO[1])["rfid_code"]
                        player.last_rfid = team_rfid
                        self.add_event(
                            "respawn",
                            f"{player.display_name} scanned {TEAM_INFO.get(player.team_id, TEAM_INFO[1])['name']} T-Base & respawned!",
                            actor=player,
                            icon_url=TEAM_INFO.get(player.team_id, TEAM_INFO[1])["rfid_tex"],
                        )
                    else:
                        # FreeForAll respawns are timer-only (no scan is
                        # involved); LastManStanding has no respawn at
                        # all, so a restore there is only ever a medkit
                        # edge case — neutral wording for both.
                        self.add_event(
                            "respawn",
                            f"{player.display_name} is back in the game!",
                            actor=player,
                        )
                    if self.mode_name in ("TeamFrag", "FreeForAll"):
                        self.audio.trigger_pool("respawn")
                else:
                    player.last_rfid = "RF07"
                    self.add_event(
                        "medkit",
                        f"{player.display_name} scanned a Medkit (+HP)!",
                        actor=player,
                        icon_url=RFID_CARDS["RF07"]["icon"],
                    )
                    self.audio.trigger_pool("medkit_used")
            player.prev_life = curr_life

        elif self.mode_name == "OldFashionDuel":
            curr_hits = status.hits_received or 0
            if self.duel_state.get("phase_code", 2) <= 1:
                rnd = self.duel_state.get("round", 1)
                can_shoot = self.duel_state.get("can_shoot", False)
                if not can_shoot and (curr_hits > player.prev_hits_rec or ammo_dropped):
                    # Firing (or landing a hit) before the DRAW signal:
                    # the opponent gets the round point and the round ends.
                    early_shooter: PlayerState | None = None
                    if curr_hits > player.prev_hits_rec:
                        delta = curr_hits - player.prev_hits_rec
                        player.hits_received += delta
                        shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                        early_shooter = self.players.get(shooters[0]) if shooters else None
                    else:
                        early_shooter = player
                    opponent = (
                        next((p for p in self.players.values() if p.gun_id != early_shooter.gun_id), None)
                        if early_shooter is not None
                        else None
                    )
                    if early_shooter is not None and opponent is not None:
                        opponent.score += 1
                        self._record_duel_round_result(opponent)
                        self.duel_state["last_winner_gun_num"] = opponent.gun_num
                        self.duel_state["last_result_text"] = (
                            f"EARLY FIRE BY P{early_shooter.gun_num} ({early_shooter.display_name})! "
                            f"+1 PT TO PLAYER {opponent.gun_num} ({opponent.display_name})"
                        )
                        self.add_event(
                            "duel",
                            f"Round {rnd}: {early_shooter.display_name} fired before FIRE! Point awarded to Player {opponent.gun_num} ({opponent.display_name})! (+1 pt)",
                            actor=opponent,
                            target=early_shooter,
                        )
                        cheat_sub = (
                            f"Early shot by {early_shooter.display_name}! "
                            f"Player {opponent.gun_num} ({opponent.display_name}) gets the point!"
                        )
                        self.audio.trigger_pool("duel_cheat", custom_subtitle=cheat_sub, force=True)
                        cheat_wait = max(0.5, self.audio.active_until - time.time()) + 0.25
                        self._announce_duel_round_win(opponent, initial_delay=cheat_wait)
                        next_wait = 9.5
                    else:
                        self._record_duel_round_result(None)
                        self.duel_state["last_winner_gun_num"] = None
                        self.duel_state["last_result_text"] = f"ROUND {rnd}: DRAW — NO POINT"
                        self.audio.trigger_pool("duel_drawn", force=True)
                        next_wait = 7.0
                    self.duel_state["subphase"] = "ROUND_END"
                    self.duel_state["phase_code"] = 2
                    self.duel_state["can_shoot"] = False
                    self.duel_state["next_transition_at"] = time.time() + next_wait
                    self._check_lead_change()
                elif can_shoot and curr_hits > player.prev_hits_rec:
                    delta = curr_hits - player.prev_hits_rec
                    player.hits_received += delta
                    shooters = count_new_shooters(player.prev_slots, status.hit_ids, delta)
                    shooter = self.players.get(shooters[0]) if shooters else None
                    if shooter:
                        shooter.score += 1
                        shooter.hits_landed += 1
                        shooter.frags += 1
                        self.duel_state["last_winner_gun_num"] = shooter.gun_num
                        self.duel_state["last_result_text"] = (
                            f"PLAYER {shooter.gun_num} ({shooter.display_name}) GETS THE POINT! (+1 PT)"
                        )
                        self.add_event(
                            "frag",
                            f"Round {rnd}: {shooter.display_name} outdrew {player.display_name}! (+1 pt)",
                            actor=shooter,
                            target=player,
                        )
                        self._record_duel_round_result(shooter)
                        self._announce_duel_round_win(shooter)
                    else:
                        self._record_duel_round_result(None)
                        self.duel_state["last_winner_gun_num"] = None
                        self.duel_state["last_result_text"] = f"ROUND {rnd}: DRAW — NO POINT"
                        self.audio.trigger_pool("duel_drawn", force=True)
                    self.duel_state["subphase"] = "ROUND_END"
                    self.duel_state["phase_code"] = 2
                    self.duel_state["can_shoot"] = False
                    self.duel_state["next_transition_at"] = time.time() + 7.0
                    self._check_lead_change()
                elif (
                    can_shoot
                    and len(self.players) >= 2
                    and all(p.ammo == 0 for p in self.players.values())
                ):
                    self.duel_state["subphase"] = "ROUND_END"
                    self.duel_state["phase_code"] = 2
                    self.duel_state["can_shoot"] = False
                    self.duel_state["next_transition_at"] = time.time() + 7.0
                    self.duel_state["last_winner_gun_num"] = None
                    self.duel_state["last_result_text"] = (
                        f"ROUND {rnd}: BOTH OUT OF AMMO — DRAW (NO POINT)"
                    )
                    self._record_duel_round_result(None)
                    self.add_event("duel", f"Round {rnd}: Both duelists missed — Draw!")
                    self.audio.trigger_pool("duel_drawn", force=True)
            player.prev_hits_rec = curr_hits

        elif self.mode_name == "TagThenShoot":
            # Status word 0 is the T-Base tag count (the gun's only SEND
            # var); tower hits arrive separately via 0xC3. Both score
            # +1, tracked and displayed as separate stats. The second
            # status word is not read: the original's RoundLoop never
            # looks at it and its meaning on real hardware is unknown.
            curr_tags = status.hits_received or 0
            if curr_tags > player.prev_hits_rec:
                delta = curr_tags - player.prev_hits_rec
                # Only the active runner scores during a timed turn;
                # the baseline below still advances for every gun, so
                # a stray scan during someone else's turn never counts
                # toward the stray gun's own run.
                if self._race_run_active(player):
                    player.tags += delta
                    player.score += delta
                    bases = max(1, int(self.settings.get("numberOfBase", 4)))
                    base_num = ((player.tags - 1) % bases) + 1
                    if delta == 1:
                        text = f"{player.display_name} tagged T-Base {base_num}! (+1 pt)"
                    else:
                        text = f"{player.display_name} tagged {delta} T-Bases! (+{delta} pt)"
                    self.add_event(
                        "hit",
                        text,
                        actor=player,
                        icon_url="/static/assets/textures/Ui/Pictograms/64x/RF08.png",
                    )
                    # Base-name callout (T-Base 1-4), then a confirmation
                    # line once the callout finishes. The gun enforces scan
                    # order, so the tag count modulo the base count is the
                    # stage of the base just tagged.
                    cue = self.audio.play_announcer_file(
                        f"DLG_GM_GLOBAL_{72 + base_num - 1:02d}.wav",
                        f"T-Base {base_num}!",
                        priority=2,
                        duration=1.5,
                    )
                    if cue is not None:
                        delay = max(0.1, self.audio.active_until - time.time()) + 0.25
                    else:
                        delay = 0.1
                    self._schedule_audio_followup(
                        delay,
                        "pool",
                        pool_key="tagnshoot_base",
                        subtitle=f"{player.display_name} tagged T-Base {base_num}!",
                        force=False,
                    )
                    self._check_lead_change()
                    # Safety net: a batched report can carry the last
                    # tag together with an already-landed final tower
                    # shot. Normally the tower path completes the run.
                    if player.tags >= bases and player.tower_hits >= bases:
                        self._complete_race_turn(completed=True)
            player.prev_hits_rec = curr_tags
            player.prev_phase_flag = status.phase_flag if status.phase_flag is not None else 1

        player.prev_slots = list(status.hit_ids)

    def _announce_duel_round_win(
        self, shooter: PlayerState, initial_delay: float = 0.0
    ) -> None:
        """Announce a duel round win: 'Player X!' (GLOBAL_54+gun_num) then
        'Wins the round!' (GLOBAL_119) once the callout finishes.

        Uses priority=-1 so the player-number callout preempts 'Fire!'
        (priority=0) both on the server and in the browser. When
        `initial_delay > 0` (e.g. after an early-fire foul line), both
        clips are scheduled sequentially via `_pending_audio`."""
        rnd = self.duel_state.get("round", 1)
        subtitle = f"Player {shooter.gun_num} ({shooter.display_name}) wins Round {rnd}! (+1 pt)"
        if 1 <= shooter.gun_num <= 16:
            player_file = f"DLG_GM_GLOBAL_{54 + shooter.gun_num:02d}.wav"
            player_sub = f"Player {shooter.gun_num} ({shooter.display_name})!"
            if initial_delay <= 0.0:
                self.audio.active_until = 0.0
                cue = self.audio.play_announcer_file(
                    player_file,
                    player_sub,
                    priority=-1,
                    duration=1.35,
                )
                if cue is not None:
                    delay = max(0.1, self.audio.active_until - time.time()) + 0.2
                else:
                    delay = 0.1
                self._schedule_audio_followup(
                    delay,
                    "pool",
                    pool_key="duel_round_win",
                    subtitle=subtitle,
                    force=True,
                )
            else:
                self._schedule_audio_followup(
                    initial_delay,
                    "file",
                    filename=player_file,
                    subtitle=player_sub,
                    priority=-1,
                    duration=1.35,
                )
                self._schedule_audio_followup(
                    initial_delay + 1.55,
                    "pool",
                    pool_key="duel_round_win",
                    subtitle=subtitle,
                    force=True,
                )
        else:
            if initial_delay <= 0.0:
                self.audio.trigger_pool(
                    "duel_round_win", custom_subtitle=subtitle, force=True
                )
            else:
                self._schedule_audio_followup(
                    initial_delay,
                    "pool",
                    pool_key="duel_round_win",
                    subtitle=subtitle,
                    force=True,
                )

    def _record_lms_hits(self, shooters: list[int], victim: PlayerState, curr_life: int) -> None:
        """Score LastManStanding hits by the original's survival formula.

        Hits and kills award no points — only counters. While alive a
        player's score is life + #players, recomputed when their life
        drops; on elimination it becomes #players - #alive, set once at
        the moment of elimination. Healing never raises the score: the
        recompute lives in the life-decrease branch only, as in the
        original's RoundLoop (a quirk, preserved).
        """
        num_players = len(self.players)
        for sgid in shooters:
            shooter = self.players.get(sgid)
            if not shooter:
                continue
            self.last_action_ts = time.time()
            shooter.miss_streak = 0
            shooter.hits_landed += 1
            shooter.last_tagged_target = victim.display_name
            victim.last_tagged_by = shooter.display_name
            victim.last_tagged_by_time = time.time()
            shooter.hits_dealt_to[victim.gun_num] = shooter.hits_dealt_to.get(victim.gun_num, 0) + 1
            victim.hits_taken_from[shooter.gun_num] = victim.hits_taken_from.get(shooter.gun_num, 0) + 1
            verb = random.choice(KILL_VERBS)
            self.add_event(
                "hit",
                f"{shooter.display_name} {verb} {victim.display_name}",
                actor=shooter,
                target=victim,
            )
            if curr_life > 0:
                self.audio.trigger_pool("hit_scored")
        if curr_life > 0:
            victim.score = victim.life_points + num_players
        else:
            alive = [p for p in self.players.values() if p.life_points > 0]
            victim.score = num_players - len(alive)
            killer = self.players.get(shooters[0]) if shooters else None
            if killer is not None:
                killer.frags += 1
                fverb = random.choice(FRAG_VERBS)
                self.add_event(
                    "frag",
                    f"ELIMINATED! {killer.display_name} {fverb} {victim.display_name}",
                    actor=killer,
                    target=victim,
                )
            else:
                self.add_event(
                    "frag",
                    f"ELIMINATED! {victim.display_name} is out!",
                    actor=victim,
                )
            self.audio.play_lms_elimination(
                len(alive),
                subtitle=f"{victim.display_name} is eliminated! {len(alive)} left!",
            )
        self._check_lead_change()

    def _record_hit(self, shooter: PlayerState, victim: PlayerState, is_frag: bool = False) -> None:
        self.last_action_ts = time.time()
        shooter.miss_streak = 0
        # Check friendly fire in TeamFrag
        if (
            self.mode_name == "TeamFrag"
            and shooter.team_id == victim.team_id
            and shooter.gun_id != victim.gun_id
        ):
            self.add_event(
                "hit",
                f"Friendly fire! {shooter.display_name} hit teammate {victim.display_name}",
                actor=shooter,
                target=victim,
            )
            self.audio.trigger_pool("friendly_fire")
            return

        shooter.hits_landed += 1
        shooter.score += 1
        shooter.streak += 1
        shooter.best_streak = max(shooter.best_streak, shooter.streak)
        shooter.last_tagged_target = victim.display_name
        victim.last_tagged_by = shooter.display_name
        victim.last_tagged_by_time = time.time()
        shooter.hits_dealt_to[victim.gun_num] = shooter.hits_dealt_to.get(victim.gun_num, 0) + 1
        victim.hits_taken_from[shooter.gun_num] = victim.hits_taken_from.get(shooter.gun_num, 0) + 1

        verb = random.choice(KILL_VERBS)
        self.add_event(
            "hit",
            f"{shooter.display_name} {verb} {victim.display_name} (+1 pt)",
            actor=shooter,
            target=victim,
        )

        if is_frag:
            shooter.frags += 1
            shooter.score += 3
            fverb = random.choice(FRAG_VERBS)
            self.add_event(
                "frag",
                f"FRAG! {shooter.display_name} {fverb} {victim.display_name} (+3 bonus)",
                actor=shooter,
                target=victim,
            )
            self.audio.trigger_pool("frag_scored", custom_subtitle=f"{shooter.display_name} fragged {victim.display_name}!")
        elif shooter.streak == 5:
            self.audio.trigger_pool("streak_5", custom_subtitle=f"{shooter.display_name} is on a 5-hit streak!")
        elif shooter.streak == 3:
            self.audio.trigger_pool("streak_3", custom_subtitle=f"{shooter.display_name} — 3-hit streak!")
        else:
            self.audio.trigger_pool("hit_scored")

        self._check_lead_change()

    def _check_lead_change(self) -> None:
        # Duel has no lead concept: rounds are announced by the
        # round-win callout + GLOBAL_119, and a generic lead-change
        # line would interrupt that sequence.
        if self.mode_name == "OldFashionDuel":
            return
        # Timed TagThenShoot turns have no lead concept either: players
        # run solo, one at a time, and a points lead mid-run says
        # nothing about who is fastest. Run results are announced at
        # completion instead.
        if self.mode_name == "TagThenShoot":
            return
        if self.mode_name == "TeamFrag":
            standings = self.get_team_standings()
            if len(standings) >= 2 and standings[0]["score"] > standings[1]["score"]:
                lead_key = f"team_{standings[0]['team_id']}"
                if lead_key != self.current_leader_key:
                    self.current_leader_key = lead_key
                    t_id = standings[0]["team_id"]
                    pool_map = {1: "lead_red", 2: "lead_blue", 3: "lead_yellow", 4: "lead_green"}
                    self.audio.trigger_pool(pool_map.get(t_id, "lead_red"))
        else:
            ranked = self.get_sorted_players()
            if len(ranked) >= 2 and ranked[0].score > ranked[1].score:
                lead_key = f"player_{ranked[0].gun_id}"
                if lead_key != self.current_leader_key:
                    self.current_leader_key = lead_key
                    self.audio.trigger_pool("lead_change_ffa", custom_subtitle=f"{ranked[0].display_name} takes the lead!")

    def get_sorted_players(self) -> list[PlayerState]:
        if self.mode_name == "LastManStanding":
            return sorted(
                self.players.values(),
                key=lambda p: (p.life_points > 0, p.life_points, p.score, p.hits_landed, -p.deaths),
                reverse=True,
            )
        if self.mode_name == "TagThenShoot":
            # Timed turns: rostered racers by race_rank_key (best
            # time, then points), with benched players appended in
            # gun-number order. Benched players stay in state.players
            # (setup screens and the roster API still need them); the
            # game displays hide them entirely, and they are excluded
            # from ranking/winners by race_ranked_players and
            # race_winners, not by disappearing here.
            racers = self.race_ranked_players()
            racer_ids = {p.gun_id for p in racers}
            benched = sorted(
                (p for p in self.players.values() if p.gun_id not in racer_ids),
                key=lambda p: p.gun_num,
            )
            return racers + benched
        return sorted(
            self.players.values(),
            key=lambda p: (p.score, p.hits_landed, -p.hits_received, p.accuracy),
            reverse=True,
        )

    def get_team_standings(self) -> list[dict[str, Any]]:
        active_teams: dict[int, dict[str, Any]] = {}
        for p in self.players.values():
            tid = p.team_id
            if tid not in active_teams:
                tinfo = TEAM_INFO.get(tid, TEAM_INFO[1])
                active_teams[tid] = {
                    "team_id": tid,
                    "name": tinfo["name"],
                    "short": tinfo["short"],
                    "color": tinfo["color"],
                    "emblem_tex": tinfo["emblem_tex"],
                    "leader_tex": tinfo["leader_tex"],
                    "slot_tex": tinfo["slot_tex"],
                    "score": 0,
                    "hits_landed": 0,
                    "frags": 0,
                    "players": [],
                }
            active_teams[tid]["score"] += p.score
            active_teams[tid]["hits_landed"] += p.hits_landed
            active_teams[tid]["frags"] += p.frags
            active_teams[tid]["players"].append(p.to_dict())

        return sorted(
            active_teams.values(),
            key=lambda t: (t["score"], t["hits_landed"], t["frags"]),
            reverse=True,
        )

    def compute_gameover_outcomes(self) -> dict[int, bool]:
        """Compute per-gun `won: bool` for 0x94 game-over packets.

        Per freefrag_single_gun.py:
        - Outright top scorer(s) get `True` (green LED `0x01`).
        - Ties at the top or losers get `False` (red LED `0x02`).
        """
        if not self.players:
            return {}
        if self.mode_name == "LastManStanding":
            # Survival decides: every player still alive gets winner
            # LEDs regardless of score, so the sole survivor is never
            # shown red behind an eliminated player's higher score. If
            # somehow nobody is alive, fall back to the score rule.
            alive = {gid for gid, p in self.players.items() if p.life_points > 0}
            if alive:
                return {gid: gid in alive for gid in self.players}
        if self.mode_name == "TeamFrag":
            teams = self.get_team_standings()
            if not teams:
                return {gid: False for gid in self.players}
            top_score = teams[0]["score"]
            top_teams = [t["team_id"] for t in teams if t["score"] == top_score]
            is_tie = len(top_teams) > 1
            return {
                gid: (not is_tie and p.team_id == top_teams[0])
                for gid, p in self.players.items()
            }

        if self.mode_name == "TagThenShoot":
            # Timed turns, shared win: every racer tied on the best
            # finished time gets a winner LED; with no finishes,
            # every racer tied on top total points does. Benched
            # players get NO outcome entry at all — no win/loss
            # packet is sent to their guns.
            winners = {p.gun_id for p in self.race_winners()}
            return {p.gun_id: p.gun_id in winners for p in self.race_racers()}

        ranked = self.get_sorted_players()
        top_score = ranked[0].score
        winners = [p.gun_id for p in ranked if p.score == top_score]
        is_tie = len(winners) > 1
        return {
            gid: (not is_tie and gid == winners[0])
            for gid in self.players
        }

    def check_idle_announcer(self) -> None:
        """Fire idle banter when no shots/hits happen for a stretch during PLAYING."""
        # Duel is excluded (its standoff silence is the game), and so
        # is the race: tags do not count as action, so banter would
        # fire during trigger waits and slow walks between bases.
        if self.phase != "PLAYING" or self.mode_name in ("OldFashionDuel", "TagThenShoot"):
            return
        now = time.time()
        remaining = self.get_remaining_seconds()
        if remaining is not None and remaining <= 12:
            return
        if now - self.last_action_ts >= 8.0 and now - self._last_idle_banter_ts >= 10.0:
            self._last_idle_banter_ts = now
            self.audio.trigger_pool("idle_banter")

    def get_disconnect_prompt(self, now: float | None = None) -> dict[str, Any] | None:
        """Mid-match disconnect prompt, or None while the match looks healthy.

        Never auto-removes and never auto-ends: the host decides, from
        the prompt. Two rules, following the original's per-mode
        behavior (its Pinger is protected mid-match, so a dropout ends
        the match only through the host-facing prompt):
        - OldFashionDuel / TagThenShoot: any player going silent
          prompts at once — these modes need every gun answering.
        - Other modes: prompt once at most one gun is still answering
          after the disconnect timeout.
        A gun counts as silent when it answered this match and then
        went quiet past the threshold, or never answered at all once
        the match is older than the threshold — so the prompt never
        flashes in the first seconds while polls are still starting.
        Silent players' scores are frozen by construction: scores only
        change when a status report arrives.
        """
        if self.phase != "PLAYING" or not self.players:
            return None
        now = time.time() if now is None else now
        match_age = now - self.match_start_ts if self.match_start_ts > 0 else 0.0

        def silent_for(player: PlayerState, threshold: float) -> bool:
            if player.last_status_ts > 0:
                return (now - player.last_status_ts) > threshold
            return match_age > threshold

        if self.mode_name == "TagThenShoot":
            # Mode-critical, but only for rostered racers: a benched
            # gun going silent must never prompt (it is not racing).
            racers = self.race_racers()
            silent = [p for p in racers if silent_for(p, SIGNAL_LOST_TIMEOUT_S)]
            if silent:
                return {
                    "reason": "mode_critical",
                    "silent_gun_nums": [p.gun_num for p in silent],
                    "silent_names": [p.display_name for p in silent],
                    "answering_count": len(racers) - len(silent),
                    "total_count": len(racers),
                }
            return None
        if self.mode_name == "OldFashionDuel":
            silent = [p for p in self.players.values() if silent_for(p, SIGNAL_LOST_TIMEOUT_S)]
            if silent:
                return {
                    "reason": "mode_critical",
                    "silent_gun_nums": [p.gun_num for p in silent],
                    "silent_names": [p.display_name for p in silent],
                    "answering_count": len(self.players) - len(silent),
                    "total_count": len(self.players),
                }
            return None
        silent = [p for p in self.players.values() if silent_for(p, MATCH_DISCONNECT_TIMEOUT_S)]
        answering_count = len(self.players) - len(silent)
        if len(self.players) >= 2 and answering_count <= 1:
            return {
                "reason": "last_gun",
                "silent_gun_nums": [p.gun_num for p in silent],
                "silent_names": [p.display_name for p in silent],
                "answering_count": answering_count,
                "total_count": len(self.players),
            }
        return None

    def check_auto_end_conditions(self) -> bool:
        """Return True if the match should transition to GAME_OVER."""
        if self.phase != "PLAYING":
            return False
        # Timerless modes (duel, LMS) never end on the clock; the
        # original has no stalemate rule for them either. TagThenShoot
        # is excluded too: its cap belongs to each timed turn, enforced
        # by the turn machine, not to the match.
        if (
            self._is_timed_mode()
            and self.mode_name != "TagThenShoot"
            and (self.get_remaining_seconds() or 0.0) <= 0
        ):
            return True
        score_limit = int(self.settings.get("scoreLimit", 0))
        if score_limit > 0 and any(p.score >= score_limit for p in self.players.values()):
            return True
        if self.mode_name == "OldFashionDuel" and self.duel_state.get("subphase") == "COMPLETE":
            return True
        if self.mode_name == "TagThenShoot" and self.race_state.get("turn_phase") == "DONE":
            return True
        if self.mode_name == "LastManStanding" and len(self.players) >= 2:
            alive = [p for p in self.players.values() if p.life_points > 0]
            if len(alive) <= 1:
                return True
        return False

    def record_score_snapshot(self, force: bool = False) -> None:
        """Record a (t, scores) sample when any score changes or when forced at match end."""
        if not self.players:
            return
        elapsed = round(max(0.0, time.time() - self.match_start_ts), 1) if self.match_start_ts > 0 else 0.0
        scores = {str(p.gun_num): p.score for p in self.players.values()}
        if self.score_history:
            last = self.score_history[-1]
            if not force and last["scores"] == scores:
                return
            if abs(elapsed - last["t"]) < 0.1:
                last["scores"] = scores
                return
        self.score_history.append({"t": elapsed, "scores": scores})

    def get_snapshot(self, base_url: str = "http://localhost:3466") -> dict[str, Any]:
        now = time.time()
        if self.phase == "PLAYING":
            self.record_score_snapshot()

        ranked = [p.to_dict() for p in self.get_sorted_players()]
        teams = self.get_team_standings() if MODE_CATALOG[self.mode_name]["teams"] else []
        remaining = self.get_remaining_seconds()

        return {
            "phase": self.phase,
            "wizard_step": self.wizard_step,
            "profile_write_status": {str(k): dict(v) for k, v in self.profile_write_status.items()},
            "bytecode_ready": self.bytecode_ready,
            "auto_pair_active": self.auto_pair_active,
            "use_simulator": self.use_simulator,
            "audio_assets_ready": audio_assets_ready(),
            "hardware_write_confirmed": self.hardware_write_confirmed,
            "usb_status": detect_ubiconnect_readonly(),
            "mode": MODE_CATALOG[self.mode_name],
            "settings": self.settings,
            "global_settings": dict(self.global_settings),
            "upload_failed_guns": list(self.last_upload_failed_guns),
            "clock": self.format_clock(),
            # None for timerless modes (duel, LMS): the clock counts up.
            "remaining_seconds": None if remaining is None else round(remaining, 1),
            "countdown_remaining": self.countdown_remaining,
            "upload_progress": self.upload_progress,
            "players": ranked,
            "teams": teams,
            "events": [e.to_dict() for e in self.events[:25]],
            "disconnect_prompt": self.get_disconnect_prompt(now),
            "lobby_notices": list(self.lobby_notices),
            "config_retry_pending": [
                self.players[g].gun_num for g in self.config_failed_guns if g in self.players
            ],
            "impatient_status": self.get_impatient_status(now),
            "duel_state": self.duel_state if self.mode_name == "OldFashionDuel" else None,
            "race_state": self.race_state if self.mode_name == "TagThenShoot" else None,
            "race_roster": self.race_roster_snapshot(),
            "race_session_best": (
                [
                    {"gun_id": gid, "bases": bases, **dict(v)}
                    for (gid, bases), v in sorted(self.race_session_best.items())
                ]
                if self.mode_name == "TagThenShoot"
                else None
            ),
            "avatars": AVATAR_NAMES,
            "team_catalog": list(TEAM_INFO.values()),
            "rfid_catalog": list(RFID_CARDS.values()),
            "mode_catalog": list(MODE_CATALOG.values()),
            "tier2_preview": TIER2_PREVIEW,
            "base_url": base_url,
            "match_history": self.match_history[-5:],
            "score_history": list(self.score_history),
            "results_revealed": self.results_revealed,
        }

