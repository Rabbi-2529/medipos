"""Start MediPOS on this computer and open it. The desktop shortcut runs this with pythonw, so no black window.

    pythonw scripts\\windows\\medipos_desktop.py               start it if needed, then open the POS
    pythonw scripts\\windows\\medipos_desktop.py --background  start it if needed, open nothing (Windows sign-in)
    python  scripts\\windows\\medipos_desktop.py --stop        stop it

The web server (waitress) and a worker that sends queued SMS and email run in this one process. Starting it
again finds the running copy on its port and only opens the window. Settings: config/settings/desktop.py and .env
(MEDIPOS_PORT, MEDIPOS_HOST). The log is logs/medipos.log.
"""
import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT / "logs"
PID_FILE = LOGS / "medipos.pid"
TITLE = "ThingBD MediPOS"
MESSAGE_EVERY = 60  # seconds between rounds of sending queued SMS and email

log = logging.getLogger("medipos.desktop")


def config():
    """Port and address from .env, read without starting Django so a second click opens the window at once."""
    import environ
    env = environ.Env()
    environ.Env.read_env(ROOT / ".env")
    return env.int("MEDIPOS_PORT", default=8800), env("MEDIPOS_HOST", default="127.0.0.1")


def answer(port):
    """What is listening on the port: "medipos", "other" or None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/manifest.webmanifest", timeout=2) as response:
            return "medipos" if "start_url" in json.load(response) else "other"
    except (OSError, ValueError):
        pass
    import socket
    with socket.socket() as s:
        s.settimeout(1)
        return "other" if s.connect_ex(("127.0.0.1", port)) == 0 else None


def alert(text):
    log.error(text)
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10)
    else:
        print(text, file=sys.stderr)


def browser():
    """Chrome or Edge, which can open MediPOS in its own window like an app."""
    places = []
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base:
            places += [Path(base, "Google", "Chrome", "Application", "chrome.exe"),
                       Path(base, "Microsoft", "Edge", "Application", "msedge.exe")]
    for place in places:
        if place.exists():
            return str(place)
    return shutil.which("chrome") or shutil.which("msedge")


def open_window(port):
    url = f"http://localhost:{port}/sales/pos/"
    exe = browser()
    if exe:
        subprocess.Popen([exe, f"--app={url}"], close_fds=True)
    else:
        webbrowser.open(url)


def open_when_ready(port):
    for _ in range(120):
        if answer(port) == "medipos":
            open_window(port)
            return
        time.sleep(0.5)
    alert("MediPOS did not start. See logs\\medipos.log in the MediPOS folder.")


def send_messages_forever():
    from django.core.management import call_command
    from django.db import close_old_connections
    while True:
        time.sleep(MESSAGE_EVERY)
        try:
            call_command("send_messages", verbosity=0)
        except Exception:  # keep the till running if a gateway is down; the log says why
            log.exception("Sending messages failed")
        finally:
            close_old_connections()


def serve(port, host):
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.desktop"
    import django
    django.setup()
    from django.core.management import call_command
    from django.core.wsgi import get_wsgi_application
    from waitress import serve as waitress_serve

    call_command("migrate", interactive=False, verbosity=0)  # after an update, the database catches up here
    PID_FILE.write_text(str(os.getpid()))
    threading.Thread(target=send_messages_forever, name="messages", daemon=True).start()
    log.warning("MediPOS started on %s:%s", host, port)
    try:
        waitress_serve(get_wsgi_application(), host=host, port=port, threads=8, ident=TITLE)
    finally:
        PID_FILE.unlink(missing_ok=True)


def stop(port):
    try:
        pid = int(PID_FILE.read_text())
    except (OSError, ValueError):
        pid = None
    if pid and answer(port) == "medipos":
        os.kill(pid, 15)
        print("MediPOS stopped.")
    else:
        print("MediPOS is not running.")
    PID_FILE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--background", action="store_true", help="Start without opening a window.")
    parser.add_argument("--stop", action="store_true", help="Stop the running MediPOS.")
    args = parser.parse_args()

    LOGS.mkdir(exist_ok=True)
    if sys.stdout is None or sys.stderr is None:  # pythonw has no console to print to
        out = open(LOGS / "desktop.log", "a", encoding="utf-8", buffering=1)
        sys.stdout, sys.stderr = sys.stdout or out, sys.stderr or out
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    port, host = config()
    if args.stop:
        return stop(port)
    state = answer(port)
    if state == "medipos":
        if not args.background:
            open_window(port)
        return
    if state == "other":
        return alert(f"Another program is using port {port}. Set MEDIPOS_PORT to a free port in the .env file.")
    if not args.background:
        threading.Thread(target=open_when_ready, args=(port,), daemon=True).start()
    try:
        serve(port, host)
    except Exception as exc:
        log.exception("MediPOS stopped with an error")
        alert(f"MediPOS stopped with an error: {exc}\n\nSee logs\\medipos.log in the MediPOS folder.")


if __name__ == "__main__":
    main()
