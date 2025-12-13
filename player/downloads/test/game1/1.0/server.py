import argparse
import random
import threading
import time
import copy
from typing import Optional, Dict
from flask import Flask, jsonify, request

app = Flask(__name__)

state_lock = threading.Lock()
state = {
    "players": [],
    "scores": {},
    "round": 1,
    "max_rounds": 3,
    "turn_index": 0,
    "rolls": {},
    "status": "waiting",
    "winner": [],
    "last_roll": {},
    "finished_seen": {},
    "finished_at": None,
}

SAFE_EXIT_TIMEOUT_SEC = 2.0


def _snapshot_state(extra: Optional[Dict] = None) -> Dict:
    snap = copy.deepcopy(state)
    if extra:
        snap.update(extra)
    return snap


@app.route("/state", methods=["GET"])
def get_state():
    """
    允許透過 /state 自動註冊玩家並在兩名玩家到齊時切到 in_game，
    避免雙方都只在輪詢狀態時卡在 waiting。
    """
    player = request.args.get("player")
    with state_lock:
        if player:
            if player not in state["players"] and len(state["players"]) < 2:
                state["players"].append(player)
                state["scores"][player] = 0
            for p in state["players"]:
                state["scores"].setdefault(p, 0)
                state["finished_seen"].setdefault(p, False)
            if len(state["players"]) >= 2 and state["status"] == "waiting":
                state["status"] = "in_game"
        if state["players"]:
            state["turn_index"] = state["turn_index"] % len(state["players"])
        # When finished: mark that this player has seen the result so the other side can safely exit too.
        if player and state.get("status") == "finished":
            state["finished_seen"][player] = True
        finished_at = state.get("finished_at")
        safe_by_timeout = False
        if state.get("status") == "finished" and finished_at:
            safe_by_timeout = (time.time() - float(finished_at)) >= SAFE_EXIT_TIMEOUT_SEC
        safe_to_exit = False
        if state.get("status") == "finished":
            safe_to_exit = all(state.get("finished_seen", {}).get(p, False) for p in state.get("players", [])) or safe_by_timeout
        payload = _snapshot_state({"safe_to_exit": safe_to_exit})
    return jsonify({"success": True, "data": payload})


@app.route("/action", methods=["POST"])
def do_action():
    body = request.get_json() or {}
    player = body.get("player")
    with state_lock:
        if state["status"] == "finished":
            if player:
                state["finished_seen"][player] = True
            finished_at = state.get("finished_at")
            safe_by_timeout = False
            if finished_at:
                safe_by_timeout = (time.time() - float(finished_at)) >= SAFE_EXIT_TIMEOUT_SEC
            safe_to_exit = all(state.get("finished_seen", {}).get(p, False) for p in state.get("players", [])) or safe_by_timeout
            payload = _snapshot_state({"safe_to_exit": safe_to_exit})
            return jsonify({"success": False, "message": "遊戲已結束", "data": payload})
        if state["status"] == "waiting":
            # 初始化玩家（最多兩人）
            if player and player not in state["players"]:
                if len(state["players"]) >= 2:
                    payload = _snapshot_state({"safe_to_exit": False})
                    return jsonify({"success": False, "message": "房間已滿", "data": payload})
                state["players"].append(player)
                state["scores"][player] = 0
                state["finished_seen"][player] = False
            if len(state["players"]) == 2:
                state["status"] = "in_game"
            else:
                payload = _snapshot_state({"safe_to_exit": False})
                return jsonify({"success": False, "message": "等待另一位玩家加入", "data": payload})
        if state["players"]:
            state["turn_index"] = state["turn_index"] % len(state["players"])
        if not player or player != state["players"][state["turn_index"]]:
            payload = _snapshot_state({"safe_to_exit": False})
            return jsonify({"success": False, "message": "尚未輪到你", "data": payload})
        roll_val = random.randint(1, 6) + random.randint(1, 6)
        state["last_roll"] = {player: roll_val}
        state["rolls"][player] = roll_val
        state["scores"][player] = state["scores"].get(player, 0) + roll_val
        message = "已擲骰"

        # Round finishes once both players have rolled exactly once.
        if len(state["rolls"]) == len(state["players"]) == 2:
            state["rolls"] = {}
            if state["round"] >= state["max_rounds"]:
                max_score = max(state["scores"].values())
                winners = [p for p, s in state["scores"].items() if s == max_score]
                state["winner"] = winners
                state["status"] = "finished"
                state["turn_index"] = 0
                state["finished_at"] = time.time()
                for p in state["players"]:
                    state["finished_seen"].setdefault(p, False)
                if player:
                    state["finished_seen"][player] = True
                payload = _snapshot_state({"safe_to_exit": False})
                return jsonify({"success": True, "message": "遊戲結束", "data": payload})
            state["round"] += 1
            # Defensive clamp: round should never exceed max_rounds.
            if state["round"] > state["max_rounds"]:
                state["round"] = state["max_rounds"]
            state["turn_index"] = 0
            message = "回合結束，下一回合開始"
        else:
            state["turn_index"] = (state["turn_index"] + 1) % len(state["players"])

        payload = _snapshot_state({"safe_to_exit": False})
        return jsonify({"success": True, "message": message, "data": payload})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
