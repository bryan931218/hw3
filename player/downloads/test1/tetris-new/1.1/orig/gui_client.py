from __future__ import annotations

import heapq
import json
import queue
import socket
import struct
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Dict, List, Optional, Tuple

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


@dataclass
class ServerConfig:
    lobby_host: str = "0.0.0.0"
    lobby_port: int = 12180
    public_host: str = "linux1.cs.nycu.edu.tw"


try: 
    import config as _user_config 
    DEFAULT_CONFIG = ServerConfig(
        lobby_host=_user_config.DEFAULT_CONFIG.get("lobby_host", "0.0.0.0"),
        lobby_port=_user_config.DEFAULT_CONFIG.get("lobby_port", 12180),
        public_host=_user_config.DEFAULT_CONFIG.get("public_host", "linux1.cs.nycu.edu.tw"),
    )
except Exception:
    DEFAULT_CONFIG = ServerConfig()


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
}

FONT_FAMILY = "DejaVu Sans"
BASE_FONT = (FONT_FAMILY, 13)
TITLE_FONT = (FONT_FAMILY, 26, "bold")
SECTION_FONT = (FONT_FAMILY, 16, "bold")
STATUS_FONT = (FONT_FAMILY, 13, "bold")
LOGIN_TITLE_FONT = (FONT_FAMILY, 36, "bold")
LOGIN_LABEL_FONT = (FONT_FAMILY, 24)
LOGIN_BUTTON_FONT = (FONT_FAMILY, 20, "bold")
MONO_FONT = ("JetBrains Mono", 12)
PASSWORD_DOT = "●"
MIDDLE_DOT = "-" 


class LobbyConnection:
    def __init__(self, host: str, port: int, on_push) -> None:
        self.sock = socket.create_connection((host, port))
        self.responses: "queue.Queue[Dict]" = queue.Queue()
        self.on_push = on_push
        self.alive = True
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader.start()

    def _reader_loop(self) -> None:
        try:
            while self.alive:
                msg = recv_message(self.sock)
                if msg.get("type") in {"HELLO", "INVITED", "GAME_READY", "GAME_FINISHED", "ROOM_CLOSED"}:
                    self.on_push(msg)
                else:
                    self.responses.put(msg)
        except Exception as exc:
            self.on_push({"type": "ERROR", "message": f"Connection lost: {exc}"})
            self.alive = False
            self.responses.put({"type": "ERROR", "message": "disconnected"})

    def request(self, payload: Dict) -> Dict:
        if not self.alive:
            raise RuntimeError("Connection closed")
        with self.lock:
            send_message(self.sock, payload)
        return self.responses.get()

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        self.sock.close()


class LoginFrame(ttk.Frame):
    def __init__(self, master: "LobbyGUI") -> None:
        super().__init__(master, padding=60)
        self.master_gui = master
        ttk.Label(self, text="HW2 Online Tetris", font=LOGIN_TITLE_FONT).grid(row=0, column=0, columnspan=2, pady=(0, 30))
        ttk.Label(self, text="Username", font=LOGIN_LABEL_FONT).grid(row=1, column=0, sticky="e", pady=12, padx=(0, 20))
        ttk.Label(self, text="Password", font=LOGIN_LABEL_FONT).grid(row=2, column=0, sticky="e", pady=12, padx=(0, 20))
        entry_opts = {"width": 12, "style": "Login.TEntry", "font": LOGIN_LABEL_FONT}
        self.username = ttk.Entry(self, **entry_opts)
        self.password = ttk.Entry(self, show=PASSWORD_DOT, **entry_opts)
        self.username.grid(row=1, column=1, padx=(0, 20), pady=12, sticky="ew")
        self.password.grid(row=2, column=1, padx=(0, 20), pady=12, sticky="ew")
        self.username.focus_set()
        button_frame = ttk.Frame(self)
        button_frame.grid(row=3, column=0, columnspan=2, pady=30)
        login_btn = ttk.Button(button_frame, text="Sign in", command=self._login, style="Login.TButton")
        register_btn = ttk.Button(button_frame, text="Register", command=self._register, style="Login.TButton")
        login_btn.pack(side="left", padx=12)
        register_btn.pack(side="left", padx=12)
        self.columnconfigure(1, weight=1)

    def _login(self) -> None:
        username = self.username.get().strip()
        password = self.password.get().strip()
        if not username or not password:
            messagebox.showwarning("Warning", "Please enter your username and password.")
            return
        self.master_gui.send_request(
            {"type": "LOGIN", "username": username, "password": password},
            on_success=lambda resp: self.master_gui.on_login(resp["user"]),
        )

    def _register(self) -> None:
        username = self.username.get().strip()
        password = self.password.get().strip()
        if not username or not password:
            messagebox.showwarning("Warning", "Please enter your username and password.")
            return
        self.master_gui.send_request(
            {
                "type": "REGISTER",
                "username": username,
                "password": password,
                "email": None,
            },
            on_success=lambda resp: messagebox.showinfo("Success", "Registration completed, please sign in."),
        )


