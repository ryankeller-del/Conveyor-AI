from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class MemoryPacket:
    packet_id: str
    agent_name: str
    task_family: str
    content: str
    signature: str
    created_at: str
    last_used_at: str = ""
    reused_count: int = 0
    status: str = "ACTIVE"
    source_notes: List[str] = field(default_factory=list)
    last_outcome: str = ""
    stale_reason: str = ""


@dataclass
class MemoryRecall:
    agent_name: str
    task_family: str
    packet_id: str
    content: str
    reused: bool
    refreshed: bool
    documented: bool
    note: str = ""


@dataclass
class MemoryEvent:
    timestamp: str
    event_type: str
    agent_name: str
    task_family: str
    packet_id: str
    reused: bool
    refreshed: bool
    invalidated: bool
    outcome: str
    note: str
    sources: List[str] = field(default_factory=list)


@dataclass
class LocalCallLease:
    agent_name: str
    task_family: str
    acquired_at: float
    waited_seconds: float
    throttled: bool
    lane: str = "swarm"


class AgentMemoryManager:
    def __init__(self, base_dir: str, max_packet_chars: int = 1800):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.events_path = os.path.join(self.base_dir, "agent_memory_events.jsonl")
        self.report_path = os.path.join(self.base_dir, "agent_memory.md")
        self._max_packet_chars = max(200, int(max_packet_chars))
        self._lock = threading.RLock()
        self._stores: Dict[str, Dict[str, object]] = {}

    def prepare(
        self,
        agent_name: str,
        task_family: str,
        task_prompt: str,
        failure_context: str = "",
        support_notes: Optional[List[str]] = None,
        force_refresh: bool = False,
        max_chars: Optional[int] = None,
    ) -> MemoryRecall:
        family = self._normalize_task_family(task_family)
        key = self._key(agent_name, family)
        notes = self._normalize_notes(support_notes or [])
        limit = min(self._max_packet_chars, int(max_chars or self._max_packet_chars))

        with self._lock:
            store = self._stores.setdefault(
                key,
                {
                    "current": None,
                    "history": [],
                    "pending_notes": [],
                    "calls": 0,
                    "successes": 0,
                    "failures": 0,
                    "reuse_count": 0,
                    "refresh_count": 0,
                    "invalidations": 0,
                    "last_outcome": "",
                    "last_prompt": "",
                },
            )
            current: Optional[MemoryPacket] = store["current"]  # type: ignore[assignment]

            reusable = bool(
                current
                and not force_refresh
                and current.status == "ACTIVE"
                and not store["pending_notes"]
            )
            if reusable:
                current.reused_count += 1
                current.last_used_at = self._ts()
                current.last_outcome = str(store.get("last_outcome", ""))
                store["reuse_count"] = int(store["reuse_count"]) + 1
                store["last_prompt"] = task_prompt[:500]
                self._append_event(
                    MemoryEvent(
                        timestamp=self._ts(),
                        event_type="memory_reused",
                        agent_name=agent_name,
                        task_family=family,
                        packet_id=current.packet_id,
                        reused=True,
                        refreshed=False,
                        invalidated=False,
                        outcome=current.last_outcome,
                        note="Reused cached local memory packet.",
                        sources=list(current.source_notes),
                    )
                )
                return MemoryRecall(
                    agent_name=agent_name,
                    task_family=family,
                    packet_id=current.packet_id,
                    content=current.content,
                    reused=True,
                    refreshed=False,
                    documented=True,
                    note="reused cached packet",
                )

            packet = self._build_packet(
                agent_name=agent_name,
                task_family=family,
                task_prompt=task_prompt,
                failure_context=failure_context,
                source_notes=self._unique_preserve_order(
                    list(store["pending_notes"]) + notes + self._notes_from_context(failure_context)
                ),
                limit=limit,
                previous=current,
            )
            packet.last_used_at = self._ts()
            store["current"] = packet
            store["history"].append(packet)
            store["pending_notes"] = []
            store["refresh_count"] = int(store["refresh_count"]) + 1
            store["last_prompt"] = task_prompt[:500]
            self._append_event(
                MemoryEvent(
                    timestamp=self._ts(),
                    event_type="memory_primed",
                    agent_name=agent_name,
                    task_family=family,
                    packet_id=packet.packet_id,
                    reused=False,
                    refreshed=True,
                    invalidated=False,
                    outcome="",
                    note="Primed compact sidecar memory for local call.",
                    sources=list(packet.source_notes),
                )
            )
            self._write_report(agent_name, family, packet, "PRIMED")
            return MemoryRecall(
                agent_name=agent_name,
                task_family=family,
                packet_id=packet.packet_id,
                content=packet.content,
                reused=False,
                refreshed=True,
                documented=True,
                note="primed new packet",
            )

    def record_call(
        self,
        agent_name: str,
        task_family: str,
        packet_id: str,
        success: bool,
        outcome: str,
        reused: bool,
        note: str = "",
    ) -> None:
        family = self._normalize_task_family(task_family)
        key = self._key(agent_name, family)
        with self._lock:
            store = self._stores.setdefault(key, self._empty_store())
            store["calls"] = int(store["calls"]) + 1
            if success:
                store["successes"] = int(store["successes"]) + 1
            else:
                store["failures"] = int(store["failures"]) + 1
            store["last_outcome"] = outcome[:300]
            packet = store.get("current")
            if isinstance(packet, MemoryPacket) and (not packet_id or packet.packet_id == packet_id):
                packet.last_outcome = outcome[:300]
                packet.last_used_at = self._ts()
                if success and reused:
                    packet.reused_count += 1
            self._append_event(
                MemoryEvent(
                    timestamp=self._ts(),
                    event_type="memory_released",
                    agent_name=agent_name,
                    task_family=family,
                    packet_id=packet_id,
                    reused=reused,
                    refreshed=False,
                    invalidated=False,
                    outcome=outcome[:300],
                    note=note[:300],
                    sources=[],
                )
            )
            self._write_report(agent_name, family, packet if isinstance(packet, MemoryPacket) else None, "RELEASED", outcome)

    def inject_solution(
        self,
        agent_name: str,
        task_family: str,
        note: str,
        source_agent: str,
        reason: str = "",
    ) -> None:
        family = self._normalize_task_family(task_family)
        clean_note = (note or "").strip()
        if not clean_note:
            return
        key = self._key(agent_name, family)
        with self._lock:
            store = self._stores.setdefault(key, self._empty_store())
            pending = list(store["pending_notes"])
            pending.append(f"[{source_agent}] {reason}: {clean_note}" if reason else f"[{source_agent}] {clean_note}")
            store["pending_notes"] = self._unique_preserve_order(pending)[-10:]
            current = store.get("current")
            if isinstance(current, MemoryPacket):
                current.status = "STALE"
                current.stale_reason = f"injected solution from {source_agent}"[:200]
            store["invalidations"] = int(store["invalidations"]) + 1
            self._append_event(
                MemoryEvent(
                    timestamp=self._ts(),
                    event_type="memory_injected",
                    agent_name=agent_name,
                    task_family=family,
                    packet_id=current.packet_id if isinstance(current, MemoryPacket) else "",
                    reused=False,
                    refreshed=False,
                    invalidated=True,
                    outcome="",
                    note=f"Injected from {source_agent}: {reason or 'solution hint'}",
                    sources=[clean_note],
                )
            )
            self._write_report(
                agent_name,
                family,
                current if isinstance(current, MemoryPacket) else None,
                "INJECTED",
                clean_note,
            )

    def invalidate(self, agent_name: str, task_family: str, reason: str) -> None:
        family = self._normalize_task_family(task_family)
        key = self._key(agent_name, family)
        with self._lock:
            store = self._stores.setdefault(key, self._empty_store())
            current = store.get("current")
            if isinstance(current, MemoryPacket):
                current.status = "STALE"
                current.stale_reason = reason[:200]
            store["invalidations"] = int(store["invalidations"]) + 1
            self._append_event(
                MemoryEvent(
                    timestamp=self._ts(),
                    event_type="memory_invalidated",
                    agent_name=agent_name,
                    task_family=family,
                    packet_id=current.packet_id if isinstance(current, MemoryPacket) else "",
                    reused=False,
                    refreshed=False,
                    invalidated=True,
                    outcome="",
                    note=reason[:300],
                    sources=[],
                )
            )
            self._write_report(
                agent_name,
                family,
                current if isinstance(current, MemoryPacket) else None,
                "INVALIDATED",
                reason,
            )

    def status(self) -> Dict[str, object]:
        with self._lock:
            packets = 0
            reuse_count = 0
            invalidations = 0
            failures = 0
            successes = 0
            latest_note = ""
            latest_agent = ""
            latest_family = ""
            for key, store in self._stores.items():
                current = store.get("current")
                if isinstance(current, MemoryPacket):
                    packets += 1
                    latest_note = current.stale_reason or current.last_outcome or latest_note
                    latest_agent = current.agent_name
                    latest_family = current.task_family
                reuse_count += int(store.get("reuse_count", 0))
                invalidations += int(store.get("invalidations", 0))
                failures += int(store.get("failures", 0))
                successes += int(store.get("successes", 0))
            return {
                "packet_count": packets,
                "reuse_count": reuse_count,
                "invalidations": invalidations,
                "failure_count": failures,
                "success_count": successes,
                "latest_note": latest_note,
                "latest_agent": latest_agent,
                "latest_task_family": latest_family,
                "entries": {
                    key: {
                        "has_packet": isinstance(store.get("current"), MemoryPacket),
                        "history_count": len(store.get("history", [])),
                        "reuse_count": int(store.get("reuse_count", 0)),
                        "invalidations": int(store.get("invalidations", 0)),
                        "calls": int(store.get("calls", 0)),
                        "last_outcome": str(store.get("last_outcome", "")),
                    }
                    for key, store in self._stores.items()
                },
            }

    def specialist_profiles(self, limit: int = 6) -> List[Dict[str, object]]:
        with self._lock:
            profiles: List[Dict[str, object]] = []
            for store in self._stores.values():
                current = store.get("current")
                if not isinstance(current, MemoryPacket):
                    continue
                calls = int(store.get("calls", 0))
                successes = int(store.get("successes", 0))
                failures = int(store.get("failures", 0))
                reuse_count = int(store.get("reuse_count", 0))
                invalidations = int(store.get("invalidations", 0))
                refresh_count = int(store.get("refresh_count", 0))
                success_rate = round(successes / max(1, calls), 4)
                trend = self._trend_label(
                    calls=calls,
                    success_rate=success_rate,
                    reuse_count=reuse_count,
                    invalidations=invalidations,
                    failures=failures,
                )
                profiles.append(
                    {
                        "agent_name": current.agent_name,
                        "task_family": current.task_family,
                        "packet_id": current.packet_id,
                        "reuse_count": reuse_count,
                        "refresh_count": refresh_count,
                        "invalidations": invalidations,
                        "last_outcome": current.last_outcome or str(store.get("last_outcome", "")),
                        "current_expert_trend": trend,
                        "success_rate": success_rate,
                        "calls": calls,
                        "successes": successes,
                        "failures": failures,
                        "last_used_at": current.last_used_at,
                    }
                )
            profiles.sort(
                key=lambda item: (
                    item.get("success_rate", 0.0),
                    item.get("reuse_count", 0),
                    item.get("refresh_count", 0),
                ),
                reverse=True,
            )
            return profiles[: max(1, limit)]

    def _build_packet(
        self,
        agent_name: str,
        task_family: str,
        task_prompt: str,
        failure_context: str,
        source_notes: List[str],
        limit: int,
        previous: Optional[MemoryPacket],
    ) -> MemoryPacket:
        parts = [
            "LOCAL MEMORY PACKET",
            f"Agent: {agent_name}",
            f"Task Family: {task_family}",
        ]
        prompt = self._compact_text(task_prompt, max(220, min(420, limit // 3)))
        if prompt:
            parts.extend(["Task Prompt:", prompt])
        if failure_context:
            compact_failure = self._compact_text(failure_context, max(220, min(520, limit // 2)))
            if compact_failure:
                parts.extend(["Failure Context:", compact_failure])
        if source_notes:
            parts.append("Injected Solutions:")
            for item in source_notes[-6:]:
                parts.append(f"- {self._compact_text(item, 220)}")
        if previous and previous.content and previous.status == "ACTIVE":
            retained = self._compact_text(previous.content, max(120, min(320, limit // 4)))
            if retained:
                parts.extend(["Retained Memory:", retained])
        content = self._compact_text("\n".join(parts), limit)
        signature = self._signature(task_family, failure_context, source_notes)
        return MemoryPacket(
            packet_id=uuid.uuid4().hex[:12],
            agent_name=agent_name,
            task_family=task_family,
            content=content,
            signature=signature,
            created_at=self._ts(),
            source_notes=source_notes[-10:],
        )

    def _write_report(
        self,
        agent_name: str,
        task_family: str,
        packet: Optional[MemoryPacket],
        action: str,
        detail: str = "",
    ) -> None:
        lines = [
            f"## {self._ts()}",
            "",
            f"Agent: {agent_name}",
            f"Task Family: {task_family}",
            f"Action: {action}",
        ]
        if packet:
            lines.append(f"Packet ID: {packet.packet_id}")
            lines.append(f"Packet Status: {packet.status}")
        if detail:
            lines.append(f"Detail: {self._compact_text(detail, 400)}")
        lines.append("")
        with open(self.report_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def _append_event(self, event: MemoryEvent) -> None:
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(event), ensure_ascii=True) + "\n")

    def _empty_store(self) -> Dict[str, object]:
        return {
            "current": None,
            "history": [],
            "pending_notes": [],
            "calls": 0,
            "successes": 0,
            "failures": 0,
            "reuse_count": 0,
            "refresh_count": 0,
            "invalidations": 0,
            "last_outcome": "",
            "last_prompt": "",
        }

    def _key(self, agent_name: str, task_family: str) -> str:
        return f"{agent_name}::{task_family}"

    def _signature(self, task_family: str, failure_context: str, source_notes: List[str]) -> str:
        payload = "|".join(
            [
                task_family,
                self._compact_text(failure_context, 300),
                "|".join(self._normalize_notes(source_notes)),
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _trend_label(
        self,
        calls: int,
        success_rate: float,
        reuse_count: int,
        invalidations: int,
        failures: int,
    ) -> str:
        if calls < 2:
            return "forming"
        if invalidations > 0 and failures > max(0, reuse_count):
            return "stale"
        if success_rate >= 0.8 and reuse_count > 0:
            return "strengthening"
        if success_rate >= 0.55:
            return "stable"
        if failures >= calls:
            return "struggling"
        return "emerging"

    def _normalize_task_family(self, task_family: str) -> str:
        clean = (task_family or "general").strip().lower()
        return clean or "general"

    def _normalize_notes(self, notes: List[str]) -> List[str]:
        cleaned = [self._compact_text(item, 300).strip() for item in notes if str(item).strip()]
        return self._unique_preserve_order([item for item in cleaned if item])[-10:]

    def _notes_from_context(self, text: str) -> List[str]:
        if not text:
            return []
        parts = []
        for line in (text or "").splitlines():
            line = line.strip("- ").strip()
            if line:
                parts.append(line)
        return self._normalize_notes(parts)

    def _compact_text(self, text: str, max_chars: int) -> str:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        return clean[: max(1, max_chars)]

    def _unique_preserve_order(self, items: List[str]) -> List[str]:
        seen = set()
        result = []
        for item in items:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    def _ts(self) -> str:
        return datetime.utcnow().isoformat() + "Z"


class LocalCallGovernor:
    def __init__(
        self,
        max_inflight: int = 1,
        min_interval_seconds: float = 0.35,
        queue_limit: int = 2,
        backoff_seconds: float = 0.2,
    ):
        self._lock = threading.Condition()
        self.max_inflight = max(1, int(max_inflight))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.queue_limit = max(1, int(queue_limit))
        self.backoff_seconds = max(0.01, float(backoff_seconds))
        self._inflight = 0
        self._last_call_at = 0.0
        self._throttle_hits = 0
        self._max_queue_depth = 0
        self._priority_waiting = 0
        self._swarm_waiting = 0
        self._last_lane = "swarm"

    def configure(
        self,
        max_inflight: int,
        min_interval_seconds: float,
        queue_limit: int,
        backoff_seconds: float,
    ) -> None:
        with self._lock:
            self.max_inflight = max(1, int(max_inflight))
            self.min_interval_seconds = max(0.0, float(min_interval_seconds))
            self.queue_limit = max(1, int(queue_limit))
            self.backoff_seconds = max(0.01, float(backoff_seconds))
            self._lock.notify_all()

    def acquire(self, agent_name: str, task_family: str, lane: str = "swarm") -> LocalCallLease:
        lane = "user" if str(lane).strip().lower() in {"user", "chat", "priority"} else "swarm"
        waited = 0.0
        throttled = False
        start = time.time()
        with self._lock:
            if lane == "user":
                self._priority_waiting += 1
            else:
                self._swarm_waiting += 1
            queue_depth = self._inflight
            self._max_queue_depth = max(self._max_queue_depth, queue_depth)
            if queue_depth >= self.queue_limit:
                throttled = True
                self._throttle_hits += 1
                delay = self.backoff_seconds
                self._lock.wait(timeout=delay)
                waited += delay
            while self._inflight >= self.max_inflight:
                throttled = True
                self._throttle_hits += 1
                delay = max(self.backoff_seconds, self.min_interval_seconds)
                self._lock.wait(timeout=delay)
                waited += delay
            elapsed = time.time() - self._last_call_at
            if self._last_call_at and elapsed < self.min_interval_seconds:
                throttled = True
                self._throttle_hits += 1
                delay = self.min_interval_seconds - elapsed
                if delay > 0:
                    self._lock.wait(timeout=delay)
                    waited += delay
            if lane != "user":
                while self._priority_waiting > 0:
                    throttled = True
                    self._throttle_hits += 1
                    delay = max(self.backoff_seconds, self.min_interval_seconds)
                    self._lock.wait(timeout=delay)
                    waited += delay
            self._inflight += 1
            self._max_queue_depth = max(self._max_queue_depth, self._inflight)
            self._last_call_at = time.time()
            self._last_lane = lane
            if lane == "user":
                self._priority_waiting = max(0, self._priority_waiting - 1)
            else:
                self._swarm_waiting = max(0, self._swarm_waiting - 1)
            return LocalCallLease(
                agent_name=agent_name,
                task_family=task_family,
                acquired_at=self._last_call_at,
                waited_seconds=round(waited, 4),
                throttled=throttled,
                lane=lane,
            )

    def release(self, lease: LocalCallLease) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            self._last_lane = lease.lane
            self._lock.notify_all()

    def status(self) -> Dict[str, object]:
        with self._lock:
            return {
                "inflight": self._inflight,
                "throttle_hits": self._throttle_hits,
                "max_queue_depth": self._max_queue_depth,
                "user_waiting": self._priority_waiting,
                "swarm_waiting": self._swarm_waiting,
                "last_lane": self._last_lane,
                "min_interval_seconds": self.min_interval_seconds,
                "max_inflight": self.max_inflight,
                "queue_limit": self.queue_limit,
                "backoff_seconds": self.backoff_seconds,
            }
