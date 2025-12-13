from __future__ import annotations

import hashlib
import json
import os
import queue
import random
import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    db_host: str = "0.0.0.0"
    db_port: int = 12080
    lobby_host: str = "0.0.0.0"
    lobby_port: int = 12180
    public_host: str = "linux1.cs.nycu.edu.tw"
    game_port_min: int = 13000
    game_port_max: int = 14000
    snapshot_interval_ms: int = 150
    gravity_ms: int = 450
    db_path: str = "db_state.json"

try: 
    import config as _user_config 

    DEFAULT_CONFIG = ServerConfig(
        db_host=_user_config.DEFAULT_CONFIG.get("db_host", "0.0.0.0"),
        db_port=_user_config.DEFAULT_CONFIG.get("db_port", 12080),
        lobby_host=_user_config.DEFAULT_CONFIG.get("lobby_host", "0.0.0.0"),
        lobby_port=_user_config.DEFAULT_CONFIG.get("lobby_port", 12180),
        public_host=_user_config.DEFAULT_CONFIG.get("public_host"),
        game_port_min=_user_config.DEFAULT_CONFIG.get("game_port_min", 13000),
        game_port_max=_user_config.DEFAULT_CONFIG.get("game_port_max", 14000),
        snapshot_interval_ms=_user_config.DEFAULT_CONFIG.get("snapshot_interval_ms", 100),
        gravity_ms=_user_config.DEFAULT_CONFIG.get("gravity_ms", 800),
    )
except Exception:
    DEFAULT_CONFIG = ServerConfig()

class DatabaseServer:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.data_lock = threading.Lock()
        self.collections = {"User": {}, "Room": {}, "GameLog": {}}
        self.db_path = config.db_path
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.db_path):
            with open(self.db_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            for key in self.collections:
                if key in payload and isinstance(payload[key], dict):
                    self.collections[key] = payload[key]

    def _flush(self) -> None:
        tmp_path = self.db_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.collections, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.db_path)

    def start(self) -> None:
        thread = threading.Thread(target=self._serve_forever, daemon=True)
        thread.start()

    def _serve_forever(self) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.config.db_host, self.config.db_port))
        server.listen()
        print(f"[DB] listening on {self.config.db_host}:{self.config.db_port}")
        while True:
            conn, addr = server.accept()
            threading.Thread(target=self._handle_client, args=(conn, addr), daemon=True).start()

    def _handle_client(self, conn: socket.socket, addr: Tuple[str, int]) -> None:
        try:
            while True:
                try:
                    request = recv_message(conn)
                except ConnectionError:
                    break
                except Exception as exc:
                    send_message(conn, {"ok": False, "error": str(exc)})
                    continue
                response = self._execute(request)
                send_message(conn, response)
        finally:
            conn.close()

    def _execute(self, req: Dict) -> Dict:
        coll = req.get("collection")
        action = req.get("action")
        data = req.get("data", {})
        if coll not in self.collections:
            return {"ok": False, "error": "unknown collection"}
        handler = getattr(self, f"_handle_{coll.lower()}_{action}", None)
        if handler:
            return handler(data)
        generic = getattr(self, f"_generic_{action}", None)
        if generic:
            return generic(coll, data)
        return {"ok": False, "error": "unsupported action"}

    # ---- Collection specific handlers -------------------------------------------------

    def _handle_user_create(self, data: Dict) -> Dict:
        username = data.get("username")
        email = data.get("email")
        password = data.get("password")
        if not username or not password:
            return {"ok": False, "error": "missing username/password"}
        import hashlib

        with self.data_lock:
            for user in self.collections["User"].values():
                if user.get("username") == username:
                    return {"ok": False, "error": "username taken"}
                if email and email == user.get("email"):
                    return {"ok": False, "error": "email in use"}
            user_id = data.get("id", str(uuid.uuid4()))
            hashed = hashlib.sha256(password.encode("utf-8")).hexdigest()
            doc = {
                "id": user_id,
                "username": username,
                "email": email,
                "password": hashed,
                "created_at": time.time(),
                "last_login_at": None,
                "online": False,
                "current_room": None,
                "invitations": [],
            }
            self.collections["User"][user_id] = doc
            self._flush()
        return {"ok": True, "data": doc}

    def _handle_room_create(self, data: Dict) -> Dict:
        name = data.get("name") or "Room"
        host_user_id = data.get("host_user_id")
        if not host_user_id:
            return {"ok": False, "error": "host required"}
        visibility = data.get("visibility", "public")
        room_id = data.get("id", str(uuid.uuid4()))
        doc = {
            "id": room_id,
            "name": name,
            "host_user_id": host_user_id,
            "visibility": visibility,
            "status": "idle",
            "members": [host_user_id],
            "invited": [],
            "created_at": time.time(),
            "game": None,
        }
        with self.data_lock:
            self.collections["Room"][room_id] = doc
            self._flush()
        return {"ok": True, "data": doc}

    def _generic_create(self, coll: str, data: Dict) -> Dict:
        with self.data_lock:
            doc_id = data.get("id", str(uuid.uuid4()))
            data = {**data, "id": doc_id}
            self.collections[coll][doc_id] = data
            self._flush()
        return {"ok": True, "data": data}

    def _generic_read(self, coll: str, data: Dict) -> Dict:
        doc_id = data.get("id")
        if not doc_id:
            return {"ok": False, "error": "missing id"}
        doc = self.collections[coll].get(doc_id)
        if not doc:
            return {"ok": False, "error": "not found"}
        return {"ok": True, "data": doc}

    def _generic_query(self, coll: str, data: Dict) -> Dict:
        filtr = data.get("filter", {})
        limit = data.get("limit")
        results = []
        for doc in self.collections[coll].values():
            keep = True
            for key, value in filtr.items():
                if doc.get(key) != value:
                    keep = False
                    break
            if keep:
                results.append(doc)
                if limit and len(results) >= limit:
                    break
        return {"ok": True, "data": results}

    def _generic_update(self, coll: str, data: Dict) -> Dict:
        doc_id = data.get("id")
        updates = data.get("update", {})
        if not doc_id:
            return {"ok": False, "error": "missing id"}
        with self.data_lock:
            doc = self.collections[coll].get(doc_id)
            if not doc:
                return {"ok": False, "error": "not found"}
            doc.update(updates)
            self._flush()
        return {"ok": True, "data": doc}

    def _generic_delete(self, coll: str, data: Dict) -> Dict:
        doc_id = data.get("id")
        if not doc_id:
            return {"ok": False, "error": "missing id"}
        with self.data_lock:
            existed = self.collections[coll].pop(doc_id, None)
            if existed is None:
                return {"ok": False, "error": "not found"}
            self._flush()
        return {"ok": True}