class DashboardFrame(ttk.Frame):
    def __init__(self, master: "LobbyGUI") -> None:
        super().__init__(master, padding=10)
        self.master_gui = master
        self.invites_data: List[Dict] = []
        self._build_layout()

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=2)
        left = ttk.Frame(self, padding=(0, 8))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(self, padding=(0, 8))
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(1, weight=1)

        # User controls
        ttk.Label(left, text="Player", font=SECTION_FONT).pack(anchor="w")
        self.user_label = ttk.Label(left, text="Not signed in")
        self.user_label.pack(anchor="w", pady=(4, 12))
        ttk.Button(left, text="Refresh", command=self.master_gui.refresh_all).pack(fill="x", pady=4)
        ttk.Button(left, text="Sign out", command=self.master_gui.logout).pack(fill="x", pady=4)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(left, text="Current room", font=SECTION_FONT).pack(anchor="w")
        self.room_info = tk.StringVar(value="Not in a room")
        ttk.Label(left, textvariable=self.room_info, wraplength=260, justify="left").pack(anchor="w", pady=(6, 10))
        self.leave_button = ttk.Button(left, text="Leave room", command=self.master_gui.leave_room, state=tk.DISABLED)
        self.leave_button.pack(fill="x", pady=4)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=10)
        # Room creation
        ttk.Label(left, text="Create room", font=SECTION_FONT).pack(anchor="w")
        self.room_name = ttk.Entry(left)
        self.room_name.pack(fill="x", pady=4)
        self.visibility = ttk.Combobox(left, values=["public", "private"], state="readonly")
        self.visibility.set("public")
        self.visibility.pack(fill="x", pady=4)
        self.create_button = ttk.Button(left, text="Create", command=self.master_gui.create_room)
        self.create_button.pack(fill="x", pady=4)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(left, text="Invite player", font=SECTION_FONT).pack(anchor="w")
        invite_frame = ttk.Frame(left)
        invite_frame.pack(fill="x", pady=4)
        self.invite_username = ttk.Entry(invite_frame)
        self.invite_username.pack(side="left", fill="x", expand=True)
        self.invite_button = ttk.Button(invite_frame, text="Invite", command=self.master_gui.invite_player, state=tk.DISABLED)
        self.invite_button.pack(side="left", padx=6)
        self.start_button = ttk.Button(left, text="Start match", command=self.master_gui.start_game, state=tk.DISABLED)
        self.start_button.pack(fill="x", pady=(8, 0))

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(left, text="Invitations", font=SECTION_FONT).pack(anchor="w")
        self.invite_list = tk.Listbox(left, height=6, font=BASE_FONT)
        self.invite_list.pack(fill="both", expand=True, pady=(4, 0))
        ttk.Button(left, text="Join selected room", command=self.master_gui.accept_invite).pack(fill="x", pady=(8, 0))

        # Room / user panes
        ttk.Label(right, text="Rooms", font=SECTION_FONT).grid(row=0, column=0, sticky="w")
        self.rooms_tree = ttk.Treeview(
            right,
            columns=("name", "host", "status", "visibility", "members"),
            show="headings",
            height=12,
            style="Lobby.Treeview",
        )
        headings = {
            "name": "Name",
            "host": "Host",
            "status": "Status",
            "visibility": "Type",
            "members": "Players",
        }
        for key, text in headings.items():
            self.rooms_tree.heading(key, text=text)
            self.rooms_tree.column(key, width=90 if key != "name" else 160)
        self.rooms_tree.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        right.rowconfigure(1, weight=1)
        room_buttons = ttk.Frame(right)
        room_buttons.grid(row=2, column=0, sticky="ew", pady=10)
        self.join_button = ttk.Button(room_buttons, text="Join room", command=self.master_gui.join_selected_room)
        self.join_button.pack(side="left", padx=(0, 8))
        self.watch_button = ttk.Button(room_buttons, text="Watch room", command=self.master_gui.watch_selected_room)
        self.watch_button.pack(side="left", padx=(0, 8))
        ttk.Button(room_buttons, text="Refresh", command=self.master_gui.refresh_rooms).pack(side="left")
        self.set_host_controls(False)
        self.set_room_presence(False, True)
        self.set_host_controls(False)
        self.set_room_presence(False, True)

        ttk.Separator(right, orient="horizontal").grid(row=3, column=0, sticky="ew", pady=8)
        ttk.Label(right, text="Online players", font=SECTION_FONT).grid(row=4, column=0, sticky="w")
        self.user_list = tk.Listbox(right, height=6, font=BASE_FONT)
        self.user_list.grid(row=5, column=0, sticky="ew", pady=(4, 0))

        ttk.Separator(right, orient="horizontal").grid(row=6, column=0, sticky="ew", pady=8)
        ttk.Label(right, text="System log", font=SECTION_FONT).grid(row=7, column=0, sticky="w")
        self.log_box = tk.Text(right, height=10, state="disabled", bg="#0f0f12", fg="#e0e0e0", font=MONO_FONT, padx=10, pady=10)
        self.log_box.grid(row=8, column=0, sticky="nsew")
        right.rowconfigure(8, weight=1)

    # ------ Dashboard updates --------------------------------------------------
    def set_user(self, username: str) -> None:
        self.user_label.config(text=f"User: {username}")

    def set_room_info(self, text: str) -> None:
        self.room_info.set(text)

    def update_rooms(self, rooms: List[Dict]) -> None:
        selected = self.selected_room_id()
        for iid in self.rooms_tree.get_children():
            self.rooms_tree.delete(iid)
        for room in rooms:
            members_count = len(room.get("members", []))
            host = room.get("host_name") or room.get("host_user_id")
            self.rooms_tree.insert(
                "",
                "end",
                iid=room["id"],
                values=(
                    room.get("name"),
                    host,
                    room.get("status"),
                    room.get("visibility"),
                    f"{members_count}/2",
                ),
            )
        if selected and self.rooms_tree.exists(selected):
            self.rooms_tree.selection_set(selected)

    def update_users(self, users: List[Dict]) -> None:
        self.user_list.delete(0, tk.END)
        for user in users:
            room_name = user.get("current_room_name")
            suffix = f" (room: {room_name})" if room_name else ""
            self.user_list.insert(tk.END, f"{user.get('username')}" + suffix)

    def update_invites(self, invites: List[Dict]) -> None:
        selected_room = self.selected_invite_room()
        self.invites_data = invites
        self.invite_list.delete(0, tk.END)
        for inv in invites:
            label = f"{inv.get('room_name', 'Room')} {inv.get('from_name') or inv.get('from')}"
            self.invite_list.insert(tk.END, label)
        if selected_room:
            for idx, inv in enumerate(self.invites_data):
                if inv.get("room_id") == selected_room:
                    self.invite_list.selection_set(idx)
                    break

    def add_log(self, text: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, f"{time.strftime('%H:%M:%S')} {text}\n")
        self.log_box.see(tk.END)
        self.log_box.configure(state="disabled")

    def selected_room_id(self) -> Optional[str]:
        selection = self.rooms_tree.selection()
        if not selection:
            return None
        return selection[0]

    def selected_invite_room(self) -> Optional[str]:
        selection = self.invite_list.curselection()
        if not selection:
            return None
        idx = selection[0]
        if 0 <= idx < len(self.invites_data):
            return self.invites_data[idx].get("room_id")
        return None

    def set_host_controls(self, is_host: bool) -> None:
        state = tk.NORMAL if is_host else tk.DISABLED
        self.invite_button.config(state=state)
        self.start_button.config(state=state)

    def set_room_presence(self, in_room: bool, can_leave: bool) -> None:
        join_state = tk.DISABLED if in_room else tk.NORMAL
        self.join_button.config(state=join_state)
        self.leave_button.config(state=tk.NORMAL if (in_room and can_leave) else tk.DISABLED)
        self.create_button.config(state=tk.DISABLED if in_room else tk.NORMAL)


