"""
建立新遊戲骨架的腳本。
用法：python create_game_template.py <game_name>
會將 template/ 複製到 games/<game_name>/，並保留 manifest.json + main.py 供開發者修改。
"""

import os
import shutil
import sys


def main():
    if len(sys.argv) < 2:
        print("請輸入遊戲名稱，例如：python create_game_template.py my_game")
        sys.exit(1)
    game_name = sys.argv[1]
    src = os.path.join(os.path.dirname(__file__), "template")
    dst = os.path.join(os.path.dirname(__file__), "games", game_name)
    if not os.path.exists(src):
        print("找不到 template/ 目錄，請確認專案結構。")
        sys.exit(1)
    if os.path.exists(dst):
        print(f"目標目錄已存在：{dst}")
        sys.exit(1)
    shutil.copytree(src, dst)
    print(
        f"已建立遊戲骨架於 {dst}。\n"
        "- 請先編輯 manifest.json（填入 entry/min_players/max_players，必要時填 server_entry）。\n"
        "- 遊戲名稱與簡介會在 Developer Client 上架時填入（不需寫在 manifest）。\n"
        "- 確認 entry 指向的檔案存在，若需要獨立 game server 再提供 server_entry。\n"
        "- main.py 內有啟動參數說明，可直接開始實作遊戲邏輯。\n"
        "完成後再用 Developer Client 上架。"
    )


if __name__ == "__main__":
    main()