class DatabaseClient:
    def __init__(self, config: ServerConfig):
        self.config = config

    def request(self, payload: Dict) -> Dict:
        with socket.create_connection((self.config.db_host, self.config.db_port), timeout=5) as sock:
            send_message(sock, payload)
            return recv_message(sock)


@dataclass
class ClientSession:
    conn: socket.socket
    addr: Tuple[str, int]
    server: "LobbyServer"
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    user_id: Optional[str] = None
    username: Optional[str] = None
    room_id: Optional[str] = None
    active: bool = True

    def safe_send(self, payload: Dict) -> None:
        if not self.active:
            return
        try:
            with self.send_lock:
                send_message(self.conn, payload)
        except Exception:
            self.active = False

    def close(self) -> None:
        if not self.active:
            return
        self.active = False
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        finally:
            self.conn.close()


class LobbyServer:
    """Central lobby coordinating rooms, invites, and game launches."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self.db = DatabaseClient(config)
        self.sessions: List[ClientSession] = []
        self.user_sessions: Dict[str, ClientSession] = {}
        self.sessions_lock = threading.Lock()
        self.listener: Optional[socket.socket] = None
        self.accept_thread: Optional[threading.Thread] = None
        self.running = threading.Event()
        self.running.set()
        self.active_games: Dict[str, "GameServer"] = {}
        self.game_lock = threading.Lock()
        self.name_cache: Dict[str, str] = {}
        self._cold_boot_cleanup()

    def start(self) -> None:
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((self.config.lobby_host, self.config.lobby_port))
        self.listener.listen()
        print(f"[Lobby] listening on {self.config.lobby_host}:{self.config.lobby_port}")
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.accept_thread.start()

    def _accept_loop(self) -> None:
        assert self.listener is not None
        while self.running.is_set():
            try:
                conn, addr = self.listener.accept()
            except OSError:
                break
            session = ClientSession(conn=conn, addr=addr, server=self)
            with self.sessions_lock:
                self.sessions.append(session)
            threading.Thread(target=self._handle_client, args=(session,), daemon=True).start()

    def _handle_client(self, session: ClientSession) -> None:
        session.safe_send({"type": "HELLO", "message": "Connected to HW2 Lobby"})
        try:
            while True:
                msg = recv_message(session.conn)
                response = self._process_message(session, msg)
                if response is not None:
                    session.safe_send(response)
        except ConnectionError:
            pass
        except Exception as exc:
            session.safe_send({"type": "ERROR", "message": str(exc)})
        finally:
            self._logout_session(session)
            session.close()
            with self.sessions_lock:
                if session in self.sessions:
                    self.sessions.remove(session)

    def _process_message(self, session: ClientSession, msg: Dict) -> Optional[Dict]:
        msg_type = msg.get("type")
        handler = getattr(self, f"_cmd_{msg_type.lower()}", None)
        if not handler:
            return {"type": "ERROR", "message": f"unknown command {msg_type}"}
        return handler(session, msg)

    def _require_login(self, session: ClientSession) -> Optional[Dict]:
        if not session.user_id:
            return {"type": "ERROR", "message": "login required"}
        return None

    def _db(self, collection: str, action: str, data: Dict) -> Dict:
        payload = {"collection": collection, "action": action, "data": data}
        resp = self.db.request(payload)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "db error"))
        return resp["data"]

    def _db_optional(self, collection: str, action: str, data: Dict) -> Optional[Dict]:
        payload = {"collection": collection, "action": action, "data": data}
        resp = self.db.request(payload)
        if not resp.get("ok"):
            return None
        return resp.get("data")

    def _db_query(self, collection: str, filtr: Dict, limit: Optional[int] = None) -> List[Dict]:
        payload = {
            "collection": collection,
            "action": "query",
            "data": {"filter": filtr, **({"limit": limit} if limit else {})},
        }
        resp = self.db.request(payload)
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "db error"))
        return resp.get("data", [])

    def _user_name(self, user_id: Optional[str]) -> str:
        if not user_id:
            return ""
        cached = self.name_cache.get(user_id)
        if cached:
            return cached
        user = self._db_optional("User", "read", {"id": user_id})
        if user:
            name = user.get("username", user_id)
            self.name_cache[user_id] = name
            return name
        return user_id

    def _decorate_room(self, room: Optional[Dict]) -> Optional[Dict]:
        if not room:
            return None
        room = dict(room)
        room["member_names"] = [self._user_name(uid) for uid in room.get("members", [])]
        room["host_name"] = self._user_name(room.get("host_user_id"))
        return room

    def _clear_invites_for_room(self, room_id: str) -> None:
        users = self._db_query("User", {})
        for user in users:
            invites = user.get("invitations", [])
            filtered = [inv for inv in invites if inv.get("room_id") != room_id]
            if len(filtered) != len(invites):
                self.db.request(
                    {
                        "collection": "User",
                        "action": "update",
                        "data": {"id": user["id"], "update": {"invitations": filtered}},
                    }
                )

    def _disband_room(self, room: Dict, *, reason: Optional[str] = None, skip_notify: Optional[str] = None) -> None:
        room_id = room.get("id")
        if not room_id:
            return
        members = list(room.get("members", []))
        try:
            self.db.request({"collection": "Room", "action": "delete", "data": {"id": room_id}})
        except Exception:
            pass
        self._clear_invites_for_room(room_id)
        for uid in members:
            try:
                self.db.request(
                    {
                        "collection": "User",
                        "action": "update",
                        "data": {"id": uid, "update": {"current_room": None}},
                    }
                )
            except Exception:
                pass
            with self.sessions_lock:
                peer = self.user_sessions.get(uid)
                if peer:
                    peer.room_id = None
        payload = {"type": "ROOM_CLOSED", "room_id": room_id}
        if reason:
            payload["reason"] = reason
        for uid in members:
            if skip_notify and uid == skip_notify:
                continue
            self.push_to_user(uid, payload)

    def _remove_host_rooms(self, host_user_id: str, *, reason: str) -> None:
        try:
            rooms = self._db_query("Room", {"host_user_id": host_user_id})
        except Exception:
            rooms = []
        for room in rooms:
            self._disband_room(room, reason=reason, skip_notify=host_user_id)

    def _remove_member_from_room(self, user_id: str, *, reason: str) -> None:
        user_doc = self._db_optional("User", "read", {"id": user_id})
        room_id = user_doc.get("current_room") if user_doc else None
        if not room_id:
            return
        room = self._fetch_room(room_id)
        if not room:
            return
        if room.get("host_user_id") == user_id:
            self._disband_room(room, reason=reason, skip_notify=user_id)
            return
        members = [uid for uid in room.get("members", []) if uid != user_id]
        try:
            self.db.request(
                {
                    "collection": "Room",
                    "action": "update",
                    "data": {"id": room_id, "update": {"members": members}},
                }
            )
        except Exception:
            pass
        try:
            self.db.request(
                {
                    "collection": "User",
                    "action": "update",
                    "data": {"id": user_id, "update": {"current_room": None}},
                }
            )
        except Exception:
            pass

    def _cold_boot_cleanup(self) -> None:
        try:
            rooms = self._db_query("Room", {})
            for room in rooms:
                self._clear_invites_for_room(room["id"])
                self.db.request({"collection": "Room", "action": "delete", "data": {"id": room["id"]}})
            logs = self._db_query("GameLog", {"status": "playing"})
            for log in logs:
                self.db.request(
                    {
                        "collection": "GameLog",
                        "action": "update",
                        "data": {
                            "id": log["id"],
                            "update": {
                                "status": "aborted",
                                "end_at": time.time(),
                                "result": {"reason": "server_restart"},
                            },
                        },
                    }
                )
            users = self._db_query("User", {})
            for user in users:
                self.db.request(
                    {
                        "collection": "User",
                        "action": "update",
                        "data": {
                            "id": user["id"],
                            "update": {"online": False, "current_room": None},
                        },
                    }
                )
        except Exception as exc:
            print("[Lobby] cold boot cleanup warning:", exc)

    def _logout_session(self, session: ClientSession) -> None:
        if not session.user_id:
            return
        user_id = session.user_id
        self._remove_host_rooms(user_id, reason="host_logout")
        self._remove_member_from_room(user_id, reason="player_logout")
        try:
            self.db.request(
                {
                    "collection": "User",
                    "action": "update",
                    "data": {"id": user_id, "update": {"online": False, "current_room": None}},
                }
            )
        except Exception:
            pass
        with self.sessions_lock:
            if user_id in self.user_sessions and self.user_sessions[user_id] is session:
                del self.user_sessions[user_id]
        session.user_id = None
        session.room_id = None

    def push_to_user(self, user_id: str, payload: Dict) -> None:
        with self.sessions_lock:
            session = self.user_sessions.get(user_id)
        if session:
            session.safe_send(payload)

    def _cmd_register(self, session: ClientSession, msg: Dict) -> Dict:
        username = msg.get("username")
        password = msg.get("password")
        email = msg.get("email")
        if not username or not password:
            return {"type": "ERROR", "message": "username/password required"}
        try:
            data = self._db(
                "User",
                "create",
                {"username": username, "password": password, "email": email},
            )
        except RuntimeError as exc:
            return {"type": "ERROR", "message": str(exc)}
        data = {k: v for k, v in data.items() if k not in {"password"}}
        return {"type": "REGISTERED", "user": data}

    def _cmd_login(self, session: ClientSession, msg: Dict) -> Dict:
        username = msg.get("username")
        password = msg.get("password")
        if not username or not password:
            return {"type": "ERROR", "message": "username/password required"}
        users = self._db_query("User", {"username": username}, limit=1)
        if not users:
            return {"type": "ERROR", "message": "user not found"}
        user = users[0]
        hashed = hashlib.sha256(password.encode("utf-8")).hexdigest()
        if user.get("password") != hashed:
            return {"type": "ERROR", "message": "invalid password"}
        if user.get("online"):
            return {"type": "ERROR", "message": "user already online"}
        self.db.request(
            {
                "collection": "User",
                "action": "update",
                "data": {"id": user["id"], "update": {"online": True, "last_login_at": time.time()}},
            }
        )
        session.user_id = user["id"]
        session.username = user.get("username")
        with self.sessions_lock:
            self.user_sessions[user["id"]] = session
        return {
            "type": "LOGGED_IN",
            "user": {k: v for k, v in user.items() if k not in {"password"}},
        }

    def _cmd_logout(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        self._logout_session(session)
        return {"type": "LOGGED_OUT"}

    def _cmd_list_users(self, session: ClientSession, msg: Dict) -> Dict:
        users = self._db_query("User", {"online": True})
        sanitized = [
            {"id": u["id"], "username": u.get("username"), "current_room": u.get("current_room")}
            for u in users
        ]
        return {"type": "ONLINE_USERS", "users": sanitized}

    def _cmd_list_rooms(self, session: ClientSession, msg: Dict) -> Dict:
        rooms = self._db_query("Room", {})
        enriched = []
        for room in rooms:
            room = self._decorate_room(room)
            if room:
                display = dict(room)
                display.pop("game", None)
                enriched.append(display)
        return {"type": "ROOMS", "rooms": enriched}

    def _cmd_create_room(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        if session.room_id:
            return {"type": "ERROR", "message": "Leave current room before creating a new one"}
        existing_by_host = self._db_query("Room", {"host_user_id": session.user_id}, limit=1)
        if existing_by_host:
            return {"type": "ERROR", "message": "You already host a room"}
        name = msg.get("name") or f"{session.username}'s Room"
        duplicates = self._db_query("Room", {"name": name}, limit=1)
        if duplicates:
            return {"type": "ERROR", "message": "Room name already taken"}
        visibility = msg.get("visibility", "public")
        try:
            room = self._db(
                "Room",
                "create",
                {"name": name, "visibility": visibility, "host_user_id": session.user_id},
            )
        except RuntimeError as exc:
            return {"type": "ERROR", "message": str(exc)}
        self.db.request(
            {
                "collection": "User",
                "action": "update",
                "data": {"id": session.user_id, "update": {"current_room": room["id"]}},
            }
        )
        session.room_id = room["id"]
        return {"type": "ROOM_CREATED", "room": self._decorate_room(room)}

    def _fetch_room(self, room_id: str) -> Optional[Dict]:
        return self._db_optional("Room", "read", {"id": room_id})

    def _cmd_join_room(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        room_id = msg.get("room_id")
        if not room_id:
            return {"type": "ERROR", "message": "room_id required"}
        if session.room_id and session.room_id != room_id:
            return {"type": "ERROR", "message": "leave current room first"}
        room = self._fetch_room(room_id)
        if not room:
            return {"type": "ERROR", "message": "room not found"}
        if session.user_id in room.get("members", []):
            session.room_id = room_id
            return {"type": "JOINED", "room": self._decorate_room(room)}
        if len(room.get("members", [])) >= 2:
            return {"type": "ERROR", "message": "room full"}
        if room.get("visibility") == "private" and session.user_id not in room.get("invited", []):
            return {"type": "ERROR", "message": "invitation required"}
        room["members"].append(session.user_id)
        update = {
            "members": room["members"],
            "invited": [uid for uid in room.get("invited", []) if uid != session.user_id],
        }
        self.db.request(
            {
                "collection": "Room",
                "action": "update",
                "data": {"id": room_id, "update": update},
            }
        )
        self.db.request(
            {
                "collection": "User",
                "action": "update",
                "data": {"id": session.user_id, "update": {"current_room": room_id}},
            }
        )
        session.room_id = room_id
        return {"type": "JOINED", "room": self._decorate_room(room)}

    def _cmd_leave_room(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        if not session.room_id:
            return {"type": "ERROR", "message": "not in a room"}
        room = self._fetch_room(session.room_id)
        if room:
            if room.get("status") == "playing":
                return {"type": "ERROR", "message": "cannot leave during a match"}
            room_id = room["id"]
            remaining = [uid for uid in room.get("members", []) if uid != session.user_id]
            if room.get("host_user_id") == session.user_id:
                # host leaves: destroy room and reset everyone
                self._disband_room(room, reason="host_left", skip_notify=session.user_id)
            else:
                update = {"members": remaining}
                self.db.request({"collection": "Room", "action": "update", "data": {"id": room_id, "update": update}})
        self.db.request(
            {
                "collection": "User",
                "action": "update",
                "data": {"id": session.user_id, "update": {"current_room": None}},
            }
        )
        session.room_id = None
        return {"type": "LEFT_ROOM"}

    def _cmd_invite(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        target_name = msg.get("username")
        if not target_name:
            return {"type": "ERROR", "message": "username required"}
        room = self._fetch_room(session.room_id) if session.room_id else None
        if not room:
            return {"type": "ERROR", "message": "create or join a room first"}
        if room.get("host_user_id") != session.user_id:
            return {"type": "ERROR", "message": "only host can invite"}
        target_users = self._db_query("User", {"username": target_name}, limit=1)
        if not target_users:
            return {"type": "ERROR", "message": "user not found"}
        target = target_users[0]
        invites = set(room.get("invited", []))
        invites.add(target["id"])
        self.db.request(
            {
                "collection": "Room",
                "action": "update",
                "data": {"id": room["id"], "update": {"invited": list(invites)}},
            }
        )
        user_invites = list(target.get("invitations", []))
        if room["id"] not in [inv.get("room_id") for inv in user_invites if isinstance(inv, dict)]:
            user_invites.append(
                {
                    "room_id": room["id"],
                    "room_name": room.get("name"),
                    "from_id": session.user_id,
                    "from_name": session.username,
                }
            )
        self.db.request(
            {
                "collection": "User",
                "action": "update",
                "data": {"id": target["id"], "update": {"invitations": user_invites}},
            }
        )
        self.push_to_user(
            target["id"],
            {
                "type": "INVITED",
                "room_id": room["id"],
                "room_name": room.get("name"),
                "from": session.username,
            },
        )
        return {"type": "INVITE_SENT"}

    def _cmd_list_invites(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        user = self._db_optional("User", "read", {"id": session.user_id})
        if not user:
            return {"type": "ERROR", "message": "user not found"}
        invites = user.get("invitations", []) if user else []
        return {"type": "INVITES", "invitations": invites}

    def _cmd_accept_invite(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        room_id = msg.get("room_id")
        if not room_id:
            return {"type": "ERROR", "message": "room_id required"}
        user = self._db_optional("User", "read", {"id": session.user_id})
        invites = [inv for inv in user.get("invitations", []) if inv.get("room_id") != room_id]
        self.db.request(
            {
                "collection": "User",
                "action": "update",
                "data": {"id": session.user_id, "update": {"invitations": invites}},
            }
        )
        return self._cmd_join_room(session, {"room_id": room_id})

    def _cmd_start_game(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        if not session.room_id:
            user_doc = self._db_optional("User", "read", {"id": session.user_id})
            if user_doc and user_doc.get("current_room"):
                session.room_id = user_doc["current_room"]
        if not session.room_id:
            return {"type": "ERROR", "message": "join a room first"}
        room = self._fetch_room(session.room_id)
        if not room:
            return {"type": "ERROR", "message": "room missing"}
        if room.get("host_user_id") != session.user_id:
            return {"type": "ERROR", "message": "only host can start"}
        if len(room.get("members", [])) != 2:
            return {"type": "ERROR", "message": "need exactly 2 players"}
        try:
            details = self._launch_game(room)
        except Exception as exc:
            print("[Lobby] failed to launch game:", exc)
            return {"type": "ERROR", "message": f"unable to start: {exc}"}
        return {"type": "GAME_STARTING", "game": details}

    def _cmd_watch_room(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        room_id = msg.get("room_id")
        if not room_id:
            return {"type": "ERROR", "message": "room_id required"}
        room = self._fetch_room(room_id)
        if not room:
            return {"type": "ERROR", "message": "room not found"}
        if session.user_id in room.get("members", []):
            return {"type": "ERROR", "message": "cannot watch your own match"}
        if room.get("status") != "playing" or not room.get("game"):
            return {"type": "ERROR", "message": "room not currently playing"}
        game_meta = room["game"]
        ticket = {
            "room_id": room_id,
            "host": game_meta.get("host") or self.config.public_host or self.config.lobby_host or "127.0.0.1",
            "port": game_meta["port"],
            "token": game_meta["token"],
            "mode": "WATCH",
            "user_id": session.user_id,
            "username": session.username,
            "bag_rule": game_meta.get("bag_rule", BAG_RULE),
            "seed": game_meta.get("seed"),
        }
        return {"type": "WATCH_READY", "ticket": ticket}

    def _cmd_my_status(self, session: ClientSession, msg: Dict) -> Dict:
        err = self._require_login(session)
        if err:
            return err
        user = self._db_optional("User", "read", {"id": session.user_id})
        room = self._fetch_room(user.get("current_room")) if user and user.get("current_room") else None
        room = self._decorate_room(room)
        if room:
            room.pop("game", None)
        return {"type": "STATUS", "user": user, "room": room}

    def _pick_port(self) -> int:
        busy = {server.port for server in self.active_games.values()}
        for _ in range(1000):
            port = random.randint(self.config.game_port_min, self.config.game_port_max)
            if port not in busy:
                return port
        raise RuntimeError("no free port available")

    def _launch_game(self, room: Dict) -> Dict:
        port = self._pick_port()
        token = uuid.uuid4().hex
        seed = random.randint(0, 2**31 - 1)
        room_id = room["id"]
        members = room.get("members", [])
        players: List[Dict[str, Any]] = []
        for idx, user_id in enumerate(members):
            user = self._db_optional("User", "read", {"id": user_id})
            if not user:
                raise RuntimeError("player missing")
            players.append({
                "user_id": user_id,
                "username": user.get("username"),
                "role": f"P{idx + 1}",
            })
        log_doc = self._db(
            "GameLog",
            "create",
            {
                "room_id": room_id,
                "players": [p["user_id"] for p in players],
                "start_at": time.time(),
                "status": "playing",
            },
        )
        try:
            game = GameServer(
                config=self.config,
                room_id=room_id,
                port=port,
                token=token,
                seed=seed,
                players=players,
                game_log_id=log_doc["id"],
                on_finished=self._on_game_finished,
            )
        except OSError as exc:
            self.db.request({"collection": "GameLog", "action": "delete", "data": {"id": log_doc["id"]}})
            raise RuntimeError(f"Port {port} unavailable: {exc}") from exc
        with self.game_lock:
            self.active_games[room_id] = game
        game.start()
        self.db.request(
            {
                "collection": "Room",
                "action": "update",
                "data": {
                    "id": room_id,
                    "update": {
                        "status": "playing",
                        "game": {
                            "port": port,
                            "token": token,
                            "seed": seed,
                            "bag_rule": BAG_RULE,
                            "started_at": time.time(),
                            "host": game.build_ticket(members[0])["host"],
                        },
                    },
                },
            }
        )
        tickets = {p["user_id"]: game.build_ticket(p["user_id"]) for p in players}
        for user_id, ticket in tickets.items():
            self.push_to_user(user_id, {"type": "GAME_READY", "ticket": ticket})
        return {"port": port, "token": token, "seed": seed, "bag_rule": BAG_RULE}

    def _on_game_finished(self, summary: Dict) -> None:
        room_id = summary.get("room_id")
        with self.game_lock:
            if room_id in self.active_games:
                del self.active_games[room_id]
        game_log_id = summary.get("game_log_id")
        if game_log_id:
            self.db.request(
                {
                    "collection": "GameLog",
                    "action": "update",
                    "data": {
                        "id": game_log_id,
                        "update": {
                            "status": "finished",
                            "end_at": time.time(),
                            "result": summary.get("result"),
                        },
                    },
                }
            )

        result = summary.get("result", {})
        if result:
            winner = result.get("winner")
            if winner:
                result["winner_name"] = self._user_name(winner)
            players_stats = result.get("players", {})
            for pid, stats in players_stats.items():
                stats["username"] = self._user_name(pid)

        if room_id:
            try:
                self.db.request(
                    {
                        "collection": "Room",
                        "action": "update",
                        "data": {"id": room_id, "update": {"status": "idle", "game": None}},
                    }
                )
            except Exception:
                pass
        for user_id in summary.get("players", []):
            self.push_to_user(user_id, {"type": "GAME_FINISHED", "result": summary.get("result")})



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

    def queue_piece(self) -> str:
        piece = self.bag.next_piece()
        self.next_queue.append(piece)
        return piece


class GameServer:
    def __init__(
        self,
        config: ServerConfig,
        room_id: str,
        port: int,
        token: str,
        seed: int,
        players: List[Dict[str, Any]],
        game_log_id: str,
        on_finished: Callable[[Dict], None],
    ) -> None:
        self.config = config
        self.room_id = room_id
        self.port = port
        self.token = token
        self.seed = seed
        self.game_log_id = game_log_id
        self.on_finished = on_finished
        self.snapshot_interval = config.snapshot_interval_ms / 1000.0
        self.gravity_interval = config.gravity_ms / 1000.0
        self.players: Dict[str, PlayerState] = {}
        for idx, meta in enumerate(players):
            bag = BagGenerator(seed)
            state = PlayerState(
                user_id=meta["user_id"],
                username=meta.get("username", f"P{idx+1}"),
                role=meta.get("role", f"P{idx+1}"),
                bag=bag,
            )
            while len(state.next_queue) < 5:
                state.next_queue.append(bag.next_piece())
            self.players[state.user_id] = state
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", self.port))
        self.listener.listen()
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.loop_thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.match_started = threading.Event()
        self.start_time: Optional[float] = None
        self.result: Optional[Dict] = None
        self.watchers: List[GameClientConnection] = []
        self.watch_lock = threading.Lock()
        self.tick = 0

    def start(self) -> None:
        print(f"[Game] room {self.room_id} listening on port {self.port}")
        self.accept_thread.start()

    def build_ticket(self, user_id: str) -> Dict:
        host = self.config.public_host or self.config.lobby_host
        if not host or host == "0.0.0.0":
            host = "127.0.0.1"
        return {
            "room_id": self.room_id,
            "user_id": user_id,
            "username": self.players[user_id].username,
            "host": host,
            "port": self.port,
            "token": self.token,
            "seed": self.seed,
            "bag_rule": BAG_RULE,
            "role": self.players[user_id].role,
        }

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
            if hello.get("type") != "HELLO":
                connection.send({"type": "ERROR", "message": "expecting HELLO"})
                return
            if hello.get("token") != self.token:
                connection.send({"type": "ERROR", "message": "invalid token"})
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
                return
            user_id = hello.get("user_id")
            if user_id not in self.players:
                connection.send({"type": "ERROR", "message": "not part of this match"})
                return
            state = self.players[user_id]
            if state.connection is not None:
                connection.send({"type": "ERROR", "message": "already connected"})
                return
            connection.user_id = user_id
            state.connection = connection
            state.inputs = queue.Queue()
            connection.send(
                {
                    "type": "WELCOME",
                    "role": state.role,
                    "board": [BOARD_WIDTH, BOARD_HEIGHT],
                    "seed": self.seed,
                    "bag_rule": BAG_RULE,
                    "gravity_ms": self.config.gravity_ms,
                }
            )
            threading.Thread(target=self._input_listener, args=(state,), daemon=True).start()
            if all(p.connection for p in self.players.values()) and not self.match_started.is_set():
                self.match_started.set()
                self.loop_thread = threading.Thread(target=self._game_loop, daemon=True)
                self.loop_thread.start()
        except Exception as exc:
            try:
                connection.send({"type": "ERROR", "message": str(exc)})
            except Exception:
                pass
            connection.close()

    def _input_listener(self, state: PlayerState) -> None:
        assert state.connection is not None
        conn = state.connection.sock
        try:
            while not self.stop_event.is_set() and state.alive:
                msg = recv_message(conn)
                if msg.get("type") == "INPUT":
                    action = msg.get("action")
                    if action:
                        state.inputs.put(action)
        except ConnectionError:
            state.alive = False
            state.disconnect_reason = "disconnect"
        finally:
            state.connection = None
            try:
                conn.close()
            except Exception:
                pass

    # ------------------------------ game loop -------------------------------
    def _game_loop(self) -> None:
        self.start_time = time.time()
        for state in self.players.values():
            if state.alive:
                self._spawn_piece(state)
        next_snapshot = time.time()
        while not self.stop_event.is_set():
            now = time.time()
            for state in self.players.values():
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

    def _ensure_queue(self, state: PlayerState) -> None:
        while len(state.next_queue) < 5:
            state.next_queue.append(state.bag.next_piece())

    def _spawn_piece(self, state: PlayerState) -> bool:
        self._ensure_queue(state)
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
            "game_log_id": self.game_log_id,
            "result": {
                "winner": winner,
                "reason": reason,
                "players": {
                    pid: {
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
        if self.on_finished and self.result:
            try:
                self.on_finished(self.result)
            except Exception as exc:
                print("[Game] failed to report result", exc)
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


def run_servers(config: Optional[ServerConfig] = None) -> None:
    cfg = config or DEFAULT_CONFIG
    db = DatabaseServer(cfg)
    db.start()
    lobby = LobbyServer(cfg)
    lobby.start()
    print("HW2 DB + Lobby servers are running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")


if __name__ == "__main__":
    run_servers()
