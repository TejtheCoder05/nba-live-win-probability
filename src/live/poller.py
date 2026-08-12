"""Subscription-aware one-poller-per-game lifecycle management."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class PollerRecord:
    stop_event: threading.Event = field(default_factory=threading.Event)
    subscribers: set[str] = field(default_factory=set)
    task: Any | None = None


class PollerRegistry:
    """Reference-count background work so viewers share exactly one loop."""

    def __init__(
        self,
        start_background_task: Callable[..., Any],
        target: Callable[[str, threading.Event], None],
        *,
        enabled: bool = True,
    ) -> None:
        self._start_background_task = start_background_task
        self._target = target
        self._enabled = enabled
        self._records: dict[str, PollerRecord] = {}
        self._starts: dict[str, int] = {}
        self._lock = threading.RLock()

    def subscribe(self, game_id: str, subscriber_id: str) -> bool:
        """Return True only when this subscription created a new poller."""
        with self._lock:
            record = self._records.get(game_id)
            created = record is None
            if record is None:
                record = PollerRecord()
                self._records[game_id] = record
                self._starts[game_id] = self._starts.get(game_id, 0) + 1
            record.subscribers.add(subscriber_id)
            if created and self._enabled:
                record.task = self._start_background_task(self._target, game_id, record.stop_event)
                logger.info("Started game poller game_id=%s", game_id)
            return created

    def unsubscribe(self, game_id: str, subscriber_id: str) -> bool:
        """Return True when the final subscriber stopped the poller."""
        with self._lock:
            record = self._records.get(game_id)
            if record is None:
                return False
            record.subscribers.discard(subscriber_id)
            if record.subscribers:
                return False
            record.stop_event.set()
            del self._records[game_id]
            logger.info("Stopped game poller game_id=%s", game_id)
            return True

    def disconnect(self, subscriber_id: str) -> tuple[str, ...]:
        with self._lock:
            games = tuple(game_id for game_id, record in self._records.items() if subscriber_id in record.subscribers)
        for game_id in games:
            self.unsubscribe(game_id, subscriber_id)
        return games

    def stop_all(self) -> None:
        with self._lock:
            records = list(self._records.values())
            self._records.clear()
        for record in records:
            record.stop_event.set()

    def subscriber_count(self, game_id: str) -> int:
        with self._lock:
            record = self._records.get(game_id)
            return 0 if record is None else len(record.subscribers)

    def start_count(self, game_id: str) -> int:
        with self._lock:
            return self._starts.get(game_id, 0)

    def active_count(self) -> int:
        with self._lock:
            return len(self._records)
