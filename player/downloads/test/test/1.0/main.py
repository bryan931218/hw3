"""
Minimal hello-world style game entry.
符合平台啟動規則：python <entry> --player <name> --server <platform_url> --room <room_id> --game-server <game_server_url>
此範例完全本地，沒有獨立 game server。
"""

import argparse


def main():
    parser = argparse.ArgumentParser(description="Minimal Hello Game")
    parser.add_argument("--player", default="Player", help="玩家名稱（平台傳入）")
    parser.add_argument("--server", default="", help="平台伺服器 URL")
    parser.add_argument("--game-server", default="", help="遊戲伺服器 URL（未使用）")
    parser.add_argument("--room", default="", help="房間 ID（未使用）")
    args = parser.parse_args()

    print("\n=== Minimal Hello Game ===")
    print(f"哈囉，{args.player}！這是一個最小化範例，沒有額外邏輯。")
    print(f"平台伺服器: {args.server or '(未提供)'}")
    if args.room:
        print(f"房間: {args.room}")
    input("\n按 Enter 返回大廳...")


if __name__ == "__main__":
    main()
