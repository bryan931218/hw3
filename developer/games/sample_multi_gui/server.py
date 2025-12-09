import argparse
import random
from flask import Flask, jsonify, request

app = Flask(__name__)

state = {
    "players": [],
    "scores": {},
    "round": 1,
    "max_rounds": 5,
    "turn_index": 0,
    "status": "waiting",
    "winner": [],
    "last_roll": {},
}


@app.route("/state", methods=["GET"])
def get_state():
    return jsonify({"success": True, "data": state})


@app.route("/action", methods=["POST"])
def roll():
    body = request.get_json() or {}
    player = body.get("player")
    if state["status"] == "finished":
        return jsonify({"success": False, "message": "遊戲已結束", "data": state})
    if player not in state["players"]:
        if len(state["players"]) >= 4:
            return jsonify({"success": False, "message": "玩家已滿", "data": state})
        state["players"].append(player)
        state["scores"][player] = 0
    if len(state["players"]) < 3:
        state["status"] = "waiting"
        return jsonify({"success": False, "message": "需要至少三名玩家", "data": state})
    state["status"] = "in_game"
    if player != state["players"][state["turn_index"]]:
        return jsonify({"success": False, "message": "尚未輪到你", "data": state})
    roll_val = random.randint(1, 6) + random.randint(1, 6)
    state["last_roll"] = {player: roll_val}
    state["scores"][player] += roll_val
    if state["turn_index"] == len(state["players"]) - 1:
        if state["round"] >= state["max_rounds"]:
            max_score = max(state["scores"].values())
            winners = [p for p, s in state["scores"].items() if s == max_score]
            state["winner"] = winners
            state["status"] = "finished"
            return jsonify({"success": True, "message": "遊戲結束", "data": state})
        state["round"] += 1
        state["turn_index"] = 0
    else:
        state["turn_index"] += 1
    return jsonify({"success": True, "message": "已擲骰", "data": state})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
