import base64
import io
import json
import os
import subprocess
import sys
import zipfile
import threading
import time
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse
import ipaddress
import select

import requests

DOWNLOAD_ROOT = os.path.join(os.path.dirname(__file__), "downloads")
SERVER_URL = os.environ.get("GAME_SERVER_URL", "http://linux1.cs.nycu.edu.tw:5000")


def ensure_server_available(url: str) -> bool:
    try:
        resp = requests.get(f"{url}/games", timeout=3)
        return resp.ok
    except Exception:
        return False


def prompt(msg: str) -> str:
    try:
        return input(msg)
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)


def parse_index(choice: str, total: int) -> Optional[int]:
    if not choice.isdigit():
        return None
    idx = int(choice)
    if idx < 1 or idx > total:
        return None
    return idx


def ensure_player_dir(player: str) -> str:
    path = os.path.join(DOWNLOAD_ROOT, player)
    os.makedirs(path, exist_ok=True)
    return path


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def get_input_timeout(prompt_text: str, timeout: float, newline_on_timeout: bool = True) -> Optional[str]:
    """
    在 timeout 秒內等待輸入，逾時回傳 None。Windows 使用 msvcrt，其他平台用 select。
    """
    if prompt_text:
        print(prompt_text, end="", flush=True)
    if os.name == "nt":
        try:
            import msvcrt

            end_time = time.time() + timeout
            buf = b""
            while time.time() < end_time:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch()
                    if ch in ("\r", "\n"):
                        print()
                        return buf.decode("utf-8")
                    elif ch == "\003":
                        raise KeyboardInterrupt
                    else:
                        buf += ch.encode("utf-8")
                        print(ch, end="", flush=True)
                time.sleep(0.05)
            return None
        except Exception:
            return None
    else:
        try:
            rlist, _, _ = select.select([sys.stdin], [], [], timeout)
            if rlist:
                line = sys.stdin.readline()
                return line.rstrip("\n")
            return None
        except Exception:
            return None


def installed_path(player: str) -> str:
    return os.path.join(ensure_player_dir(player), "installed.json")


def load_installed(player: str) -> Dict:
    path = installed_path(player)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_installed(player: str, data: Dict) -> None:
    path = installed_path(player)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def decode_and_extract(player: str, game_id: str, version: str, file_data: str) -> str:
    target_dir = os.path.join(ensure_player_dir(player), game_id, version)
    os.makedirs(target_dir, exist_ok=True)
    buffer = io.BytesIO(base64.b64decode(file_data))
    with zipfile.ZipFile(buffer, "r") as zf:
        zf.extractall(target_dir)
    return target_dir


def fetch_game_detail(game_id: str) -> Optional[Dict]:
    try:
        resp = requests.get(f"{SERVER_URL}/games/{game_id}")
        if resp.ok:
            return resp.json().get("data")
    except Exception:
        return None
    return None


def download_game_version(player: str, game_id: str, version: Optional[str] = None) -> bool:
    params = {}
    if version:
        params["version"] = version
    try:
        resp = requests.get(f"{SERVER_URL}/games/{game_id}/download", params=params)
        data = resp.json()
        if not data.get("success"):
            print(data.get("message", "下載失敗"))
            return False
        payload = data["data"]
        extract_path = decode_and_extract(player, game_id, payload["version"], payload["file_data"])
        installed = load_installed(player)
        installed[game_id] = {"version": payload["version"], "path": extract_path, "name": payload.get("name", game_id)}
        save_installed(player, installed)
        print(f"已安裝 {game_id} 版本 {payload['version']}")
        return True
    except Exception as exc:
        print(f"下載失敗: {exc}")
        return False


def ensure_latest_version(player: str, game_id: str, target_version: Optional[str] = None) -> bool:
    """
    確保玩家已安裝目標版本（若未指定則為最新版本）。必要時提示並自動下載。
    回傳 True 代表已符合條件；False 代表使用者拒絕或失敗。
    """
    installed = load_installed(player)
    detail = fetch_game_detail(game_id)
    if not detail:
        print("無法取得遊戲資訊，請稍後重試")
        return False
    latest_version = detail.get("latest_version")
    desired = target_version or latest_version
    name = detail.get("name", game_id)
    local_ver = installed.get(game_id, {}).get("version")
    if local_ver == desired:
        return True
    if not installed.get(game_id):
        ans = prompt(f"尚未安裝 {name}（將安裝版本 {desired}），是否立即下載？(y/N): ").strip().lower()
        if ans != "y":
            print("未安裝，無法進入房間/建立房間")
            return False
        return download_game_version(player, game_id, desired)
    # 已安裝但版本不同
    ans = prompt(f"{name} 已有版本 {local_ver}，需要更新為 {desired} 才能進入，是否更新？(y/N): ").strip().lower()
    if ans != "y":
        print("已取消，請更新後再試")
        return False
    return download_game_version(player, game_id, desired)


