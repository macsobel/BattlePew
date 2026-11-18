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


def main() -> int:
    multiprocessing.freeze_support()

    port = _find_free_port()
    os.environ["BATTLETAG_PORT"] = str(port)
    os.environ["BATTLETAG_CONFIRM_WRITE"] = "1"

    config = uvicorn.Config(
        "battletag.app:app",
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)

    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    base_url = f"http://127.0.0.1:{port}"
    if not _wait_for_server(f"{base_url}/api/state"):
        server.should_exit = True
        server_thread.join(timeout=3.0)
        print("BattlePew server failed to start.", file=sys.stderr)
        return 1

    try:
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
