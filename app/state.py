import json
import os
import threading
from copy import deepcopy
from typing import Any


class BotState:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data = {
            "profiles": {},
            "memories": {},
            "last_reviews": {},
            "meme_cursors": {},
        }
        self._load()

    @staticmethod
    def _pr_key(repo_name: str, pr_number: int) -> str:
        return f"{repo_name}#{pr_number}"

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                self._data["profiles"] = data.get("profiles", {}) or {}
                self._data["memories"] = data.get("memories", {}) or {}
                self._data["last_reviews"] = data.get("last_reviews", {}) or {}
                self._data["meme_cursors"] = data.get("meme_cursors", {}) or {}
        except Exception:
            return

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self._data, handle, indent=2, sort_keys=True)

    def get_profile(self, repo_name: str) -> str:
        with self._lock:
            return str(self._data["profiles"].get(repo_name, "balanced"))

    def set_profile(self, repo_name: str, profile: str) -> None:
        with self._lock:
            self._data["profiles"][repo_name] = profile
            self._save()

    def add_memory(self, repo_name: str, pr_number: int, note: str) -> None:
        key = self._pr_key(repo_name, pr_number)
        with self._lock:
            memories = self._data["memories"].setdefault(key, [])
            memories.append(note)
            self._save()

    def get_memory(self, repo_name: str, pr_number: int) -> list[str]:
        key = self._pr_key(repo_name, pr_number)
        with self._lock:
            return list(self._data["memories"].get(key, []))

    def set_last_review(self, repo_name: str, pr_number: int, payload: dict[str, Any]) -> None:
        key = self._pr_key(repo_name, pr_number)
        with self._lock:
            self._data["last_reviews"][key] = payload
            self._save()

    def get_last_review(self, repo_name: str, pr_number: int) -> dict[str, Any] | None:
        key = self._pr_key(repo_name, pr_number)
        with self._lock:
            payload = self._data["last_reviews"].get(key)
            if payload is None:
                return None
            return deepcopy(payload)

    def get_meme_mode(self, repo_name: str) -> bool:
        # Meme mode is now globally enabled by design.
        return True

    def set_meme_mode(self, repo_name: str, enabled: bool) -> None:
        # Backward-compatible no-op. Meme mode is always on.
        return

    def next_meme_index(self, repo_name: str, bucket: str, pool_size: int) -> int:
        if pool_size <= 1:
            return 0
        key = f"{repo_name}:{bucket}"
        with self._lock:
            raw_previous = self._data["meme_cursors"].get(key, -1)
            previous = int(raw_previous) if isinstance(raw_previous, int) else -1
            index = (previous + 1) % pool_size
            self._data["meme_cursors"][key] = index
            self._save()
            return index
