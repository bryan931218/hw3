import base64
import io
import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from typing import Dict, List, Optional, Tuple
import requests

from .database import Database
from . import game_runtime

STORAGE_ROOT = os.path.join(os.path.dirname(__file__), "storage", "games")
ROOM_HEARTBEAT_TIMEOUT = 15
FINISHED_ROOM_GRACE_SECONDS = 30 
ONLINE_TIMEOUT = int(os.environ.get("ONLINE_TIMEOUT", "20"))  # seconds
REQUIRED_MANIFEST_KEYS = ["entry", "min_players", "max_players", "server_entry"]


def _touch_room_heartbeat(room: Dict, player: str) -> None:
    hb = room.setdefault("heartbeats", {})
    hb[player] = time.time()


def _cleanup_rooms(data: Dict) -> None:
    """
    Auto-close rooms whose members stopped heartbeating and clear finished rooms after a grace period.
    """
    now = time.time()
    to_delete = []
    for rid, room in list(data.get("rooms", {}).items()):
        # Skip already finished rooms; delete later if grace expired
        if room.get("status") == "finished":
            ended_at = room.get("ended_at", now)
            if now - ended_at > FINISHED_ROOM_GRACE_SECONDS:
                to_delete.append(rid)
            continue
        # Initialize missing heartbeat entries for all players
        hb = room.setdefault("heartbeats", {})
        for p in room.get("players", []):
            hb.setdefault(p, room.get("created_at", now))
        stale_players = [p for p, ts in hb.items() if now - ts > ROOM_HEARTBEAT_TIMEOUT]
        if stale_players:
            # Waiting room: only host timeout should close the room. Others are simply removed.
            if room.get("status") == "waiting":
                host = room.get("host")
                if host in stale_players:
                    room["status"] = "finished"
                    room["ended_at"] = int(now)
                    room["ended_reason"] = f"房主 {host} 斷線超時，房間結束"
                    game_runtime.stop_game_server(rid)
                else:
                    room["players"] = [p for p in room.get("players", []) if p not in stale_players]
                    for p in stale_players:
                        hb.pop(p, None)
                continue
            # In-game room: any player timeout ends the room.
            room["status"] = "finished"
            room["ended_at"] = int(now)
            room["ended_reason"] = f"玩家 {', '.join(stale_players)} 斷線超時，房間結束"
            game_runtime.stop_game_server(rid)
    for rid in to_delete:
        game_runtime.stop_game_server(rid)
        data["rooms"].pop(rid, None)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "game"


def _save_game_blob(game_id: str, version: str, file_data_b64: str) -> str:
    os.makedirs(os.path.join(STORAGE_ROOT, game_id), exist_ok=True)
    path = os.path.join(STORAGE_ROOT, game_id, f"{version}.zip")
    with open(path, "wb") as f:
        f.write(base64.b64decode(file_data_b64))
    return path


