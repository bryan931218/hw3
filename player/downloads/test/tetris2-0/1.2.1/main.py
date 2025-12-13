from __future__ import annotations

import argparse
import heapq
import json
import queue
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

MAX_MESSAGE_SIZE = 65536
RENDER_DELAY_MS = 150


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


BOARD_WIDTH = 10
BOARD_HEIGHT = 20

TETROMINO_SHAPES = {
    "I": [
        [(0, 1), (1, 1), (2, 1), (3, 1)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
        [(0, 2), (1, 2), (2, 2), (3, 2)],
        [(1, 0), (1, 1), (1, 2), (1, 3)],
    ],
    "O": [[(1, 0), (2, 0), (1, 1), (2, 1)]] * 4,
    "T": [
        [(1, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (1, 2)],
        [(1, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "S": [
        [(1, 0), (2, 0), (0, 1), (1, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
        [(1, 1), (2, 1), (0, 2), (1, 2)],
        [(0, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "Z": [
        [(0, 0), (1, 0), (1, 1), (2, 1)],
        [(2, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (1, 2), (2, 2)],
        [(1, 0), (0, 1), (1, 1), (0, 2)],
    ],
    "J": [
        [(0, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (1, 1), (0, 2), (1, 2)],
    ],
    "L": [
        [(2, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 2)],
        [(0, 1), (1, 1), (2, 1), (0, 2)],
        [(0, 0), (1, 0), (1, 1), (1, 2)],
    ],
}

CELL_COLORS = {
    "I": "#5ED3FF",
    "O": "#F6ED63",
    "T": "#C376F0",
    "S": "#4CD58C",
    "Z": "#F45B5B",
    "J": "#5B7CF4",
    "L": "#F4A55B",
}

KEY_BINDINGS = {
    "Left": "LEFT",
    "Right": "RIGHT",
    "Down": "SOFT_DROP",
    "Up": "CW",
    "z": "CCW",
    "space": "HARD_DROP",
    "c": "HOLD",
}

FONT_FAMILY = "DejaVu Sans"
BASE_FONT = (FONT_FAMILY, 13)
SECTION_FONT = (FONT_FAMILY, 16, "bold")
STATUS_FONT = (FONT_FAMILY, 13, "bold")
MIDDLE_DOT = "·"


class TetrisClient(tk.Tk):
    def __init__(self, host: str, port: int, player_name: str, room_id: str, *, mode: str = "PLAY", expected_players: int = 2) -> None:
        super().__init__()
        self.title(f"Tetris - {player_name}")
        self.room_id = room_id
        self.player_name = player_name or "Player"
        self.mode = mode
        self.expected_players = expected_players
        self.sock = socket.create_connection((host, port))
        self.lock = threading.Lock()
        self.queue: "queue.Queue[Dict]" = queue.Queue()
        self.remaining = 0
        self.user_id: Optional[str] = None
        self.player_names: Dict[str, str] = {}
        self.render_delay = RENDER_DELAY_MS / 1000.0
        self.snapshot_buffer: List[Tuple[float, Dict[str, object]]] = []
        self.result_window: Optional[tk.Toplevel] = None
        self.ready_sent = False
        self.started = False
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.resizable(True, True)
        self._build_ui()
        self._handshake()
        self.network_thread = threading.Thread(target=self._network_loop, daemon=True)
        self.network_thread.start()
        self.after(50, self._process_queue)

    # ------------------------------- UI ------------------------------------
    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)
        left_name = f"{self.player_name} (You)" if self.mode != "WATCH" else "Player 1"
        right_name = "Opponent" if self.mode != "WATCH" else "Player 2"
        self.name_vars = [tk.StringVar(value=left_name), tk.StringVar(value=right_name)]
        self.stat_vars = [tk.StringVar(value="Score 0 | Lines 0"), tk.StringVar(value="Score 0 | Lines 0")]
        info_row = ttk.Frame(container)
        info_row.grid(row=0, column=0, columnspan=2, pady=(0, 8))
        self.next_canvases: List[tk.Canvas] = []
        for idx in range(2):
            frame = ttk.Frame(info_row, padding=6)
            frame.grid(row=0, column=idx, padx=10)
            ttk.Label(frame, textvariable=self.name_vars[idx], font=SECTION_FONT).pack()
            ttk.Label(frame, textvariable=self.stat_vars[idx], font=BASE_FONT).pack()
            ttk.Label(frame, text="Next:", font=("Noto Sans", 14)).pack(pady=(4, 0))
            preview = tk.Canvas(frame, width=4 * 18, height=4 * 18, bg="#1a1a1a", highlightthickness=0)
            preview.pack()
            self.next_canvases.append(preview)
        self.left_cell = 24
        self.right_cell = 24 if self.mode == "WATCH" else 16
        self.my_canvas = tk.Canvas(container, width=BOARD_WIDTH * self.left_cell, height=BOARD_HEIGHT * self.left_cell, bg="#0a0a0f")
        self.opp_canvas = tk.Canvas(container, width=BOARD_WIDTH * self.right_cell, height=BOARD_HEIGHT * self.right_cell, bg="#0a0a0f")
        self.my_canvas.grid(row=1, column=0, padx=8, pady=8)
        self.opp_canvas.grid(row=1, column=1, padx=8, pady=8)
        self.info_var = tk.StringVar(value="Connecting to game server...")
        self.status_var = tk.StringVar(value="")
        ttk.Label(container, textvariable=self.info_var, font=BASE_FONT).grid(row=2, column=0, columnspan=2, pady=6)
        ttk.Label(container, textvariable=self.status_var, font=SECTION_FONT).grid(row=3, column=0, columnspan=2, pady=(0, 4))
        if self.mode != "WATCH":
            btns = ttk.Frame(container)
            btns.grid(row=4, column=0, columnspan=2, pady=(0, 6))
            self.ready_button = ttk.Button(btns, text="Ready", command=self._send_ready)
            self.ready_button.pack(side="left", padx=6)
        else:
            self.ready_button = None
        ttk.Label(
            container,
            text="Shortcuts: ←/→ move, ↓ soft drop, ↑ rotate, Z counter-rotate, Space hard drop, C hold",
            font=("Noto Sans", 14),
        ).grid(row=5 if self.mode != "WATCH" else 4, column=0, columnspan=2, pady=(4, 0))
        self.bind("<KeyPress>", self._on_key)
        self.focus_force()

    # ------------------------------- Network --------------------------------
    def _handshake(self) -> None:
        payload = {
            "type": "HELLO",
            "room_id": self.room_id,
            "player": self.player_name,
            "mode": self.mode,
        }
        send_message(self.sock, payload)
        welcome = recv_message(self.sock)
        if welcome.get("type") != "WELCOME":
            raise RuntimeError(welcome.get("message", "Unable to join match"))
        self.user_id = welcome.get("user_id")
        gravity = welcome.get("gravity_ms")
        role = welcome.get("role", "Player")
        self.info_var.set(f"Role {role} {MIDDLE_DOT} Gravity {gravity}ms")
        if self.mode != "WATCH":
            self.status_var.set("Click Ready to start")
            if self.ready_button:
                self.ready_button.state(["!disabled"])

    def _send_ready(self) -> None:
        if self.ready_sent or self.mode == "WATCH":
            return
        try:
            send_message(self.sock, {"type": "READY", "room_id": self.room_id})
            self.ready_sent = True
            self.status_var.set("Ready. Waiting for opponent...")
            if self.ready_button:
                self.ready_button.state(["disabled"])
        except Exception:
            self.info_var.set("Failed to send READY, connection lost?")

    def _network_loop(self) -> None:
        try:
            while True:
                msg = recv_message(self.sock)
                self.queue.put(msg)
                if msg.get("type") == "GAME_OVER":
                    break
        except Exception as exc:
            self.queue.put({"type": "ERROR", "message": str(exc)})

    def _process_queue(self) -> None:
        while not self.queue.empty():
            msg = self.queue.get()
            kind = msg.get("type")
            if kind == "SNAPSHOT":
                self.started = True
                deliver_at = time.time() + self.render_delay
                heapq.heappush(self.snapshot_buffer, (deliver_at, msg))
            elif kind == "GAME_OVER":
                self._apply_due_snapshots(force=True)
                result = msg.get("result", {})
                winner = result.get("winner")
                if winner == self.user_id:
                    self.info_var.set("Match finished: Victory!")
                elif winner is None:
                    self.info_var.set("Match finished: Draw")
                else:
                    self.info_var.set("Match finished: Defeat")
                self._show_results(result)
            elif kind == "READY_STATE":
                players = msg.get("players", [])
                ready = sum(1 for p in players if p.get("ready"))
                names = ", ".join(p.get("username", "") for p in players if p.get("username"))
                self.info_var.set(f"Ready {ready}/{self.expected_players} {MIDDLE_DOT} {names or 'Waiting...'}")
                for p in players:
                    pid = p.get("user_id")
                    if pid and p.get("username"):
                        self.player_names[pid] = p["username"]
            elif kind == "ERROR":
                self.info_var.set(f"Error: {msg.get('message')}")
        self._apply_due_snapshots()
        self.after(50, self._process_queue)

    def _apply_due_snapshots(self, *, force: bool = False) -> None:
        now = time.time()
        while self.snapshot_buffer and (force or self.snapshot_buffer[0][0] <= now):
            _, snap = heapq.heappop(self.snapshot_buffer)
            self._render_snapshot(snap)

    # ------------------------------- Rendering ------------------------------
    def _render_snapshot(self, snap: Dict) -> None:
        self.remaining = snap.get("remaining")
        players = snap.get("players", [])
        my_state = next((p for p in players if p.get("user_id") == self.user_id), None)
        opp_state = next((p for p in players if p is not my_state), None)
        if self.mode == "WATCH":
            left_state = players[0] if players else None
            right_state = players[1] if len(players) > 1 else None
        else:
            left_state = my_state
            right_state = opp_state

        def update_slot(idx: int, state: Optional[Dict], canvas: tk.Canvas, cell: int) -> None:
            if not state:
                self._draw_next(self.next_canvases[idx], None)
                return
            name = state.get("username") or f"Player {idx + 1}"
            if self.mode != "WATCH" and state.get("user_id") == self.user_id:
                name = f"{name} (You)"
            self.name_vars[idx].set(name)
            self._draw_board(canvas, state, cell)
            self.stat_vars[idx].set(f"Score {state.get('score')} | Lines {state.get('lines')}")
            next_piece = (state.get("next") or [None])[0]
            self._draw_next(self.next_canvases[idx], next_piece)

        update_slot(0, left_state, self.my_canvas, self.left_cell)
        update_slot(1, right_state, self.opp_canvas, self.right_cell)

        if self.mode == "WATCH" and left_state and right_state:
            self.title(f"{left_state.get('username', 'Player 1')} vs {right_state.get('username', 'Player 2')}")

        self.status_var.set("")

        for state in players:
            uid = state.get("user_id")
            if uid:
                self.player_names[uid] = state.get("username", uid)

    def _draw_board(self, canvas: tk.Canvas, state: Dict, cell: int) -> None:
        canvas.delete("all")
        grid = self._compose_grid(state)
        for y, row in enumerate(grid):
            for x, val in enumerate(row):
                if val == ".":
                    continue
                color = CELL_COLORS.get(val, "#666666")
                x0, y0 = x * cell, y * cell
                canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill=color, outline="#151515")

    def _draw_next(self, canvas: tk.Canvas, piece: Optional[str]) -> None:
        canvas.delete("all")
        if not piece or piece not in TETROMINO_SHAPES:
            return
        cell = 18
        offsets = TETROMINO_SHAPES[piece][0]
        min_x = min(x for x, _ in offsets)
        min_y = min(y for _, y in offsets)
        for dx, dy in offsets:
            x0 = (dx - min_x) * cell
            y0 = (dy - min_y) * cell
            canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill=CELL_COLORS.get(piece, "#aaa"), outline="#151515")

    def _compose_grid(self, state: Dict) -> List[List[str]]:
        board_rows = state.get("board", [])
        grid: List[List[str]] = [[ "." for _ in range(BOARD_WIDTH)] for _ in range(BOARD_HEIGHT)]
        for y, row in enumerate(board_rows):
            for x, val in enumerate(row):
                if y < BOARD_HEIGHT and x < BOARD_WIDTH:
                    grid[y][x] = val if val != "." else "."
        active = state.get("active")
        if active:
            for x, y in self._active_cells(active):
                if 0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT:
                    grid[y][x] = active.get("kind", ".")
        return grid

    def _active_cells(self, active: Dict) -> List[tuple]:
        kind = active.get("kind", "I")
        rotation = active.get("rotation", 0) % 4
        shape = TETROMINO_SHAPES.get(kind, TETROMINO_SHAPES["I"])[rotation]
        base_x = active.get("x", 0)
        base_y = active.get("y", 0)
        return [(base_x + dx, base_y + dy) for dx, dy in shape]

    # ------------------------------- Input ----------------------------------
    def _on_key(self, event: tk.Event) -> None:  # type: ignore[override]
        if self.mode == "WATCH":
            return
        action = KEY_BINDINGS.get(event.keysym)
        if not action:
            return
        payload = {"type": "INPUT", "action": action, "ts": time.time()}
        try:
            with self.lock:
                send_message(self.sock, payload)
        except Exception:
            self.info_var.set("Failed to send input. Connection lost?")

    def _on_close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass
        self.destroy()

    # ------------------------------- Result ---------------------------------
    def _show_results(self, result: Dict) -> None:
        if self.result_window and tk.Toplevel.winfo_exists(self.result_window):
            return
        win_text = "Draw"
        winner = result.get("winner")
        if winner:
            winner_name = result.get("winner_name") or self.player_names.get(winner, winner)
            if winner == self.user_id:
                win_text = "Victory!"
            else:
                win_text = f"{winner_name} wins"

        window = tk.Toplevel(self)
        window.title("Match Summary")
        window.resizable(False, False)
        ttk.Label(window, text=win_text, font=SECTION_FONT).pack(padx=16, pady=(16, 8))
        tree = ttk.Treeview(
            window,
            columns=("player", "score", "lines", "status"),
            show="headings",
            height=4,
        )
        headings = {
            "player": "Player",
            "score": "Score",
            "lines": "Lines",
            "status": "Status",
        }
        for key, text in headings.items():
            tree.heading(key, text=text)
            tree.column(key, anchor="center", width=120)
        tree.pack(padx=16, pady=8)
        players = result.get("players", {})
        for pid, stats in players.items():
            name = stats.get("username") or self.player_names.get(pid, pid)
            winner_id = result.get("winner")
            if winner_id is None:
                status = "Draw"
            elif pid == winner_id:
                status = "Win"
            else:
                status = "Loss"
            tree.insert(
                "",
                "end",
                values=(name, stats.get("score", 0), stats.get("lines", 0), status),
            )
        ttk.Button(window, text="Close", command=window.destroy).pack(pady=(0, 16))
        self.result_window = window


def resolve_host_port(url: str) -> Tuple[str, int]:
    if not url:
        return "127.0.0.1", 13000
    normalized = url
    if "://" not in normalized:
        normalized = f"tcp://{normalized}"
    parsed = urlparse(normalized)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    return host, port


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Platform-friendly Tetris client.")
    parser.add_argument("--player", default="Player", help="玩家名稱（平台傳入）")
    parser.add_argument("--server", default="", help="平台伺服器 URL（未使用，僅保留介面）")
    parser.add_argument("--game-server", dest="game_server", default="", help="遊戲伺服器 URL")
    parser.add_argument("--room", required=True, help="房間 ID")
    parser.add_argument("--watch", action="store_true", help="觀戰模式")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    host, port = resolve_host_port(args.game_server)
    mode = "WATCH" if args.watch else "PLAY"
    try:
        app = TetrisClient(host, port, args.player, args.room, mode=mode)
        app.mainloop()
    except Exception as exc:
        messagebox.showerror("Error", f"Cannot start Tetris client: {exc}")


if __name__ == "__main__":
    main()
