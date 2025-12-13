"""
GUI 雙人井字棋：兩位玩家輪流點按格子，先連成三子者勝。
介面加入玩家身分、狀態列與紀錄面板，保持線上同步並處理斷線。
"""

import argparse
import threading
import time
import tkinter as tk
from tkinter import messagebox
from typing import Dict

import requests


class TicTacToeGUI:
    def __init__(self, server_base: str, room: str, player: str, platform_server: str):
        self.server = server_base.rstrip("/")
        self.room = room
        self.player = player
        self.platform_server = platform_server.rstrip("/")

        self.root = tk.Tk()
        self.root.title(f"TicTacToe - {player}")
        self.root.configure(bg="#0f172a")
        self.root.geometry("430x540")

        self.status = tk.StringVar(value="讀取狀態中...")
        self.player_info = tk.StringVar(value="等待玩家加入...")
        self.buttons = []
        self.board_state = None
        self.turn_player = None
        self.finished = False
        self.log_list = None
        self.closed = False
        self.result_reported = False

        self._build_ui()
        self._start_poll()

    def _report_result(self, winners):
        if self.result_reported:
            return
        if not self.platform_server or not self.room or not self.player:
            return
        try:
            requests.post(
                f"{self.platform_server}/rooms/{self.room}/result",
                json={"player": self.player, "winners": winners or []},
                timeout=2,
            )
        except Exception:
            pass
        self.result_reported = True

    def _build_ui(self):
        tk.Label(self.root, text="TicTacToe", fg="#e2e8f0", bg="#0f172a", font=("Segoe UI", 20, "bold")).pack(
            pady=(10, 4)
        )
        tk.Label(self.root, textvariable=self.player_info, fg="#cbd5e1", bg="#0f172a", font=("Segoe UI", 11)).pack(
            pady=(0, 8)
        )

        board_frame = tk.Frame(self.root, padx=10, pady=10, bg="#0f172a")
        board_frame.pack()
        for r in range(3):
            row_btns = []
            for c in range(3):
                btn = tk.Button(
                    board_frame,
                    text=" ",
                    width=6,
                    height=3,
                    font=("Segoe UI", 18, "bold"),
                    fg="#0f172a",
                    bg="#e2e8f0",
                    activebackground="#cbd5e1",
                    command=lambda r=r, c=c: self.handle_click(r, c),
                )
                btn.grid(row=r, column=c, padx=6, pady=6)
                row_btns.append(btn)
            self.buttons.append(row_btns)

        tk.Label(self.root, textvariable=self.status, font=("Segoe UI", 13), fg="#e2e8f0", bg="#0f172a").pack(
            pady=(4, 6)
        )

        log_frame = tk.LabelFrame(self.root, text="紀錄", fg="#94a3b8", bg="#0f172a", bd=0, padx=8, pady=6)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_list = tk.Listbox(
            log_frame, height=6, bg="#0f172a", fg="#e2e8f0", selectbackground="#1e293b", activestyle="none"
        )
        self.log_list.pack(fill="both", expand=True)

    def _append_log(self, msg: str):
        if not self.log_list:
            return
        ts = time.strftime("%H:%M:%S")
        self.log_list.insert(tk.END, f"[{ts}] {msg}")
        self.log_list.see(tk.END)

    def _end_with_message(self, msg: str):
        self.status.set(msg)
        try:
            messagebox.showinfo("遊戲結束", msg)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        self._leave_room()

    def handle_click(self, r: int, c: int):
        if self.finished or self.turn_player != self.player:
            return

        def _send():
            try:
                resp = requests.post(
                    f"{self.server}/action",
                    json={"player": self.player, "action": {"type": "move", "row": r, "col": c}},
                    timeout=2,
                ).json()
                self.root.after(0, lambda m=resp.get("message", ""): self.status.set(m))
                if resp.get("success"):
                    self.root.after(0, lambda: self._append_log(f"{self.player} 落子 ({r+1},{c+1})"))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self.status.set(f"送出失敗: {e}"))

        threading.Thread(target=_send, daemon=True).start()

    def _start_poll(self):
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        fail_count = 0
        while True:
            try:
                resp = requests.get(f"{self.server}/state", params={"player": self.player}, timeout=2).json()
                if resp.get("success"):
                    state = resp["data"]
                    self.root.after(0, lambda s=state: self._render_state(s))
                    fail_count = 0
                else:
                    self.root.after(0, lambda m=resp.get("message", ""): self.status.set(m))
            except Exception as exc:
                fail_count += 1
                if self.finished and fail_count >= 2:
                    self._end_with_message("連線中斷，返回大廳")
                    break
                self.root.after(0, lambda e=exc: self.status.set(f"同步失敗，將重試：{e}"))
            time.sleep(2)

    def _render_state(self, state: Dict):
        board = state.get("board", [])
        players = state.get("players", [])
        symbols = state.get("symbols", {})
        self.board_state = board

        sym_me = symbols.get(self.player, "?")
        sym_other = [symbols[p] for p in players if p != self.player]
        other_txt = sym_other[0] if sym_other else "-"
        self.player_info.set(f"你是 {self.player} ({sym_me})  |  對手: {other_txt}")

        if state.get("status") == "finished":
            self.finished = True
            winners = state.get("winner", [])
            if winners is not None:
                self._report_result(winners)
            if winners is None:
                self.status.set("有玩家離開，遊戲中止")
                self._append_log("有玩家離開，遊戲中止")
            elif not winners:
                self.status.set("平手")
                self._append_log("平手，遊戲結束")
            elif self.player in winners:
                self.status.set("你獲勝！")
                self._append_log("你獲勝！")
            else:
                self.status.set(f"勝者: {', '.join(winners)}")
                self._append_log(f"勝者: {', '.join(winners)}")
        else:
            self.finished = False
            turn_idx = state.get("turn_index", 0)
            if turn_idx < len(players):
                self.turn_player = players[turn_idx]
                your_turn = " (你的回合)" if self.turn_player == self.player else ""
                self.status.set(f"輪到 {self.turn_player}{your_turn}")
                if your_turn:
                    self._append_log("輪到你")
        # 更新棋盤文字
        for r in range(3):
            for c in range(3):
                val = board[r][c] if r < len(board) and c < len(board[r]) else None
                text = val if val else " "
                state_flag = tk.NORMAL if not self.finished and self.turn_player == self.player and not val else tk.DISABLED
                self.buttons[r][c].config(text=text, state=state_flag)

    def run(self):
        self.root.mainloop()
        self._leave_room()

    def _leave_room(self):
        if not self.platform_server or not self.room or not self.player:
            return
        try:
            requests.post(
                f"{self.platform_server}/rooms/{self.room}/leave", json={"player": self.player}, timeout=2
            )
        except Exception:
            pass


def main():
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument("--player", required=True)
        parser.add_argument("--server", required=True, help="平台伺服器（用於關閉房間）")
        parser.add_argument("--game-server", required=True, help="遊戲伺服器位址")
        parser.add_argument("--room", required=True)
        args = parser.parse_args()
        gui = TicTacToeGUI(args.game_server, args.room, args.player, args.server)
        gui.run()
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