def register() -> bool:
    print("\n=== 玩家註冊 ===")
    username = prompt("帳號: ").strip()
    password = prompt("密碼: ").strip()
    resp = requests.post(f"{SERVER_URL}/player/register", json={"username": username, "password": password})
    data = resp.json()
    print(data["message"])
    return data.get("success", False)


def login() -> str:
    print("\n=== 玩家登入 ===")
    username = prompt("帳號: ").strip()
    password = prompt("密碼: ").strip()
    resp = requests.post(f"{SERVER_URL}/player/login", json={"username": username, "password": password})
    data = resp.json()
    print(data["message"])
    return username if data.get("success") else ""


def list_store_games():
    resp = requests.get(f"{SERVER_URL}/games")
    if resp.status_code != 200:
        print("無法取得列表")
        return []
    games = resp.json().get("data", [])
    print("\n=== 商城遊戲列表 ===")
    if not games:
        print("目前沒有可遊玩的遊戲")
        return games
    for idx, g in enumerate(games, 1):
        score = g.get("average_score")
        score_text = f"{score}/5" if score else "尚無評分"
        print(f"{idx}. {g['name']} ({g['id']}) by {g['developer']} - {score_text} 最新 {g['latest_version']}")
    return games


def view_game_detail():
    games = list_store_games()
    if not games:
        return
    choice = prompt("輸入要查看的遊戲編號: ").strip()
    if parse_index(choice, len(games)) is None:
        print("選擇無效")
        return
    game = games[int(choice) - 1]
    resp = requests.get(f"{SERVER_URL}/games/{game['id']}")
    if resp.status_code != 200:
        print("讀取失敗")
        return
    detail = resp.json()["data"]
    print(f"\n名稱: {detail['name']}")
    print(f"作者: {detail['developer']}")
    print(f"簡介: {detail['description']}")
    print(f"類型: {detail['game_type']} 人數 {detail['min_players']}-{detail['max_players']}")
    print(f"最新版本: {detail['latest_version']}")
    if detail.get("average_score"):
        print(f"平均評分: {detail['average_score']}")
    if detail.get("ratings"):
        print("評論:")
        for r in detail["ratings"]:
            print(f"- {r['player']} 給 {r['score']} 分: {r['comment']}")


def download_or_update(player: str):
    games = list_store_games()
    if not games:
        return
    choice = prompt("選擇要下載/更新的遊戲編號: ").strip()
    if parse_index(choice, len(games)) is None:
        print("選擇無效")
        return
    game = games[int(choice) - 1]
    installed = load_installed(player)
    local_version = installed.get(game["id"], {}).get("version")
    if local_version == game["latest_version"]:
        print("已是最新版本")
        return
    resp = requests.get(f"{SERVER_URL}/games/{game['id']}/download")
    data = resp.json()
    if not data.get("success"):
        print(data.get("message"))
        return
    payload = data["data"]
    extract_path = decode_and_extract(player, game["id"], payload["version"], payload["file_data"])
    installed[game["id"]] = {"version": payload["version"], "path": extract_path, "name": game["name"]}
    save_installed(player, installed)
    print(f"下載完成，安裝於 {extract_path}")


def list_installed_games(player: str):
    installed = load_installed(player)
    print("\n=== 已安裝遊戲 ===")
    if not installed:
        print("尚未下載任何遊戲")
        return
    for gid, info in installed.items():
        print(f"- {info.get('name', gid)} ({gid}) 版本 {info.get('version')}")


