import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from typing import Dict, Optional, Tuple

RUNTIME_ROOT = os.path.join(os.path.dirname(__file__), "storage", "runtime")
processes: Dict[str, subprocess.Popen] = {}


def _find_free_port(bind_host: str = "0.0.0.0") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((bind_host, 0))
        return s.getsockname()[1]


def _wait_port(host: str, port: int, timeout_s: float = 3.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _extract(zip_path: str, game_id: str, version: str) -> str:
    target_dir = os.path.join(RUNTIME_ROOT, game_id, version)
    if os.path.exists(target_dir):
        return target_dir
    os.makedirs(target_dir, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(target_dir)
    return target_dir


def start_game_server(game_id: str, version: str, room_id: str, zip_path: str) -> Tuple[bool, str, Optional[Dict]]:
    extract_dir = _extract(zip_path, game_id, version)
    manifest_path = os.path.join(extract_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        return False, "manifest.json 不存在", None
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    server_entry = manifest.get("server_entry")
    if not server_entry:
        return True, "此遊戲沒有獨立 game server，改由客戶端自行處理", None
    entry_path = os.path.join(extract_dir, server_entry)
    if not os.path.exists(entry_path):
        return False, f"找不到 server_entry: {server_entry}", None
    bind_host = os.environ.get("GAME_SERVER_HOST", "0.0.0.0")
    public_host = os.environ.get("GAME_SERVER_PUBLIC_HOST", "linux1.cs.nycu.edu.tw")
    if public_host in ("0.0.0.0", "127.0.0.1"):
        try:
            public_host = socket.gethostbyname(socket.gethostname())
        except Exception:
            public_host = bind_host
    port = _find_free_port(bind_host)
    cmd = [sys.executable, entry_path, "--room", room_id, "--port", str(port)]
    proc = subprocess.Popen(cmd, cwd=extract_dir)
    processes[room_id] = proc

    # Avoid race: wait briefly until the process binds the assigned port.
    if not _wait_port("127.0.0.1", port, timeout_s=3.0):
        exit_code = proc.poll()
        stop_game_server(room_id)
        if exit_code is not None:
            return False, f"game server 啟動失敗 (exit {exit_code})", None
        return False, "game server 啟動逾時", None

    return True, "game server 已啟動", {"host": public_host, "port": port}


def stop_game_server(room_id: str) -> None:
    proc = processes.pop(room_id, None)
    if proc and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