def _validate_upload(file_data_b64: str) -> Tuple[bool, str, Optional[Dict]]:
    def _norm_path(p: str) -> str:
        p = (p or "").strip().replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        p = p.lstrip("/")
        return p

    try:
        raw = base64.b64decode(file_data_b64)
    except Exception:
        return False, "檔案格式錯誤（base64 解碼失敗）", None
    try:
        buffer = io.BytesIO(raw)
        with zipfile.ZipFile(buffer, "r") as zf:
            if "manifest.json" not in zf.namelist():
                return False, "缺少 manifest.json", None
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            if not isinstance(manifest, dict):
                return False, "manifest.json 格式錯誤（需為 JSON 物件）", None
            missing = [k for k in REQUIRED_MANIFEST_KEYS if k not in manifest]
            if missing:
                return False, f"manifest 缺少欄位: {', '.join(missing)}", None
            extra_keys = sorted([k for k in manifest.keys() if k not in REQUIRED_MANIFEST_KEYS])
            if extra_keys:
                return False, f"manifest 不允許欄位: {', '.join(extra_keys)}", None

            zip_files = {_norm_path(n) for n in zf.namelist() if not n.endswith("/")}

            entry = manifest.get("entry")
            if not isinstance(entry, str) or not entry.strip():
                return False, "entry 必須為字串", None
            entry = _norm_path(entry)
            if not entry:
                return False, "entry 必須為字串", None
            if ".." in entry.split("/"):
                return False, "entry 不可包含 ..", None
            manifest["entry"] = entry
            if entry not in zip_files:
                return False, f"找不到入口檔 {entry}", None
            server_entry = manifest.get("server_entry")
            if not isinstance(server_entry, str) or not server_entry.strip():
                return False, "server_entry 必須為字串", None
            server_entry = _norm_path(server_entry)
            if not server_entry:
                return False, "server_entry 必須為字串", None
            if ".." in server_entry.split("/"):
                return False, "server_entry 不可包含 ..", None
            manifest["server_entry"] = server_entry
            if server_entry not in zip_files:
                return False, f"找不到 server_entry: {server_entry}", None
            try:
                manifest["min_players"] = int(manifest.get("min_players", 0))
                manifest["max_players"] = int(manifest.get("max_players", 0))
            except (TypeError, ValueError):
                return False, "min_players/max_players 必須為整數", None
            if manifest["min_players"] <= 0 or manifest["max_players"] <= 0:
                return False, "玩家數需大於 0", None
            if manifest["min_players"] > manifest["max_players"]:
                return False, "min_players 不可大於 max_players", None
            return True, "ok", manifest
    except zipfile.BadZipFile:
        return False, "上傳檔案不是有效的 zip", None
    except json.JSONDecodeError:
        return False, "manifest.json 解析失敗", None
    except Exception as exc:
        return False, f"檔案驗證失敗: {exc}", None


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
    version: str,
    file_data_b64: str,
    game_type: str = "",
) -> Tuple[bool, str, Optional[Dict]]:
    ok, msg, manifest = _validate_upload(file_data_b64)
    if not ok:
        return False, msg, None
    m_min = manifest.get("min_players")
    m_max = manifest.get("max_players")
    m_type = manifest.get("type", "")
    if game_type and m_type and game_type != m_type:
        return False, "遊戲類型與 manifest 不一致，請修正後再上傳", None
    resolved_type = game_type or m_type or "custom"
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
            "game_type": resolved_type,
            "min_players": m_min,
            "max_players": m_max,
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
    ok, msg, manifest = _validate_upload(file_data_b64)
    if not ok:
        return False, msg, None
    def _update(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        game = data["games"].get(game_id)
        if not game:
            return False, "遊戲不存在", None
        if game["developer"] != developer:
            return False, "無權限更新此遊戲", None
        if not game.get("active", True):
            return False, "遊戲已下架", None
        # 確保與原始設定一致
        if manifest.get("type") and game.get("game_type") and manifest.get("type") != game.get("game_type"):
            return False, "遊戲類型與原上架設定不一致", None
        if manifest.get("min_players") != game.get("min_players") or manifest.get("max_players") != game.get("max_players"):
            return False, "玩家人數設定與原上架設定不一致", None
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
        active_room_count = sum(
            1
            for r in (data.get("rooms") or {}).values()
            if r.get("game_id") == game_id and r.get("status") != "finished"
        )
        # Soft disable (downlist) immediately; keep any existing rooms running.
        game["active"] = False
        game["accept_new_rooms"] = False
        game["deactivated_at"] = int(time.time())
        if active_room_count:
            return True, f"已下架（不再接受新房間），現有房間保留: {active_room_count}"
        return True, "已下架（不再接受新房間）"

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


def _player_game_stats(data: Dict, player: str, game_id: str) -> Dict:
    played_count = data.get("players", {}).get(player, {}).get("played_games", {}).get(game_id, 0) or 0
    return {"plays": int(played_count)}


def game_detail(db: Database, game_id: str, player: Optional[str] = None) -> Optional[Dict]:
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
    if player and player in data.get("players", {}):
        game["player_stats"] = _player_game_stats(data, player, game_id)
    return game


def download_game(db: Database, game_id: str, version: Optional[str] = None) -> Tuple[bool, str, Optional[Dict]]:
    data = db.snapshot()
    game = data["games"].get(game_id)
    if not game:
        return False, "遊戲不存在", None
    if not game.get("active", True):
        has_active_room = any(
            r.get("game_id") == game_id and r.get("status") != "finished" for r in (data.get("rooms") or {}).values()
        )
        if not has_active_room:
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


def game_integrity(db: Database, game_id: str, version: Optional[str] = None) -> Tuple[bool, str, Optional[Dict]]:
    """
    Return expected per-file SHA256 for a given game version so clients can verify local files weren't tampered with.
    """
    data = db.snapshot()
    game = data["games"].get(game_id)
    if not game:
        return False, "遊戲不存在或已下架", None
    if not game.get("active", True):
        has_active_room = any(
            r.get("game_id") == game_id and r.get("status") != "finished" for r in (data.get("rooms") or {}).values()
        )
        if not has_active_room:
            return False, "遊戲不存在或已下架", None
    target_version = version or game["latest_version"]
    version_rec = next((v for v in game["versions"] if v["version"] == target_version), None)
    if not version_rec:
        return False, "指定版本不存在", None
    zip_path = version_rec.get("path")
    if not zip_path or not os.path.exists(zip_path):
        return False, "伺服器檔案遺失", None
    try:
        def _ignore_integrity_path(name: str) -> bool:
            normalized = (name or "").replace("\\", "/").lstrip("/")
            if not normalized:
                return True
            parts = [p for p in normalized.split("/") if p]
            if not parts:
                return True
            if parts[0] in {"__MACOSX", ".git", ".idea", ".vscode"}:
                return True
            if "__pycache__" in parts:
                return True
            base = parts[-1]
            if base in {".DS_Store", "Thumbs.db"}:
                return True
            if base.endswith((".pyc", ".pyo")):
                return True
            return False

        file_hashes: Dict[str, str] = {}
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = (info.filename or "").replace("\\", "/")
                if not name:
                    continue
                if _ignore_integrity_path(name):
                    continue
                content = zf.read(info)
                file_hashes[name] = hashlib.sha256(content).hexdigest()
        manifest = {
            "game_id": game_id,
            "version": target_version,
            "files": file_hashes,
        }
        return True, "OK", manifest
    except zipfile.BadZipFile:
        return False, "伺服器檔案損毀（zip 解析失敗）", None
    except Exception as exc:
        return False, f"完整性資訊產生失敗: {exc}", None


def create_room(db: Database, host: str, game_id: str) -> Tuple[bool, str, Optional[Dict]]:
    def _create(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        game = data["games"].get(game_id)
        if not game or not game.get("active", True):
            return False, "遊戲不存在或已下架", None
        if not game.get("accept_new_rooms", True):
            return False, "此遊戲暫不接受建立新房間", None
        if host not in data["players"]:
            return False, "玩家不存在", None
        try:
            max_rooms = int(os.environ.get("MAX_ROOMS", "0"))
        except ValueError:
            max_rooms = 0
        if max_rooms > 0 and len(data.get("rooms", {})) >= max_rooms:
            return False, "目前房間數已達上限，請加入其他房間", None
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
            "heartbeats": {},
            "ended_reason": None,
        }
        _touch_room_heartbeat(room, host)
        data["rooms"][room_id] = room
        return True, "房間建立成功", room

    return db.update(_create)


def join_room(db: Database, player: str, room_id: str) -> Tuple[bool, str, Optional[Dict]]:
    def _join(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        _cleanup_rooms(data)
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在", None
        if room["status"] != "waiting":
            if room["status"] == "finished":
                return False, room.get("ended_reason", "房間已結束"), None
            return False, "遊戲已開始", None
        game = data["games"].get(room["game_id"])
        # Even if the game is inactive (downlisted), existing rooms remain joinable.
        if not game:
            return False, "遊戲不存在", None
        room.setdefault("max_players", game.get("max_players"))
        room.setdefault("min_players", game.get("min_players"))
        if player not in data["players"]:
            return False, "玩家不存在", None
        if player in room["players"]:
            return False, "已在房間中", None
        if len(room["players"]) >= game["max_players"]:
            return False, "房間已滿", None
        room["players"].append(player)
        _touch_room_heartbeat(room, player)
        return True, "加入成功", room

    return db.update(_join)


def leave_room(db: Database, player: str, room_id: str) -> Tuple[bool, str, Optional[Dict]]:
    def _leave(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        _cleanup_rooms(data)
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在", None
        if room.get("status") == "finished":
            return False, room.get("ended_reason", "房間已結束"), room
        if player not in room["players"]:
            return False, "不在此房間", None
        host = room.get("host")
        # Waiting room: only host leaving closes the room; others simply leave.
        if room.get("status") == "waiting" and player != host:
            room["players"] = [p for p in room["players"] if p != player]
            room.setdefault("heartbeats", {}).pop(player, None)
            return True, "已離開房間", dict(room)
        # Otherwise (host leaving / in-game): close the room.
        room["players"] = [p for p in room["players"] if p != player]
        room["status"] = "finished"
        room["ended_at"] = int(time.time())
        if player == host:
            room["ended_reason"] = f"房主 {player} 離開房間，房間已關閉"
        else:
            room["ended_reason"] = f"{player} 離開房間，房間已關閉"
        game_runtime.stop_game_server(room_id)
        return True, "房間已關閉", dict(room)

    return db.update(_leave)


def room_heartbeat(db: Database, room_id: str, player: str) -> Tuple[bool, str, Optional[Dict]]:
    def _beat(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        _cleanup_rooms(data)
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在或已關閉", None
        if player not in room.get("players", []):
            return False, "你不在此房間", None
        if room.get("status") == "finished":
            return False, room.get("ended_reason", "房間已結束"), dict(room)
        _touch_room_heartbeat(room, player)
        return True, "ok", dict(room)

    return db.update(_beat)


def list_rooms(db: Database) -> List[Dict]:
    def _clean(data: Dict) -> List[Dict]:
        _cleanup_rooms(data)
        rooms = []
        for rid, r in data["rooms"].items():
            if r.get("status") == "finished":
                continue
            game = data["games"].get(r.get("game_id"), {})
            if "max_players" not in r and game.get("max_players") is not None:
                r["max_players"] = game.get("max_players")
            if "min_players" not in r and game.get("min_players") is not None:
                r["min_players"] = game.get("min_players")
            rooms.append(r)
        return rooms

    return db.update(_clean)


def get_room(db: Database, room_id: str) -> Optional[Dict]:
    def _get(data: Dict) -> Optional[Dict]:
        _cleanup_rooms(data)
        room = data["rooms"].get(room_id)
        if not room:
            return None
        game = data["games"].get(room.get("game_id"), {})
        room_copy = dict(room)
        room_copy.setdefault("max_players", game.get("max_players"))
        room_copy.setdefault("min_players", game.get("min_players"))
        return room_copy

    return db.update(_get)


def list_players(db: Database) -> List[Dict]:
    data = db.snapshot()
    now = time.time()
    sessions = (data.get("sessions") or {}).get("player") or {}
    players = []
    for name, info in data["players"].items():
        last_seen = sessions.get(name)
        online = bool(last_seen and now - float(last_seen) <= ONLINE_TIMEOUT)
        players.append({"name": name, "online": online})
    return players


def start_room(db: Database, room_id: str, player: str) -> Tuple[bool, str, Optional[Dict]]:
    def _start(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        _cleanup_rooms(data)
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在", None
        if room.get("status") == "finished":
            return False, room.get("ended_reason", "房間已結束"), room
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
        for p in room["players"]:
            _touch_room_heartbeat(room, p)
        return True, "遊戲開始", room

    return db.update(_start)


def _mark_played(data: Dict, player: str, game_id: str) -> None:
    player_info = data["players"].setdefault(player, {})
    played = player_info.setdefault("played_games", {})
    played[game_id] = played.get(game_id, 0) + 1


def mark_room_played(db: Database, room_id: str, player: str) -> Tuple[bool, str, Optional[Dict]]:
    """
    Increment play count for ALL players in the room exactly once per room,
    intended to be called right before launching the game clients.
    """

    def _mark(data: Dict) -> Tuple[bool, str, Optional[Dict]]:
        _cleanup_rooms(data)
        room = data.get("rooms", {}).get(room_id)
        if not room:
            return False, "房間不存在或已關閉", None
        if room.get("status") != "in_game":
            return False, "房間尚未開始遊戲", None
        players = list(room.get("players") or [])
        if player not in players:
            return False, "你不在此房間", None
        if room.get("played_counted"):
            return True, "已記錄遊玩次數", {"room_id": room_id, "counted": True}
        game_id = room.get("game_id")
        if not game_id:
            return False, "房間遊戲資訊錯誤", None
        for p in players:
            _mark_played(data, p, game_id)
        room["played_counted"] = True
        room["played_counted_at"] = int(time.time())
        return True, "已記錄遊玩次數", {"room_id": room_id, "counted": True}

    return db.update(_mark)


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
        if not game.get("active", True):
            return False, "遊戲已下架，無法評分"

        # If player already rated this game, overwrite the existing rating.
        existing_id = None
        for rid in game.get("ratings", []) or []:
            r = data.get("ratings", {}).get(rid)
            if r and r.get("player") == player and r.get("game_id") == game_id:
                existing_id = rid
                break
        if existing_id:
            data["ratings"][existing_id]["score"] = score
            data["ratings"][existing_id]["comment"] = comment
            data["ratings"][existing_id]["created_at"] = int(time.time())
            return True, "已更新評價"

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
        _cleanup_rooms(data)
        room = data["rooms"].get(room_id)
        if not room:
            return False, "房間不存在", None
        if player not in room["players"]:
            return False, "你不在此房間", None
        if room["status"] == "finished":
            return False, room.get("ended_reason", "房間已結束"), room
        room["status"] = "finished"
        room["ended_at"] = int(time.time())
        room["ended_reason"] = f"{player} 關閉了房間"
        game_runtime.stop_game_server(room_id)
        closed = dict(room)
        return True, "房間已關閉", closed

    return db.update(_close)
