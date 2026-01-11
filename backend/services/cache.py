"""
Simple in-memory cache service.
"""

import os
from time import time
from threading import Lock
from typing import Dict, Any, Optional

# Cache TTL from environment
_CACHE_TTL_SECONDS = int(os.getenv("FPL_CACHE_TTL_SECONDS", "300"))


class CacheService:
    """Simple in-memory cache with TTL."""
    
    def __init__(self, ttl_seconds: int = None):
        self.ttl = ttl_seconds or _CACHE_TTL_SECONDS
        self._lock = Lock()
        self._cache: Dict[str, Dict[Any, Any]] = {}
    
    def get(self, namespace: str, key: Any) -> Optional[Any]:
        """Get cached value if not expired."""
        with self._lock:
            item = self._cache.get(namespace, {}).get(key)
            if not item:
                return None
            ts, data = item
            if time() - ts > self.ttl:
                self._cache[namespace].pop(key, None)
                return None
            return data
    
    def set(self, namespace: str, key: Any, data: Any) -> None:
        """Set cached value with current timestamp."""
        with self._lock:
            self._cache.setdefault(namespace, {})[key] = (time(), data)
    
    def clear(self, namespace: str = None) -> None:
        """Clear cache for namespace or all."""
        with self._lock:
            if namespace:
                self._cache.pop(namespace, None)
            else:
                self._cache.clear()


# Global cache instance
cache = CacheService()

