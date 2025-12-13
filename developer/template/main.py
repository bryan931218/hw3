"""
Minimal game template for uploads.

約定：
- `manifest.json` 的 `entry` 必須指向此檔案或你自己的入口檔。
- `manifest.json` 只需提供 `entry/min_players/max_players/server_entry`，遊戲名稱與簡介由開發者上架時填入。
- 平台啟動遊戲時會以：
  python <entry> --player <name> --server <platform_url> --game-server <game_server_url> --room <room_id>
  執行。若你不需要 game server，可忽略 `--game-server` 或 `--room`。
- 若你的遊戲需要獨立 game server，請在 manifest.json 填寫 `server_entry`，
  平台會用：python <server_entry> --room <id> --port <port> 啟動。

請在下面的 `main()` 實作遊戲邏輯。對於 GUI 遊戲，可在此建立視窗並運行主迴圈；
對於 CLI 遊戲，可用文字互動。
"""

import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", default="Player", help="玩家名稱（平台傳入）")
    parser.add_argument("--server", default="", help="平台伺服器 URL")
    parser.add_argument("--game-server", default="", help="遊戲伺服器 URL（若有 server_entry）")
    parser.add_argument("--room", default="", help="房間 ID")
    args = parser.parse_args()
    # TODO: 在此實作你的遊戲邏輯。以下僅為佔位示範。
    print(
        f"Hello {args.player}! 這裡可以放入你的遊戲邏輯。\n"
        f"平台伺服器: {args.server}\n"
        f"遊戲伺服器: {args.game_server or '(未使用)'}\n"
        f"房間: {args.room or '(未使用)'}"
    )


if __name__ == "__main__":
    main()
