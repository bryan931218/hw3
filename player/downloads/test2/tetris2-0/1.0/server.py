from __future__ import annotations

import argparse
import json
import queue
import random
import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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


@dataclass
class ServerConfig:
    snapshot_interval_ms: int = 150
    gravity_ms: int = 450


try:
    import config as _user_config

    DEFAULT_CONFIG = ServerConfig(
        snapshot_interval_ms=_user_config.DEFAULT_CONFIG.get("snapshot_interval_ms", 150),
        gravity_ms=_user_config.DEFAULT_CONFIG.get("gravity_ms", 450),
    )
except Exception:
    DEFAULT_CONFIG = ServerConfig()


# ---------------------------- Game server primitives ---------------------------------
BOARD_WIDTH = 10
BOARD_HEIGHT = 20
MATCH_DURATION: Optional[int] = None

TETROMINO_SHAPES: Dict[str, List[List[Tuple[int, int]]]] = {
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

PIECE_ORDER = list(TETROMINO_SHAPES.keys())
PIECE_IDS = {name: idx + 1 for idx, name in enumerate(PIECE_ORDER)}
BAG_RULE = "7bag"
PIECE_NAMES = {v: k for k, v in PIECE_IDS.items()}


@dataclass
class PieceState:
    kind: str
    rotation: int
    x: int
    y: int


@dataclass
class GameClientConnection:
    sock: socket.socket
    addr: Tuple[str, int]
    mode: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    user_id: Optional[str] = None

    def send(self, payload: Dict) -> None:
        with self.lock:
            send_message(self.sock, payload)

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        self.sock.close()


class BagGenerator:
    def __init__(self, seed: int):
        self.random = random.Random(seed)
        self.bag: List[str] = []

    def next_piece(self) -> str:
        if not self.bag:
            self._refill()
        return self.bag.pop()

    def _refill(self) -> None:
        bag = PIECE_ORDER.copy()
        for idx in range(len(bag) - 1, 0, -1):
            swap_idx = self.random.randint(0, idx)
            bag[idx], bag[swap_idx] = bag[swap_idx], bag[idx]
        self.bag = bag


@dataclass
class PlayerState:
    user_id: str
    username: str
    role: str
    bag: BagGenerator
    ready: bool = False
    board: List[List[int]] = field(default_factory=lambda: [[0 for _ in range(BOARD_WIDTH)] for _ in range(BOARD_HEIGHT)])
    next_queue: List[str] = field(default_factory=list)
    hold: Optional[str] = None
    can_hold: bool = True
    active: Optional[PieceState] = None
    last_drop: float = field(default_factory=time.time)
    alive: bool = True
    score: int = 0
    lines: int = 0
    combo: int = 0
    inputs: "queue.Queue[Dict]" = field(default_factory=queue.Queue)
    connection: Optional[GameClientConnection] = None
    disconnect_reason: Optional[str] = None

    def ensure_queue(self) -> None:
        while len(self.next_queue) < 5:
            self.next_queue.append(self.bag.next_piece())


class TetrisRoomServer:
    def __init__(
        self,
        room_id: str,
        port: int,
        config: ServerConfig,
        *,
        max_players: int = 2,
        seed: Optional[int] = None,
    ) -> None:
        self.room_id = room_id
        self.port = port
        self.config = config
        self.max_players = max_players
        self.seed = seed or random.randint(1, 1_000_000_000)
        self.snapshot_interval = config.snapshot_interval_ms / 1000.0
        self.gravity_interval = config.gravity_ms / 1000.0
        self.players: Dict[str, PlayerState] = {}
        self.watchers: List[GameClientConnection] = []
        self.watch_lock = threading.Lock()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", self.port))
        self.listener.listen()
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.loop_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.match_started = threading.Event()
        self.finished = threading.Event()
        self.state_lock = threading.Lock()
        self.start_time: Optional[float] = None
        self.result: Optional[Dict] = None
        self.tick = 0

    def start(self) -> None:
        print(f"[Tetris] room {self.room_id} listening on port {self.port} (seed {self.seed})")
        self.accept_thread.start()

    def wait(self) -> None:
        self.finished.wait()

    # ------------------------------ networking ------------------------------
    def _accept_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                conn, addr = self.listener.accept()
            except OSError:
                break
            threading.Thread(target=self._handle_connection, args=(conn, addr), daemon=True).start()

    def _handle_connection(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        connection = GameClientConnection(sock=conn, addr=addr, mode="PLAY")
        try:
            hello = recv_message(conn)
        except Exception as exc:
            try:
                connection.send({"type": "ERROR", "message": str(exc)})
            except Exception:
                pass
            connection.close()
            return
        if hello.get("type") != "HELLO":
            connection.send({"type": "ERROR", "message": "expecting HELLO"})
            connection.close()
            return
        if hello.get("room_id") != self.room_id:
            connection.send({"type": "ERROR", "message": "room mismatch"})
            connection.close()
            return
        mode = hello.get("mode", "PLAY")
        if mode == "WATCH":
            connection.mode = "WATCH"
            connection.send(
                {
                    "type": "WELCOME",
                    "role": "SPECTATOR",
                    "board": [BOARD_WIDTH, BOARD_HEIGHT],
                    "seed": self.seed,
                    "bag_rule": BAG_RULE,
                    "gravity_ms": self.config.gravity_ms,
                }
            )
            with self.watch_lock:
                self.watchers.append(connection)
            self._notify_ready_state()
            return

        username = hello.get("player") or hello.get("username") or "Player"
        requested_id = hello.get("user_id")
        with self.state_lock:
            user_id = requested_id or self._make_user_id(username)
            if user_id not in self.players and len(self.players) >= self.max_players:
                connection.send({"type": "ERROR", "message": "room full"})
                connection.close()
                return
            state = self.players.get(user_id)
            if state is None:
                role = self._assign_role()
                state = PlayerState(user_id=user_id, username=username, role=role, bag=BagGenerator(self.seed))
                state.ensure_queue()
                self.players[user_id] = state
            else:
                state.username = username or state.username
            if state.connection:
                connection.send({"type": "ERROR", "message": "already connected"})
                connection.close()
                return
            state.connection = connection
            state.ready = False
            state.alive = True
            state.disconnect_reason = None
            state.inputs = queue.Queue()
            connection.user_id = user_id

        connection.send(
            {
                "type": "WELCOME",
                "role": state.role,
                "user_id": state.user_id,
                "board": [BOARD_WIDTH, BOARD_HEIGHT],
                "seed": self.seed,
                "bag_rule": BAG_RULE,
                "gravity_ms": self.config.gravity_ms,
            }
        )
        self._notify_ready_state()
        threading.Thread(target=self._player_listener, args=(state,), daemon=True).start()

    def _player_listener(self, state: PlayerState) -> None:
        assert state.connection is not None
        conn = state.connection.sock
        try:
            while not self.stop_event.is_set():
                msg = recv_message(conn)
                kind = msg.get("type")
                if kind == "READY":
                    state.ready = True
                    self._notify_ready_state()
                    self._try_start()
                elif kind == "INPUT":
                    action = msg.get("action")
                    if action:
                        state.inputs.put(action)
        except ConnectionError:
            pass
        except Exception as exc:
            try:
                state.connection.send({"type": "ERROR", "message": str(exc)})
            except Exception:
                pass
        finally:
            with self.state_lock:
                state.connection = None
                if self.match_started.is_set():
                    state.alive = False
                    state.disconnect_reason = state.disconnect_reason or "disconnect"
                else:
                    state.ready = False
            self._notify_ready_state()

    def _assign_role(self) -> str:
        roles = {p.role for p in self.players.values()}
        for idx in range(1, self.max_players + 1):
            candidate = f"P{idx}"
            if candidate not in roles:
                return candidate
        return f"P{len(self.players) + 1}"

    def _make_user_id(self, username: str) -> str:
        return f"{self.room_id}-{username}-{uuid.uuid4().hex[:8]}"

    # ------------------------------ game loop -------------------------------
    def _try_start(self) -> None:
        with self.state_lock:
            if self.match_started.is_set() or self.stop_event.is_set():
                return
            active_players = [p for p in self.players.values() if p.connection]
            if len(active_players) < self.max_players:
                return
            if not all(p.ready for p in active_players):
                return
            self.match_started.set()
            self.start_time = time.time()
            for state in active_players:
                state.board = [[0 for _ in range(BOARD_WIDTH)] for _ in range(BOARD_HEIGHT)]
                state.hold = None
                state.active = None
                state.can_hold = True
                state.score = 0
                state.lines = 0
                state.alive = True
                state.disconnect_reason = None
                state.bag = BagGenerator(self.seed)
                state.next_queue = []
                state.ensure_queue()
                self._spawn_piece(state)
        self.loop_thread = threading.Thread(target=self._game_loop, daemon=True)
        self.loop_thread.start()

    def _game_loop(self) -> None:
        next_snapshot = time.time()
        while not self.stop_event.is_set():
            now = time.time()
            for state in list(self.players.values()):
                if not state.alive:
                    continue
                self._process_inputs(state)
                if now - state.last_drop >= self.gravity_interval:
                    moved = self._move(state, 0, 1)
                    if not moved:
                        self._lock_piece(state)
                    state.last_drop = now
                if state.active is None and state.alive:
                    self._spawn_piece(state)
            if now >= next_snapshot:
                self._broadcast_snapshot()
                next_snapshot = now + self.snapshot_interval
            if self._should_end(now):
                break
            time.sleep(0.02)
        self.stop_event.set()
        self._broadcast_final()
        self._finalize()

    def _process_inputs(self, state: PlayerState) -> None:
        while not state.inputs.empty():
            try:
                action = state.inputs.get_nowait()
            except queue.Empty:
                break
            if action == "LEFT":
                self._move(state, -1, 0)
            elif action == "RIGHT":
                self._move(state, 1, 0)
            elif action == "SOFT_DROP":
                if self._move(state, 0, 1):
                    state.score += 1
            elif action == "HARD_DROP":
                dist = 0
                while self._move(state, 0, 1):
                    dist += 1
                state.score += dist * 2
                self._lock_piece(state)
            elif action == "CW":
                self._rotate(state, 1)
            elif action == "CCW":
                self._rotate(state, -1)
            elif action == "HOLD":
                self._hold(state)

    def _spawn_piece(self, state: PlayerState) -> bool:
        state.ensure_queue()
        kind = state.next_queue.pop(0)
        piece = PieceState(kind=kind, rotation=0, x=3, y=0)
        if not self._valid(state.board, piece):
            state.alive = False
            state.disconnect_reason = state.disconnect_reason or "topped_out"
            state.active = None
            return False
        state.active = piece
        state.can_hold = True
        state.last_drop = time.time()
        return True

    def _valid(self, board: List[List[int]], piece: PieceState) -> bool:
        for (x, y) in self._cells(piece):
            if x < 0 or x >= BOARD_WIDTH:
                return False
            if y >= BOARD_HEIGHT:
                return False
            if y >= 0 and board[y][x]:
                return False
        return True

    def _cells(self, piece: PieceState) -> List[Tuple[int, int]]:
        shape = TETROMINO_SHAPES[piece.kind][piece.rotation % len(TETROMINO_SHAPES[piece.kind])]
        return [(piece.x + dx, piece.y + dy) for dx, dy in shape]

    def _move(self, state: PlayerState, dx: int, dy: int) -> bool:
        if not state.active:
            return False
        piece = PieceState(kind=state.active.kind, rotation=state.active.rotation, x=state.active.x + dx, y=state.active.y + dy)
        if self._valid(state.board, piece):
            state.active = piece
            return True
        return False

    def _rotate(self, state: PlayerState, direction: int) -> bool:
        if not state.active:
            return False
        new_rot = (state.active.rotation + direction) % 4
        for shift in [0, -1, 1, -2, 2]:
            piece = PieceState(kind=state.active.kind, rotation=new_rot, x=state.active.x + shift, y=state.active.y)
            if self._valid(state.board, piece):
                state.active = piece
                return True
        return False

    def _hold(self, state: PlayerState) -> None:
        if not state.active or not state.can_hold:
            return
        current_kind = state.active.kind
        if state.hold:
            swap = state.hold
            state.hold = current_kind
            state.active = PieceState(kind=swap, rotation=0, x=3, y=0)
            if not self._valid(state.board, state.active):
                state.alive = False
                state.disconnect_reason = "topped_out"
        else:
            state.hold = current_kind
            state.active = None
            self._spawn_piece(state)
        state.can_hold = False
        state.last_drop = time.time()

    def _lock_piece(self, state: PlayerState) -> None:
        if not state.active:
            return
        for x, y in self._cells(state.active):
            if 0 <= y < BOARD_HEIGHT:
                state.board[y][x] = PIECE_IDS[state.active.kind]
            else:
                state.alive = False
                if state.disconnect_reason is None:
                    state.disconnect_reason = "topped_out"
        state.active = None
        self._clear_lines(state)
        state.can_hold = True

    def _clear_lines(self, state: PlayerState) -> None:
        cleared = 0
        new_rows = []
        for row in state.board:
            if all(row):
                cleared += 1
            else:
                new_rows.append(row)
        while len(new_rows) < BOARD_HEIGHT:
            new_rows.insert(0, [0 for _ in range(BOARD_WIDTH)])
        state.board = new_rows
        state.lines += cleared
        if cleared == 1:
            state.score += 100
        elif cleared == 2:
            state.score += 300
        elif cleared == 3:
            state.score += 500
        elif cleared == 4:
            state.score += 800

    def _should_end(self, now: float) -> bool:
        alive = [p for p in self.players.values() if p.alive]
        if not alive:
            self.result = self._result_payload(None, "double_ko")
            return True
        if len(alive) == 1:
            self.result = self._result_payload(alive[0].user_id, "knockout")
            return True
        if MATCH_DURATION and self.start_time and now - self.start_time >= MATCH_DURATION:
            ranked = sorted(self.players.values(), key=lambda p: (p.lines, p.score), reverse=True)
            winner = ranked[0].user_id if ranked else None
            self.result = self._result_payload(winner, "timeout")
            return True
        return False

    def _result_payload(self, winner: Optional[str], reason: str) -> Dict:
        return {
            "room_id": self.room_id,
            "result": {
                "winner": winner,
                "reason": reason,
                "players": {
                    pid: {
                        "username": state.username,
                        "score": state.score,
                        "lines": state.lines,
                        "alive": state.alive,
                        "disconnect": state.disconnect_reason,
                    }
                    for pid, state in self.players.items()
                },
            },
            "players": list(self.players.keys()),
        }

    def _board_strings(self, state: PlayerState) -> List[str]:
        rows = []
        for y in range(BOARD_HEIGHT):
            row = ""
            for x in range(BOARD_WIDTH):
                val = state.board[y][x]
                row += PIECE_NAMES.get(val, ".") if val else "."
            rows.append(row)
        return rows

    def _active_payload(self, state: PlayerState) -> Optional[Dict]:
        if not state.active:
            return None
        return {
            "kind": state.active.kind,
            "x": state.active.x,
            "y": state.active.y,
            "rotation": state.active.rotation,
        }

    def _broadcast_snapshot(self) -> None:
        self.tick += 1
        remaining = None
        if MATCH_DURATION and self.start_time:
            remaining = max(0, MATCH_DURATION - (time.time() - self.start_time))
        payload = {
            "type": "SNAPSHOT",
            "room_id": self.room_id,
            "tick": self.tick,
            "remaining": remaining,
            "players": [
                {
                    "user_id": state.user_id,
                    "username": state.username,
                    "role": state.role,
                    "board": self._board_strings(state),
                    "active": self._active_payload(state),
                    "next": state.next_queue[:5],
                    "hold": state.hold,
                    "score": state.score,
                    "lines": state.lines,
                    "alive": state.alive,
                }
                for state in self.players.values()
            ],
        }
        self._broadcast(payload)

    def _broadcast_final(self) -> None:
        if not self.result:
            self.result = self._result_payload(None, "aborted")
        payload = {"type": "GAME_OVER", "result": self.result["result"]}
        self._broadcast(payload)

    def _notify_ready_state(self) -> None:
        payload = {
            "type": "READY_STATE",
            "room_id": self.room_id,
            "players": [
                {
                    "user_id": state.user_id,
                    "username": state.username,
                    "ready": state.ready,
                    "connected": bool(state.connection),
                }
                for state in self.players.values()
            ],
        }
        self._broadcast(payload)

    def _broadcast(self, payload: Dict) -> None:
        for state in self.players.values():
            conn = state.connection
            if conn:
                try:
                    conn.send(payload)
                except Exception:
                    state.connection = None
        with self.watch_lock:
            alive_watchers = []
            for watcher in self.watchers:
                try:
                    watcher.send(payload)
                    alive_watchers.append(watcher)
                except Exception:
                    watcher.close()
            self.watchers = alive_watchers

    def _finalize(self) -> None:
        for state in self.players.values():
            if state.connection:
                state.connection.close()
                state.connection = None
        with self.watch_lock:
            for watcher in self.watchers:
                watcher.close()
            self.watchers = []
        try:
            self.listener.close()
        except Exception:
            pass
        self.finished.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal Tetris room server (platform-managed).")
    parser.add_argument("--room", required=True, help="Room ID provided by platform")
    parser.add_argument("--port", type=int, required=True, help="Port assigned by platform")
    parser.add_argument("--seed", type=int, default=None, help="Optional seed to make piece order deterministic")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = DEFAULT_CONFIG
    server = TetrisRoomServer(room_id=args.room, port=args.port, config=cfg, seed=args.seed)
    server.start()
    try:
        server.wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
