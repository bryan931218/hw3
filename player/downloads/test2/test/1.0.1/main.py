import argparse
import time
from typing import Dict

import requests


def get_state(server: str, room: str, player: str) -> Dict:
    resp = requests.get(f"{server}/game/{room}/state", params={"player": player})
    return resp.json()


def act_roll(server: str, room: str, player: str) -> Dict:
    resp = requests.post(
        f"{server}/game/{room}/action", json={"player": player, "action": {"type": "roll"}}
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
        turn_player = state["players"][state["turn_index"]] if status != "finished" else None
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
    parser.add_argument("--server", default="", help="伺服器位址")
    parser.add_argument("--room", default="", help="房間 ID")
    args = parser.parse_args()
    if not args.server or not args.room or not args.player:
        print("缺少參數，請由大廳客戶端啟動")
        return
    play_network(args.server, args.room, args.player)


if __name__ == "__main__":
    main()
