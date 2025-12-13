import base64
import io
import json
import os
import sys
import zipfile
import threading
import time

import requests

SERVER_URL = os.environ.get("GAME_SERVER_URL", "http://linux1.cs.nycu.edu.tw:5000")
BASE_GAME_DIR = os.path.join(os.path.dirname(__file__), "games")


def menu_title(title: str, username: str | None) -> str:
    return f"{title} ({username})" if username else title


def ensure_server_available(url: str) -> bool:
    try:
        resp = requests.get(f"{url}/games", timeout=3)
        return resp.ok
    except Exception:
        return False


def zip_folder(folder_path: str) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder_path):
            for f in files:
                abs_path = os.path.join(root, f)
                rel_path = os.path.relpath(abs_path, folder_path)
                zf.write(abs_path, rel_path)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def prompt(msg: str) -> str:
    try:
        return input(msg)
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)


def register() -> bool:
    print(f"\n=== {menu_title('開發者註冊', None)} ===")
    username = prompt("帳號: ").strip()
    password = prompt("密碼: ").strip()
    resp = requests.post(f"{SERVER_URL}/dev/register", json={"username": username, "password": password})
    data = resp.json()
    print(data["message"])
    return data.get("success", False)


def login() -> str:
    print(f"\n=== {menu_title('開發者登入', None)} ===")
    username = prompt("帳號: ").strip()
    password = prompt("密碼: ").strip()
    resp = requests.post(f"{SERVER_URL}/dev/login", json={"username": username, "password": password})
    data = resp.json()
    print(data["message"])
    return username if data.get("success") else ""


def choose_local_folder() -> str:
    """
    提供開發者/games 底下的資料夾清單，輸入編號即可選擇。
    若輸入自訂路徑，亦會接受。
    """
    candidates = []
    if os.path.isdir(BASE_GAME_DIR):
        for name in sorted(os.listdir(BASE_GAME_DIR)):
            full = os.path.join(BASE_GAME_DIR, name)
            if os.path.isdir(full):
                candidates.append(full)
    print("\n可用遊戲資料夾：")
    if not candidates:
        print("- (找不到可用資料夾，將使用自訂路徑)")
    else:
        for idx, path in enumerate(candidates, 1):
            print(f"{idx}. {path}")
    choice = prompt("輸入編號或自訂路徑: ").strip()
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(candidates):
            return candidates[idx - 1]
        print("編號不存在")
        return ""
    return choice


def fetch_games() -> list:
    resp = requests.get(f"{SERVER_URL}/games")
    if resp.status_code != 200:
        print("無法取得遊戲列表")
        return []
    return resp.json().get("data", [])


def choose_game(my_name: str) -> str:
    games = [g for g in fetch_games() if g["developer"] == my_name]
    if not games:
        print("沒有上架的遊戲")
        return ""
    for idx, g in enumerate(games, 1):
        status = "下架" if not g.get("active", True) else f"最新版本 {g['latest_version']}"
        print(f"{idx}. {g['name']} - {status}")
    choice = prompt("選擇遊戲編號: ").strip()
    if not choice.isdigit() or int(choice) < 1 or int(choice) > len(games):
        print("選擇無效")
        return ""
    return games[int(choice) - 1]["id"]


def upload_game_flow(dev_name: str):
    print(f"\n=== {menu_title('上架新遊戲', dev_name)} ===")
    name = prompt("遊戲名稱: ").strip()
    description = prompt("簡介: ").strip()
    version = prompt("版本號 (例如 1.0.0): ").strip() or "1.0.0"
    path = choose_local_folder()
    if not os.path.isdir(path):
        print("路徑不存在")
        return
    # manifest 由遊戲資料夾提供：entry/min_players/max_players/server_entry
    manifest_path = os.path.join(path, "manifest.json")
    if not os.path.exists(manifest_path):
        print("缺少 manifest.json，請先在遊戲資料夾內建立。")
        return
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as exc:
        print(f"manifest.json 解析失敗：{exc}")
        return
    required_keys = ["entry", "min_players", "max_players", "server_entry"]
    missing = [k for k in required_keys if k not in manifest]
    if missing:
        print(f"manifest.json 缺少欄位: {', '.join(missing)}")
        return
    entry = manifest.get("entry")
    if not entry or not os.path.exists(os.path.join(path, entry)):
        print(f"找不到入口檔 {entry}，請確認 manifest.json 與檔案內容。")
        return
    server_entry = (manifest.get("server_entry") or "").strip()
    if server_entry and not os.path.exists(os.path.join(path, server_entry)):
        print(f"找不到 server_entry {server_entry}，請確認 manifest.json 與檔案內容。")
        return
    try:
        min_players = int(manifest.get("min_players"))
        max_players = int(manifest.get("max_players"))
    except (TypeError, ValueError):
        print("manifest.json 的 min_players/max_players 必須為整數")
        return
    if min_players <= 0 or max_players <= 0 or min_players > max_players:
        print("manifest.json 的玩家人數設定不合法（需 >0 且 min<=max）")
        return
    file_data = zip_folder(path)
    payload = {
        "developer": dev_name,
        "name": name,
        "description": description,
        "version": version,
        "file_data": file_data,
    }
    resp = requests.post(f"{SERVER_URL}/games", json=payload)
    data = resp.json()
    print(data.get("message"))


