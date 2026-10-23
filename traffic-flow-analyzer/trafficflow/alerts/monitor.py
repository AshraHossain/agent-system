"""Threshold-based alerting with optional webhook notifications.

Monitors live congestion levels and fires HTTP POST webhooks when a segment
crosses a configured severity threshold.

Quick start::

    from trafficflow.alerts.monitor import AlertMonitor, AlertRule, AlertConfig
    from trafficflow.contracts import CongestionLevel
    from trafficflow.data.live import MockLiveFeed

    feed = MockLiveFeed()
    rules = [
        AlertRule(segment_id="S1", min_level=CongestionLevel.HEAVY,
                  webhook_url="https://hooks.example.com/traffic"),
    ]
    monitor = AlertMonitor(AlertConfig(rules=rules, feed=feed))
    monitor.run(poll_interval_seconds=30)  # blocks; Ctrl-C to stop
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from trafficflow.contracts import CongestionLevel
from trafficflow.core.congestion import congestion_index, level_for
from trafficflow.data.feed import Feed

if TYPE_CHECKING:
    from trafficflow.alerts.store import AlertStore

logger = logging.getLogger(__name__)

_LEVEL_ORDER = {
    CongestionLevel.FREE: 0,
    CongestionLevel.MODERATE: 1,
    CongestionLevel.HEAVY: 2,
    CongestionLevel.SEVERE: 3,
}


@dataclass(frozen=True)
class AlertRule:
    """Fire when *segment_id* reaches or exceeds *min_level*."""

    segment_id: str
    min_level: CongestionLevel
    webhook_url: str = ""
    name: str = ""


@dataclass
class AlertConfig:
    rules: list[AlertRule]
    feed: Feed
    poll_interval_seconds: int = 30


@dataclass
class AlertEvent:
    ts: datetime
    segment_id: str
    level: CongestionLevel
    speed_kmh: float
    rule: AlertRule


@dataclass
class AlertMonitor:
    config: AlertConfig
    store: AlertStore | None = None
    _active: dict[str, CongestionLevel] = field(default_factory=dict, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self) -> list[AlertEvent]:
        """Evaluate all rules against the current feed state.

        Returns a list of AlertEvent for every rule whose threshold is met
        (including rules that were already firing — callers deduplicate as
        needed).  Fires webhooks for any newly triggered or cleared events.
        """
        feed = self.config.feed
        segs = {s.id: s for s in feed.segments()}
        events: list[AlertEvent] = []

        for rule in self.config.rules:
            seg = segs.get(rule.segment_id)
            if seg is None:
                continue
            try:
                hist = feed.history(rule.segment_id, days=0.02)  # ~30-min window
                if hist.empty:
                    continue
                speed = float(hist.iloc[-1])
            except Exception:
                logger.debug("Could not read history for %s", rule.segment_id)
                continue

            idx = congestion_index(speed, seg.free_flow_kmh)
            current_level = level_for(idx)
            prev_level = self._active.get(rule.segment_id)

            threshold_met = _LEVEL_ORDER[current_level] >= _LEVEL_ORDER[rule.min_level]
            was_active = (
                prev_level is not None and _LEVEL_ORDER[prev_level] >= _LEVEL_ORDER[rule.min_level]
            )

            if threshold_met:
                self._active[rule.segment_id] = current_level
                ev = AlertEvent(
                    ts=datetime.now(),
                    segment_id=rule.segment_id,
                    level=current_level,
                    speed_kmh=round(speed, 2),
                    rule=rule,
                )
                events.append(ev)
                if self.store is not None:
                    self.store.record(
                        ev.ts,
                        ev.segment_id,
                        ev.level,
                        ev.speed_kmh,
                        rule_name=rule.name or rule.segment_id,
                    )
                if not was_active and rule.webhook_url:
                    self._fire(ev)
            elif was_active:
                # Cleared — remove from active set
                self._active.pop(rule.segment_id, None)

        return events

    def run(self, poll_interval_seconds: int | None = None) -> None:
        """Block and poll until stop() is called or KeyboardInterrupt."""
        interval = poll_interval_seconds or self.config.poll_interval_seconds
        self._stop.clear()
        logger.info(
            "AlertMonitor started (interval=%ds, rules=%d)", interval, len(self.config.rules)
        )
        try:
            while not self._stop.wait(interval):
                try:
                    evs = self.check()
                    for ev in evs:
                        logger.info(
                            "ALERT %s %s speed=%.1f km/h",
                            ev.segment_id,
                            ev.level.value,
                            ev.speed_kmh,
                        )
                except Exception:
                    logger.exception("check() failed")
        except KeyboardInterrupt:
            pass
        logger.info("AlertMonitor stopped")

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fire(self, event: AlertEvent) -> None:
        """POST a JSON payload to the rule's webhook URL (best-effort)."""
        if not event.rule.webhook_url:
            return
        payload = {
            "ts": event.ts.isoformat(),
            "segment_id": event.segment_id,
            "level": event.level.value,
            "speed_kmh": event.speed_kmh,
            "rule_name": event.rule.name or event.rule.segment_id,
        }
        try:
            import httpx

            httpx.post(event.rule.webhook_url, json=payload, timeout=5)
            logger.debug("Webhook fired for %s -> %s", event.segment_id, event.rule.webhook_url)
        except Exception as exc:
            logger.warning("Webhook POST failed: %s", exc)

    @staticmethod
    def payload_json(event: AlertEvent) -> str:
        return json.dumps(
            {
                "ts": event.ts.isoformat(),
                "segment_id": event.segment_id,
                "level": event.level.value,
                "speed_kmh": event.speed_kmh,
            }
        )
