import argparse
import json
import os
import time
import sys
from typing import Dict

import requests


def get_state(server: str, room: str, player: str) -> Dict:
    try:
        resp = requests.get(f"{server}/state", params={"player": player}, timeout=2)
        return resp.json()
    except Exception as exc:
        return {"success": False, "message": "連線中斷，請稍後再試"}


def act_roll(server: str, room: str, player: str) -> Dict:
    try:
        resp = requests.post(
            f"{server}/action", json={"player": player, "action": {"type": "roll"}}, timeout=2
        )
        return resp.json()
    except Exception as exc:
        return {"success": False, "message": "連線中斷，請稍後再試"}


def leave_room_platform(platform_server: str, room_id: str, player: str):
    try:
        requests.post(f"{platform_server}/rooms/{room_id}/leave", json={"player": player}, timeout=2)
    except Exception:
        pass


def report_result_platform(platform_server: str, room_id: str, player: str, winners):
    if not platform_server or not room_id or not player:
        return
    try:
        requests.post(
            f"{platform_server}/rooms/{room_id}/result",
            json={"player": player, "winners": winners or []},
            timeout=2,
        )
    except Exception:
        pass


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def read_any_key_blocking() -> bool:
    """
    Block until any key is pressed.
    Windows: msvcrt.getwch(); POSIX: cbreak mode read(1).
    Fallback: input() (requires Enter) for IDE consoles.
    """
    if os.name == "nt":
        try:
            import msvcrt

            msvcrt.getwch()
            return True
        except Exception:
            pass
    try:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            sys.stdin.read(1)
            return True
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception:
        try:
            input()
            return True
        except Exception:
            return False


def play_network(server: str, platform_server: str, room: str, player: str):
    last_snapshot = None
    fail_count = 0
    exit_requested = False
    exit_requested_at = None
    result_reported = False
    while True:
        state_resp = get_state(server, room, player)
        if not state_resp.get("success"):
            fail_count += 1
            if fail_count >= 3:
                print("連線中斷，返回大廳")
                leave_room_platform(platform_server, room, player)
                input("按 Enter 返回大廳")
                return
            time.sleep(1)
            continue
        fail_count = 0
        state = state_resp["data"]
        status = state.get("status")
        safe_to_exit = bool(state.get("safe_to_exit", False))
        scores = state.get("scores", {})
        round_idx = state.get("round")
        players = state.get("players", [])
        max_rounds = state.get("max_rounds", 3)
        try:
            round_shown = min(int(round_idx or 1), int(max_rounds or 3))
        except Exception:
            round_shown = round_idx
        turn_player = None
        if players and status not in ("finished",):
            try:
                turn_player = players[state.get("turn_index", 0)]
            except Exception:
                turn_player = players[0]

        snapshot = json.dumps(
            {
                "status": status,
                "scores": scores,
                "round": round_shown,
                "last_roll": state.get("last_roll"),
                "turn": turn_player,
                "players": players,
                "safe_to_exit": safe_to_exit,
            },
            sort_keys=True,
        )
        if snapshot != last_snapshot:
            last_snapshot = snapshot
            clear_screen()
            print(
                "\n============================\n"
                "   🎲 雙人骰子對戰\n"
                "============================"
            )
            print("玩法：輪到自己時按 Enter 擲骰，三回合後分數高者獲勝。")
            banner = f"\n─── 回合 {round_shown}/{max_rounds} ───"
            print(banner)
            if state.get("last_roll"):
                lr = state["last_roll"]
                who, val = list(lr.items())[0]
                print(f"最新擲骰 ➜ {who}: {val}")
            if scores:
                score_line = " | ".join([f"{p}: {scores.get(p,0)}" for p in players])
                print(f"比分   ➜ {score_line}")
            if status == "finished":
                winners = state.get("winner", [])
                if not result_reported:
                    report_result_platform(platform_server, room, player, winners)
                    result_reported = True
                if winners is not None:
                    if not winners or (isinstance(winners, list) and len(winners) > 1):
                        print("平手！")
                    else:
                        if isinstance(winners, list):
                            print(f"勝者: {winners[0]}")
                        else:
                            print(f"勝者: {winners}")
                else:
                    print("有玩家離開，遊戲中止。")
                print("\n按任意鍵結束遊戲")
            elif status == "waiting":
                print("等待另一位玩家加入中...")
            elif player != turn_player:
                print(f"輪到 {turn_player}，等待中...")
            else:
                print("輪到你擲骰，按 Enter ⏎ ")
        if status == "finished":
            if not result_reported:
                report_result_platform(platform_server, room, player, state.get("winner", []))
                result_reported = True
            if not exit_requested:
                read_any_key_blocking()
                exit_requested = True
                exit_requested_at = time.time()
            safe_to_exit_effective = safe_to_exit or ("safe_to_exit" not in state)
            if exit_requested and not safe_to_exit_effective and exit_requested_at:
                if time.time() - float(exit_requested_at) >= 2.0:
                    safe_to_exit_effective = True
            if exit_requested and safe_to_exit_effective:
                leave_room_platform(platform_server, room, player)
                return
            time.sleep(0.2)
            continue
        if status == "waiting":
            time.sleep(1)
            continue
        if player != turn_player:
            time.sleep(1)
            continue
        input()  # 輪到自己時才等待輸入
        roll_resp = act_roll(server, room, player)
        if roll_resp.get("data", {}).get("status") == "finished":
            last_snapshot = None
        print(roll_resp.get("message"))
        time.sleep(0.5)


def main():
    try:
        parser = argparse.ArgumentParser(description="Sample CLI dice duel")
        parser.add_argument("--player", default="", help="當前玩家名稱（由大廳客戶端傳入）")
        parser.add_argument("--server", default="", help="平台伺服器位址（未使用）")
        parser.add_argument("--game-server", default="", help="遊戲伺服器位址（由平台提供）")
        parser.add_argument("--room", default="", help="房間 ID")
        args = parser.parse_args()
        game_server = args.game_server or args.server
        if not game_server or not args.room or not args.player:
            return
        play_network(game_server, args.server, args.room, args.player)
    except KeyboardInterrupt:
        try:
            leave_room_platform(args.server, args.room, args.player)
        except Exception:
            pass
        return


if __name__ == "__main__":
    main()