def update_game_flow(dev_name: str):
    print(f"\n=== {menu_title('更新遊戲版本', dev_name)} ===")
    game_id = choose_game(dev_name)
    if not game_id:
        return
    version = prompt("新版本號: ").strip()
    path = choose_local_folder()
    notes = prompt("更新說明: ").strip()
    if not os.path.isdir(path):
        print("路徑不存在")
        return
    payload = {
        "developer": dev_name,
        "version": version,
        "file_data": zip_folder(path),
        "notes": notes,
    }
    resp = requests.put(f"{SERVER_URL}/games/{game_id}", json=payload)
    print(resp.json().get("message"))


def remove_game_flow(dev_name: str):
    print(f"\n=== {menu_title('下架遊戲', dev_name)} ===")
    game_id = choose_game(dev_name)
    if not game_id:
        return
    print("下架後：將無法被新玩家下載或建立新房間；若有進行中房間會被阻止下架。")
    confirm = prompt(f"確認下架 {game_id}? (y/N): ").lower()
    if confirm != "y":
        print("已取消")
        return
    resp = requests.delete(f"{SERVER_URL}/games/{game_id}", json={"developer": dev_name})
    print(resp.json().get("message"))


def logout(dev_name: str):
    try:
        requests.post(f"{SERVER_URL}/dev/logout", json={"username": dev_name})
    except Exception:
        pass


def start_heartbeat(dev: str, stop_event: threading.Event, interval: int = 5):
    def _beat():
        while not stop_event.is_set():
            try:
                requests.post(f"{SERVER_URL}/dev/heartbeat", json={"username": dev})
            except Exception:
                pass
            stop_event.wait(interval)

    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    return t


def view_games(dev_name: str):
    print(f"\n=== {menu_title('我的遊戲', dev_name)} ===")
    games = [g for g in fetch_games() if g["developer"] == dev_name]
    if not games:
        print("沒有上架的遊戲")
        return
    for g in games:
        status = "下架" if not g.get("active", True) else f"最新版本 {g['latest_version']}"
        print(f"- {g['name']} ({g['id']}): {status}，人數 {g['min_players']}-{g['max_players']}")


def main():
    dev = None
    hb_stop = None
    try:
        dev = run_flow()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if dev:
                logout(dev)
        except Exception:
            pass
        try:
            if hb_stop:
                hb_stop.set()
        except Exception:
            pass
        sys.exit(0)


def run_flow():
    print(f"=== {menu_title('Developer Client', None)} ===")
    print(f"Server: {SERVER_URL}")
    if not ensure_server_available(SERVER_URL):
        print("無法連線伺服器，請確認位址或網路後再試")
        sys.exit(1)
    dev = ""
    hb_stop = threading.Event()
    while not dev:
        print("\n1) 登入  2) 註冊  3) 離開")
        choice = prompt("選擇: ").strip()
        if choice == "1":
            dev = login()
        elif choice == "2":
            register()
        elif choice == "3":
            sys.exit(0)
        else:
            print("無效選擇")

    start_heartbeat(dev, hb_stop)

    while True:
        print(
            f"\n=== 開發者主選單 ({dev}) ===\n"
            "1) 我的遊戲\n"
            "2) 上架新遊戲\n"
            "3) 更新版本\n"
            "4) 下架遊戲\n"
            "5) 登出並離開\n"
        )
        choice = prompt("選擇: ").strip()
        if choice == "1":
            view_games(dev)
        elif choice == "2":
            upload_game_flow(dev)
        elif choice == "3":
            update_game_flow(dev)
        elif choice == "4":
            remove_game_flow(dev)
        elif choice == "5":
            logout(dev)
            hb_stop.set()
            break
        else:
            print("無效選擇，請重新輸入")
    return dev


if __name__ == "__main__":
    main()
