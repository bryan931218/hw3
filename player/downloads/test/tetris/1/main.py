"""
Tetris Online Duel client (簡化示例版)
平台啟動：python main.py --player <name> --server <platform_url> --game-server <gs_url> --room <room_id>
此版僅示範連線/ready/訊息收發，未實作完整方塊遊戲邏輯。
"""

import argparse
import json
import queue
import socket
import struct
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Dict, Optional

MAX_MESSAGE_SIZE = 65536


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def recv_message(sock: socket.socket) -> Dict:
    header = _recv_exact(sock, 4)
    (size,) = struct.unpack("!I", header)
    if size <= 0 or size > MAX_MESSAGE_SIZE:
        raise ValueError("invalid message size")
    payload = _recv_exact(sock, size)
    return json.loads(payload.decode("utf-8"))


def send_message(sock: socket.socket, payload: Dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_MESSAGE_SIZE:
        raise ValueError("payload too large")
    packet = struct.pack("!I", len(data)) + data
    sock.sendall(packet)


class GameClient:
    def __init__(self, player: str, server_host: str, server_port: int, room: str):
        self.player = player
        self.server_host = server_host
        self.server_port = server_port
        self.room = room
        self.sock: Optional[socket.socket] = None
        self.inbox: "queue.Queue[Dict]" = queue.Queue()
        self.root = tk.Tk()
        self.root.title(f"Tetris Duel - {player}")
        self.status_var = tk.StringVar(value="連線中...")
        self.log_text = tk.Text(self.root, height=12, width=60, state="disabled")
        tk.Label(self.root, textvariable=self.status_var).pack(pady=4)
        self.log_text.pack(padx=8, pady=4)
        tk.Button(self.root, text="Ready", command=self._send_ready).pack(pady=4)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def start(self):
        try:
            self.sock = socket.create_connection((self.server_host, self.server_port), timeout=3)
            send_message(self.sock, {"type": "join", "player": self.player, "room": self.room})
        except Exception as exc:
            messagebox.showerror("連線失敗", str(exc))
            self.root.destroy()
            return
        threading.Thread(target=self._reader, daemon=True).start()
        self.root.after(200, self._poll_inbox)
        self.root.mainloop()

    def _reader(self):
        try:
            while True:
                msg = recv_message(self.sock)
                self.inbox.put(msg)
        except Exception:
            self.inbox.put({"type": "info", "text": "連線中斷"})

    def _poll_inbox(self):
        while not self.inbox.empty():
            msg = self.inbox.get()
            self._handle(msg)
        self.root.after(200, self._poll_inbox)

    def _handle(self, msg: Dict):
        mtype = msg.get("type")
        if mtype == "info":
            self._log(msg.get("text", ""))
        elif mtype == "start":
            self._log("對戰開始（示例版未實作盤面）")
        elif mtype == "end":
            self._log(f"遊戲結束：{msg.get('result','')}")
            messagebox.showinfo("遊戲結束", msg.get("result", ""))
            self._on_close()
        else:
            self._log(str(msg))

    def _send_ready(self):
        try:
            send_message(self.sock, {"type": "ready", "player": self.player})
            self._log("已送出 ready")
        except Exception as exc:
            self._log(f"送出失敗: {exc}")

    def _log(self, text: str):
        self.status_var.set(text)
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.configure(state="disabled")
        self.log_text.see(tk.END)

    def _on_close(self):
        try:
            if self.sock:
                send_message(self.sock, {"type": "leave", "player": self.player})
                self.sock.close()
        except Exception:
            pass
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Tetris online duel client (示例)")
    parser.add_argument("--player", required=True)
    parser.add_argument("--server", default="")
    parser.add_argument("--game-server", required=True, help="由平台提供的 game server URL")
    parser.add_argument("--room", required=True)
    args = parser.parse_args()

    host_port = args.game_server.replace("http://", "").replace("https://", "")
    if ":" in host_port:
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host, port = host_port, 80

    GameClient(args.player, host, port, args.room).start()


if __name__ == "__main__":
    main()
