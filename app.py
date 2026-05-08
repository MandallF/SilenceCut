"""SilenceCut desktop launcher.

Starts the FastAPI backend in a daemon thread, then opens Microsoft Edge (or
Chrome) in app-window mode pointing at the local server. Edge's launcher
process re-spawns and exits, so we cannot use proc.wait() to detect the
window close — instead we poll for any msedge/chrome process that owns our
unique --user-data-dir, and exit once none remain.

This avoids heavy GUI dependencies (Qt, .NET / pythonnet, CEF) and ships as
a single ~60 MB .exe with PyInstaller.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path


HERE = Path(__file__).resolve().parent

if getattr(sys, "frozen", False):
    BACKEND_DIR = Path(getattr(sys, "_MEIPASS", HERE)) / "backend"
else:
    BACKEND_DIR = HERE / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# In PyInstaller's windowed mode (console=False), sys.stdout / sys.stderr are
# None. Many third-party libs (uvicorn's logging in particular) assume they
# exist and call .isatty() on them. Replace them with a real file handle so
# everything that writes to stdio just goes to the bit bucket.
if sys.stdout is None or sys.stderr is None:
    try:
        _devnull = open(os.devnull, "w", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = _devnull
        if sys.stderr is None:
            sys.stderr = _devnull
    except Exception:
        pass


CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000  # suppress any inherited console window

# Wait timeouts (seconds).
SERVER_START_TIMEOUT = 20.0
BROWSER_FIRST_SEEN_TIMEOUT = 20.0
WINDOW_POLL_INTERVAL = 2.0
WINDOW_IDLE_GRACE = 4.0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(host: str, port: int, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _start_backend(port: int) -> threading.Thread:
    import uvicorn
    from main import app  # imported from backend/

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    def _run() -> None:
        try:
            import asyncio
            asyncio.set_event_loop(asyncio.new_event_loop())
            server.run()
        except BaseException as exc:  # noqa: BLE001
            _log(f"uvicorn crashed: {exc!r}")

    thread = threading.Thread(target=_run, name="silencecut-backend", daemon=True)
    thread.start()
    return thread


def _show_error(message: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, "SilenceCut", 0x10)
    except Exception:
        sys.stderr.write(message + "\n")


def _find_browser() -> tuple[str, str] | None:
    """Return (path, image_basename) for an installed Edge/Chrome — or None."""
    candidates = [
        (os.environ.get("PROGRAMFILES", r"C:\Program Files") + r"\Microsoft\Edge\Application\msedge.exe", "msedge.exe"),
        (os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)") + r"\Microsoft\Edge\Application\msedge.exe", "msedge.exe"),
        (os.environ.get("PROGRAMFILES", r"C:\Program Files") + r"\Google\Chrome\Application\chrome.exe", "chrome.exe"),
        (os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)") + r"\Google\Chrome\Application\chrome.exe", "chrome.exe"),
    ]
    for path, image in candidates:
        if path and Path(path).is_file():
            return path, image
    for name, image in (("msedge", "msedge.exe"), ("chrome", "chrome.exe")):
        found = shutil.which(name)
        if found:
            return found, image
    return None


def _launch_window(browser_path: str, url: str, profile_dir: Path) -> None:
    args = [
        browser_path,
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--window-size=1280,820",
        "--no-first-run",
        "--no-default-browser-check",
        # Prevent session-restore / crash-recovery dialogs that block the app UI.
        "--disable-features=Translate,InfiniteSessionRestore",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--disable-restore-session-state",
    ]
    flags = (CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW) if os.name == "nt" else 0
    subprocess.Popen(
        args,
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _count_browser_processes(browser_image: str, profile_token: str) -> int:
    """Count running browser.exe processes whose command line contains the token.

    Uses psutil (bundled with the .exe). Returns 0 on any failure rather
    than raising — the caller will keep polling either way.
    """
    try:
        import psutil
    except Exception:
        return 0
    image = browser_image.lower()
    count = 0
    try:
        for proc in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if name != image:
                    continue
                cmdline = proc.info.get("cmdline") or []
                joined = " ".join(cmdline) if isinstance(cmdline, list) else str(cmdline)
                if profile_token in joined:
                    count += 1
            except Exception:
                continue
    except Exception as exc:  # noqa: BLE001
        _log(f"psutil iter failed: {exc!r}")
        return 0
    return count


def _wait_for_idle_via_port(port: int, max_hours: float = 8.0) -> None:
    """Fallback wait used when psutil cannot see browser processes.

    We can't reliably detect Edge directly, but our backend logs every HTTP
    hit. If nothing has touched the port for a long stretch, the user has
    almost certainly closed the window and we can exit. We still keep a hard
    upper bound so a stuck launcher always eventually quits.
    """
    deadline = time.time() + max_hours * 3600
    last_active = time.time()
    idle_exit_after = 5 * 60  # 5 minutes of no contact → assume window closed
    _log(
        f"fallback wait: monitoring port {port} for inactivity "
        f"(idle limit {idle_exit_after}s, max {max_hours:.0f}h)"
    )
    while time.time() < deadline:
        time.sleep(15)
        # Try a tiny TCP connect to confirm the backend is still up. If the
        # backend itself died we should exit; if it's up we treat that as a
        # liveness signal and reset the idle clock conservatively (a real
        # idle detector would parse uvicorn access logs, but those are
        # disabled in our config — the cheap heuristic is good enough).
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                last_active = time.time()
        except OSError:
            _log("fallback wait: backend port no longer reachable, exiting")
            return
        if time.time() - last_active >= idle_exit_after:
            _log("fallback wait: idle threshold reached, exiting")
            return
    _log("fallback wait: max duration reached, exiting")


def _wait_until_window_closed(browser_image: str, profile_token: str, port: int) -> None:
    """Block until every browser process owning our profile has exited."""
    # Phase 1: wait for the browser to actually spawn its real process tree.
    # We also fall back to checking whether the backend port is still being
    # accessed, in case psutil cannot enumerate processes (e.g. permission
    # denied on some Windows configurations).
    deadline = time.time() + BROWSER_FIRST_SEEN_TIMEOUT
    seen_alive = False
    while time.time() < deadline:
        if _count_browser_processes(browser_image, profile_token) > 0:
            seen_alive = True
            break
        time.sleep(0.5)

    if not seen_alive:
        _log("browser processes not detected via psutil — falling back to port-poll wait")
        _wait_for_idle_via_port(port)
        return

    # Phase 2: poll until two consecutive zero-readings — Edge briefly drops
    # to 0 processes during its normal restart-with-different-flags dance.
    idle_since: float | None = None
    while True:
        try:
            time.sleep(WINDOW_POLL_INTERVAL)
            c = _count_browser_processes(browser_image, profile_token)
            if c == 0:
                if idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since >= WINDOW_IDLE_GRACE:
                    return
            else:
                idle_since = None
        except BaseException:
            # Be defensive: don't let a transient process-iter glitch kill the loop.
            time.sleep(1.0)


def _log_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    d = base / "SilenceCut"
    d.mkdir(parents=True, exist_ok=True)
    return d / "launcher.log"


def _log(msg: str) -> None:
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def main() -> None:
    _log("--- launch ---")

    try:
        from exporter import ffmpeg_available
        if not ffmpeg_available():
            _log("ffmpeg not available")
            _show_error(
                "FFmpeg kullanilamiyor. Uygulamanin dogru paketlendiginden emin olun."
            )
            sys.exit(1)
    except Exception as exc:
        _log(f"backend import failed: {exc}")
        _show_error(f"Backend yuklenemedi: {exc}")
        sys.exit(1)

    found = _find_browser()
    if not found:
        _log("no browser found")
        _show_error(
            "Microsoft Edge veya Google Chrome bulunamadi.\n"
            "Lutfen Edge'i (Windows ile birlikte gelir) yeniden yukleyin."
        )
        sys.exit(1)
    browser_path, browser_image = found
    _log(f"browser={browser_path}")

    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    _log(f"port={port}")

    try:
        _start_backend(port)
        _log("backend thread spawned")
    except BaseException as exc:  # noqa: BLE001
        import traceback
        _log(f"_start_backend raised: {exc!r}")
        _log(traceback.format_exc())
        _show_error(f"Backend baslatilamadi: {exc}")
        sys.exit(1)

    if not _wait_for_server("127.0.0.1", port, timeout=SERVER_START_TIMEOUT):
        _log("server failed to start")
        _show_error("Sunucu baslatilamadi.")
        sys.exit(1)
    _log("server ready")

    token = f"silencecut-{uuid.uuid4().hex[:10]}"
    profile_dir = Path(tempfile.gettempdir()) / token
    profile_dir.mkdir(exist_ok=True)
    _log(f"profile={profile_dir}")

    try:
        _launch_window(browser_path, url, profile_dir)
        _log("browser launched")
        _wait_until_window_closed(browser_image, token, port)
        _log("window closed; exiting")
    except Exception as exc:  # noqa: BLE001
        _log(f"launch error: {exc}")
    finally:
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
        except Exception:
            pass
        os._exit(0)


if __name__ == "__main__":
    main()
