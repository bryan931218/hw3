import os
import os
import time
from typing import Dict, Tuple

from .database import Database

# How long a session stays valid since the last heartbeat (seconds).
# Backward compatible env var: SESSION_TTL.
SESSION_TIMEOUT = int(os.environ.get("SESSION_TIMEOUT", os.environ.get("SESSION_TTL", "3600")))

# Prevent immediate re-login from another device within this window (seconds).
# Backward compatible env var: SESSION_LOGIN_LOCK.
CONCURRENT_LOGIN_LOCK = int(os.environ.get("CONCURRENT_LOGIN_LOCK", os.environ.get("SESSION_LOGIN_LOCK", "30")))

def _get_table(user_type: str) -> str:
    if user_type not in ("developer", "player"):
        raise ValueError("Unknown user type")
    return f"{user_type}s"


def _ensure_sessions(data: Dict) -> Dict[str, Dict[str, float]]:
    sessions = data.setdefault("sessions", {})
    sessions.setdefault("developer", {})
    sessions.setdefault("player", {})
    return sessions


def register(db: Database, user_type: str, username: str, password: str) -> Tuple[bool, str]:
    table = _get_table(user_type)
    if not username or not password:
        return False, "帳號或密碼不可為空"

    def _register(data: Dict) -> Tuple[bool, str]:
        if username in data[table]:
            return False, "帳號已被使用"
        data[table][username] = {"password": password}
        if user_type == "developer":
            data[table][username]["games"] = []
        else:
            data[table][username]["played_games"] = {}
        _ensure_sessions(data)
        return True, f"{user_type} registered"

    return db.update(_register)


def login(db: Database, user_type: str, username: str, password: str) -> Tuple[bool, str]:
    table = _get_table(user_type)

    def _login(data: Dict) -> Tuple[bool, str]:
        user = data[table].get(username)
        if not user or user.get("password") != password:
            return False, "帳號或密碼錯誤"
        sessions = _ensure_sessions(data)[user_type]
        now = time.time()
        last_seen = sessions.get(username)
        if last_seen and now - last_seen < CONCURRENT_LOGIN_LOCK:
            return False, "帳號已在其他裝置登入"
        sessions[username] = now
        user["online"] = True
        return True, "登入成功"

    return db.update(_login)


def logout(db: Database, user_type: str, username: str) -> None:
    table = _get_table(user_type)

    def _logout(data: Dict) -> None:
        sessions = _ensure_sessions(data)[user_type]
        sessions.pop(username, None)
        if username in data.get(table, {}):
            data[table][username]["online"] = False

    try:
        db.update(_logout)
    except Exception:
        pass


def is_logged_in(db: Database, user_type: str, username: str) -> bool:
    # IMPORTANT: keep this read-only to avoid persisting to disk on every API request.
    # Session freshness is updated only by explicit heartbeat endpoints.
    try:
        data = db.snapshot()
        sessions = (data.get("sessions") or {}).get(user_type) or {}
        last_seen = sessions.get(username)
        if not last_seen:
            return False
        return (time.time() - float(last_seen)) <= SESSION_TIMEOUT
    except Exception:
        return False


def heartbeat(db: Database, user_type: str, username: str) -> None:
    def _beat(data: Dict) -> None:
        sessions = _ensure_sessions(data)[user_type]
        if username in sessions:
            sessions[username] = time.time()

    try:
        db.update(_beat)
    except Exception:
        pass
