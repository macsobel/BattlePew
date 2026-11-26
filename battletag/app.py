"""FastAPI + asyncio server for Battle Tag (Arena Master UI + Phone HUD + Audio)."""

from __future__ import annotations

import asyncio
import io
import os
import pathlib
import random
import socket
import time
from typing import Any

import qrcode
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import battletag.audio as audio_mod
from battletag import __version__
from battletag.asset_extractor import extract_audio_from_installer
from battletag.audio import USER_ASSETS_DIR, AudioCue, refresh_audio_paths
from battletag.hardware import RealHIDTransport, detect_ubiconnect_readonly, load_mode_sequences
from battletag.modes import MODE_CATALOG, ArenaEngine
from battletag.profile_writes import ProfileWriteQueue, WriteJob, execute_profile_write
from battletag.protocol import build_profile_block
from battletag.updates import UpdateChecker

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "battletag" / "static"


def get_lan_ip() -> str:
    """Detect the Mac's primary LAN IPv4 address for the Phone HUD QR code."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


app = FastAPI(title="BattlePew — Arena Master", version=__version__)

# Serve /static/assets/* from repo static/assets (e.g. custom icons) first,
# falling back to extracted user assets (textures/fonts) before /static mount.
STATIC_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/static/assets/{asset_path:path}")
async def serve_static_asset(asset_path: str) -> FileResponse:
    bundled_root = (STATIC_DIR / "assets").resolve()
    bundled_target = (bundled_root / asset_path).resolve()
    if bundled_target.is_relative_to(bundled_root) and bundled_target.is_file():
        return FileResponse(bundled_target)
    user_root = audio_mod.resolve_static_assets_dir().resolve()
    user_target = (user_root / asset_path).resolve()
    if user_target.is_relative_to(user_root) and user_target.is_file():
        return FileResponse(user_target)
    raise HTTPException(status_code=404, detail="Static asset not found")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/audio/{category}/{filename:path}")
async def serve_audio_file(category: str, filename: str) -> FileResponse:
    dirs = {
        "announcer": audio_mod.ANNOUNCER_DIR,
        "ui": audio_mod.UI_SFX_DIR,
        "music": audio_mod.MUSIC_DIR,
        "gun": audio_mod.GUN_SOUNDS_DIR,
    }
    base_dir = dirs.get(category)
    if base_dir is None or not base_dir.is_dir():
        raise HTTPException(status_code=404, detail="Audio category not available")
    target = (base_dir / filename).resolve()
    if target.is_relative_to(base_dir.resolve()) and target.is_file():
        return FileResponse(target)
    if category == "music" and filename.endswith(".wav"):
        ogg_target = (base_dir / (filename[:-4] + ".ogg")).resolve()
        if ogg_target.is_relative_to(base_dir.resolve()) and ogg_target.is_file():
            return FileResponse(ogg_target, media_type="audio/ogg")
    raise HTTPException(status_code=404, detail="Audio file not found")


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale: list[WebSocket] = []
        for ws in list(self.active):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


ws_manager = ConnectionManager()
pending_notifications: list[dict[str, Any]] = []


def _enqueue_notification(msg: dict[str, Any]) -> None:
    pending_notifications.append(msg)


async def _broadcast_toast(
    message: str,
    *,
    kind: str | None = None,
    name: str | None = None,
    avatar: str | None = None,
    gun_num: int | None = None,
    icon_url: str | None = None,
) -> None:
    """Push a temporary top-middle pill toast to all connected UI clients.

    `kind` is the frontend's bundling key: same-kind toasts arriving
    within a few seconds coalesce into one pill (e.g. six vest
    disconnects become "6 vests disconnected"). Kinds without a
    frontend template show individually, verbatim.
    """
    payload: dict[str, Any] = {"message": message}
    if kind is not None:
        payload["kind"] = kind
    if name is not None:
        payload["name"] = name
    if avatar is not None:
        payload["avatar"] = avatar
    if gun_num is not None:
        payload["gun_num"] = gun_num
    if icon_url is not None:
        payload["icon_url"] = icon_url
    await ws_manager.broadcast({"type": "toast", "data": payload})


engine = ArenaEngine(notify_cb=_enqueue_notification)
# Never pre-populate the lobby with simulator players on startup.
engine.players.clear()
engine.hardware_write_confirmed = os.environ.get("BATTLETAG_CONFIRM_WRITE", "0") == "1"
if engine.hardware_write_confirmed:
    engine.use_simulator = False

# Profile writes from all interfaces funnel through one background queue
# so concurrent saves never interleave on the single HID handle.
profile_write_queue = ProfileWriteQueue()
update_checker = UpdateChecker(current_version=__version__)

_match_task: asyncio.Task | None = None
_bot_demo_enabled: bool = False


def _new_hardware_transport() -> RealHIDTransport:
    """New transport sharing the engine's ref->number memory and
    host-Remove ignore list, so guns re-pairing after a UbiConnect
    loss reclaim their player numbers (instead of taking them in
    announcement order) and removed guns stay removed."""
    return RealHIDTransport(
        confirm_write=True,
        number_memory=engine.ref_number_memory,
        ignored_refs=engine.ignored_refs,
    )


def _configured_port() -> int:
    try:
        return int(os.environ.get("BATTLETAG_PORT", "3466"))
    except ValueError:
        return 3466


def get_base_url(request: Request | None = None) -> str:
    lan_ip = get_lan_ip()
    port = _configured_port()
    if request and request.url.port:
        port = request.url.port
    return f"http://{lan_ip}:{port}"


def _build_state_snapshot(request: Request | None = None) -> dict[str, Any]:
    snap = engine.get_snapshot(base_url=get_base_url(request))
    snap["update_status"] = update_checker.to_dict()
    return snap


async def broadcast_state() -> None:
    while pending_notifications:
        note = pending_notifications.pop(0)
        await ws_manager.broadcast(note)
    snap = _build_state_snapshot()
    await ws_manager.broadcast({"type": "state", "data": snap})


# --- Request Schemas ---
class ModeSelectRequest(BaseModel):
    mode: str
    settings: dict[str, Any] | None = None


class SettingsPatchRequest(BaseModel):
    settings: dict[str, Any]


class GlobalSettingsRequest(BaseModel):
    settings: dict[str, Any]


class ProfileUpdateRequest(BaseModel):
    gun_num: int
    name: str | None = None
    avatar: str | None = None
    team_id: int | None = None


class ProfileWriteRequest(BaseModel):
    name: str
    avatar: str


class WizardStepRequest(BaseModel):
    step: int


class RaceRosterRequest(BaseModel):
    # Full roster (TV): order + bench partition the paired guns.
    order: list[int] | None = None
    bench: list[int] | None = None
    # Single toggle (phone HUD): opt one player in or out.
    gun_num: int | None = None
    in_race: bool | None = None


class SimActionRequest(BaseModel):
    action: str  # "shot" | "hit" | "rfid" | "set_guns" | "toggle_bots"
    shooter_gid: int | None = None
    target_gid: int | None = None
    gun_id: int | None = None
    rfid_code: str | None = None
    num_guns: int | None = None
    enable_bots: bool | None = None


class TransportToggleRequest(BaseModel):
    use_simulator: bool
    confirm_write: bool = False


# --- HTML Pages ---
@app.get("/", response_class=HTMLResponse)
async def serve_arena_master() -> HTMLResponse:
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Arena Master UI loading...</h1>")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/join", response_class=HTMLResponse)
async def serve_join_page() -> HTMLResponse:
    """Player-number picker every phone lands on after scanning the QR
    code. Picking a number opens that gun's locked HUD; changing guns
    means rescanning the QR code."""
    html_path = STATIC_DIR / "join.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Join loading...</h1>")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/hud")
async def redirect_hud_to_join() -> RedirectResponse:
    """The bare /hud URL no longer serves a switchable HUD — phones join
    through the player picker."""
    return RedirectResponse("/join", status_code=307)


@app.get("/player/{gun_num}", response_class=HTMLResponse)
async def serve_phone_hud(gun_num: int | None = None) -> HTMLResponse:
    html_path = STATIC_DIR / "hud.html"
    if not html_path.exists():
        return HTMLResponse("<h1>Phone HUD loading...</h1>")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# --- QR Code Endpoint ---
@app.get("/api/qr")
async def generate_qr_code(
    request: Request,
    gun_num: int | None = Query(default=None),
) -> Response:
    base = get_base_url(request)
    target_url = f"{base}/player/{gun_num}" if gun_num else f"{base}/join"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(target_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#141821", back_color="#FFFFFF")
    buf = io.BytesIO()
    # pyrefly: ignore[unexpected-keyword]
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


# --- State & Control Endpoints ---
@app.get("/api/state")
async def get_state(request: Request) -> JSONResponse:
    return JSONResponse(_build_state_snapshot(request=request))


@app.get("/api/updates")
async def get_updates() -> JSONResponse:
    return JSONResponse(update_checker.to_dict())


@app.post("/api/updates/check")
async def check_updates() -> JSONResponse:
    result = await update_checker.check_async(force=True)
    await broadcast_state()
    return JSONResponse(result)


@app.post("/api/audio/ubiconnect-connected")
async def api_play_ubiconnect_connected_sfx() -> JSONResponse:
    """Fallback host audio playback when browser autoplay blocks Web UI SFX."""
    engine.audio._play_local_nonblocking(
        AudioCue(category="ui", url="/audio/ui/GunDetect.wav", filename="GunDetect.wav")
    )
    return JSONResponse({"ok": True})


class ExtractAssetsRequest(BaseModel):
    path: str


_asset_extract_status: dict[str, Any] = {
    "running": False,
    "progress": 0.0,
    "stage": "",
    "error": "",
    "done": False,
}


def _run_asset_extraction(source_path: pathlib.Path, cleanup_temp: bool = False) -> None:
    try:
        def on_progress(frac: float, stage: str) -> None:
            _asset_extract_status["progress"] = round(float(frac), 3)
            _asset_extract_status["stage"] = stage

        extract_audio_from_installer(source_path, USER_ASSETS_DIR, progress_cb=on_progress)
        refresh_audio_paths()
        _asset_extract_status.update({"running": False, "progress": 1.0, "stage": "Audio assets ready!", "error": "", "done": True})
    except Exception as exc:
        _asset_extract_status.update({"running": False, "error": str(exc), "stage": "Extraction failed", "done": False})
    finally:
        if cleanup_temp:
            try:
                source_path.unlink(missing_ok=True)
            except Exception:
                pass


@app.post("/api/assets/extract")
async def api_extract_assets(req: ExtractAssetsRequest) -> JSONResponse:
    if _asset_extract_status["running"]:
        return JSONResponse({"ok": False, "detail": "Extraction already in progress."}, status_code=409)
    source_path = pathlib.Path(req.path).expanduser()
    if not source_path.is_file():
        raise HTTPException(status_code=400, detail=f"File not found: {source_path}")
    _asset_extract_status.update({"running": True, "progress": 0.02, "stage": "Starting extraction…", "error": "", "done": False})
    loop = asyncio.get_running_loop()

    async def _worker() -> None:
        await loop.run_in_executor(None, _run_asset_extraction, source_path, False)
        await broadcast_state()

    asyncio.create_task(_worker())
    return JSONResponse({"ok": True})


@app.post("/api/assets/extract-upload")
async def api_extract_assets_upload(request: Request) -> JSONResponse:
    if _asset_extract_status["running"]:
        return JSONResponse({"ok": False, "detail": "Extraction already in progress."}, status_code=409)
    import tempfile
    raw_name = request.headers.get("x-filename", "installer.zip")
    suffix = pathlib.Path(raw_name).suffix or ".zip"
    tmp_fd, tmp_name = tempfile.mkstemp(prefix="battletag_installer_", suffix=suffix)
    os.close(tmp_fd)
    tmp_path = pathlib.Path(tmp_name)
    with tmp_path.open("wb") as out:
        async for chunk in request.stream():
            out.write(chunk)
    _asset_extract_status.update({"running": True, "progress": 0.02, "stage": "Starting extraction…", "error": "", "done": False})
    loop = asyncio.get_running_loop()

    async def _worker() -> None:
        await loop.run_in_executor(None, _run_asset_extraction, tmp_path, True)
        await broadcast_state()

    asyncio.create_task(_worker())
    return JSONResponse({"ok": True})


@app.get("/api/assets/extract-status")
async def api_extract_assets_status() -> JSONResponse:
    return JSONResponse(dict(_asset_extract_status))


@app.post("/api/mode")
async def api_select_mode(req: ModeSelectRequest) -> JSONResponse:
    if engine.phase not in ("LOBBY", "GAME_OVER"):
        raise HTTPException(status_code=400, detail="Cannot change mode during an active match.")
    engine.phase = "LOBBY"
    engine.select_mode(req.mode, req.settings)
    await broadcast_state()
    return JSONResponse({"ok": True, "mode": req.mode})


@app.post("/api/settings")
async def api_update_settings(req: SettingsPatchRequest) -> JSONResponse:
    if engine.phase not in ("LOBBY", "GAME_OVER"):
        raise HTTPException(status_code=400, detail="Cannot change settings during an active match.")
    engine.update_settings(req.settings)
    await broadcast_state()
    return JSONResponse({"ok": True, "settings": engine.settings})


@app.get("/api/global-settings")
async def api_get_global_settings() -> JSONResponse:
    return JSONResponse({"ok": True, "settings": engine.global_settings})


@app.post("/api/global-settings")
async def api_update_global_settings(req: GlobalSettingsRequest) -> JSONResponse:
    # Audio volumes apply live even during an active match; beam power and
    # gun volume are baked into the 0x92 config so they are safe to change in
    # setup but not while a match is uploading, counting down, or running.
    if engine.phase in ("UPLOADING", "COUNTDOWN", "PLAYING") and any(
        k in ("beam_power", "gun_volume") for k in req.settings
    ):
        raise HTTPException(status_code=400, detail="Cannot change T-Blaster settings during an active match.")
    updated = engine.update_global_settings(req.settings)
    await broadcast_state()
    return JSONResponse({"ok": True, "settings": updated})


@app.post("/api/profile")
async def api_update_profile(req: ProfileUpdateRequest) -> JSONResponse:
    """Apply a team change. Names/avatars are NOT accepted here: a name or
    avatar save writes to the gun's flash via the profile-write queue
    (POST /api/players/{gun_num}/write-profile), so the display can never
    drift from what the gun stores."""
    if req.name is not None or req.avatar is not None:
        raise HTTPException(
            status_code=400,
            detail="Change names and avatars in the profile editor — saving writes them to the gun.",
        )
    if engine.phase != "LOBBY" or engine.wizard_step != 2 or not MODE_CATALOG.get(engine.mode_name, {}).get("teams"):
        raise HTTPException(
            status_code=400,
            detail="Teams can only be changed on the game selection screen in a team mode.",
        )
    try:
        p = engine.update_player_profile(
            gun_num=req.gun_num,
            team_id=req.team_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not p:
        raise HTTPException(status_code=404, detail=f"Player {req.gun_num} not found")
    await broadcast_state()
    return JSONResponse({"ok": True, "player": p.to_dict()})


@app.post("/api/wizard-step")
async def api_set_wizard_step(req: WizardStepRequest) -> JSONResponse:
    """Record which wizard step the main screen is showing (1 = Pair Guns,
    2 = Mode & Settings). Profile editing is open only on step 1, from
    either interface, so the HUD gates on this too."""
    step = engine.set_wizard_step(req.step)
    await broadcast_state()
    return JSONResponse({"ok": True, "wizard_step": step})


@app.post("/api/players/{gun_num}/impatient")
async def api_player_impatient(gun_num: int) -> JSONResponse:
    result = engine.trigger_impatient(gun_num)
    if not result.get("ok"):
        return JSONResponse(result, status_code=int(result.get("status_code", 400)))
    await ws_manager.broadcast({
        "type": "impatient_toast",
        "data": {
            "gun_num": result["gun_num"],
            "name": result["name"],
            "avatar": result["avatar"],
        },
    })
    await broadcast_state()
    return JSONResponse(result)


@app.post("/api/players/{gun_num}/write-profile", status_code=202)
async def api_write_profile_to_gun(gun_num: int, req: ProfileWriteRequest) -> JSONResponse:
    """Queue a name/avatar save for one gun's flash memory.

    Saving in either interface (roster editor or phone HUD) writes to
    the gun immediately — there is no separate "save to gun" step. The
    write runs in the background queue (one at a time, newest save per
    gun wins); progress arrives via the snapshot's profile_write_status.
    Editing is open only on the pairing page, in setup phases. A save
    that changes nothing is a no-op and never touches the gun.
    """
    if not engine.profiles_editable():
        raise HTTPException(status_code=400, detail=engine.profiles_locked_reason())
    player = next((p for p in engine.players.values() if p.gun_num == gun_num), None)
    if player is None:
        raise HTTPException(status_code=404, detail=f"Player {gun_num} not found")
    try:
        build_profile_block(req.name, req.avatar)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not engine.use_simulator and not engine.hardware_write_confirmed:
        raise HTTPException(
            status_code=403,
            detail="Writing to a T-Blaster requires explicit hardware-write authorization.",
        )
    clean_name = req.name.strip()
    if clean_name == player.name and req.avatar == player.avatar:
        return JSONResponse({"ok": True, "unchanged": True})
    profile_write_queue.submit(WriteJob(gun_num=gun_num, name=clean_name, avatar=req.avatar))
    engine.profile_write_status[gun_num] = {"state": "queued", "detail": ""}
    await broadcast_state()
    return JSONResponse({"ok": True, "queued": True}, status_code=202)


@app.post("/api/players/{gun_num}/remove")
async def api_remove_player(gun_num: int) -> JSONResponse:
    """Host Remove: drop one player from the pairing page, pre-match only.

    Host-side only — no wire "unpair" message exists. The gun is
    unbound and ignore-listed so the auto-pair loop does not instantly
    re-add it; a fresh pairing scan clears the ignore list. Other
    players and their numbers are untouched, and the ref->number
    memory keeps the number for the gun's return.
    """
    if engine.phase != "LOBBY":
        raise HTTPException(status_code=400, detail="Players can only be removed before a match.")
    player = next((p for p in engine.players.values() if p.gun_num == gun_num), None)
    if player is None:
        raise HTTPException(status_code=404, detail=f"Player {gun_num} not found")
    ref_hex = player.ref_hex
    removed = engine.remove_player(gun_num)
    if removed is None:
        raise HTTPException(status_code=400, detail="Players can only be removed before a match.")
    if not engine.use_simulator and engine.hw is not None and ref_hex:
        engine.hw.unpair_gun(ref_hex, ignore=True)
    engine.audio.play_ui("ChangeValue.wav")
    await _broadcast_toast(
        "was removed by the host.",
        kind="player_removed_host",
        name=removed.display_name,
        avatar=removed.avatar,
        gun_num=removed.gun_num,
    )
    await broadcast_state()
    return JSONResponse({"ok": True, "removed_gun_num": gun_num})


async def _profile_write_worker() -> None:
    """Run queued profile writes one at a time, forever.

    The worker owns the "writing" status; execute_profile_write sets the
    terminal status. A job that fails (gun gone, match started, no ACK)
    is reported in the status, never retried silently, and never blocks
    the queue.
    """
    while True:
        job = await profile_write_queue.take()
        engine.profile_write_status[job.gun_num] = {"state": "writing", "detail": ""}
        await broadcast_state()
        try:
            await asyncio.to_thread(execute_profile_write, engine, job)
        except Exception as exc:  # noqa: BLE001 - a bad job must not kill the worker
            engine.profile_write_status[job.gun_num] = {"state": "failed", "detail": str(exc)}
        await broadcast_state()


@app.post("/api/transport")
async def api_toggle_transport(req: TransportToggleRequest) -> JSONResponse:
    if not req.use_simulator and not (req.confirm_write or engine.hardware_write_confirmed):
        raise HTTPException(
            status_code=403,
            detail="Real hardware mode requires explicit confirm_write=True authorization from the user.",
        )
    prev_sim = engine.use_simulator
    engine.use_simulator = req.use_simulator
    if req.confirm_write:
        engine.hardware_write_confirmed = True
    if prev_sim and not engine.use_simulator:
        engine.players.clear()
        engine.bytecode_ready = False
        engine.bytecode_uploaded = False
        engine.config_failed_guns = []
    elif not prev_sim and engine.use_simulator and not engine.players:
        engine._sync_players_from_sim()
        engine.bytecode_ready = False
        engine.bytecode_uploaded = False
        engine.config_failed_guns = []
    await broadcast_state()
    return JSONResponse(
        {
            "ok": True,
            "use_simulator": engine.use_simulator,
            "hardware_write_confirmed": engine.hardware_write_confirmed,
        }
    )


@app.post("/api/match/start")
async def api_start_match() -> JSONResponse:
    global _match_task
    if engine.phase in ("UPLOADING", "COUNTDOWN", "PLAYING"):
        raise HTTPException(status_code=400, detail="Match is already running.")
    mode_info = MODE_CATALOG.get(engine.mode_name, {})
    min_required = int(mode_info.get("min_players", 2))
    max_allowed = int(mode_info.get("max_players", 8))
    mode_title = mode_info.get("title", engine.mode_name)
    if engine.mode_name == "TagThenShoot":
        # The race gates on the ROSTER, not the pairing: benched
        # players neither count toward the player limits nor block
        # the start with a missing vest / offline blaster.
        racers = engine.race_roster_players()
        if len(racers) < min_required:
            raise HTTPException(
                status_code=400,
                detail=f"Need at least {min_required} racer{'s' if min_required != 1 else ''} to start {mode_title}.",
            )
        if len(racers) > max_allowed:
            raise HTTPException(
                status_code=400,
                detail=f"{mode_title} supports at most {max_allowed} racers.",
            )
        if not all(p.vest_ok and p.gun_online for p in racers):
            raise HTTPException(
                status_code=400,
                detail="All racers must have their T-Blasters online and vests connected before starting.",
            )
    else:
        if len(engine.players) < min_required:
            raise HTTPException(
                status_code=400,
                detail=f"Need at least {min_required} paired T-Blaster{'s' if min_required != 1 else ''} to start {mode_title}.",
            )
        if len(engine.players) > max_allowed:
            raise HTTPException(
                status_code=400,
                detail=f"{mode_title} supports at most {max_allowed} players.",
            )
        if not all(p.vest_ok and p.gun_online for p in engine.players.values()):
            raise HTTPException(
                status_code=400,
                detail="All paired players must have their T-Blasters online and vests connected before starting.",
            )
    if _match_task and not _match_task.done():
        _match_task.cancel()
    _match_task = asyncio.create_task(_run_match_lifecycle())
    return JSONResponse({"ok": True, "phase": engine.phase})


@app.post("/api/race/roster")
async def api_race_roster(req: RaceRosterRequest) -> JSONResponse:
    """Set the Tag'n Shoot race roster/order (TV, full roster) or opt
    one player in/out (phone HUD, single toggle). Setup only: the
    engine rejects edits outside LOBBY or in another mode, so the
    roster can never change mid-match. Benching never unpairs."""
    if req.order is not None and req.bench is not None:
        ok, reason = engine.set_race_roster(req.order, req.bench)
    elif req.gun_num is not None and req.in_race is not None:
        ok, reason = engine.toggle_race_roster(req.gun_num, req.in_race)
    else:
        raise HTTPException(
            status_code=400,
            detail="Send a full roster {order, bench} or a toggle {gun_num, in_race}.",
        )
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    await broadcast_state()
    return JSONResponse({"ok": True, "race_roster": engine.race_roster_snapshot()})


@app.post("/api/race/skip-run")
async def api_race_skip_run() -> JSONResponse:
    """Host skip-run (TV only): forfeit the current race run and move
    on. The engine validates the turn phase; players have no skip
    control on the HUD."""
    ok, reason = engine.skip_race_run()
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    await broadcast_state()
    return JSONResponse({"ok": True, "race_state": engine.race_state})


@app.post("/api/duel/skip")
async def api_duel_skip() -> JSONResponse:
    """Host skip-duel (TV only): finish or skip the current tournament duel pairing."""
    ok, reason = engine.skip_duel_pairing()
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    await broadcast_state()
    return JSONResponse({"ok": True, "duel_state": engine.duel_state})


@app.post("/api/match/rematch")
async def api_match_rematch() -> JSONResponse:
    global _match_task
    if _match_task and not _match_task.done():
        _match_task.cancel()
    _match_task = asyncio.create_task(_run_match_lifecycle())
    return JSONResponse({"ok": True, "phase": engine.phase})


@app.post("/api/match/stop")
async def api_stop_match() -> JSONResponse:
    global _bot_demo_enabled
    _bot_demo_enabled = False
    await _finish_match(reason="Match ended by Arena Master")
    return JSONResponse({"ok": True, "phase": engine.phase})


@app.post("/api/manual-launch/trigger")
async def api_manual_launch_trigger() -> JSONResponse:
    if engine.phase == "WAIT_TRIGGER":
        engine._manual_launch_triggered = True
        return JSONResponse({"ok": True})
    # Timed TagThenShoot turns wait inside PLAYING: release only the
    # current turn's wait, so a stray tap can never auto-release a
    # later turn.
    if (
        engine.mode_name == "TagThenShoot"
        and engine.phase == "PLAYING"
        and engine.race_state.get("turn_phase") == "WAIT_TRIGGER"
    ):
        engine._turn_triggered = True
        return JSONResponse({"ok": True})
    if (
        engine.mode_name == "OldFashionDuel"
        and engine.phase == "PLAYING"
        and engine.duel_state.get("subphase") == "WAIT_TRIGGER"
    ):
        engine._duel_round_triggered = True
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "reason": "Not waiting for trigger"}, status_code=400)


async def _announce_winner_followups(items: list[tuple[str, str]]) -> None:
    """Play winner callouts in sequence after the "winner is/are" intro.

    The intro line runs ~1.9 s and each callout ~1.2-1.9 s; items play
    in order with a 2 s spacing, stopping if results are no longer
    shown. A tie announces every shared winner, as the original does.
    """
    try:
        for i, (wav_name, subtitle) in enumerate(items):
            await asyncio.sleep(1.95 if i == 0 else 2.0)
            if engine.phase == "GAME_OVER" and engine.results_revealed:
                engine.audio.play_announcer_file(wav_name, subtitle, priority=-2, duration=2.0)
                await broadcast_state()
            else:
                return
    except asyncio.CancelledError:
        pass


def _trigger_winner_announcement() -> None:
    if not engine.players:
        return
    is_team = bool(MODE_CATALOG.get(engine.mode_name, {}).get("teams"))
    if is_team:
        totals: dict[int, int] = {}
        for p in engine.players.values():
            totals[p.team_id] = totals.get(p.team_id, 0) + p.score
        ranked_teams = sorted(totals.items(), key=lambda item: item[1], reverse=True)
        if not ranked_teams:
            return
        top_score = ranked_teams[0][1]
        tied = [tid for tid, sc in ranked_teams if sc == top_score]
        team_wavs = {
            1: ("DLG_GM_FRAG_TEAM_05.wav", "The Red Team!"),
            2: ("DLG_GM_FRAG_TEAM_06.wav", "The Blue Team!"),
            3: ("DLG_GM_FRAG_TEAM_07.wav", "The Yellow Team!"),
            4: ("DLG_GM_FRAG_TEAM_08.wav", "The Green Team!"),
        }
        items = [team_wavs[tid] for tid in tied if tid in team_wavs]
        if not items:
            return
        engine.audio.play_announcer_file(
            "DLG_GM_GLOBAL_54.wav", "And the winners are...", priority=-1, duration=1.9
        )
        asyncio.create_task(_announce_winner_followups(items))
        return

    if engine.mode_name == "TagThenShoot":
        # Timed turns, shared win: every racer tied on the best
        # finished time (or, with no finishes, on top total points).
        # Benched players are excluded by race_winners().
        winners = engine.race_winners()
    elif engine.mode_name == "LastManStanding":
        # Survival decides: any alive player beats all eliminated players,
        # and among alive players higher score / remaining HP wins.
        ranked_players = engine.get_sorted_players()
        if not ranked_players:
            return
        top_key = (ranked_players[0].life_points > 0, ranked_players[0].score, ranked_players[0].life_points)
        winners = [
            p for p in ranked_players
            if (p.life_points > 0, p.score, p.life_points) == top_key
        ]
    elif engine.mode_name == "OldFashionDuel" and engine.duel_state.get("is_tournament") and engine.duel_state.get("early_clinch"):
        ranked_players = engine.get_sorted_players()
        if not ranked_players:
            return
        records = engine.duel_state.get("match_records") or {}
        top_wins = int((records.get(str(ranked_players[0].gun_num)) or {}).get("wins", 0))
        top_score = ranked_players[0].score
        winners = [
            p for p in ranked_players
            if (int((records.get(str(p.gun_num)) or {}).get("wins", 0)), p.score) == (top_wins, top_score)
        ]
    else:
        ranked_players = sorted(engine.players.values(), key=lambda p: p.score, reverse=True)
        if not ranked_players:
            return
        top_score = ranked_players[0].score
        winners = [p for p in ranked_players if p.score == top_score]
    if not winners:
        return
    if len(winners) > 1:
        # Shared winners: "And the winners are..." plus a Set B callout
        # for every tied winner. The original has no tie-breaks.
        items = [
            (f"DLG_GM_GLOBAL_{96 + w.gun_num:02d}.wav", f"Player {w.gun_num} — {w.name}!")
            for w in winners
            if 1 <= w.gun_num <= 8
        ]
        if not items:
            return
        engine.audio.play_announcer_file(
            "DLG_GM_GLOBAL_54.wav", "And the winners are...", priority=-1, duration=1.9
        )
        asyncio.create_task(_announce_winner_followups(items))
        return
    winner = winners[0]
    if not (1 <= winner.gun_num <= 8):
        return
    # FinalRankings announces winners with Set B (GLOBAL_97-104); Set A
    # (55-62) is the duel-round set. GLOBAL_96 says "Race over" and is
    # never a callout, which is why the set starts at 97 for Player 1.
    wav = f"DLG_GM_GLOBAL_{96 + winner.gun_num:02d}.wav"
    sub = f"Player {winner.gun_num} — {winner.name}!"
    engine.audio.play_announcer_file(
        "DLG_GM_GLOBAL_53.wav", "And the winner is...", priority=-1, duration=1.9
    )
    asyncio.create_task(_announce_winner_followups([(wav, sub)]))


@app.post("/api/results/reveal")
async def api_reveal_results() -> JSONResponse:
    was_revealed = engine.results_revealed
    engine.results_revealed = True
    if not was_revealed:
        if engine.match_history:
            headline = engine.match_history[-1].get("headline")
            if headline:
                engine.add_event("system", headline)
        _trigger_winner_announcement()
    await broadcast_state()
    return JSONResponse({"ok": True})


@app.post("/api/match/lobby")
async def api_return_to_lobby() -> JSONResponse:
    global _match_task, _bot_demo_enabled
    _bot_demo_enabled = False
    if _match_task and not _match_task.done():
        _match_task.cancel()
    was_game_over = engine.phase == "GAME_OVER"
    engine.phase = "LOBBY"
    # Guns sit in their end-of-round state (winner/loser LED blink) until told
    # otherwise: release them, and force a fresh code upload at the next launch
    # since the guns are no longer in the state that upload left them in.
    if was_game_over and not engine.use_simulator and engine.hw and engine.hardware_write_confirmed and engine.players:
        try:
            await asyncio.to_thread(engine.hw.send_gameover_clear, sorted(engine.players.keys()))
        except Exception as exc:
            await _broadcast_toast(f"Could not reset T-Blasters: {exc}", kind="reset_failed")
        engine.bytecode_ready = False
        engine.bytecode_uploaded = False
        engine.config_failed_guns = []
    # The live feed belongs to the match that just ended: clear it so the
    # lobby does not show the previous match's kill-feed (including its
    # VICTORY line). The match record itself is kept in match_history.
    # Guns stay paired — returning to setup never unpairs anything.
    engine.events.clear()
    engine._vest_miss_counts.clear()
    now = time.time()
    for p in engine.players.values():
        p.status_badge = "READY"
        p.gun_online = True
        if p.last_seen_ts > 0:
            p.last_seen_ts = now
    engine.audio.play_music("musicmenu.wav")
    await broadcast_state()
    return JSONResponse({"ok": True, "phase": "LOBBY"})


@app.post("/api/pair")
async def api_trigger_pairing() -> JSONResponse:
    """Scan for T-Blasters in pairing mode and register them."""
    if engine.use_simulator:
        return JSONResponse({"ok": True, "message": "Simulator mode (hardware pairing not applicable)"})
    if not engine.hardware_write_confirmed:
        raise HTTPException(status_code=403, detail="Hardware writes not confirmed.")
    if engine.hw is None:
        engine.hw = _new_hardware_transport()
        await asyncio.to_thread(engine.hw.open)

    # A fresh pairing scan re-admits guns the host removed earlier.
    engine.hw.clear_ignored_refs()

    engine.audio.play_music("musicintroduction.wav")
    # PAIRING pauses the lobby auto-pair loop (it only runs in LOBBY), so the
    # manual scan is the only reader on the HID handle while it runs.
    engine.phase = "PAIRING"
    await broadcast_state()

    try:
        paired = await asyncio.to_thread(engine.hw.init_and_pair, 8, 8.0)
    finally:
        engine.phase = "LOBBY"
    if paired:
        engine.sync_paired_hardware_guns(paired)
        engine.bytecode_ready = False
        # Check vests right away so the roster shows real vest state, not
        # the default "connected", before the first upload.
        await _refresh_vest_states()
        engine.audio.play_ui("GunDetect.wav", subtitle=f"Paired {len(paired)} T-Blasters!")
    await broadcast_state()
    return JSONResponse({"ok": True, "paired_count": len(engine.players)})


@app.post("/api/upload")
async def api_upload_mode() -> JSONResponse:
    """Flash current mode bytecode to all paired guns and verify readiness."""
    if engine.phase in ("COUNTDOWN", "PLAYING"):
        raise HTTPException(status_code=400, detail="Cannot upload during an active match.")
    ok = await _execute_bytecode_upload()
    return JSONResponse({"ok": ok, "bytecode_ready": engine.bytecode_ready})


@app.post("/api/config/retry")
async def api_retry_config() -> JSONResponse:
    """Retry 0x92 config for the guns that missed it, without re-uploading.

    Only valid after a bytecode upload succeeded but config failed for
    some guns (config_retry_pending in the snapshot). Each pending gun
    gets the normal config sequence again; guns that ACK join the ready
    roster. If the host removes the last pending gun instead, the match
    is ready with the rest — see ArenaEngine.remove_player.
    """
    if engine.use_simulator:
        raise HTTPException(status_code=400, detail="Config retry is hardware-only.")
    if engine.phase in ("UPLOADING", "COUNTDOWN", "PLAYING"):
        raise HTTPException(status_code=400, detail="Cannot retry config during an active match.")
    if not engine.bytecode_uploaded or not engine.config_failed_guns:
        raise HTTPException(status_code=400, detail="No failed config to retry.")
    if engine.hw is None or not engine.hardware_write_confirmed:
        raise HTTPException(status_code=403, detail="Hardware writes not confirmed.")
    still_failed: list[int] = []
    for gid in list(engine.config_failed_guns):
        player = engine.players.get(gid)
        if player is None:
            continue
        ok = await asyncio.to_thread(
            engine.hw.configure_gun,
            gid,
            engine.mode_name,
            engine.settings,
            player.team_id,
            engine.global_settings["gun_volume"],
            engine.global_settings["beam_power"],
        )
        if not ok:
            still_failed.append(gid)
    engine.config_failed_guns = still_failed
    engine.last_upload_failed_guns = list(still_failed)
    if not still_failed:
        engine.bytecode_ready = True
        engine.audio.play_ui("Validation.wav")
    await broadcast_state()
    return JSONResponse(
        {
            "ok": not still_failed,
            "still_pending": [engine.players[g].gun_num for g in still_failed if g in engine.players],
        }
    )


@app.post("/api/guns/unpair")
async def api_unpair_guns() -> JSONResponse:
    """Reset paired guns roster."""
    if not engine.use_simulator and engine.hw:
        engine.hw.unpair_all()
    engine.players.clear()
    engine.sync_race_roster()
    engine.bytecode_ready = False
    engine.bytecode_uploaded = False
    engine.config_failed_guns = []
    engine.audio.play_ui("ChangeValue.wav")
    await broadcast_state()
    return JSONResponse({"ok": True})


@app.post("/api/sim/action")
async def api_sim_action(req: SimActionRequest) -> JSONResponse:
    global _bot_demo_enabled
    if req.action == "set_guns" and req.num_guns:
        engine.set_num_simulated_guns(req.num_guns)
        await broadcast_state()
        return JSONResponse({"ok": True})
    if req.action == "add_gun":
        count = engine.add_simulated_gun()
        await broadcast_state()
        return JSONResponse({"ok": True, "count": count})
    if req.action == "toggle_bots":
        _bot_demo_enabled = bool(req.enable_bots) if req.enable_bots is not None else not _bot_demo_enabled
        return JSONResponse({"ok": True, "bot_demo_enabled": _bot_demo_enabled})
    if req.action in ("shot", "hit"):
        if not req.shooter_gid:
            raise HTTPException(status_code=400, detail="shooter_gid required")
        target = req.target_gid if req.action == "hit" else None
        res = engine.sim.sim_fire_shot(req.shooter_gid, target)
        # Immediately poll affected guns so UI updates with zero latency
        for gid in list(engine.players.keys()):
            st = engine.sim.poll_gun(gid, engine.mode_name)
            if st:
                engine.ingest_status_report(st)
        await broadcast_state()
        return JSONResponse(res)
    if req.action == "rfid":
        if not req.gun_id or not req.rfid_code:
            raise HTTPException(status_code=400, detail="gun_id and rfid_code required")
        res = engine.sim.sim_scan_rfid(req.gun_id, req.rfid_code)
        st = engine.sim.poll_gun(req.gun_id, engine.mode_name)
        if st:
            engine.ingest_status_report(st)
        await broadcast_state()
        return JSONResponse(res)

    raise HTTPException(status_code=400, detail=f"Unknown simulator action: {req.action}")


async def _lobby_auto_pairing_loop() -> None:
    """Continuously check for T-Blasters in pairing mode while in Lobby,
    keep the paired guns' vest state live with periodic C1 sweeps, and
    broadcast UbiConnect plug/unplug transitions immediately."""
    last_vest_sweep = 0.0
    last_usb_check = 0.0
    last_usb_connected: bool | None = None
    while True:
        try:
            now = time.time()
            if engine.phase != "PLAYING" and (now - last_usb_check) >= 1.0:
                last_usb_check = now
                usb_info = await asyncio.to_thread(detect_ubiconnect_readonly)
                usb_connected = bool(usb_info.get("connected"))
                if last_usb_connected is not None and usb_connected != last_usb_connected:
                    if not usb_connected:
                        if engine.hw is not None and engine.phase == "LOBBY":
                            try:
                                await asyncio.to_thread(engine.hw.close)
                            except Exception:
                                pass
                            engine.hw = None
                        engine.add_lobby_notice(
                            "UbiConnect lost — guns will need to re-pair when it returns."
                        )
                        await _broadcast_toast(
                            "UbiConnect lost — guns will need to re-pair when it returns.",
                            kind="ubiconnect_lost",
                            icon_url="/static/assets/textures/Ui/ubiconnect-disconnected.png",
                        )
                    elif (
                        engine.phase == "LOBBY"
                        and not engine.use_simulator
                        and engine.hardware_write_confirmed
                    ):
                        # Reconnected: the original rebuilds its proxy
                        # empty when the UbiConnect goes away and every
                        # gun must re-register, so re-run init + a
                        # pairing scan. Numbers are restored from the
                        # ref map (saved file mapping + session memory
                        # shared with the fresh transport).
                        engine.add_lobby_notice("UbiConnect found — re-pairing guns…")
                        await _broadcast_toast(
                            "UbiConnect found — re-pairing guns…",
                            kind="ubiconnect_found",
                            icon_url="/static/assets/textures/Ui/ubiconnect-connected.png",
                        )
                        try:
                            if engine.hw is not None:
                                try:
                                    await asyncio.to_thread(engine.hw.close)
                                except Exception:
                                    pass
                            engine.hw = _new_hardware_transport()
                            await asyncio.to_thread(engine.hw.open)
                            await asyncio.to_thread(engine.hw.ensure_initialized)
                            repaired = await asyncio.to_thread(engine.hw.init_and_pair, 8, 6.0)
                            if repaired:
                                engine.sync_paired_hardware_guns(repaired)
                                engine.bytecode_ready = False
                                await _refresh_vest_states()
                                last_vest_sweep = time.time()
                                msg = f"Re-paired {len(repaired)} T-Blaster(s) after the UbiConnect reconnect."
                                engine.add_lobby_notice(msg)
                                await _broadcast_toast(
                                    msg,
                                    kind="repaired",
                                    icon_url="/static/assets/textures/Ui/ubiconnect-connected.png",
                                )
                            else:
                                msg = "No T-Blasters re-paired yet — run a pairing scan when they are on and in range."
                                engine.add_lobby_notice(msg)
                                await _broadcast_toast(
                                    msg,
                                    kind="no_repair",
                                    icon_url="/static/assets/textures/Ui/ubiconnect-connected.png",
                                )
                        except Exception:
                            pass
                    last_usb_connected = usb_connected
                    await broadcast_state()
                else:
                    last_usb_connected = usb_connected

            if (
                engine.phase == "LOBBY"
                and not engine.use_simulator
                and engine.hardware_write_confirmed
                and engine.auto_pair_active
            ):
                if engine.hw is None:
                    try:
                        engine.hw = _new_hardware_transport()
                        await asyncio.to_thread(engine.hw.open)
                        await asyncio.to_thread(engine.hw.ensure_initialized)
                    except Exception:
                        pass
                if engine.hw is not None:
                    new_guns = await asyncio.to_thread(engine.hw.check_for_new_guns_in_pairing, 120)
                    if new_guns:
                        all_paired = sorted(list(engine.hw.paired_by_ref.values()), key=lambda g: g["gun_num"])
                        engine.sync_paired_hardware_guns(all_paired)
                        engine.bytecode_ready = False
                        await _refresh_vest_states()
                        last_vest_sweep = time.time()
                        engine.audio.play_ui("GunDetect.wav", subtitle="T-Blaster paired automatically!")
                        await broadcast_state()
            # Periodic vest sweep while guns sit paired in the lobby: the
            # roster is where the host checks vests before starting, so its
            # status must be live, not just from the last upload. Every 2 s,
            # matching the original's fast-Pinger ping cadence in setup:
            # the sweep is also the liveness probe behind the 4 s lobby
            # quiet-timeout, so a live gun must be heard from well inside
            # it. The sweep itself exits early once every gun has answered.
            if (
                engine.phase == "LOBBY"
                and not engine.use_simulator
                and engine.hardware_write_confirmed
                and engine.hw is not None
                and engine.players
                and (time.time() - last_vest_sweep) >= 2.0
            ):
                last_vest_sweep = time.time()
                await _refresh_vest_states()
                await broadcast_state()
        except Exception:
            pass
        await asyncio.sleep(0.4)


async def _startup_update_check() -> None:
    try:
        await update_checker.check_async(force=False)
        await broadcast_state()
    except Exception:
        pass


@app.on_event("startup")
async def on_startup() -> None:
    if engine.hardware_write_confirmed and not engine.use_simulator:
        try:
            engine.hw = _new_hardware_transport()
            await asyncio.to_thread(engine.hw.open)
            await asyncio.to_thread(engine.hw.ensure_initialized)
        except Exception as exc:
            print(f"Hardware init note: {exc}")
    asyncio.create_task(_lobby_auto_pairing_loop())
    asyncio.create_task(_profile_write_worker())
    if os.environ.get("BATTLETAG_DISABLE_UPDATE_CHECK") != "1":
        asyncio.create_task(_startup_update_check())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if engine.hw is not None:
        try:
            await asyncio.to_thread(engine.hw.close)
        except Exception:
            pass
        engine.hw = None


async def _refresh_vest_states(announce_changes: bool = False) -> None:
    """Run one C1 vest sweep and fold the results into player state.

    Hardware only: in simulator mode vest state comes from the simulated
    guns. A failed sweep never raises — vest display must not break the
    match loop or the lobby loop that call this. When announce_changes is
    set (live match), guns whose vest state changed get a toast
    notification so the host notices mid-game (bundled when several
    change at once); in the lobby the roster shows the state live and
    no toast is posted (setup events would otherwise leak into the
    next match's feed).
    """
    if engine.use_simulator or engine.hw is None or not engine.players:
        return
    try:
        await asyncio.to_thread(engine.hw.vest_check, set(engine.players.keys()))
    except Exception:
        return
    for player, connected in engine.apply_vest_states(dict(engine.hw.vest_states)):
        if announce_changes:
            if not connected and not player.gun_online:
                await _broadcast_toast(
                    f"T-Blaster not responding: {player.display_name} (Player {player.gun_num})",
                    kind="gun_offline",
                    name=player.display_name,
                    avatar=player.avatar,
                    gun_num=player.gun_num,
                )
            else:
                await _broadcast_toast(
                    f"Vest {'connected' if connected else 'disconnected'}: {player.display_name} (Player {player.gun_num})",
                    kind="vest_connect" if connected else "vest_disconnect",
                    name=player.display_name,
                    avatar=player.avatar,
                    gun_num=player.gun_num,
                )
    if engine.phase == "LOBBY" and not announce_changes:
        # Quiet guns leave the roster: the time-based timeout first
        # (silence >4 s), the sweep-count removal as the backstop. Each
        # removal is unbound transport-side (no ignore-listing: a gun
        # that comes back may re-pair and reclaim its number), and a
        # changed roster invalidates any completed upload.
        removed_by_gid: dict[int, Any] = {}
        for p in engine.remove_quiet_lobby_players():
            removed_by_gid[p.gun_id] = p
        for p in engine.remove_silent_lobby_players():
            if p.gun_id not in removed_by_gid:
                removed_by_gid[p.gun_id] = p
                engine.add_lobby_notice(
                    f"Player {p.gun_num} ({p.display_name}) went quiet "
                    "and was removed from the roster."
                )
        if removed_by_gid:
            for p in removed_by_gid.values():
                if p.ref_hex:
                    engine.hw.unpair_gun(p.ref_hex)
                await _broadcast_toast(
                    "went quiet and was removed from the roster.",
                    kind="player_quiet",
                    name=p.display_name,
                    avatar=p.avatar,
                    gun_num=p.gun_num,
                )
            engine.bytecode_ready = False
            engine.bytecode_uploaded = False


# Serializes bytecode uploads: two concurrent uploads (a double-clicked
# Launch, or an API upload racing the match lifecycle's) would interleave
# the unlock/erase/prepare/chunk sequence on one HID handle and strand
# guns in bootloader mode. The wrapper also guarantees the phase never
# stays UPLOADING when the upload raises (e.g. PermissionError before the
# hardware is confirmed): the phase is restored and the error re-raised.
_upload_lock = asyncio.Lock()


async def _execute_bytecode_upload() -> bool:
    """Flash current mode bytecode to all paired guns (serialized, phase-safe)."""
    async with _upload_lock:
        prev_phase = engine.phase
        try:
            return await _execute_bytecode_upload_inner()
        except BaseException:
            engine.phase = prev_phase if prev_phase != "UPLOADING" else "LOBBY"
            engine.bytecode_ready = False
            engine.bytecode_uploaded = False
            engine.config_failed_guns = []
            await broadcast_state()
            raise


async def _execute_bytecode_upload_inner() -> bool:
    """Flash current mode bytecode to all paired guns and configure 0x92 parameters."""
    seqs = load_mode_sequences()
    chunks = seqs["uploads"][engine.mode_name]["chunks"]
    total_chunks = len(chunks)
    total_steps = total_chunks + 4  # unlock, erase, prepare, chunks, lock

    prev_phase = engine.phase
    engine.phase = "UPLOADING"
    engine.upload_progress = {"current": 0, "total": total_steps}
    engine.audio.play_music("musicintroduction.wav")
    await broadcast_state()

    if engine.use_simulator:
        for step in range(1, total_steps + 1):
            await asyncio.sleep(0.50)
            engine.upload_progress = {"current": step, "total": total_steps}
            await broadcast_state()
    else:
        if not engine.hardware_write_confirmed:
            raise PermissionError("Hardware writes not confirmed.")
        if engine.hw is None:
            engine.hw = _new_hardware_transport()
            await asyncio.to_thread(engine.hw.open)
        await asyncio.to_thread(engine.hw.ensure_initialized)

        gids = sorted(engine.players.keys())
        if not gids:
            engine.phase = prev_phase if prev_phase != "UPLOADING" else "LOBBY"
            await broadcast_state()
            return False

        loop = asyncio.get_running_loop()
        progress_tasks: set[asyncio.Task[None]] = set()

        def _on_chunk(cur: int, tot: int) -> None:
            def _publish() -> None:
                engine.upload_progress = {"current": cur, "total": tot}
                task = asyncio.create_task(broadcast_state())
                progress_tasks.add(task)
                task.add_done_callback(progress_tasks.discard)

            if not loop.is_closed():
                loop.call_soon_threadsafe(_publish)

        upload_ok = await asyncio.to_thread(engine.hw.upload_mode_bytecode, engine.mode_name, gids, _on_chunk)
        await broadcast_state()
        if not upload_ok:
            # A gun that missed a chunk is stuck in bootloader mode: say which
            # guns, stay un-ready, and let the user retry the upload (from
            # unlock) instead of starting a match the gun cannot play.
            engine.last_upload_failed_guns = list(engine.hw.upload_failed_guns)
            engine.bytecode_ready = False
            engine.bytecode_uploaded = False
            engine.config_failed_guns = []
            engine.phase = "LOBBY"
            await broadcast_state()
            return False
        engine.bytecode_uploaded = True

        # 0x92 config per gun, with beam power and gun volume from the global
        # settings store. A gun that misses its config would play with stale
        # settings, so it is recorded for a per-gun retry (or a host Remove)
        # instead of nuking the whole launch: the code upload above already
        # succeeded and must not be repeated for a config miss.
        config_failed: list[int] = []
        for gid in gids:
            p = engine.players[gid]
            ok = await asyncio.to_thread(
                engine.hw.configure_gun,
                gid,
                engine.mode_name,
                engine.settings,
                p.team_id,
                engine.global_settings["gun_volume"],
                engine.global_settings["beam_power"],
            )
            if not ok:
                config_failed.append(gid)
        if config_failed:
            engine.last_upload_failed_guns = config_failed
            engine.config_failed_guns = list(config_failed)
            engine.bytecode_ready = False
            engine.phase = "LOBBY"
            await broadcast_state()
            return False

        # Wait one 250 ms tick after all guns ACK 0x92 config before the C1 vest
        # sweep, matching UTActivity.State.PlayersSetup.lua.
        await asyncio.sleep(0.25)

        # Vest check: one C1 sweep so the roster shows each gun's real vest
        # state before the match. Periodic sweeps keep it live afterwards.
        await _refresh_vest_states()

    engine.last_upload_failed_guns = []
    engine.config_failed_guns = []
    engine.bytecode_uploaded = True
    engine.bytecode_ready = True
    engine.phase = prev_phase if prev_phase in ("LOBBY", "READY") else "LOBBY"
    engine.audio.play_ui("Validation.wav")
    await broadcast_state()
    return True


async def _send_race_countdown(gid: int | None, seconds: int) -> bool:
    """Send one per-run 0x93 countdown to a single gun, retrying once
    on echo failure. `start_countdown` already retries 3x per call;
    this is one more full attempt. Returns False only when both
    attempts fail — the caller NEVER aborts the match for this mode:
    the engine countdown still runs and the run continues without
    the gun-side countdown (toast + log only)."""
    if gid is None or engine.hw is None:
        return False
    ok = await asyncio.to_thread(engine.hw.start_countdown, [gid], seconds)
    if not ok:
        ok = await asyncio.to_thread(engine.hw.start_countdown, [gid], seconds)
    return ok


async def _run_match_lifecycle() -> None:
    """Execute Pair (if needed) -> Upload (if needed) -> Countdown -> Active Match Poll -> Game Over."""
    try:
        min_required = 1 if engine.mode_name == "TagThenShoot" else 2
        if not engine.use_simulator:
            if not engine.hardware_write_confirmed:
                raise PermissionError("Hardware writes not confirmed.")
            if engine.hw is None:
                engine.hw = _new_hardware_transport()
                await asyncio.to_thread(engine.hw.open)
            await asyncio.to_thread(engine.hw.ensure_initialized)

            if len(engine.players) < min_required:
                engine.phase = "PAIRING"
                engine.audio.play_music("musicintroduction.wav")
                await broadcast_state()
                paired = await asyncio.to_thread(engine.hw.init_and_pair, 8, 6.0)
                if paired:
                    engine.sync_paired_hardware_guns(paired)

            if len(engine.players) < min_required:
                await _broadcast_toast(
                    f"Pairing incomplete: need at least {min_required} {'player' if min_required == 1 else 'players'}.",
                    kind="pairing_incomplete",
                )
                engine.phase = "LOBBY"
                await broadcast_state()
                return

        # Upload bytecode if not already ready
        if not engine.bytecode_ready:
            ok = await _execute_bytecode_upload()
            if not ok:
                engine.phase = "LOBBY"
                await broadcast_state()
                return

        engine.reset_stats_for_new_match()

        # Manual Launch: Player 1 pulls their trigger to begin the countdown.
        # 2-player OldFashionDuel always starts in manual launch mode; 3–8
        # player duel tournaments and TagThenShoot run per-pairing / per-turn
        # trigger waits and countdowns inside PLAYING.
        cd_secs = int(engine.settings.get("countdownSeconds", 10))
        is_duel_tournament = engine.mode_name == "OldFashionDuel" and len(engine.players) > 2
        per_turn_flow = engine.mode_name == "TagThenShoot" or is_duel_tournament
        manual_start = (
            int(engine.settings.get("gameLaunch", 0)) == 1
            or engine.mode_name == "OldFashionDuel"
        )
        if manual_start and engine.players and not per_turn_flow:
            p1 = min(engine.players.values(), key=lambda p: p.gun_num)
            engine.phase = "WAIT_TRIGGER"
            engine._manual_launch_triggered = False
            engine.add_event("system", f"Waiting for {p1.display_name} (Player {p1.gun_num}) to pull the trigger...")
            await broadcast_state()
            poll_tick = 0
            while engine.phase == "WAIT_TRIGGER" and not engine._manual_launch_triggered:
                if not engine.use_simulator and engine.hw:
                    if poll_tick % 20 == 0:
                        await asyncio.to_thread(engine.hw.send_manual_launch_prompt, p1.gun_id)
                    triggered = await asyncio.to_thread(engine.hw.poll_manual_trigger, p1.gun_id)
                    if triggered:
                        engine._manual_launch_triggered = True
                        break
                poll_tick += 1
                await asyncio.sleep(0.25)
            if engine.phase != "WAIT_TRIGGER":
                return
            cd_secs = max(1, cd_secs // 2)

        # Phase 2: Countdown. TagThenShoot and 3+ player duel tournaments
        # skip the match-level countdown and run per-turn/per-pairing waits inside PLAYING.
        if not per_turn_flow:
            engine.phase = "COUNTDOWN"
            engine.countdown_remaining = cd_secs
            engine.audio.trigger_pool("countdown_intro", force=True)
            if not engine.use_simulator and engine.hw:
                ok = await asyncio.to_thread(engine.hw.start_countdown, sorted(engine.players.keys()), cd_secs)
                if not ok:
                    await _broadcast_toast("Countdown echo failed on hardware.", kind="countdown_echo_failed")
                    engine.phase = "LOBBY"
                    await broadcast_state()
                    return

            for rem in range(cd_secs, 0, -1):
                engine.countdown_remaining = rem
                engine.audio.play_countdown_number(rem)
                await broadcast_state()
                if not engine.use_simulator and engine.hw and rem == 5:
                    await asyncio.to_thread(engine.hw.send_ping)
                await asyncio.sleep(1.0)

            if not engine.use_simulator and engine.hw:
                await asyncio.to_thread(engine.hw.drain_stale_echoes, 0.4)

        # Phase 3: Active Match
        engine.phase = "PLAYING"
        engine.countdown_remaining = 0
        engine.match_start_ts = time.time()
        engine.audio.play_music("musicingame.wav")
        # TagThenShoot and duel tournaments announce their own opening turns/pairings.
        if not per_turn_flow:
            engine.audio.trigger_pool("game_start", force=True)
        engine.add_event("system", f"{MODE_CATALOG[engine.mode_name]['title']} match started!")
        await broadcast_state()

        last_bot_step = time.time()
        race_prompt_tick = 0
        duel_prompt_tick = 0
        while engine.phase == "PLAYING":
            engine.tick_mode_timers()
            engine.tick_race_turns()
            engine.tick_pending_audio()
            # TagThenShoot timed turns on hardware: poll the active
            # gun's trigger while its turn waits, and send the
            # per-turn countdown to that gun alone when its countdown
            # starts. The trigger poll runs ~4x/s (a pull is a human
            # action; the 0xC2 query is one extra packet per cycle),
            # the prompt every ~5 s, matching the match-level wait.
            if engine.mode_name == "TagThenShoot" and not engine.use_simulator and engine.hw:
                rs = engine.race_state
                race_prompt_tick += 1
                if rs.get("turn_phase") == "WAIT_TRIGGER" and rs.get("active_gun_id") is not None:
                    gid = rs["active_gun_id"]
                    if race_prompt_tick % 100 == 0:
                        await asyncio.to_thread(engine.hw.send_manual_launch_prompt, gid)
                    if race_prompt_tick % 5 == 0:
                        pulled = await asyncio.to_thread(engine.hw.poll_manual_trigger, gid)
                        if not pulled:
                            rs["trigger_armed"] = True
                        elif rs.get("trigger_armed", True):
                            engine._turn_triggered = True
                elif rs.get("turn_phase") == "COUNTDOWN" and not rs.get("countdown_sent", True):
                    rs["countdown_sent"] = True
                    gid = rs.get("active_gun_id")
                    ok = await _send_race_countdown(gid, int(rs.get("countdown_seconds", 5)))
                    if not ok:
                        # Never abort a race mid-match for a failed
                        # countdown echo: retry once (done above),
                        # toast, and let the engine countdown carry
                        # the run. Other modes keep their abort.
                        await _broadcast_toast(
                            "Countdown echo failed — continuing without the gun countdown.",
                            kind="countdown_echo_failed",
                        )
                    else:
                        await asyncio.to_thread(engine.hw.drain_stale_echoes, 0.4)
            elif engine.mode_name == "OldFashionDuel" and engine.duel_state.get("is_tournament"):
                ds = engine.duel_state
                pending_go = ds.get("pairing_gameover_pending")
                if pending_go:
                    ds["pairing_gameover_pending"] = None
                    if not engine.use_simulator and engine.hw and engine.hardware_write_confirmed:
                        await asyncio.to_thread(engine.hw.send_game_over, pending_go, engine.mode_name)
                if not engine.use_simulator and engine.hw:
                    active_pair = [g for g in (ds.get("active_pair") or []) if g in engine.players]
                    duel_prompt_tick += 1
                    if ds.get("subphase") == "WAIT_TRIGGER" and active_pair:
                        if duel_prompt_tick % 100 == 0:
                            prompt_gid = active_pair[(duel_prompt_tick // 100) % len(active_pair)]
                            await asyncio.to_thread(engine.hw.send_manual_launch_prompt, prompt_gid)
                        if duel_prompt_tick % 5 == 0:
                            armed = ds.setdefault("trigger_armed", {})
                            if not isinstance(armed, dict):
                                armed = {}
                                ds["trigger_armed"] = armed
                            for gid in active_pair:
                                pulled = await asyncio.to_thread(engine.hw.poll_manual_trigger, gid)
                                if not pulled:
                                    armed[gid] = True
                                elif armed.get(gid, False):
                                    engine._duel_round_triggered = True
                                    break
                    elif ds.get("subphase") == "COUNTDOWN" and not ds.get("countdown_sent", True) and active_pair:
                        ds["countdown_sent"] = True
                        cd_pair = int(ds.get("countdown_seconds", 5))
                        ok = await asyncio.to_thread(engine.hw.start_countdown, active_pair, cd_pair)
                        if not ok:
                            ok = await asyncio.to_thread(engine.hw.start_countdown, active_pair, cd_pair)
                        if ok:
                            await asyncio.to_thread(engine.hw.drain_stale_echoes, 0.4)
                        else:
                            await _broadcast_toast(
                                "Countdown echo failed — continuing without the gun countdown.",
                                kind="countdown_echo_failed",
                            )
            if engine.mode_name == "OldFashionDuel" and engine.duel_state.get("is_tournament"):
                ds = engine.duel_state
                if ds.get("subphase") in ("INTRO", "WAIT", "DRAW", "ROUND_END"):
                    poll_gids = [g for g in (ds.get("active_pair") or []) if g in engine.players]
                else:
                    poll_gids = []
            else:
                poll_gids = sorted(engine.players.keys())
            for gid in poll_gids:
                player = engine.players[gid]
                downlink = None
                if engine.mode_name == "OldFashionDuel":
                    downlink = int(engine.duel_state.get("phase_code", 2))
                elif engine.mode_name == "TagThenShoot":
                    downlink = player.frags

                if engine.use_simulator:
                    st = engine.sim.poll_gun(gid, engine.mode_name, downlink_word=downlink)
                elif engine.hw:
                    st = await asyncio.to_thread(engine.hw.poll_gun, gid, engine.mode_name, downlink)
                else:
                    st = None

                if st:
                    engine.ingest_status_report(st)

            if not engine.use_simulator and engine.hw:
                engine.ingest_ubiconnect_hits(engine.hw.ubiconnect_hits)
                await asyncio.to_thread(engine.hw.send_ping)
            elif engine.use_simulator:
                # The simulator reports tower hits through the same
                # channel as the hardware 0xC3 path.
                engine.ingest_ubiconnect_hits(engine.sim.ubiconnect_hits)

            # Optional bot arena action in simulator mode
            if engine.use_simulator and _bot_demo_enabled and (time.time() - last_bot_step) > 1.35:
                last_bot_step = time.time()
                _run_simulator_bot_step()

            # Check time warnings (timed modes only: duel and LMS have
            # no match clock, and TagThenShoot skips the 60 s mark).
            if engine._is_timed_mode():
                engine.audio.check_time_remaining(engine.get_remaining_seconds(), engine.mode_name)
            engine.check_idle_announcer()

            # Check auto-end
            if engine.check_auto_end_conditions():
                await _finish_match(reason="Match complete!")
                break

            await broadcast_state()
            await asyncio.sleep(0.05 if not engine.use_simulator else 0.2)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        await _broadcast_toast(f"Engine notice: {exc}", kind="engine_notice")
        engine.phase = "LOBBY"
        await broadcast_state()


def _run_simulator_bot_step() -> None:
    """Generate realistic simulated laser-tag action when Live Demo Bot mode is active."""
    gids = list(engine.players.keys())
    if len(gids) < 2:
        return
    shooter = random.choice(gids)
    s_player = engine.players[shooter]
    # If shooter is out of life in TeamFrag/FFA, scan team base
    if engine.mode_name in ("TeamFrag", "FreeForAll") and s_player.life_points <= 0:
        rfid = {1: "RF03", 2: "RF04", 3: "RF05", 4: "RF06"}.get(s_player.team_id, "RF03")
        engine.sim.sim_scan_rfid(shooter, rfid)
        return
    # If shooter is out of ammo/clips, scan ammo pack
    if s_player.ammo <= 0 and s_player.clips <= 0:
        engine.sim.sim_scan_rfid(shooter, "RF01")
        return
    if engine.mode_name == "TagThenShoot":
        sg = engine.sim.guns[shooter]
        if sg.phase_flag == 1:
            # Scan the next base in course order (base 1 -> RF03, ...).
            engine.sim.sim_scan_rfid(shooter, f"RF0{sg.tag_stage + 3}")
        else:
            engine.sim.sim_fire_shot(shooter, shooter)
        return
    if engine.mode_name == "OldFashionDuel":
        ds = engine.duel_state
        if ds.get("subphase") == "WAIT_TRIGGER":
            engine._duel_round_triggered = True
            return
        if ds.get("subphase") != "DRAW" or not ds.get("can_shoot"):
            return
        pair = [g for g in (ds.get("active_pair") or gids) if g in engine.players]
        if len(pair) < 2:
            return
        d_shooter = random.choice(pair)
        d_target = next(g for g in pair if g != d_shooter)
        if random.random() < 0.75:
            engine.sim.sim_fire_shot(d_shooter, d_target)
        else:
            engine.sim.sim_fire_shot(d_shooter, None)
        return

    enemies = [
        g
        for g in gids
        if g != shooter
        and (engine.mode_name != "TeamFrag" or engine.players[g].team_id != s_player.team_id)
        and (engine.mode_name != "LastManStanding" or engine.players[g].life_points > 0)
    ]
    if not enemies:
        return
    if random.random() < 0.65:
        target = random.choice(enemies)
        engine.sim.sim_fire_shot(shooter, target)
    else:
        engine.sim.sim_fire_shot(shooter, None)


async def _finish_match(reason: str = "Match Over") -> None:
    if engine.phase == "GAME_OVER":
        return
    # A race stopped mid-run keeps the partial run's points: snapshot
    # it into the history before outcomes/standings are computed.
    engine.finalize_race_on_stop()
    engine.record_score_snapshot(force=True)
    engine.phase = "GAME_OVER"
    engine.match_end_ts = time.time()
    outcomes = engine.compute_gameover_outcomes()
    for gid, won in outcomes.items():
        if gid in engine.players:
            engine.players[gid].status_badge = "WINNER" if won else "FINISHED"

    if not engine.use_simulator and engine.hw and engine.hardware_write_confirmed:
        await asyncio.to_thread(engine.hw.send_game_over, outcomes, engine.mode_name)
        # Note: Do NOT close engine.hw! The guns stay paired across matches until explicitly reset.

    engine.audio.play_ui("Ranking.wav")
    # The race ends on its own line ("Race over!" / "Wow! What a
    # race!") instead of the generic game-end pool.
    if engine.mode_name == "TagThenShoot":
        engine.audio.trigger_pool("race_over", force=True)
    else:
        engine.audio.trigger_pool("game_end", force=True)
    engine.audio.play_music("musicmenu.wav")

    # Determine headline winner
    winners = [engine.players[gid].display_name for gid, won in outcomes.items() if won and gid in engine.players]
    if winners:
        headline = f"VICTORY: {', '.join(winners).upper()}!"
    else:
        headline = "DRAW GAME — TIE SCORE!"

    # Defer the VICTORY/DRAW headline in the live feed until
    # /api/results/reveal so pressing "See Results" reveals the winner.
    engine.add_event("system", f"Match complete! ({reason})")

    engine.match_history.append(
        {
            "timestamp": time.strftime("%H:%M:%S"),
            "mode": MODE_CATALOG[engine.mode_name]["title"],
            "headline": headline,
            "standings": [p.to_dict() for p in engine.get_sorted_players()],
        }
    )
    await broadcast_state()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws_manager.connect(ws)
    try:
        await ws.send_json(
            {
                "type": "state",
                "data": _build_state_snapshot(),
            }
        )
        if engine.audio.current_music:
            track = engine.audio.current_music
            await ws.send_json(
                {
                    "type": "audio",
                    "cue": {
                        "category": "music",
                        "url": f"/audio/music/{track}",
                        "sound_file": f"/audio/music/{track}",
                        "channel": "music",
                        "filename": track,
                        "priority": 0,
                        "subtitle": f"Music: {track}",
                        "loop": True,
                        "volume": 0.9,
                    },
                }
            )
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)
    except Exception:
        ws_manager.disconnect(ws)