def create_room(player: str):
    games = list_store_games()
    if not games:
        return None
    choice = prompt("選擇要建立房間的遊戲編號: ").strip()
    if parse_index(choice, len(games)) is None:
        print("選擇無效")
        return None
    game = games[int(choice) - 1]
    # 確保已安裝最新版本
    if not ensure_latest_version(player, game["id"], game.get("latest_version")):
        return None
    resp = requests.post(f"{SERVER_URL}/rooms", json={"player": player, "game_id": game["id"]})
    data = resp.json()
    print(data.get("message"))
    if data.get("success"):
        room = data["data"]
        print(f"房號 {room['id']} 遊戲 {room['game_id']} 版本 {room['version']}")
        return room
    # 若房間數量已滿，建議直接查看現有房間
    if "上限" in data.get("message", ""):
        list_rooms()
    return None


def list_rooms(installed_games: Optional[List[str]] = None):
    resp = requests.get(f"{SERVER_URL}/rooms")
    if resp.status_code != 200:
        print("無法取得房間列表")
        return []
    rooms = resp.json().get("data", [])
    # 若缺少 max_players，嘗試從遊戲詳細補齊，避免顯示 ?.
    for r in rooms:
        detail = fetch_game_detail(r.get("game_id"))
        r["game_name"] = detail.get("name") if detail else r.get("game_id")
        if r.get("max_players") in (None, 0, "?") and detail and detail.get("max_players"):
            r["max_players"] = detail.get("max_players")
    print("\n=== 房間列表 ===")
    if not rooms:
        print("目前沒有房間")
    for r in rooms:
        if installed_games is not None:
            # 標示玩家是否已安裝，方便決策
            installed_flag = "已安裝" if r["game_id"] in installed_games else "未安裝"
        else:
            installed_flag = ""
        max_p = r.get("max_players") or "?"
        name = r.get("game_name", r.get("game_id"))
        suffix = f" | {installed_flag}" if installed_flag else ""
        print(
            f"- 房號 {r['id']} | 遊戲 {name} ({r['game_id']}) | 狀態 {r['status']} "
            f"| 玩家 {len(r['players'])}/{max_p}{suffix}"
        )
    return rooms


def join_room(player: str) -> Optional[Dict]:
    installed = load_installed(player)
    rooms = list_rooms()  # 顯示所有房間，並提示是否已安裝
    if not rooms:
        print("沒有符合你已安裝遊戲的房間")
        return None
    rid = prompt("輸入要加入的房號: ").strip()
    # 先取得房間資訊以核對版本，避免未更新就加入
    detail = fetch_room(rid)
    if not detail:
        print("房間不存在或已關閉")
        return None
    target_version = detail.get("version")
    if not ensure_latest_version(player, detail.get("game_id"), target_version):
        return None
    resp = requests.post(f"{SERVER_URL}/rooms/{rid}/join", json={"player": player})
    data = resp.json()
    print(data.get("message"))
    if data.get("success"):
        return data["data"]
    return None


def leave_room(player: str, room_id: str) -> bool:
    try:
        resp = requests.post(f"{SERVER_URL}/rooms/{room_id}/leave", json={"player": player})
        data = resp.json()
        print(data.get("message"))
        return data.get("success", False)
    except Exception as exc:
        print(f"離開房間失敗: {exc}")
        return False


def start_room(player: str, room_id: str) -> Optional[Dict]:
    resp = requests.post(f"{SERVER_URL}/rooms/{room_id}/start", json={"player": player})
    data = resp.json()
    print(data.get("message"))
    if data.get("success"):
        return data.get("data")
    return None


def close_room(player: str, room_id: str):
    try:
        resp = requests.post(f"{SERVER_URL}/rooms/{room_id}/close", json={"player": player})
        data = resp.json()
        print(data.get("message"))
        return data.get("success", False)
    except Exception as exc:
        print(f"關閉房間失敗: {exc}")
        return False


def fetch_room(room_id: str) -> Optional[Dict]:
    try:
        resp = requests.get(f"{SERVER_URL}/rooms/{room_id}")
        if resp.ok:
            return resp.json().get("data")
    except Exception:
        return None
    return None


