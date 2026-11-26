"""Cross-platform desktop launcher for BattlePew (Windows & Linux)."""

from __future__ import annotations

import json
import multiprocessing
import os
import pathlib
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import uvicorn


def _window_prefs_path() -> pathlib.Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(pathlib.Path.home() / "AppData" / "Roaming")
        return pathlib.Path(base) / "BattlePew" / "window.json"
    xdg = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(xdg) / "BattlePew" / "window.json"


def _load_fullscreen_pref() -> bool:
    try:
        data = json.loads(_window_prefs_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("fullscreen"), bool):
            return data["fullscreen"]
    except Exception:
        pass
    return True


def _save_fullscreen_pref(fullscreen: bool) -> None:
    try:
        path = _window_prefs_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fullscreen": bool(fullscreen)}), encoding="utf-8")
    except Exception:
        pass


class DesktopApi:
    """JS-to-Python bridge for fullscreen/windowed toggle and quitting."""

    def __init__(self, initial_fullscreen: bool) -> None:
        self._window = None
        self._is_fullscreen = bool(initial_fullscreen)

    def bind_window(self, window) -> None:
        self._window = window

    def get_fullscreen(self) -> bool:
        return self._is_fullscreen

    def toggle_fullscreen(self) -> bool:
        if self._window is None:
            return self._is_fullscreen
        self._window.toggle_fullscreen()
        self._is_fullscreen = not self._is_fullscreen
        _save_fullscreen_pref(self._is_fullscreen)
        return self._is_fullscreen

    def set_fullscreen(self, enabled: bool) -> bool:
        want = bool(enabled)
        if want == self._is_fullscreen:
            return self._is_fullscreen
        return self.toggle_fullscreen()

    def quit_app(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def open_external(self, url: str) -> bool:
        try:
            parsed = urllib.parse.urlparse(str(url))
        except Exception:
            return False
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return False
        return bool(webbrowser.open(str(url), new=2))


_KEYBIND_JS = """
(() => {
  if (window.__battlepewDesktopKeysBound) return;
  window.__battlepewDesktopKeysBound = true;
  document.addEventListener('keydown', (e) => {
    const api = window.pywebview && window.pywebview.api;
    if (!api) return;
    const isF11 = e.key === 'F11';
    const isAltEnter = e.key === 'Enter' && e.altKey && !e.ctrlKey && !e.metaKey;
    if (isF11 || isAltEnter) {
      e.preventDefault();
      api.toggle_fullscreen().then((fs) => {
        if (window.__onDesktopFullscreenChanged) window.__onDesktopFullscreenChanged(fs);
      });
      return;
    }
    if (e.key === 'Escape' && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const tag = (document.activeElement && document.activeElement.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      if (document.querySelector('.modal-overlay.open')) return;
      api.set_fullscreen(false).then((fs) => {
        if (window.__onDesktopFullscreenChanged) window.__onDesktopFullscreenChanged(fs);
      });
    }
  });
})();
"""


def _find_free_port(start: int = 3466, end: int = 3475) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(url: str, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.15)
    return False


def _ensure_std_streams() -> None:
    """Prevent None stdout/stderr crashes in PyInstaller --windowed builds on Windows."""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _parse_smoke_test_args(argv: list[str]) -> tuple[bool, str | None]:
    smoke_test = "--smoke-test" in argv
    log_path: str | None = None
    for idx, arg in enumerate(argv):
        if arg == "--smoke-test-log" and idx + 1 < len(argv):
            log_path = argv[idx + 1]
        elif arg.startswith("--smoke-test-log="):
            log_path = arg.split("=", 1)[1]
    return smoke_test, log_path


def _write_smoke_log(log_path: str | None, lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
    except Exception:
        pass
    if log_path:
        path = pathlib.Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def _run_smoke_test(base_url: str, log_path: str | None) -> int:
    lines: list[str] = []
    try:
        with urllib.request.urlopen(f"{base_url}/api/state", timeout=5.0) as resp:
            if resp.status != 200:
                raise RuntimeError(f"/api/state returned HTTP {resp.status}")
            state = json.loads(resp.read().decode("utf-8"))
        modes = state.get("modes") or state.get("mode_catalog") or []
        if not modes:
            raise RuntimeError("/api/state returned no game modes")
        lines.append(f"OK /api/state (modes={len(modes)})")

        for route in ("/", "/hud"):
            with urllib.request.urlopen(f"{base_url}{route}", timeout=5.0) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"{route} returned HTTP {resp.status}")
                body = resp.read()
                if len(body) < 100:
                    raise RuntimeError(f"{route} returned unexpectedly small body ({len(body)} bytes)")
            lines.append(f"OK {route} ({len(body)} bytes)")

        # pyrefly: ignore[missing-import]
        import webview  # noqa: F401
        lines.append(f"OK pywebview imported ({getattr(webview, '__version__', 'unknown')})")
        lines.append("SMOKE_TEST_PASSED")
        _write_smoke_log(log_path, lines)
        return 0
    except Exception as exc:
        lines.append(f"SMOKE_TEST_FAILED: {exc!r}")
        _write_smoke_log(log_path, lines)
        return 1


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    _ensure_std_streams()

    args = list(sys.argv[1:] if argv is None else argv)
    smoke_test, smoke_log = _parse_smoke_test_args(args)

    port = _find_free_port()
    os.environ["BATTLETAG_PORT"] = str(port)
    os.environ["BATTLETAG_CONFIRM_WRITE"] = "1"
    if smoke_test:
        os.environ["BATTLETAG_DISABLE_UPDATE_CHECK"] = "1"

    from battletag.app import app as battletag_app

    config = uvicorn.Config(
        battletag_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
        log_config=None,
    )
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    if not _wait_for_server(f"{base_url}/api/state"):
        server.should_exit = True
        server_thread.join(timeout=3.0)
        if smoke_test:
            _write_smoke_log(smoke_log, ["SMOKE_TEST_FAILED: BattlePew server failed to start."])
        else:
            print("BattlePew server failed to start.", file=sys.stderr)
        return 1

    if smoke_test:
        rc = _run_smoke_test(base_url, smoke_log)
        server.should_exit = True
        server_thread.join(timeout=5.0)
        return rc

    try:
        # pyrefly: ignore[missing-import]
        import webview

        start_fullscreen = _load_fullscreen_pref()
        api = DesktopApi(start_fullscreen)
        win = webview.create_window(
            "BattlePew",
            base_url,
            width=1280,
            height=800,
            min_size=(900, 600),
            fullscreen=start_fullscreen,
            js_api=api,
        )
        api.bind_window(win)

        def _on_loaded() -> None:
            try:
                win.evaluate_js(_KEYBIND_JS)
            except Exception:
                pass

        win.events.loaded += _on_loaded
        webview.start()
    except Exception:
        webbrowser.open(base_url)
        try:
            while server_thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
