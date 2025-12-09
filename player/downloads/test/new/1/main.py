"""
多人 GUI 骰子賽跑（線上同步）：3-4 名玩家輪流按鈕擲骰，預設 5 回合。分數最高者獲勝。
"""

import argparse
import threading
import time
import tkinter as tk
from typing import Dict

import requests


class DiceRaceGUI:
    def __init__(self, server_base: str, room: str, player: str):
        self.server = server_base.rstrip("/")
        self.room = room
        self.player = player
        self.root = tk.Tk()
        self.root.title(f"Dice Race - {player}")
        self.status = tk.StringVar()
        self.result = tk.StringVar()
        self.score_labels: Dict[str, tk.Label] = {}
        self.turn_player = None
        self.finished = False
        self._build_ui()
        threading.Thread(target=self._poll_loop, daemon=True).start()

    def _build_ui(self):
        top = tk.Frame(self.root, padx=10, pady=10)
        top.pack()
        self.status_lbl = tk.Label(self.root, textvariable=self.status, font=("Arial", 12))
        self.status_lbl.pack(pady=8)
        btn_frame = tk.Frame(self.root)
        btn_frame.pack()
        self.roll_btn = tk.Button(btn_frame, text="擲骰", font=("Arial", 14), command=self.roll)
        self.roll_btn.grid(row=0, column=0, padx=8)
        self.result_lbl = tk.Label(self.root, textvariable=self.result, font=("Arial", 12), fg="blue")
        self.result_lbl.pack(pady=8)

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
                    self.root.after(0, lambda: self.status.set("連線結束，返回大廳"))
                    break
                self.root.after(0, lambda e=exc: self.status.set(f"同步失敗，將重試：{e}"))
            time.sleep(2)

    def _render_state(self, state: Dict):
        players = state.get("players", [])
        scores = state.get("scores", {})
        # 初始化分數標籤
        if not self.score_labels and scores:
            for p in players:
                lbl = tk.Label(self.root, text=f"{p}: {scores.get(p,0)}", font=("Arial", 12))
                lbl.pack(anchor="w")
                self.score_labels[p] = lbl
        # 更新分數
        for p, lbl in self.score_labels.items():
            lbl.config(text=f"{p}: {scores.get(p,0)}")

        self.turn_player = players[state.get("turn_index", 0)] if players else None
        round_idx = state.get("round", 1)
        max_rounds = state.get("max_rounds", 5)
        if state.get("status") == "finished":
            self.finished = True
            self.roll_btn.config(state=tk.DISABLED)
            winners = state.get("winner", [])
            if not winners:
                self.status.set("平手")
            elif self.player in winners:
                self.status.set(f"你獲勝！ (總分 {scores.get(self.player,0)})")
            else:
                self.status.set(f"勝者: {', '.join(winners)}")
        else:
            your_turn = " (你的回合)" if self.turn_player == self.player else ""
            self.status.set(f"回合 {round_idx}/{max_rounds}，輪到 {self.turn_player}{your_turn}")
            self.roll_btn.config(state=tk.NORMAL if self.turn_player == self.player else tk.DISABLED)
        if state.get("last_roll"):
            lr = state["last_roll"]
            self.result.set(f"最新擲骰: {list(lr.keys())[0]} -> {list(lr.values())[0]}")

    def roll(self):
        if self.finished or self.turn_player != self.player:
            return
        def _send():
            try:
                resp = requests.post(
                    f"{self.server}/action",
                    json={"player": self.player, "action": {"type": "roll"}},
                    timeout=2,
                ).json()
                self.root.after(0, lambda m=resp.get("message", ""): self.status.set(m))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self.status.set(f"送出失敗: {e}"))
        threading.Thread(target=_send, daemon=True).start()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--player", required=True)
    parser.add_argument("--server", required=True, help="平台伺服器（未使用）")
    parser.add_argument("--game-server", required=True, help="遊戲伺服器位址")
    parser.add_argument("--room", required=True)
    args = parser.parse_args()
    gui = DiceRaceGUI(args.game_server, args.room, args.player)
    gui.run()


if __name__ == "__main__":
    main()