def room_lobby(player: str, room: Dict):
    """
    房間內的選單：房主可開始遊戲；其他人等待開始。
    僅在狀態變更時刷新畫面，移除手動「重新整理」選項，房間資訊與選單同時顯示。
    """
    hb_stop = threading.Event()
    start_room_heartbeat(player, room["id"], hb_stop)
    launched = False
    last_view = None
    last_reason = None

    def render(room_info: Dict, status: str, host: str, force: bool = False) -> bool:
        nonlocal last_view
        snapshot = json.dumps(
            {
                "status": status,
                "players": room_info.get("players", []),
                "game": room_info.get("game_id"),
                "version": room_info.get("version"),
            },
            sort_keys=True,
        )
        if not force and snapshot == last_view:
            return False
        last_view = snapshot
        players_line = ", ".join(room_info.get("players", []))
        print(
            f"\n=== 房間 {room_info['id']} ===\n"
            f"遊戲: {room_info['game_id']} 版本: {room_info['version']}\n"
            f"房主: {host} | 玩家 ({len(room_info.get('players', []))}/{room_info.get('max_players','?')}): {players_line}\n"
            f"狀態: {status}"
        )
        if player == host:
            print("1) 開始遊戲  2) 離開房間")
        else:
            print("1) 離開房間")
        return True

    try:
        while True:
            latest = fetch_room(room["id"])
            if not latest:
                if last_reason:
                    print(f"房間已關閉：{last_reason}")
                else:
                    print("房間已關閉或不存在")
                return
            room = latest
            if room.get("ended_reason"):
                last_reason = room.get("ended_reason")
            if room.get("max_players") in (None, 0, "?"):
                detail = fetch_game_detail(room.get("game_id"))
                if detail and detail.get("max_players"):
                    room["max_players"] = detail.get("max_players")
            status = room.get("status")
            host = room.get("host")
            rendered = render(room, status, host)
            if status == "in_game" and not launched:
                launched = True
                ok = launch_game(player, room["id"], room["game_id"])
                if ok:
                    return
                launched = False  # 啟動失敗，留在房間畫面
                continue
            if status == "finished":
                reason = room.get("ended_reason") or "房間已結束"
                print(f"房間已結束：{reason}")
                return
            if rendered:
                print("選擇: ", end='', flush=True)
            choice = get_input_timeout("", 2, newline_on_timeout=False)
            if choice is None:
                continue
            choice = (choice or "").strip()
            if not choice:
                continue
            choice = choice[:1] 
            if player == host:
                if choice == "1":
                    started = start_room(player, room["id"])
                    if started:
                        room = started
                    continue
                elif choice == "2":
                    close_room(player, room["id"])
                    return
                else:
                    print("請輸入 1-2")
                    rendered = render(room, status, host, force=True)
                    if rendered:
                        print("選擇: ", end='', flush=True)
            else:
                if choice == "1":
                    leave_room(player, room["id"])
                    return
                else:
                    print("請輸入 1")
                    rendered = render(room, status, host, force=True)
                    if rendered:
                        print("選擇: ", end='', flush=True)
    finally:
        hb_stop.set()


