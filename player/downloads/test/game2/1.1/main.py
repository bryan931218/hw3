"""
GUI 雙人井字棋範例：兩位玩家輪流點按格子。先連成三子者獲勝，填滿則平手。
"""

import argparse
import threading
import time
import tkinter as tk
from typing import Dict

import requests


class TicTacToeGUI:
    def __init__(self, server_base: str, room: str, player: str):
        """
        server_base: 遊戲伺服器 URL（包含 host:port），由平台傳入
        room: 房間 ID
        player: 玩家名稱
        """
        self.server = server_base.rstrip("/")
        self.room = room
        self.player = player
        self.root = tk.Tk()
        self.root.title(f"TicTacToe - {player}")
        self.root.configure(bg="#0f172a")
        self.status = tk.StringVar()
        self.status.set("讀取狀態中...")
        self.buttons = []
        self.board_state = None
        self.turn_player = None
        self.finished = False
        self._build_ui()
        self._start_poll()

    def _build_ui(self):
        title = tk.Label(self.root, text="TicTacToe", fg="#e2e8f0", bg="#0f172a", font=("Segoe UI", 18, "bold"))
        title.pack(pady=(12, 6))

        frame = tk.Frame(self.root, padx=10, pady=10, bg="#0f172a")
        frame.pack()
        for r in range(3):
            row_btns = []
            for c in range(3):
                btn = tk.Button(
                    frame,
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
        status_lbl = tk.Label(self.root, textvariable=self.status, font=("Segoe UI", 12), fg="#e2e8f0", bg="#0f172a")
        status_lbl.pack(pady=8)

    def handle_click(self, r: int, c: int):
        if self.finished or self.turn_player != self.player:
            return
        try:
            resp = requests.post(
                f"{self.server}/action",
                json={"player": self.player, "action": {"type": "move", "row": r, "col": c}},
                timeout=5,
            ).json()
            self.status.set(resp.get("message"))
        except Exception as exc:
            self.status.set(f"送出失敗: {exc}")
        self._poll_once()

    def _start_poll(self):
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _poll_loop(self):
        while True:
            self.root.after(0, self._poll_once)
            time.sleep(1)

    def _poll_once(self):
        try:
            resp = requests.get(f"{self.server}/state", params={"player": self.player}, timeout=5).json()
            if not resp.get("success"):
                self.status.set(resp.get("message"))
                return
            state = resp["data"]
            self._render_state(state)
        except Exception as exc:
            self.status.set(f"同步失敗: {exc}")

    def _render_state(self, state: Dict):
        board = state.get("board", [])
        players = state.get("players", [])
        self.board_state = board
        if state.get("status") == "finished":
            self.finished = True
            winners = state.get("winner", [])
            if not winners:
                self.status.set("平手")
            elif self.player in winners:
                self.status.set("你獲勝！")
            else:
                self.status.set(f"勝者: {', '.join(winners)}")
        else:
            self.finished = False
            turn_idx = state.get("turn_index", 0)
            if turn_idx < len(players):
                self.turn_player = players[turn_idx]
                your_turn = " (你的回合)" if self.turn_player == self.player else ""
                self.status.set(f"輪到 {self.turn_player}{your_turn}")
        # 更新棋盤文字
        for r in range(3):
            for c in range(3):
                val = board[r][c] if r < len(board) and c < len(board[r]) else None
                text = val if val else " "
                state_flag = tk.NORMAL if not self.finished and self.turn_player == self.player and not val else tk.DISABLED
                self.buttons[r][c].config(text=text, state=state_flag)

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", required=True)
    parser.add_argument("--server", required=True, help="平台伺服器（未使用）")
    parser.add_argument("--game-server", required=True, help="遊戲伺服器位址")
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    gui = TicTacToeGUI(args.game_server, args.room, args.player)
    gui.run()


if __name__ == "__main__":
    main()
