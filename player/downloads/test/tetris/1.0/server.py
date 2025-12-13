"""
Tetris Online Duel game server (簡化占位版)。
平台啟動方式：python server.py --room <id> --port <port>
此伺服器只做連線握手與簡單對戰訊息，未實作完整俄羅斯方塊邏輯，作為上架示例。
"""

import argparse
import json
import socket
import struct
import threading
from typing import Dict

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


class DuelServer:
    def __init__(self, room_id: str, port: int):
        self.room_id = room_id
        self.port = port
        self.players = {}
        self.ready = set()
        self.lock = threading.Lock()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", port))
        self.listener.listen(5)

    def start(self):
        print(f"[Tetris Duel] room {self.room_id} listening on {self.port}")
        threading.Thread(target=self._accept_loop, daemon=True).start()
        try:
            while True:
                pass
        except KeyboardInterrupt:
            self.listener.close()

    def _accept_loop(self):
        while True:
            try:
                conn, addr = self.listener.accept()
                threading.Thread(target=self._handle_conn, args=(conn, addr), daemon=True).start()
            except OSError:
                break

    def _handle_conn(self, conn: socket.socket, addr):
        player = None
        try:
            while True:
                msg = recv_message(conn)
                mtype = msg.get("type")
                if mtype == "join":
                    player = msg.get("player")
                    with self.lock:
                        self.players[player] = conn
                    send_message(conn, {"type": "info", "text": f"Welcome {player}, waiting for opponent"})
                    self._broadcast({"type": "info", "text": f"{player} joined room {self.room_id}"})
                elif mtype == "ready":
                    player = msg.get("player", player)
                    with self.lock:
                        self.ready.add(player)
                        if len(self.ready) >= 2:
                            self._broadcast({"type": "start"})
                elif mtype == "leave":
                    break
        except Exception:
            pass
        finally:
            if player:
                with self.lock:
                    self.players.pop(player, None)
            try:
                conn.close()
            except Exception:
                pass

    def _broadcast(self, payload: Dict):
        with self.lock:
            for p, s in list(self.players.items()):
                try:
                    send_message(s, payload)
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description="Tetris duel game server (simplified)")
    parser.add_argument("--room", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    DuelServer(args.room, args.port).start()


if __name__ == "__main__":
    main()