def launch_game(player: str, room_id: str, game_id: str) -> bool:
    installed = load_installed(player)
    info = installed.get(game_id)
    if not info:
        print("尚未下載遊戲")
        return False
    room = fetch_room(room_id)
    if room:
        target_ver = room.get("version")
        if target_ver and info.get("version") != target_ver:
            if not ensure_latest_version(player, game_id, target_ver):
                return
            info = load_installed(player).get(game_id)
    path = info["path"]
    manifest = os.path.join(path, "manifest.json")
    entry = "main.py"
    has_game_server = False
    if os.path.exists(manifest):
        with open(manifest, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)
            entry = manifest_data.get("entry", "main.py")
            has_game_server = bool(manifest_data.get("server_entry"))
    script = os.path.join(path, entry)
    if not os.path.exists(script):
        print("找不到遊戲入口，請確認下載內容")
        return False
    # 取得房間詳細以取得 game_server 端點，若沒有則回退平台伺服器
    gs_url = SERVER_URL
    try:
        room_resp = requests.get(f"{SERVER_URL}/rooms/{room_id}")
        if room_resp.ok:
            room_data = room_resp.json().get("data", {})
            game_server = room_data.get("game_server", {})
            if game_server.get("host") and game_server.get("port"):
                gs_url = f"http://{game_server['host']}:{game_server['port']}"
    except Exception:
        pass
    # 避免拿到 0.0.0.0，改用平台 URL 的 host
    try:
        parsed = urlparse(gs_url)
        platform_host = urlparse(SERVER_URL).hostname or "localhost"
        # 若 game_server host 是 0.0.0.0、127.0.0.1 或私有網段，改用平台 host
        if parsed.hostname:
            try:
                ip = ipaddress.ip_address(parsed.hostname)
                if ip.is_loopback or ip.is_private:
                    gs_url = urlunparse((parsed.scheme, f"{platform_host}:{parsed.port}", parsed.path, "", "", ""))
            except ValueError:
                # 不是 IP，直接使用原始 host
                pass
    except Exception:
        pass
    # 簡單預檢 game server：僅對需要 server_entry 的遊戲做 TCP 連線檢查（避免剛啟動時的 race）
    if has_game_server:
        parsed = urlparse(gs_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 80
        import socket

        deadline = time.time() + 6.0
        last_exc: Optional[Exception] = None
        while time.time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    last_exc = None
                    break
            except Exception as exc:
                last_exc = exc
                time.sleep(0.2)
        if last_exc is not None:
            print(f"無法連線到遊戲伺服器（可能仍在啟動中），請稍後再試：{last_exc}")
            return False
    print(f"啟動遊戲 {info.get('name', game_id)} (版本 {info['version']})")
    cmd = [sys.executable, script, "--player", player, "--server", SERVER_URL, "--room", room_id]
    cmd.extend(["--game-server", gs_url])
    try:
        ret = subprocess.call(cmd)
        if ret != 0:
            print(f"遊戲啟動失敗 (exit {ret})")
            return False
    except FileNotFoundError:
        print("找不到 Python 或遊戲檔案，啟動失敗")
        return False
    except Exception as exc:
        print(f"遊戲啟動失敗: {exc}")
        return False
    return True


def rate_game(player: str):
    installed = load_installed(player)
    if not installed:
        print("尚未下載任何遊戲")
        return
    games = list(installed.items())
    for idx, (gid, info) in enumerate(games, 1):
        print(f"{idx}. {info.get('name', gid)} ({gid})")
    choice = prompt("選擇要評分的遊戲: ").strip()
    if parse_index(choice, len(games)) is None:
        print("選擇無效")
        return
    gid, info = games[int(choice) - 1]
    try:
        score = int(prompt("評分 1-5: ").strip() or "0")
    except ValueError:
        print("請輸入數字 1-5")
        return
    if score < 1 or score > 5:
        print("評分需介於 1-5")
        return
    comment = prompt("評論: ").strip()
    resp = requests.post(
        f"{SERVER_URL}/ratings", json={"player": player, "game_id": gid, "score": score, "comment": comment}
    )
    print(resp.json().get("message"))


def logout(player: str):
    try:
        requests.post(f"{SERVER_URL}/player/logout", json={"username": player})
    except Exception:
        pass


def start_heartbeat(player: str, stop_event: threading.Event, interval: int = 5):
    def _beat():
        while not stop_event.is_set():
            try:
                requests.post(f"{SERVER_URL}/player/heartbeat", json={"username": player})
            except Exception:
                pass
            stop_event.wait(interval)

    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    return t


def start_room_heartbeat(player: str, room_id: str, stop_event: threading.Event, interval: int = 4):
    """
    Ping the platform while留在房間或進入遊戲中，讓伺服器偵測斷線玩家並能提供結束原因。
    """

    def _beat():
        while not stop_event.is_set():
            try:
                resp = requests.post(
                    f"{SERVER_URL}/rooms/{room_id}/heartbeat", json={"player": player}, timeout=2
                )
                if resp.ok:
                    payload = resp.json()
                    if not payload.get("success"):
                        msg = payload.get("message") or "房間已結束"
                        print(f"\n[房間通知] {msg}")
                        break
            except Exception:
                pass
            stop_event.wait(interval)

    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    return t


def view_my_profile(player: str):
    resp = requests.get(f"{SERVER_URL}/player/me", params={"username": player})
    if not resp.ok:
        print("無法取得個人資料，請確認登入狀態")
        return
    data = resp.json().get("data", {})
    print("\n=== 我的紀錄 ===")
    print(f"玩家: {data.get('name', player)}")
    played = data.get("played_games", {})
    if not played:
        print("- 尚未有遊戲紀錄")
    else:
        print("- 已遊玩：")
        for gid, cnt in played.items():
            print(f"  {gid} 次數 {cnt}")
    ratings = data.get("ratings", [])
    if ratings:
        print("- 我的評分：")
        for r in ratings:
            print(f"  {r['game_id']} 給 {r['score']} 分: {r.get('comment','')}")


def view_status(player: str):
    print("\n=== 大廳狀態 ===")
    try:
        players_resp = requests.get(f"{SERVER_URL}/players")
        rooms_resp = requests.get(f"{SERVER_URL}/rooms")
        games_resp = requests.get(f"{SERVER_URL}/games")
    except Exception as exc:
        print(f"讀取失敗: {exc}")
        return
    if players_resp.ok:
        players = players_resp.json().get("data", [])
        print("\n玩家列表 (online/offline):")
        if not players:
            print("- 無玩家")
        for p in players:
            status = "在線" if p.get("online") else "離線"
            print(f"- {p.get('name')} [{status}]")
    if rooms_resp.ok:
        rooms = rooms_resp.json().get("data", []) or []
        print("\n房間列表 (所有遊戲):")
        if not rooms:
            print("- 無房間")
        for r in rooms:
            detail = fetch_game_detail(r.get("game_id")) or {}
            game_name = detail.get("name", r.get("game_id"))
            max_p = r.get("max_players") or detail.get("max_players") or "?"
            print(
                f"- 房號 {r['id']} | 遊戲 {game_name} ({r.get('game_id')}) "
                f"| 狀態 {r['status']} | 玩家 {len(r.get('players', []))}/{max_p} | 房主 {r.get('host','?')}"
            )
    if games_resp.ok:
        games = games_resp.json().get("data", [])
        print("\n上架遊戲列表:")
        if not games:
            print("- 尚無遊戲")
        for g in games:
            score = g.get("average_score")
            score_text = f"{score}/5" if score else "尚無評分"
            print(f"- {g['name']} ({g['id']}) v{g['latest_version']} by {g['developer']} | {score_text}")


def main():
    player = None
    hb_stop = None
    try:
        player, hb_stop = run_flow()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if player:
                logout(player)
        except Exception:
            pass
        try:
            if hb_stop:
                hb_stop.set()
        except Exception:
            pass
        sys.exit(0)


def run_flow():
    print("=== Lobby Client ===")
    print(f"Server: {SERVER_URL}")
    if not ensure_server_available(SERVER_URL):
        print("無法連線伺服器")
        sys.exit(1)
    player = ""
    current_room = None
    hb_stop = threading.Event()
    while not player:
        print("\n1) 登入  2) 註冊  3) 離開")
        choice = prompt("選擇: ").strip()
        if choice == "1":
            player = login()
        elif choice == "2":
            register()
        elif choice == "3":
            sys.exit(0)
        else:
            print("無效選擇")

    start_heartbeat(player, hb_stop)

    while True:
        print(
            "\n=== 大廳主選單 ===\n"
            "1) 瀏覽遊戲\n"
            "2) 開始遊戲\n"
            "3) 狀態看板\n"
            "4) 評分與評論\n"
            "5) 我的紀錄\n"
            "6) 離開\n"
        )
        choice = prompt("選擇: ").strip()
        if choice == "1":
            while True:
                print(
                    "\n--- 商城 / 下載 ---\n"
                    "1) 瀏覽商城\n"
                    "2) 查看遊戲詳細\n"
                    "3) 下載/更新遊戲\n"
                    "4) 查看已安裝遊戲\n"
                    "5) 返回主選單\n"
                )
                sub = prompt("選擇: ").strip()
                if sub == "1":
                    list_store_games()
                elif sub == "2":
                    view_game_detail()
                elif sub == "3":
                    download_or_update(player)
                elif sub == "4":
                    list_installed_games(player)
                elif sub == "5":
                    break
                else:
                    print("請輸入 1-5")
        elif choice == "2":
            while True:
                print(
                    "\n--- 開始遊戲 ---\n"
                    "1) 建立房間\n"
                    "2) 加入房間\n"
                    "3) 查看房間列表\n"
                    "4) 返回主選單\n"
                )
                sub = prompt("選擇: ").strip()
                if sub == "1":
                    current_room = create_room(player)
                    if current_room:
                        room_lobby(player, current_room)
                        current_room = None
                elif sub == "2":
                    current_room = join_room(player)
                    if current_room:
                        room_lobby(player, current_room)
                        current_room = None
                elif sub == "3":
                    list_rooms() 
                elif sub == "4":
                    break
                else:
                    print("請輸入 1-4")
        elif choice == "3":
            view_status(player)
        elif choice == "4":
            rate_game(player)
        elif choice == "5":
            view_my_profile(player)
        elif choice == "6":
            logout(player)
            hb_stop.set()
            break
        else:
            print("請輸入 1-6")
    return player, hb_stop


if __name__ == "__main__":
    main()
