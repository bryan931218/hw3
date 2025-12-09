import os
import time
from typing import Dict, Tuple

from .database import Database

# 若每 5 秒回報一次心跳，容忍兩次漏報 => 約 10 秒即視為登出
HEARTBEAT_TIMEOUT = 10  # seconds
active_sessions: Dict[str, Dict[str, float]] = {"developer": {}, "player": {}}


def _get_table(user_type: str) -> str:
    if user_type not in ("developer", "player"):
        raise ValueError("Unknown user type")
    return f"{user_type}s"


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
        return True, f"{user_type} registered"

    return db.update(_register)


def login(db: Database, user_type: str, username: str, password: str) -> Tuple[bool, str]:
    table = _get_table(user_type)

    def _login(data: Dict) -> Tuple[bool, str]:
        user = data[table].get(username)
        if not user or user.get("password") != password:
            return False, "帳號或密碼錯誤"
        now = time.time()
        # 若已有舊 session 但已逾時，視為離線
        last_seen = active_sessions[user_type].get(username)
        if last_seen and now - last_seen < HEARTBEAT_TIMEOUT:
            return False, "帳號已在其他裝置登入"
        active_sessions[user_type][username] = now
        # 標記目前線上狀態（方便玩家列表呈現）
        user["online"] = True
        return True, "登入成功"

    return db.update(_login)


def logout(user_type: str, username: str) -> None:
    if username in active_sessions.get(user_type, {}):
        active_sessions[user_type].pop(username, None)
    # 嘗試標記離線（若資料存在）
    try:
        data_path = os.path.join(os.path.dirname(__file__), "data.json")
        db = Database(data_path)  # lightweight re-open to update flag
        table = _get_table(user_type)

        def _mark(data: Dict):
            if username in data.get(table, {}):
                data[table][username]["online"] = False

        db.update(_mark)
    except Exception:
        pass


def is_logged_in(user_type: str, username: str) -> bool:
    now = time.time()
    sessions = active_sessions.get(user_type, {})
    last_seen = sessions.get(username)
    if not last_seen:
        return False
    if now - last_seen > HEARTBEAT_TIMEOUT:
        sessions.pop(username, None)
        return False
    sessions[username] = now  # touch
    return True


def heartbeat(user_type: str, username: str) -> None:
    if username in active_sessions.get(user_type, {}):
        active_sessions[user_type][username] = time.time()
