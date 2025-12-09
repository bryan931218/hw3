import argparse
import random
import time


def roll():
    return random.randint(1, 6) + random.randint(1, 6)


def play_game(player1: str, player2: str):
    scores = {player1: 0, player2: 0}
    print("=== 雙人骰子對戰 ===")
    print("每回合兩位玩家各擲 2 顆骰子，總和較高者得 1 分。三回合後總分高者獲勝。\n")
    for round_idx in range(1, 4):
        input(f"[回合 {round_idx}] {player1} 按 Enter 擲骰")
        p1 = roll()
        print(f"{player1} 擲出 {p1}")
        input(f"[回合 {round_idx}] {player2} 按 Enter 擲骰")
        p2 = roll()
        print(f"{player2} 擲出 {p2}")
        if p1 > p2:
            scores[player1] += 1
            print(f"{player1} 取得本回合 1 分")
        elif p2 > p1:
            scores[player2] += 1
            print(f"{player2} 取得本回合 1 分")
        else:
            print("平手，此回合不計分")
        print(f"目前比分: {player1} {scores[player1]} - {player2} {scores[player2]}\n")
        time.sleep(0.5)
    if scores[player1] > scores[player2]:
        print(f"遊戲結束，{player1} 勝利！")
    elif scores[player2] > scores[player1]:
        print(f"遊戲結束，{player2} 勝利！")
    else:
        print("平手！感謝遊玩。")


def main():
    parser = argparse.ArgumentParser(description="Sample CLI dice duel")
    parser.add_argument("--player", default="", help="當前玩家名稱（由大廳客戶端傳入）")
    parser.add_argument("--server", default="", help="伺服器位址（僅顯示用）")
    args = parser.parse_args()
    p1 = args.player or input("玩家 1 名稱: ").strip() or "Player1"
    p2 = input("玩家 2 名稱: ").strip() or "Player2"
    if args.server:
        print(f"(連線資訊: {args.server})")
    play_game(p1, p2)


if __name__ == "__main__":
    main()
