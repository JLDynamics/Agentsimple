import os
import socket
import subprocess
import sys
import time

HOST = "127.0.0.1"
PORT = 8000
URL = f"http://{HOST}:{PORT}"


def app_dir():
    """Where our files live: the bundle dir when frozen, else this script's folder."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def run_server():
    """Become the Chainlit server. Runs in the spawned child process."""
    os.environ["CHAINLIT_HOST"] = HOST
    os.environ["CHAINLIT_PORT"] = str(PORT)

    from chainlit.config import config
    from chainlit.cli import run_chainlit

    config.run.headless = True
    run_chainlit(os.path.join(app_dir(), "chainlit_app.py"))


def start_server_process():
    """Launch a second copy of ourselves, told to run as the server."""
    env = dict(os.environ, AGENT_RUN_SERVER="1")
    if getattr(sys, "frozen", False):
        cmd = [sys.executable]                              # frozen exe re-runs itself
    else:
        cmd = [sys.executable, os.path.abspath(__file__)]   # python desktop.py
    return subprocess.Popen(cmd, env=env, cwd=app_dir())


def wait_for_server(host, port, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def run_desktop():
    """Open the native window pointed at the server."""
    import webview

    server = start_server_process()
    try:
        if not wait_for_server(HOST, PORT):
            print("The agent server did not start in time.")
            return
        webview.create_window("Simple Agent", URL, width=1100, height=760)
        webview.start()
    finally:
        server.terminate()


if __name__ == "__main__":
    if os.environ.get("AGENT_RUN_SERVER") == "1":
        run_server()
    else:
        run_desktop()
