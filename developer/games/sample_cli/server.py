import argparse
import random
from flask import Flask, jsonify, request

app = Flask(__name__)

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
}


@app.route("/state", methods=["GET"])
def get_state():
    """
    允許透過 /state 自動註冊玩家並在兩名玩家到齊時切到 in_game，
    避免雙方都只在輪詢狀態時卡在 waiting。
    """
    player = request.args.get("player")
    if player:
        if player not in state["players"] and len(state["players"]) < 2:
            state["players"].append(player)
            state["scores"][player] = 0
        # 確保每個玩家都有分數欄位
        for p in state["players"]:
            state["scores"].setdefault(p, 0)
        if len(state["players"]) >= 2 and state["status"] == "waiting":
            state["status"] = "in_game"
    if state["players"]:
        state["turn_index"] = state["turn_index"] % len(state["players"])
    return jsonify({"success": True, "data": state})


@app.route("/action", methods=["POST"])
def do_action():
    body = request.get_json() or {}
    player = body.get("player")
    if state["status"] == "finished":
        return jsonify({"success": False, "message": "遊戲已結束", "data": state})
    if state["status"] == "waiting":
        # 初始化玩家
        if player not in state["players"]:
            state["players"].append(player)
            state["scores"][player] = 0
        if len(state["players"]) == 2:
            state["status"] = "in_game"
        else:
            return jsonify({"success": False, "message": "等待另一位玩家加入", "data": state})
    if state["players"]:
        state["turn_index"] = state["turn_index"] % len(state["players"])
    if player != state["players"][state["turn_index"]]:
        return jsonify({"success": False, "message": "尚未輪到你", "data": state})
    roll_val = random.randint(1, 6) + random.randint(1, 6)
    state["last_roll"] = {player: roll_val}
    state["rolls"][player] = roll_val
    # 每次擲骰即累計分數，讓比分即時更新
    state["scores"][player] = state["scores"].get(player, 0) + roll_val
    message = "已擲骰"
    if len(state["rolls"]) == 2:
        state["rolls"] = {}
        # 回合結束時才推進 round，並在達到 max_rounds 立即結算
        if state["round"] >= state["max_rounds"]:
            max_score = max(state["scores"].values())
            winners = [p for p, s in state["scores"].items() if s == max_score]
            state["winner"] = winners
            state["status"] = "finished"
            state["turn_index"] = 0
            return jsonify({"success": True, "message": "遊戲結束", "data": state})
        state["round"] += 1
        state["turn_index"] = 0
        message = "回合結束，下一回合開始"
    else:
        state["turn_index"] = 1
    return jsonify({"success": True, "message": message, "data": state})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
