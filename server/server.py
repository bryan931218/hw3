import os
from flask import Flask, jsonify, request

from . import auth, game_manager
from .database import Database

db = Database(os.path.join(os.path.dirname(__file__), "data.json"))
app = Flask(__name__)


def _resp(ok: bool, message: str, data=None, status: int = 200):
    code = status
    if not ok:
        code = 400 if status == 200 else status
    payload = {"success": ok, "message": message}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), code


@app.route("/dev/register", methods=["POST"])
def dev_register():
    body = request.get_json() or {}
    ok, msg = auth.register(db, "developer", body.get("username", ""), body.get("password", ""))
    return _resp(ok, msg, status=200 if ok else 400)


@app.route("/dev/login", methods=["POST"])
def dev_login():
    body = request.get_json() or {}
    ok, msg = auth.login(db, "developer", body.get("username", ""), body.get("password", ""))
    return _resp(ok, msg, status=200 if ok else 401)


@app.route("/dev/logout", methods=["POST"])
def dev_logout():
    body = request.get_json() or {}
    auth.logout("developer", body.get("username", ""))
    return _resp(True, "已登出")


@app.route("/dev/heartbeat", methods=["POST"])
def dev_heartbeat():
    body = request.get_json() or {}
    username = body.get("username", "")
    if not auth.is_logged_in("developer", username):
        return _resp(False, "未登入", status=401)
    auth.heartbeat("developer", username)
    return _resp(True, "ok")


@app.route("/player/register", methods=["POST"])
def player_register():
    body = request.get_json() or {}
    ok, msg = auth.register(db, "player", body.get("username", ""), body.get("password", ""))
    return _resp(ok, msg, status=200 if ok else 400)


@app.route("/player/login", methods=["POST"])
def player_login():
    body = request.get_json() or {}
    ok, msg = auth.login(db, "player", body.get("username", ""), body.get("password", ""))
    return _resp(ok, msg, status=200 if ok else 401)


@app.route("/player/logout", methods=["POST"])
def player_logout():
    body = request.get_json() or {}
    auth.logout("player", body.get("username", ""))
    return _resp(True, "已登出")


@app.route("/games", methods=["GET"])
def list_games():
    include_inactive = request.args.get("all") == "1"
    games = game_manager.list_games(db, include_inactive=include_inactive)
    return _resp(True, "ok", games)


@app.route("/games/<game_id>", methods=["GET"])
def game_detail(game_id):
    detail = game_manager.game_detail(db, game_id)
    if not detail:
        return _resp(False, "遊戲不存在", status=404)
    return _resp(True, "ok", detail)


@app.route("/games", methods=["POST"])
def upload_game():
    body = request.get_json() or {}
    dev = body.get("developer", "")
    if not auth.is_logged_in("developer", dev):
        return _resp(False, "請先登入開發者帳號", status=401)
    auth.heartbeat("developer", dev)
    required = ["name", "description", "game_type", "min_players", "max_players", "version", "file_data"]
    missing = [k for k in required if body.get(k) in (None, "")]
    if missing:
        return _resp(False, f"缺少欄位: {', '.join(missing)}", status=400)
    try:
        min_players = int(body["min_players"])
        max_players = int(body["max_players"])
    except (TypeError, ValueError):
        return _resp(False, "玩家數量需為整數", status=400)
    ok, msg, data = game_manager.create_game(
        db,
        dev,
        body["name"],
        body["description"],
        body["game_type"],
        min_players,
        max_players,
        body["version"],
        body["file_data"],
    )
    return _resp(ok, msg, data, status=201 if ok else 400)


@app.route("/games/<game_id>", methods=["PUT"])
def update_game(game_id):
    body = request.get_json() or {}
    dev = body.get("developer", "")
    if not auth.is_logged_in("developer", dev):
        return _resp(False, "請先登入開發者帳號", status=401)
    auth.heartbeat("developer", dev)
    if not body.get("version") or not body.get("file_data"):
        return _resp(False, "缺少版本或檔案資料", status=400)
    ok, msg, data = game_manager.update_game_version(
        db, dev, game_id, body.get("version", ""), body.get("file_data", ""), body.get("notes", "")
    )
    return _resp(ok, msg, data, status=200 if ok else 400)