class MatchWindow(tk.Toplevel):
    def __init__(self, master: "LobbyGUI", ticket: Dict) -> None:
        super().__init__(master)
        self.title(f"HW2 Match - {ticket.get('username')}")
        self.ticket = ticket
        self.sock = socket.create_connection((ticket["host"], ticket["port"]))
        self.lock = threading.Lock()
        self.queue: "queue.Queue[Dict]" = queue.Queue()
        self.remaining = 0
        self.mode = ticket.get("mode", "PLAY")
        self.user_id = ticket.get("user_id")
        self.player_names: Dict[str, str] = {}
        self.result_window: Optional[tk.Toplevel] = None
        self.render_delay = RENDER_DELAY_MS / 1000.0
        self.snapshot_buffer: List[Tuple[float, Dict[str, Any]]] = []
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.resizable(False, False)
        self._build_ui()
        self._handshake()
        self.network_thread = threading.Thread(target=self._network_loop, daemon=True)
        self.network_thread.start()
        self.after(50, self._process_queue)

    def _build_ui(self) -> None:
        container = ttk.Frame(self, padding=16)
        container.pack()
        left_name = f"{self.ticket.get('username', 'You')} (You)" if self.mode != "WATCH" else "Player 1"
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
        self.info_var = tk.StringVar(value="Waiting for start...")
        self.status_var = tk.StringVar(value="")
        ttk.Label(container, textvariable=self.info_var, font=BASE_FONT).grid(row=2, column=0, columnspan=2, pady=6)
        ttk.Label(container, textvariable=self.status_var, font=SECTION_FONT).grid(row=3, column=0, columnspan=2, pady=(0, 4))
        ttk.Label(
            container,
            text="Shortcuts: ←/→ move, ↓ soft drop, ↑ rotate, Z counter-rotate, Space hard drop",
            font=("Noto Sans", 14),
        ).grid(row=4, column=0, columnspan=2, pady=(4, 0))
        self.bind("<KeyPress>", self._on_key)
        self.focus_force()

    def _handshake(self) -> None:
        payload = {
            "type": "HELLO",
            "room_id": self.ticket["room_id"],
            "token": self.ticket["token"],
            "mode": self.mode,
        }
        if self.user_id:
            payload["user_id"] = self.user_id
        send_message(self.sock, payload)
        welcome = recv_message(self.sock)
        if welcome.get("type") != "WELCOME":
            raise RuntimeError(welcome.get("message", "Unable to join match"))
        self.info_var.set(f"Role {welcome.get('role')} {MIDDLE_DOT} Gravity {welcome.get('gravity_ms')}ms")

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
            elif kind == "ERROR":
                self.info_var.set(f"Error: {msg.get('message')}")
        self._apply_due_snapshots()
        self.after(50, self._process_queue)

    def _apply_due_snapshots(self, *, force: bool = False) -> None:
        now = time.time()
        while self.snapshot_buffer and (force or self.snapshot_buffer[0][0] <= now):
            _, snap = heapq.heappop(self.snapshot_buffer)
            self._render_snapshot(snap)

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
        width = max(x for x, _ in offsets) - min_x + 1
        height = max(y for _, y in offsets) - min_y + 1
        start_x = (4 - width) / 2
        start_y = (4 - height) / 2
        color = CELL_COLORS.get(piece, "#888888")
        for x, y in offsets:
            px = start_x + (x - min_x)
            py = start_y + (y - min_y)
            x0 = px * cell
            y0 = py * cell
            canvas.create_rectangle(x0, y0, x0 + cell, y0 + cell, fill=color, outline="#151515")

    def _compose_grid(self, state: Dict) -> List[List[str]]:
        board = [list(row) for row in state.get("board", [])]
        for row in board:
            if len(row) < BOARD_WIDTH:
                row.extend(["."] * (BOARD_WIDTH - len(row)))
        while len(board) < BOARD_HEIGHT:
            board.insert(0, ["."] * BOARD_WIDTH)
        active = state.get("active")
        if active:
            for x, y in self._active_cells(active):
                if 0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT:
                    board[y][x] = active.get("kind", ".")
        return board

    def _active_cells(self, active: Dict) -> List[tuple]:
        kind = active.get("kind", "I")
        rotation = active.get("rotation", 0) % 4
        shape = TETROMINO_SHAPES.get(kind, TETROMINO_SHAPES["I"])[rotation]
        base_x = active.get("x", 0)
        base_y = active.get("y", 0)
        return [(base_x + dx, base_y + dy) for dx, dy in shape]

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
            winner = result.get("winner")
            if winner is None:
                status = "Draw"
            elif pid == winner:
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


class LobbyGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("HW2 Online Tetris Client")
        self.geometry("1200x970")
        self._setup_theme()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.push_queue: "queue.Queue[Dict]" = queue.Queue()
        self.connection = LobbyConnection(self._resolve_host(), DEFAULT_CONFIG.lobby_port, self._enqueue_push)
        self.status_var = tk.StringVar(value="Connected to lobby")
        self.status_bar = ttk.Label(self, textvariable=self.status_var, anchor="w", style="Status.TLabel", padding=(12, 6))
        self.status_bar.pack(side="bottom", fill="x")
        self.current_frame: Optional[ttk.Frame] = None
        self.dashboard: Optional[DashboardFrame] = None
        self.user: Optional[Dict] = None
        self.user_id: Optional[str] = None
        self.room: Optional[Dict] = None
        self.match_window: Optional[MatchWindow] = None
        self.request_lock = threading.Lock()
        self.auto_refresh_ms = 10000
        self.show_login()
        self.after(100, self._process_push)
        self.after(self.auto_refresh_ms, self._auto_refresh_tick)

    def _setup_theme(self) -> None:
        self.option_add("*Font", BASE_FONT)
        style = ttk.Style(self)
        style.configure("TLabel", font=BASE_FONT)
        style.configure("TButton", font=BASE_FONT)
        style.configure("TEntry", font=BASE_FONT)
        style.configure("TCombobox", font=BASE_FONT)
        style.configure("Treeview", font=BASE_FONT, rowheight=28)
        style.configure("TNotebook", font=BASE_FONT)
        style.configure("Status.TLabel", font=STATUS_FONT)
        style.configure("Login.TButton", font=LOGIN_BUTTON_FONT, padding=(20, 10))
        style.configure("Login.TEntry", font=LOGIN_LABEL_FONT, padding=(10, 20))
        style.configure("Lobby.Treeview", font=BASE_FONT, rowheight=28)
        style.configure("Lobby.Treeview.Heading", font=SECTION_FONT)
        style.configure("Treeview.Heading", font=SECTION_FONT)

    def _resolve_host(self) -> str:
        if getattr(DEFAULT_CONFIG, "public_host", None):
            return DEFAULT_CONFIG.public_host
        if DEFAULT_CONFIG.lobby_host != "0.0.0.0":
            return DEFAULT_CONFIG.lobby_host
        return "127.0.0.1"

    # --------------------------- Frame switching --------------------------------
    def show_login(self) -> None:
        if self.current_frame:
            self.current_frame.destroy()
        frame = LoginFrame(self)
        frame.pack(expand=True, fill="both")
        self.current_frame = frame

    def show_dashboard(self) -> None:
        if self.current_frame:
            self.current_frame.destroy()
        self.dashboard = DashboardFrame(self)
        self.dashboard.pack(fill="both", expand=True)
        self.current_frame = self.dashboard
        if self.user:
            self.dashboard.set_user(self.user.get("username"))
        self.refresh_all()

    # --------------------------- Lobby actions ----------------------------------
    def send_request(
        self,
        payload: Dict,
        on_success=None,
        *,
        refresh_after: bool = False,
        suppress_errors: bool = False,
    ) -> None:
        def worker() -> None:
            try:
                with self.request_lock:
                    resp = self.connection.request(payload)
            except Exception as exc:
                self.after(0, lambda: self._show_error(str(exc)))
                return
            self.after(
                0, lambda: self._dispatch_response(resp, on_success, refresh_after, suppress_errors)
            )

        threading.Thread(target=worker, daemon=True).start()

    def _dispatch_response(self, resp: Dict, on_success, refresh_after: bool, suppress_errors: bool) -> None:
        if resp.get("type") == "ERROR":
            if not suppress_errors:
                self._show_error(resp.get("message", "Unknown error"))
            return
        if on_success:
            on_success(resp)
        if refresh_after and self.user:
            self.refresh_all()

    def on_login(self, user: Dict) -> None:
        self.user = user
        self.user_id = user.get("id")
        self.status_var.set(f"Signed in as {user.get('username')}")
        self.show_dashboard()

    def logout(self) -> None:
        self.send_request({"type": "LOGOUT"}, on_success=lambda _: self._after_logout(), refresh_after=False)

    def _after_logout(self) -> None:
        self.user = None
        self.user_id = None
        self.room = None
        self.status_var.set("Signed out")
        self.show_login()

    def refresh_all(self) -> None:
        if not self.user:
            return
        self.refresh_status()
        self.refresh_rooms()
        self.refresh_users()
        self.refresh_invites()

    def refresh_status(self) -> None:
        self.send_request({"type": "MY_STATUS"}, on_success=self._update_status, refresh_after=False)

    def _update_status(self, resp: Dict) -> None:
        self.user = resp.get("user")
        self.room = resp.get("room")
        if self.dashboard and self.user:
            self.dashboard.set_user(self.user.get("username"))
        if self.dashboard:
            if self.room:
                members = ", ".join(self.room.get("member_names") or [])
                host_name = self.room.get("host_name") or ""
                info = (
                    f"Room: {self.room.get('name')}\n"
                    f"Host: {host_name}\n"
                    f"Status: {self.room.get('status')}\n"
                    f"Members: {members}"
                )
            else:
                info = "Not in a room"
            self.dashboard.set_room_info(info)
            in_room = bool(self.room)
            is_host = in_room and self.user and self.room.get("host_user_id") == self.user.get("id")
            allow_leave = in_room and self.room.get("status") != "playing"
            self.dashboard.set_host_controls(bool(is_host))
            self.dashboard.set_room_presence(in_room, allow_leave)

    def refresh_rooms(self) -> None:
        self.send_request(
            {"type": "LIST_ROOMS"},
            on_success=lambda resp: self.dashboard and self.dashboard.update_rooms(resp.get("rooms", [])),
            refresh_after=False,
        )

    def refresh_users(self) -> None:
        self.send_request(
            {"type": "LIST_USERS"},
            on_success=lambda resp: self.dashboard and self.dashboard.update_users(resp.get("users", [])),
            refresh_after=False,
        )

    def refresh_invites(self) -> None:
        self.send_request(
            {"type": "LIST_INVITES"},
            on_success=lambda resp: self.dashboard and self.dashboard.update_invites(resp.get("invitations", [])),
            refresh_after=False,
        )

    def create_room(self) -> None:
        if not self.dashboard:
            return
        name = self.dashboard.room_name.get().strip() or None
        visibility = self.dashboard.visibility.get()

        def _on_success(resp: Dict) -> None:
            room = resp.get("room") or {}
            self._show_info(f"Room created: {room.get('name', '')}")
            self.dashboard.room_name.delete(0, tk.END)
            self.refresh_all()

        self.send_request(
            {"type": "CREATE_ROOM", "name": name, "visibility": visibility},
            on_success=_on_success,
            refresh_after=False,
        )

    def join_selected_room(self) -> None:
        if not self.dashboard:
            return
        room_id = self.dashboard.selected_room_id()
        if not room_id:
            messagebox.showinfo("Info", "Please select a room first")
            return
        if self.room and self.room.get("id") != room_id:
            messagebox.showinfo("Info", "Please leave your current room before joining another")
            return
        if self.room and self.room.get("id") == room_id:
            messagebox.showinfo("Info", "You are already in this room")
            return
        def _on_success(resp: Dict) -> None:
            room = resp.get("room") or {}
            self._show_info(f"Joined room: {room.get('name', room_id)}")
            self.refresh_all()

        self.send_request({"type": "JOIN_ROOM", "room_id": room_id}, on_success=_on_success, refresh_after=False)

    def watch_selected_room(self) -> None:
        if not self.dashboard:
            return
        room_id = self.dashboard.selected_room_id()
        if not room_id:
            messagebox.showinfo("Info", "Please select a room first")
            return

        if self.room and self.room.get("id") == room_id:
            messagebox.showinfo("Info", "You cannot watch a room you are playing in")
            return

        def _on_success(resp: Dict) -> None:
            ticket = resp.get("ticket")
            if not ticket:
                self._show_error("Unable to spectate this room")
                return
            self._show_info("Opening spectator view...")
            self.launch_match(ticket)

        self.send_request({"type": "WATCH_ROOM", "room_id": room_id}, on_success=_on_success, refresh_after=False)

    def leave_room(self) -> None:
        def _on_success(resp: Dict) -> None:
            self._show_info("Left room")
            self.refresh_all()

        self.send_request({"type": "LEAVE_ROOM"}, on_success=_on_success, refresh_after=False)

    def invite_player(self) -> None:
        if not self.dashboard:
            return
        username = self.dashboard.invite_username.get().strip()
        if not username:
            messagebox.showinfo("Info", "Please enter a username")
            return
        def _on_success(resp: Dict) -> None:
            self._show_info("Invite sent")
            self.refresh_invites()

        self.send_request({"type": "INVITE", "username": username}, on_success=_on_success, refresh_after=False)

    def accept_invite(self) -> None:
        if not self.dashboard:
            return
        room_id = self.dashboard.selected_invite_room()
        if not room_id:
            messagebox.showinfo("Info", "Please select an invitation")
            return
        def _on_success(resp: Dict) -> None:
            self._show_info("Invitation accepted")
            self.refresh_all()

        self.send_request({"type": "ACCEPT_INVITE", "room_id": room_id}, on_success=_on_success, refresh_after=False)

    def start_game(self) -> None:
        def _on_success(resp: Dict) -> None:
            self._show_info("Preparing match")
            self.refresh_all()

        self.send_request({"type": "START_GAME"}, on_success=_on_success, refresh_after=False)

    # --------------------------- Push handling ----------------------------------
    def _enqueue_push(self, msg: Dict) -> None:
        self.push_queue.put(msg)

    def _process_push(self) -> None:
        while not self.push_queue.empty():
            msg = self.push_queue.get()
            kind = msg.get("type")
            if kind == "HELLO" and self.dashboard:
                self.dashboard.add_log(msg.get("message", ""))
            elif kind == "INVITED" and self.dashboard:
                room_name = msg.get("room_name") or msg.get("room_id")
                inviter = msg.get("from")
                self.dashboard.add_log(f"Invitation from {inviter} (room {room_name})")
                self.refresh_invites()
            elif kind == "GAME_READY":
                ticket = msg.get("ticket")
                if ticket:
                    self._show_info("Match ready. Launching window...")
                    self.launch_match(ticket)
                    self.refresh_all()
            elif kind == "ROOM_CLOSED":
                room_name = msg.get("room_name") or msg.get("room_id")
                self._show_info(f"Room {room_name} closed by host")
                self.refresh_all()
            elif kind == "GAME_FINISHED" and self.dashboard:
                result = msg.get("result", {})
                winner = result.get("winner")
                winner_name = result.get("winner_name") or winner
                if winner:
                    self.dashboard.add_log(f"Match finished. Winner: {winner_name}")
                else:
                    self.dashboard.add_log("Match finished. Draw")
                self.refresh_all()
            elif kind == "ERROR":
                self._show_error(msg.get("message", "Unknown error"))
        self.after(150, self._process_push)

    def launch_match(self, ticket: Dict) -> None:
        if self.match_window and tk.Toplevel.winfo_exists(self.match_window):
            self.match_window.destroy()
        try:
            self.match_window = MatchWindow(self, ticket)
        except Exception as exc:
            self._show_error(f"Cannot start match: {exc}")

    # --------------------------- UI helpers ------------------------------------
    def _show_error(self, message: str) -> None:
        messagebox.showerror("Error", message)
        if self.dashboard:
            self.dashboard.add_log(f"[Error] {message}")

    def _show_info(self, message: str) -> None:
        self.status_var.set(message)
        if self.dashboard:
            self.dashboard.add_log(message)

    # --------------------------- Shutdown --------------------------------------
    def _on_close(self) -> None:
        try:
            self.connection.close()
        except Exception:
            pass
        if self.match_window and tk.Toplevel.winfo_exists(self.match_window):
            self.match_window.destroy()
        self.destroy()

    def _auto_refresh_tick(self) -> None:
        if self.user:
            self.refresh_all()
        self.after(self.auto_refresh_ms, self._auto_refresh_tick)


def main() -> None:
    app = LobbyGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
