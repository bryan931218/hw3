"""
快速重置伺服端資料（清空 data.json 並移除 runtime/game temp）。
Demo 前可執行：python server/reset_data.py
"""

import os
import shutil

from .database import Database, DEFAULT_DATA


def main():
    data_path = os.path.join(os.path.dirname(__file__), "data.json")
    db = Database(data_path)
    db.reset()
    runtime_dir = os.path.join(os.path.dirname(__file__), "storage", "runtime")
    if os.path.exists(runtime_dir):
        shutil.rmtree(runtime_dir, ignore_errors=True)
    print("資料已重置，runtime 暫存已清除。")


if __name__ == "__main__":
    main()
