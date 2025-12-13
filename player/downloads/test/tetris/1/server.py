"""
Tetris Online Duel - game server (簡化示例版)
平台啟動方式：python server.py --room <id> --port <port>
協定：長度前置的 JSON（4 bytes big-endian + payload）
事件：
- join: {"type": "join", "player": "<name>", "room": "<id>"}
- ready: {"type": "ready", "player": "<name>"}
- leave: {"type": "leave", "player": "<name>"}
廣播：
- info/start/end 給所有連線玩家

此版本未實作完整俄羅斯方塊邏輯，僅提供連線/ready/開始/結束流程作為架構示例。
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
    def __init__(self, room: str, port: int) -> None:
        self.room = room
        self.port = port
        self.players: Dict[str, socket.socket] = {}
        self.ready = set()
        self.lock = threading.Lock()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", port))
        self.listener.listen(5)

    def start(self) -> None:
        print(f"[Tetris Duel] room {self.room} listening on {self.port}")
        threading.Thread(target=self._accept_loop, daemon=True).start()
        try:
            while True:
                import time
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                self.listener.close()
            except Exception:
                pass

    def _accept_loop(self):
        while True:
            try:
                conn, addr = self.listener.accept()
                threading.Thread(target=self._handle_conn, args=(conn,), daemon=True).start()
            except OSError:
                break

    def _handle_conn(self, conn: socket.socket):
        player = None
        try:
            while True:
                msg = recv_message(conn)
                mtype = msg.get("type")
                if mtype == "join":
                    player = msg.get("player")
                    room = msg.get("room")
                    if room != self.room:
                        send_message(conn, {"type": "info", "text": "房號不符"})
                        break
                    with self.lock:
                        self.players[player] = conn
                    self._broadcast({"type": "info", "text": f"{player} 加入房間"})
                elif mtype == "ready":
                    if not player:
                        continue
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
                    self.ready.discard(player)
                self._broadcast({"type": "info", "text": f"{player} 離開"})
            try:
                conn.close()
            except Exception:
                pass
            self._maybe_end()

    def _maybe_end(self):
        with self.lock:
            if len(self.players) == 0:
                return
            if len(self.players) == 1 and len(self.ready) == 0:
                self._broadcast({"type": "end", "result": "對手離開，遊戲結束"})

    def _broadcast(self, payload: Dict):
        with self.lock:
            for _, s in list(self.players.items()):
                try:
                    send_message(s, payload)
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser(description="Tetris duel game server (示例)")
    parser.add_argument("--room", required=True, help="房間 ID")
    parser.add_argument("--port", required=True, type=int, help="綁定的埠號")
    args = parser.parse_args()
    DuelServer(args.room, args.port).start()


if __name__ == "__main__":
    main()
