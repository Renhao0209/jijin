import time
from typing import Any, Dict, Optional


class TTLCache:
    def __init__(self) -> None:
        self._data: Dict[str, Any] = {}
        self._exp: Dict[str, float] = {}

    def get(self, key: str) -> Optional[Any]:
        exp = self._exp.get(key)
        if exp is None:
            return None
        if exp < time.time():
            self._data.pop(key, None)
            self._exp.pop(key, None)
            return None
        return self._data.get(key)

    def set(self, key: str, value: Any, ttl: int) -> None:
        self._data[key] = value
        self._exp[key] = time.time() + ttl

    def clear(self) -> None:
        self._data.clear()
        self._exp.clear()
