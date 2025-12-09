import argparse
from flask import Flask, jsonify, request

app = Flask(__name__)

state = {
    "players": [],
    "symbols": {},
    "board": [[None] * 3 for _ in range(3)],
    "turn_index": 0,
    "status": "waiting",
    "winner": [],
}


def check_win(board, symbol):
    lines = []
    lines.extend(board)
    lines.extend([[board[r][c] for r in range(3)] for c in range(3)])
    lines.append([board[i][i] for i in range(3)])
    lines.append([board[i][2 - i] for i in range(3)])
    for line in lines:
        if all(cell == symbol for cell in line):
            return True
    return False


@app.route("/state", methods=["GET"])
def get_state():
    return jsonify({"success": True, "data": state})


@app.route("/action", methods=["POST"])
def move():
    body = request.get_json() or {}
    player = body.get("player")
    action = body.get("action", {})
    r = action.get("row")
    c = action.get("col")
    if state["status"] == "finished":
        return jsonify({"success": False, "message": "遊戲已結束", "data": state})
    if player not in state["players"]:
        state["players"].append(player)
        state["symbols"][player] = "X" if len(state["players"]) == 1 else "O"
    if len(state["players"]) < 2:
        state["status"] = "waiting"
        return jsonify({"success": False, "message": "等待另一位玩家加入", "data": state})
    state["status"] = "in_game"
    if player != state["players"][state["turn_index"]]:
        return jsonify({"success": False, "message": "尚未輪到你", "data": state})
    if r is None or c is None or r < 0 or r > 2 or c < 0 or c > 2:
        return jsonify({"success": False, "message": "座標錯誤", "data": state})
    if state["board"][r][c] is not None:
        return jsonify({"success": False, "message": "此格已被佔用", "data": state})
    symbol = state["symbols"][player]
    state["board"][r][c] = symbol
    if check_win(state["board"], symbol):
        state["status"] = "finished"
        state["winner"] = [player]
        return jsonify({"success": True, "message": "你獲勝！", "data": state})
    if all(cell is not None for row in state["board"] for cell in row):
        state["status"] = "finished"
        state["winner"] = []
        return jsonify({"success": True, "message": "平手", "data": state})
    state["turn_index"] = 1 - state["turn_index"]
    return jsonify({"success": True, "message": "已落子", "data": state})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    app.run(host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