@app.route("/games/<game_id>", methods=["DELETE"])
def remove_game(game_id):
    body = request.get_json() or {}
    dev = body.get("developer", "")
    if not auth.is_logged_in("developer", dev):
        return _resp(False, "請先登入開發者帳號", status=401)
    auth.heartbeat("developer", dev)
    ok, msg = game_manager.remove_game(db, dev, game_id)
    return _resp(ok, msg, status=200 if ok else 400)


@app.route("/games/<game_id>/download", methods=["GET"])
def download_game(game_id):
    version = request.args.get("version")
    ok, msg, data = game_manager.download_game(db, game_id, version)
    return _resp(ok, msg, data, status=200 if ok else 404)


@app.route("/rooms", methods=["GET"])
def rooms():
    return _resp(True, "ok", game_manager.list_rooms(db))


@app.route("/rooms/<room_id>", methods=["GET"])
def room_detail(room_id):
    match = game_manager.get_room(db, room_id)
    if not match:
        return _resp(False, "房間不存在", status=404)
    return _resp(True, "ok", match)


@app.route("/players", methods=["GET"])
def list_players():
    return _resp(True, "ok", game_manager.list_players(db))


@app.route("/player/me", methods=["GET"])
def player_me():
    username = request.args.get("username", "")
    if not auth.is_logged_in("player", username):
        return _resp(False, "請先登入玩家帳號", status=401)
    info = game_manager.player_info(db, username)
    if not info:
        return _resp(False, "玩家不存在", status=404)
    return _resp(True, "ok", info)


@app.route("/rooms", methods=["POST"])
def create_room():
    body = request.get_json() or {}
    player = body.get("player", "")
    if not auth.is_logged_in("player", player):
        return _resp(False, "請先登入玩家帳號", status=401)
    auth.heartbeat("player", player)
    ok, msg, data = game_manager.create_room(db, player, body.get("game_id", ""))
    return _resp(ok, msg, data, status=201 if ok else 400)


@app.route("/rooms/<room_id>/join", methods=["POST"])
def join_room(room_id):
    body = request.get_json() or {}
    player = body.get("player", "")
    if not auth.is_logged_in("player", player):
        return _resp(False, "請先登入玩家帳號", status=401)
    auth.heartbeat("player", player)
    ok, msg, data = game_manager.join_room(db, player, room_id)
    return _resp(ok, msg, data, status=200 if ok else 400)


@app.route("/rooms/<room_id>/leave", methods=["POST"])
def leave_room(room_id):
    body = request.get_json() or {}
    player = body.get("player", "")
    if not auth.is_logged_in("player", player):
        return _resp(False, "請先登入玩家帳號", status=401)
    auth.heartbeat("player", player)
    ok, msg, data = game_manager.leave_room(db, player, room_id)
    return _resp(ok, msg, data, status=200 if ok else 400)


@app.route("/rooms/<room_id>/start", methods=["POST"])
def start_room(room_id):
    body = request.get_json() or {}
    player = body.get("player", "")
    if not auth.is_logged_in("player", player):
        return _resp(False, "請先登入玩家帳號", status=401)
    auth.heartbeat("player", player)
    ok, msg, data = game_manager.start_room(db, room_id, player)
    return _resp(ok, msg, data, status=200 if ok else 400)


@app.route("/rooms/<room_id>/close", methods=["POST"])
def close_room(room_id):
    body = request.get_json() or {}
    player = body.get("player", "")
    if not auth.is_logged_in("player", player):
        return _resp(False, "請先登入玩家帳號", status=401)
    auth.heartbeat("player", player)
    ok, msg, data = game_manager.close_room(db, room_id, player)
    return _resp(ok, msg, data, status=200 if ok else 400)


@app.route("/ratings", methods=["POST"])
def add_rating():
    body = request.get_json() or {}
    player = body.get("player", "")
    if not auth.is_logged_in("player", player):
        return _resp(False, "請先登入玩家帳號", status=401)
    auth.heartbeat("player", player)
    try:
        score = int(body.get("score", 0))
    except (TypeError, ValueError):
        return _resp(False, "評分需為數字", status=400)
    ok, msg = game_manager.add_rating(db, player, body.get("game_id", ""), score, body.get("comment", ""))
    return _resp(ok, msg, status=200 if ok else 400)


@app.route("/player/heartbeat", methods=["POST"])
def player_heartbeat():
    body = request.get_json() or {}
    username = body.get("username", "")
    if not auth.is_logged_in("player", username):
        return _resp(False, "未登入", status=401)
    auth.heartbeat("player", username)
    return _resp(True, "ok")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
