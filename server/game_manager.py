import base64
import os
import re
import shutil
import time
from typing import Dict, List, Optional, Tuple
import requests

from .database import Database
from . import game_runtime

STORAGE_ROOT = os.path.join(os.path.dirname(__file__), "storage", "games")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "game"


def _save_game_blob(game_id: str, version: str, file_data_b64: str) -> str:
    os.makedirs(os.path.join(STORAGE_ROOT, game_id), exist_ok=True)
    path = os.path.join(STORAGE_ROOT, game_id, f"{version}.zip")
    with open(path, "wb") as f:
        f.write(base64.b64decode(file_data_b64))
    return path


def reset_rooms(db: Database) -> Tuple[bool, str]:
    def _reset(data: Dict) -> Tuple[bool, str]:
        for rid in list(data.get("rooms", {}).keys()):
            game_runtime.stop_game_server(rid)
        data["rooms"] = {}
        data.setdefault("next_ids", {}).setdefault("room", 1)
        return True, "已清空房間"

    return db.update(_reset)


def create_game(
    db: Database,
    developer: str,
    name: str,
    description: str,
    game_type: str,
    min_players: int,
    max_players: int,
    version: str,
    file_data_b64: str,
) -> Tuple[bool, str, Optional[Dict]]:
    slug = _slugify(name)

    def _create(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        if developer not in data["developers"]:
            return False, "開發者不存在，請重新登入", None
        if slug in data["games"]:
            return False, "遊戲名稱已存在", None
        file_path = _save_game_blob(slug, version, file_data_b64)
        game_info = {
            "id": slug,
            "name": name,
            "developer": developer,
            "description": description,
            "game_type": game_type,
            "min_players": min_players,
            "max_players": max_players,
            "active": True,
            "versions": [
                {
                    "version": version,
                    "path": file_path,
                    "uploaded_at": int(time.time()),
                    "notes": "Initial release",
                }
            ],
            "latest_version": version,
            "ratings": [],
        }
        data["games"][slug] = game_info
        data["developers"][developer].setdefault("games", []).append(slug)
        return True, "上架成功", game_info

    return db.update(_create)


def update_game_version(
    db: Database,
    developer: str,
    game_id: str,
    version: str,
    file_data_b64: str,
    notes: str = "",
) -> Tuple[bool, str, Optional[Dict]]:
    def _update(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        game = data["games"].get(game_id)
        if not game:
            return False, "遊戲不存在", None
        if game["developer"] != developer:
            return False, "無權限更新此遊戲", None
        if not game.get("active", True):
            return False, "遊戲已下架", None
        if any(v["version"] == version for v in game["versions"]):
            return False, "版本重複，請使用新的版本號", None
        file_path = _save_game_blob(game_id, version, file_data_b64)
        record = {
            "version": version,
            "path": file_path,
            "uploaded_at": int(time.time()),
            "notes": notes or "版本更新",
        }
        game["versions"].append(record)
        game["latest_version"] = version
        return True, "更新完成", game

    return db.update(_update)


def remove_game(db: Database, developer: str, game_id: str) -> Tuple[bool, str]:
    def _remove(data: Dict) -> Tuple[bool, str]:
        game = data["games"].get(game_id)
        if not game:
            return False, "遊戲不存在"
        if game["developer"] != developer:
            return False, "無權限下架此遊戲"
        # 關閉並清除所有與此遊戲相關的房間
        room_ids = [rid for rid, r in data.get("rooms", {}).items() if r.get("game_id") == game_id]
        for rid in room_ids:
            game_runtime.stop_game_server(rid)
            data["rooms"].pop(rid, None)
        # 移除所有相關評分
        rating_ids = [rid for rid, r in data.get("ratings", {}).items() if r.get("game_id") == game_id]
        for rid in rating_ids:
            data["ratings"].pop(rid, None)
        # 從開發者列表中移除
        dev_games = data["developers"].get(developer, {}).get("games", [])
        data["developers"][developer]["games"] = [g for g in dev_games if g != game_id]
        # 刪除檔案
        storage_dir = os.path.join(STORAGE_ROOT, game_id)
        if os.path.isdir(storage_dir):
            shutil.rmtree(storage_dir, ignore_errors=True)
        # 最後移除遊戲
        data["games"].pop(game_id, None)
        return True, "已刪除遊戲"

    return db.update(_remove)


def list_games(db: Database, include_inactive: bool = False) -> List[Dict]:
    data = db.snapshot()
    # 若不包含 inactive，直接過濾；inactive 不對外顯示
    games = [g for g in data["games"].values() if include_inactive or g.get("active", True)]
    for g in games:
        if g.get("ratings"):
            rs = [data["ratings"][rid] for rid in g["ratings"] if rid in data["ratings"]]
            if rs:
                g["average_score"] = round(sum(r["score"] for r in rs) / len(rs), 2)
    return games


def game_detail(db: Database, game_id: str) -> Optional[Dict]:
    data = db.snapshot()
    game = data["games"].get(game_id)
    if not game or not game.get("active", True):
        return None
    ratings = [data["ratings"][rid] for rid in game.get("ratings", [])]
    game = {**game, "ratings": ratings}
    if ratings:
        game["average_score"] = round(sum(r["score"] for r in ratings) / len(ratings), 2)
    else:
        game["average_score"] = None
    return game


def download_game(db: Database, game_id: str, version: Optional[str] = None) -> Tuple[bool, str, Optional[Dict]]:
    data = db.snapshot()
    game = data["games"].get(game_id)
    if not game:
        return False, "遊戲不存在", None
    if not game.get("active", True):
        return False, "遊戲已下架", None
    target_version = version or game["latest_version"]
    match = next((v for v in game["versions"] if v["version"] == target_version), None)
    if not match:
        return False, "指定版本不存在", None
    try:
        with open(match["path"], "rb") as f:
            blob = base64.b64encode(f.read()).decode("utf-8")
    except FileNotFoundError:
        return False, "伺服器檔案遺失", None
    return True, "OK", {"file_data": blob, "version": target_version, "name": game["name"], "game_id": game_id}


def create_room(db: Database, host: str, game_id: str) -> Tuple[bool, str, Optional[Dict]]:
    def _create(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        game = data["games"].get(game_id)
        if not game or not game.get("active", True):
            return False, "遊戲不存在或已下架", None
        if host not in data["players"]:
            return False, "玩家不存在", None
        room_id = str(data["next_ids"]["room"])
        data["next_ids"]["room"] += 1
        room = {
            "id": room_id,
            "game_id": game_id,
            "version": game["latest_version"],
            "host": host,
            "players": [host],
            "max_players": game.get("max_players"),
            "min_players": game.get("min_players"),
            "status": "waiting",
            "created_at": int(time.time()),
        }
        data["rooms"][room_id] = room
        return True, "房間建立成功", room

    return db.update(_create)


def join_room(db: Database, player: str, room_id: str) -> Tuple[bool, str, Optional[Dict]]:
    def _join(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在", None
        if room["status"] != "waiting":
            return False, "遊戲已開始", None
        game = data["games"].get(room["game_id"])
        if not game or not game.get("active", True):
            return False, "遊戲不可用", None
        # 補齊人數上限/下限資訊
        room.setdefault("max_players", game.get("max_players"))
        room.setdefault("min_players", game.get("min_players"))
        if player not in data["players"]:
            return False, "玩家不存在", None
        if player in room["players"]:
            return False, "已在房間中", None
        if len(room["players"]) >= game["max_players"]:
            return False, "房間已滿", None
        room["players"].append(player)
        return True, "加入成功", room

    return db.update(_join)


def leave_room(db: Database, player: str, room_id: str) -> Tuple[bool, str, Optional[Dict]]:
    def _leave(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在", None
        if player not in room["players"]:
            return False, "不在此房間", None
        # 任何玩家離開都結束房間並通知其餘玩家回大廳（避免留在遊戲中）
        room["players"] = [p for p in room["players"] if p != player]
        room["status"] = "finished"
        room["ended_at"] = int(time.time())
        game_runtime.stop_game_server(room_id)
        closed = dict(room)
        data["rooms"].pop(room_id, None)
        return True, "房間已關閉", closed

    return db.update(_leave)


def list_rooms(db: Database) -> List[Dict]:
    def _clean(data: Dict) -> List[Dict]:
        # 淨空已結束的房間或已下架遊戲的房間並持久化
        inactive_games = {gid for gid, g in data["games"].items() if not g.get("active", True)}
        to_delete = [
            rid
            for rid, r in data["rooms"].items()
            if r.get("status") == "finished" or r.get("game_id") in inactive_games
        ]
        for rid in to_delete:
            data["rooms"].pop(rid, None)
        rooms = []
        for rid, r in data["rooms"].items():
            game = data["games"].get(r.get("game_id"), {})
            if "max_players" not in r and game.get("max_players") is not None:
                r["max_players"] = game.get("max_players")
            if "min_players" not in r and game.get("min_players") is not None:
                r["min_players"] = game.get("min_players")
            rooms.append(r)
        return rooms

    return db.update(_clean)


def get_room(db: Database, room_id: str) -> Optional[Dict]:
    data = db.snapshot()
    room = data["rooms"].get(room_id)
    if not room:
        return None
    game = data["games"].get(room.get("game_id"), {})
    room = dict(room)
    room.setdefault("max_players", game.get("max_players"))
    room.setdefault("min_players", game.get("min_players"))
    return room


def list_players(db: Database) -> List[Dict]:
    data = db.snapshot()
    players = []
    for name, info in data["players"].items():
        players.append({"name": name, "online": info.get("online", False)})
    return players


def player_info(db: Database, player: str) -> Optional[Dict]:
    data = db.snapshot()
    p = data["players"].get(player)
    if not p:
        return None
    played = p.get("played_games", {})
    given_ratings = [r for r in data["ratings"].values() if r.get("player") == player]
    return {"name": player, "played_games": played, "ratings": given_ratings}


def start_room(db: Database, room_id: str, player: str) -> Tuple[bool, str, Optional[Dict]]:
    def _start(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在", None
        game = data["games"].get(room["game_id"])
        if not game:
            return False, "遊戲不存在", None
        room.setdefault("max_players", game.get("max_players"))
        room.setdefault("min_players", game.get("min_players"))
        if room["status"] != "waiting":
            return False, "遊戲已開始", room
        if player != room.get("host"):
            return False, "只有房主可開始遊戲", None
        if len(room["players"]) < game["min_players"]:
            return False, "人數不足", None
        # 啟動對應遊戲的獨立 game server（若 manifest 指定 server_entry）
        version_rec = next((v for v in game["versions"] if v["version"] == room["version"]), None)
        if not version_rec:
            return False, "找不到版本檔案", None
        ok, msg, server_info = game_runtime.start_game_server(game["id"], room["version"], room_id, version_rec["path"])
        if not ok:
            return False, msg, None
        room["status"] = "in_game"
        room["started_at"] = int(time.time())
        if server_info:
            room["game_server"] = server_info
        else:
            public_host = os.environ.get("GAME_SERVER_PUBLIC_HOST", os.environ.get("HOST", "127.0.0.1"))
            public_port = os.environ.get("PORT", "5000")
            room["game_server"] = {"host": public_host, "port": public_port}
        # 預先註冊房間內所有玩家到 game server，避免只有房主進入遊戲
        try:
            host = room["game_server"]["host"]
            port = room["game_server"]["port"]
            base = f"http://{host}:{port}"
            for p in room["players"]:
                try:
                    requests.get(f"{base}/state", params={"player": p}, timeout=1)
                except Exception:
                    pass
        except Exception:
            pass
        for p in room["players"]:
            _mark_played(data, p, room["game_id"])
        return True, "遊戲開始", room

    return db.update(_start)


def _mark_played(data: Dict, player: str, game_id: str) -> None:
    player_info = data["players"].setdefault(player, {})
    played = player_info.setdefault("played_games", {})
    played[game_id] = played.get(game_id, 0) + 1


def add_rating(db: Database, player: str, game_id: str, score: int, comment: str) -> Tuple[bool, str]:
    if score < 1 or score > 5:
        return False, "評分需在 1-5 分之間"

    def _add(data: Dict) -> Tuple[bool, str]:
        if player not in data["players"]:
            return False, "玩家不存在"
        played_count = data["players"].get(player, {}).get("played_games", {}).get(game_id, 0)
        if played_count <= 0:
            return False, "必須玩過遊戲才能評分"
        game = data["games"].get(game_id)
        if not game:
            return False, "遊戲不存在"
        rating_id = str(data["next_ids"]["rating"])
        data["next_ids"]["rating"] += 1
        data["ratings"][rating_id] = {
            "id": rating_id,
            "player": player,
            "game_id": game_id,
            "score": score,
            "comment": comment,
            "created_at": int(time.time()),
        }
        game.setdefault("ratings", []).append(rating_id)
        return True, "已送出評價"

    return db.update(_add)


def close_room(db: Database, room_id: str, player: str) -> Tuple[bool, str, Optional[Dict]]:
    def _close(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在", None
        if player not in room["players"]:
            return False, "你不在此房間", None
        if room["status"] == "finished":
            return False, "房間已結束", room
        room["status"] = "finished"
        room["ended_at"] = int(time.time())
        game_runtime.stop_game_server(room_id)
        closed = dict(room)
        data["rooms"].pop(room_id, None)
        return True, "房間已關閉", closed

    return db.update(_close)
