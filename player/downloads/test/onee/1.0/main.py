import argparse
import random
import time
import sys

import requests


def notify_close(platform_server: str, room_id: str, player: str) -> None:
    try:
        requests.post(f"{platform_server}/rooms/{room_id}/close", json={"player": player}, timeout=2)
    except Exception:
        pass


def play_round(player: str, rival: str) -> str:
    """
    本地熱座骰子對戰：每位玩家各擲兩顆骰子，比總和。
    若總分相同則重擲，直到分出勝負。
    回傳獲勝玩家名稱。
    """
    while True:
        p_roll = random.randint(1, 6) + random.randint(1, 6)
        r_roll = random.randint(1, 6) + random.randint(1, 6)
        print(f"{player} 擲出 {p_roll}，{rival} 擲出 {r_roll}")
        if p_roll > r_roll:
            return player
        if r_roll > p_roll:
            return rival
        print("平手，重新擲骰...")
        time.sleep(0.8)


def main():
    parser = argparse.ArgumentParser(description="Local dice duel without game server")
    parser.add_argument("--player", default="", help="當前玩家名稱（由大廳客戶端傳入）")
    parser.add_argument("--server", default="", help="平台伺服器位址")
    parser.add_argument("--room", default="", help="房間 ID")
    args = parser.parse_args()

    player = args.player or "Player"
    rival = "對手"
    print(
        "\n============================\n"
        "   🎲 本地雙人熱座骰子對戰\n"
        "============================\n"
        "規則：兩人各擲兩顆骰子，總分高者勝。平手則重擲。\n"
        "此範例完全在客戶端進行，不需要獨立 game server。\n"
    )
    try:
        winner = play_round(player, rival)
        print(f"\n🏆 勝者：{winner}")
        input("\n按 Enter 返回大廳...")
    except KeyboardInterrupt:
        print("\n已中止遊戲")
    finally:
        # 若有房間資訊，嘗試通知平台關閉房間
        if args.server and args.room:
            notify_close(args.server, args.room, player)


if __name__ == "__main__":
    main()
