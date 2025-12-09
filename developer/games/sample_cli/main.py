import argparse
import time
from typing import Dict

import requests


def get_state(server: str, room: str, player: str) -> Dict:
    resp = requests.get(f"{server}/state", params={"player": player})
    return resp.json()


def act_roll(server: str, room: str, player: str) -> Dict:
    resp = requests.post(
        f"{server}/action", json={"player": player, "action": {"type": "roll"}}
    )
    return resp.json()


def play_network(server: str, room: str, player: str):
    print("=== 雙人骰子對戰（線上同步） ===")
    print("輪到自己時按 Enter 擲骰，三回合後分數高者獲勝。")
    while True:
        state_resp = get_state(server, room, player)
        if not state_resp.get("success"):
            print(state_resp.get("message"))
            return
        state = state_resp["data"]
        status = state.get("status")
        scores = state.get("scores", {})
        round_idx = state.get("round")
        players = state.get("players", [])
        turn_player = None
        if players and status != "finished":
            try:
                turn_player = players[state.get("turn_index", 0)]
            except Exception:
                turn_player = players[0]
        print(f"\n回合 {round_idx}/{state.get('max_rounds', 3)} | 比分: {scores}")
        if state.get("last_roll"):
            print(f"最新擲骰: {state['last_roll']}")
        if status == "finished":
            winners = state.get("winner", [])
            if not winners:
                print("平手！")
            else:
                print(f"勝者: {', '.join(winners)}")
            return
        if status == "waiting":
            print("等待另一位玩家加入中...")
            time.sleep(1)
            continue
        if player != turn_player:
            print(f"輪到 {turn_player}，等待中...")
            time.sleep(1)
            continue
        input("輪到你擲骰，按 Enter")
        roll_resp = act_roll(server, room, player)
        print(roll_resp.get("message"))
        time.sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description="Sample CLI dice duel")
    parser.add_argument("--player", default="", help="當前玩家名稱（由大廳客戶端傳入）")
    parser.add_argument("--server", default="", help="平台伺服器位址（未使用）")
    parser.add_argument("--game-server", default="", help="遊戲伺服器位址（由平台提供）")
    parser.add_argument("--room", default="", help="房間 ID")
    args = parser.parse_args()
    game_server = args.game_server or args.server
    if not game_server or not args.room or not args.player:
        print("缺少參數，請由大廳客戶端啟動")
        return
    play_network(game_server, args.room, args.player)


if __name__ == "__main__":
    main()
