import json
import os
import threading
from copy import deepcopy
from typing import Any, Callable, Dict


DEFAULT_DATA = {
    "developers": {},
    "players": {},
    "games": {},
    "rooms": {},
    "ratings": {},
    "sessions": {"developer": {}, "player": {}},
    "next_ids": {"room": 1, "rating": 1},
}


class Database:
    """
    Tiny JSON-backed storage with coarse-grained locking.
    Keeps everything in memory and persists on every write so server restarts do not lose data.
    """

    def __init__(self, path: str = "server/data.json"):
        self.path = path
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        if not os.path.exists(self.path):
            self._write(DEFAULT_DATA)
        self.data = self._read()

    def _read(self) -> Dict[str, Any]:
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: Dict[str, Any]) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def snapshot(self) -> Dict[str, Any]:
        with self.lock:
            return deepcopy(self.data)

    def update(self, updater: Callable[[Dict[str, Any]], Any]) -> Any:
        """
        Apply an update function under lock, persist, and return the function's return value.
        The updater should mutate the provided data dict directly.
        """
        with self.lock:
            result = updater(self.data)
            self._write(self.data)
            return result

    def reset(self) -> None:
        with self.lock:
            self.data = deepcopy(DEFAULT_DATA)
            self._write(self.data)
