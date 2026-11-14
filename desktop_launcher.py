"""Cross-platform desktop launcher for BattlePew (Windows & Linux)."""

from __future__ import annotations

import multiprocessing
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn


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

        webview.create_window(
            "BattlePew",
            base_url,
            width=1280,
            height=800,
            min_size=(900, 600),
        )
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
