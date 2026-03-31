from __future__ import annotations

import heapq
import hashlib
import itertools
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
    generation_id: str
    content: str
    signature: str
    created_at: str
    aspiration_prompt: str = ""
    last_used_at: str = ""
    reused_count: int = 0
    restore_count: int = 0
    status: str = "ACTIVE"
    source_notes: List[str] = field(default_factory=list)
    last_outcome: str = ""
    stale_reason: str = ""
    pressure: float = 0.0
    compacted_at: str = ""
    compaction_reason: str = ""


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
    pressure: float = 0.0
    compacted: bool = False
    compaction_reason: str = ""


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


@dataclass(order=True)
class _QueuedLocalCall:
    priority: int
    sequence: int
    agent_name: str = field(compare=False)
    task_family: str = field(compare=False)
    lane: str = field(compare=False)


@dataclass
class GenerationMemoryRecord:
    record_id: str
    timestamp: str
    generation_id: str
    source_generation_id: str
    agent_name: str
    task_family: str
    packet_id: str
    aspiration_prompt: str
    distilled_lessons: str
    raw_packet_path: str
    reason: str
    status: str
    restore_count: int = 0


@dataclass
class GenerationMemoryRestore:
    content: str
    record_ids: List[str] = field(default_factory=list)
    restored: bool = False
    note: str = ""
    restore_count: int = 0
    latest_aspiration: str = ""


class GenerationMemoryArchive:
    def __init__(self, base_dir: str, max_record_chars: int = 1200):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.events_path = os.path.join(self.base_dir, "generation_memory.jsonl")
        self.report_path = os.path.join(self.base_dir, "generation_memory.md")
        self.state_path = os.path.join(self.base_dir, "generation_memory_state.json")
        self.raw_dir = os.path.join(self.base_dir, "raw")
        os.makedirs(self.raw_dir, exist_ok=True)
        self._max_record_chars = max(240, int(max_record_chars))
        self._lock = threading.RLock()
        self._records: List[GenerationMemoryRecord] = []
        self._restore_events = 0
        self._current_generation_id = ""
        self._latest_aspiration = ""
        self._latest_note = ""
        self._load_from_disk()

    def begin_generation(
        self,
        generation_id: str,
        aspiration_prompt: str,
        source_generation_id: str = "",
    ) -> None:
        generation_id = (generation_id or "").strip()
        aspiration_prompt = self._compact_text(aspiration_prompt, 400)
        source_generation_id = (source_generation_id or "").strip()
        if not generation_id:
            return
        with self._lock:
            self._current_generation_id = generation_id
            self._latest_aspiration = aspiration_prompt
            record = GenerationMemoryRecord(
                record_id=uuid.uuid4().hex[:12],
                timestamp=self._ts(),
                generation_id=generation_id,
                source_generation_id=source_generation_id,
                agent_name="generation",
                task_family="generation",
                packet_id="",
                aspiration_prompt=aspiration_prompt,
                distilled_lessons=self._compact_text(
                    aspiration_prompt or "Generation started",
                    self._max_record_chars,
                ),
                raw_packet_path="",
                reason="generation_start",
                status="ACTIVE",
            )
            self._records.append(record)
            self._append_record(record)
            self._write_report(record, "GENERATION STARTED", aspiration_prompt)
            self._write_state()

    def capture_packet(
        self,
        packet: MemoryPacket,
        reason: str,
        aspiration_prompt: str = "",
        raw_trace: str = "",
        source_generation_id: str = "",
    ) -> Optional[GenerationMemoryRecord]:
        if not packet:
            return None
        aspiration_prompt = self._compact_text(
            aspiration_prompt or packet.aspiration_prompt or packet.content,
            400,
        )
        distilled_lessons = self._distill_packet(packet, reason=reason, raw_trace=raw_trace)
        if not distilled_lessons:
            return None
        with self._lock:
            record = GenerationMemoryRecord(
                record_id=uuid.uuid4().hex[:12],
                timestamp=self._ts(),
                generation_id=self._current_generation_id or packet.generation_id or "",
                source_generation_id=source_generation_id or packet.generation_id or "",
                agent_name=packet.agent_name,
                task_family=packet.task_family,
                packet_id=packet.packet_id,
                aspiration_prompt=aspiration_prompt,
                distilled_lessons=distilled_lessons,
                raw_packet_path="",
                reason=self._compact_text(reason, 240),
                status=packet.status,
                restore_count=max(0, int(packet.restore_count)),
            )
            raw_packet_path = self._write_raw_packet(packet, record.record_id, raw_trace)
            record.raw_packet_path = raw_packet_path
            self._records.append(record)
            self._append_record(record)
            self._write_report(record, "CAPTURED", reason)
            self._write_state()
            self._latest_note = reason[:300]
            return record

    def restore(
        self,
        agent_name: str,
        task_family: str,
        task_prompt: str,
        failure_context: str = "",
        max_records: int = 3,
        max_chars: Optional[int] = None,
        include_raw: bool = False,
    ) -> GenerationMemoryRestore:
        family = self._normalize(task_family)
        agent = (agent_name or "").strip()
        limit = min(self._max_record_chars, int(max_chars or self._max_record_chars))
        with self._lock:
            candidates = self._rank_candidates(agent, family)
            if not candidates:
                return GenerationMemoryRestore()
            selected = candidates[: max(1, int(max_records))]
            parts = [
                "GENERATION MEMORY RESTORE",
                f"Agent: {agent or 'unknown'}",
                f"Task Family: {family}",
            ]
            if task_prompt.strip():
                parts.extend(
                    [
                        "Current Task:",
                        self._compact_text(task_prompt, max(120, min(360, limit // 2))),
                    ]
                )
            if failure_context.strip():
                parts.extend(
                    [
                        "Failure Context:",
                        self._compact_text(failure_context, max(120, min(300, limit // 3))),
                    ]
                )
            record_ids: List[str] = []
            latest_aspiration = ""
            for idx, record in enumerate(selected, start=1):
                record_ids.append(record.record_id)
                latest_aspiration = record.aspiration_prompt or latest_aspiration
                parts.append(
                    f"{idx}. Gen={record.generation_id or 'n/a'} | From={record.source_generation_id or 'n/a'} | "
                    f"Goal={self._compact_text(record.aspiration_prompt or 'n/a', 180)}"
                )
                parts.append(f"   Lesson: {self._compact_text(record.distilled_lessons, 260)}")
                if include_raw and record.raw_packet_path and os.path.exists(record.raw_packet_path):
                    try:
                        with open(record.raw_packet_path, "r", encoding="utf-8") as handle:
                            raw_packet = json.load(handle)
                        raw_excerpt = self._compact_text(json.dumps(raw_packet, ensure_ascii=True), 260)
                        if raw_excerpt:
                            parts.append(f"   Raw: {raw_excerpt}")
                    except Exception:
                        pass
            content = self._compact_text("\n".join(parts), limit)
            if not content:
                return GenerationMemoryRestore()
            self._restore_events += 1
            note = f"restored {len(record_ids)} lineage records"
            self._latest_note = note
            self._write_state()
            self._write_report(
                None,
                "RESTORE",
                note,
                extra_lines=[
                    f"Agent: {agent}",
                    f"Task Family: {family}",
                    f"Record IDs: {', '.join(record_ids)}",
                    f"Restore Count: {self._restore_events}",
                ],
            )
            self._append_jsonl(
                {
                    "timestamp": self._ts(),
                    "event_type": "generation_restore",
                    "agent_name": agent,
                    "task_family": family,
                    "record_ids": record_ids,
                    "restore_count": self._restore_events,
                    "note": note,
                }
            )
            return GenerationMemoryRestore(
                content=content,
                record_ids=record_ids,
                restored=True,
                note=note,
                restore_count=self._restore_events,
                latest_aspiration=latest_aspiration,
            )

    def status(self) -> Dict[str, object]:
        with self._lock:
            latest_record = self._records[-1] if self._records else None
            return {
                "record_count": len(self._records),
                "restore_count": self._restore_events,
                "latest_generation_id": self._current_generation_id,
                "latest_aspiration": self._latest_aspiration,
                "latest_note": self._latest_note,
                "base_dir": self.base_dir,
                "events_path": self.events_path,
                "report_path": self.report_path,
                "state_path": self.state_path,
                "latest_record_id": latest_record.record_id if latest_record else "",
                "latest_record_agent": latest_record.agent_name if latest_record else "",
                "latest_record_task_family": latest_record.task_family if latest_record else "",
            }

    def _load_from_disk(self) -> None:
        if not os.path.exists(self.events_path):
            return
        try:
            with open(self.events_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    payload = json.loads(line)
                    event_type = str(payload.get("event_type", ""))
                    if event_type == "generation_record":
                        self._records.append(
                            GenerationMemoryRecord(
                                record_id=str(payload.get("record_id", "")),
                                timestamp=str(payload.get("timestamp", "")),
                                generation_id=str(payload.get("generation_id", "")),
                                source_generation_id=str(payload.get("source_generation_id", "")),
                                agent_name=str(payload.get("agent_name", "")),
                                task_family=str(payload.get("task_family", "")),
                                packet_id=str(payload.get("packet_id", "")),
                                aspiration_prompt=str(payload.get("aspiration_prompt", "")),
                                distilled_lessons=str(payload.get("distilled_lessons", "")),
                                raw_packet_path=str(payload.get("raw_packet_path", "")),
                                reason=str(payload.get("reason", "")),
                                status=str(payload.get("status", "")),
                                restore_count=int(payload.get("restore_count", 0)),
                            )
                        )
                        self._latest_note = str(payload.get("reason", self._latest_note))
                    elif event_type == "generation_restore":
                        self._restore_events = int(payload.get("restore_count", self._restore_events))
                        self._latest_note = str(payload.get("note", self._latest_note))
                    elif event_type == "generation_start":
                        self._current_generation_id = str(payload.get("generation_id", self._current_generation_id))
                        self._latest_aspiration = str(payload.get("aspiration_prompt", self._latest_aspiration))
        except Exception:
            return

    def _rank_candidates(self, agent_name: str, task_family: str) -> List[GenerationMemoryRecord]:
        exact: List[GenerationMemoryRecord] = []
        family_matches: List[GenerationMemoryRecord] = []
        fallback: List[GenerationMemoryRecord] = []
        for record in reversed(self._records):
            if not record.distilled_lessons:
                continue
            if record.agent_name == agent_name and record.task_family == task_family:
                exact.append(record)
            elif record.task_family == task_family:
                family_matches.append(record)
            else:
                fallback.append(record)
        return exact + family_matches + fallback

    def _distill_packet(self, packet: MemoryPacket, reason: str, raw_trace: str = "") -> str:
        parts = [
            f"Agent: {packet.agent_name}",
            f"Task Family: {packet.task_family}",
            f"Aspiration: {self._compact_text(packet.aspiration_prompt or packet.content, 280)}",
            f"Lesson: {self._compact_text(packet.content, 420)}",
        ]
        if packet.source_notes:
            parts.append("Source Notes:")
            for note in packet.source_notes[-4:]:
                parts.append(f"- {self._compact_text(note, 180)}")
        if packet.last_outcome:
            parts.append(f"Last Outcome: {self._compact_text(packet.last_outcome, 220)}")
        if packet.stale_reason:
            parts.append(f"Stale Reason: {self._compact_text(packet.stale_reason, 220)}")
        if reason:
            parts.append(f"Reason: {self._compact_text(reason, 220)}")
        if raw_trace:
            parts.append(f"Raw Trace: {self._compact_text(raw_trace, 260)}")
        return self._compact_text("\n".join(parts), self._max_record_chars)

    def _write_raw_packet(self, packet: MemoryPacket, record_id: str, raw_trace: str = "") -> str:
        payload = asdict(packet)
        if raw_trace:
            payload["raw_trace"] = self._compact_text(raw_trace, 1000)
        path = os.path.join(self.raw_dir, f"{record_id}.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True, default=str)
        return path

    def _append_record(self, record: GenerationMemoryRecord) -> None:
        self._append_jsonl(
            {
                "event_type": "generation_record",
                **asdict(record),
            }
        )

    def _append_jsonl(self, payload: Dict[str, object]) -> None:
        with open(self.events_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def _write_report(
        self,
        record: Optional[GenerationMemoryRecord],
        action: str,
        detail: str = "",
        extra_lines: Optional[List[str]] = None,
    ) -> None:
        lines = [
            f"## {self._ts()}",
            "",
            f"Action: {action}",
        ]
        if record:
            lines.extend(
                [
                    f"Record ID: {record.record_id}",
                    f"Generation ID: {record.generation_id}",
                    f"Agent: {record.agent_name}",
                    f"Task Family: {record.task_family}",
                    f"Reason: {record.reason}",
                ]
            )
        if detail:
            lines.append(f"Detail: {self._compact_text(detail, 400)}")
        if extra_lines:
            lines.extend(extra_lines)
        lines.append("")
        with open(self.report_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def _write_state(self) -> None:
        payload = {
            "updated_at": self._ts(),
            "generation_id": self._current_generation_id,
            "latest_aspiration": self._latest_aspiration,
            "latest_note": self._latest_note,
            "record_count": len(self._records),
            "restore_count": self._restore_events,
        }
        with open(self.state_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=True)

    def _compact_text(self, text: str, max_chars: int) -> str:
        clean = re.sub(r"\s+", " ", (text or "").strip())
        return clean[: max(1, max_chars)]

    def _ts(self) -> str:
        return datetime.utcnow().isoformat() + "Z"

    def _normalize(self, task_family: str) -> str:
        clean = (task_family or "general").strip().lower()
        return clean or "general"


class AgentMemoryManager:
    def __init__(self, base_dir: str, max_packet_chars: int = 1800):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.events_path = os.path.join(self.base_dir, "agent_memory_events.jsonl")
        self.report_path = os.path.join(self.base_dir, "agent_memory.md")
        self._max_packet_chars = max(200, int(max_packet_chars))
        self._lock = threading.RLock()
        self._stores: Dict[str, Dict[str, object]] = {}
        self._generation_archive: Optional[GenerationMemoryArchive] = None

    def attach_generation_archive(self, archive: Optional[GenerationMemoryArchive]) -> None:
        with self._lock:
            self._generation_archive = archive

    def prepare(
        self,
        agent_name: str,
        task_family: str,
        task_prompt: str,
        failure_context: str = "",
        support_notes: Optional[List[str]] = None,
        force_refresh: bool = False,
        max_chars: Optional[int] = None,
        pressure_threshold: float = 0.78,
    ) -> MemoryRecall:
        family = self._normalize_task_family(task_family)
        key = self._key(agent_name, family)
        notes = self._normalize_notes(support_notes or [])
        limit = min(self._max_packet_chars, int(max_chars or self._max_packet_chars))
        threshold = max(0.1, min(1.5, float(pressure_threshold)))

        with self._lock:
            store = self._stores.setdefault(
                key,
                {
                    "current": None,
                    "history": [],
                    "pending_notes": [],
                    "last_captured_packet_id": "",
                    "calls": 0,
                    "successes": 0,
                    "failures": 0,
                    "reuse_count": 0,
                    "refresh_count": 0,
                    "invalidations": 0,
                    "last_outcome": "",
                    "last_prompt": "",
                    "last_pressure": 0.0,
                    "last_compaction_reason": "",
                    "last_compaction_triggered": False,
                    "generation_id": "",
                },
            )
            current: Optional[MemoryPacket] = store["current"]  # type: ignore[assignment]
            pressure = self._memory_pressure(task_prompt, failure_context, notes, current, limit)
            signature = self._signature(
                task_family=family,
                task_prompt=task_prompt,
                failure_context=failure_context,
            )
            compaction_triggered = bool(current and pressure >= threshold)

            reusable = bool(
                current
                and not force_refresh
                and current.status == "ACTIVE"
                and not store["pending_notes"]
                and current.signature == signature
                and not compaction_triggered
            )
            if reusable:
                current.reused_count += 1
                current.last_used_at = self._ts()
                current.last_outcome = str(store.get("last_outcome", ""))
                current.pressure = pressure
                store["reuse_count"] = int(store["reuse_count"]) + 1
                store["last_prompt"] = task_prompt[:500]
                store["last_pressure"] = pressure
                store["last_compaction_reason"] = ""
                store["last_compaction_triggered"] = False
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
                    pressure=pressure,
                    compacted=False,
                )

            compaction_reason = ""
            if current and compaction_triggered:
                compaction_reason = (
                    f"auto-compacted at pressure {pressure:.2f} >= {threshold:.2f}"
                )
            elif current and force_refresh:
                compaction_reason = "forced refresh for local memory"
            elif current and current.signature != signature:
                compaction_reason = "task signature changed"
            elif current and store["pending_notes"]:
                compaction_reason = "pending solution hints required refresh"

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
                generation_id=str(store.get("generation_id", "")),
            )
            packet.last_used_at = self._ts()
            packet.pressure = pressure
            packet.compacted_at = self._ts() if current else ""
            packet.compaction_reason = compaction_reason
            if current and self._generation_archive and isinstance(current, MemoryPacket):
                self._capture_generation_record(
                    current=current,
                    reason=compaction_reason or "memory refreshed",
                    aspiration_prompt=current.aspiration_prompt or str(store.get("last_prompt", "")),
                )
            store["current"] = packet
            store["history"].append(packet)
            store["pending_notes"] = []
            store["refresh_count"] = int(store["refresh_count"]) + 1
            store["last_prompt"] = task_prompt[:500]
            store["last_pressure"] = pressure
            store["last_compaction_reason"] = compaction_reason
            store["last_compaction_triggered"] = compaction_triggered
            store["generation_id"] = packet.generation_id
            if current and isinstance(current, MemoryPacket):
                current.status = "COMPACTED" if compaction_triggered else "STALE"
                current.stale_reason = compaction_reason
            self._append_event(
                MemoryEvent(
                    timestamp=self._ts(),
                    event_type="memory_compacted" if compaction_triggered else "memory_primed",
                    agent_name=agent_name,
                    task_family=family,
                    packet_id=packet.packet_id,
                    reused=False,
                    refreshed=True,
                    invalidated=False,
                    outcome="",
                    note=(
                        "Auto-compacted local memory packet for local call."
                        if compaction_triggered
                        else "Primed compact sidecar memory for local call."
                    ),
                    sources=list(packet.source_notes),
                )
            )
            self._write_report(
                agent_name,
                family,
                packet,
                "COMPACTED" if compaction_triggered else "PRIMED",
            )
            return MemoryRecall(
                agent_name=agent_name,
                task_family=family,
                packet_id=packet.packet_id,
                content=packet.content,
                reused=False,
                refreshed=True,
                documented=True,
                note="auto-compacted packet" if compaction_triggered else "primed new packet",
                pressure=pressure,
                compacted=compaction_triggered,
                compaction_reason=compaction_reason,
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
            if self._generation_archive and isinstance(packet, MemoryPacket):
                self._capture_generation_record(
                    current=packet,
                    reason=f"call released: {outcome[:120]}",
                    aspiration_prompt=packet.aspiration_prompt or str(store.get("last_prompt", "")),
                )
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
            if self._generation_archive and isinstance(current, MemoryPacket):
                self._capture_generation_record(
                    current=current,
                    reason=f"injected solution from {source_agent}: {reason or 'solution hint'}",
                    aspiration_prompt=current.aspiration_prompt or str(store.get("last_prompt", "")),
                    raw_trace=clean_note,
                )
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
            if self._generation_archive and isinstance(current, MemoryPacket):
                self._capture_generation_record(
                    current=current,
                    reason=reason,
                    aspiration_prompt=current.aspiration_prompt or str(store.get("last_prompt", "")),
                )
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
            latest_pressure = 0.0
            latest_compaction_reason = ""
            compaction_triggered = False
            generation_status = self._generation_archive.status() if self._generation_archive else {}
            for key, store in self._stores.items():
                current = store.get("current")
                if isinstance(current, MemoryPacket):
                    packets += 1
                    latest_note = current.stale_reason or current.last_outcome or latest_note
                    latest_agent = current.agent_name
                    latest_family = current.task_family
                    latest_pressure = max(latest_pressure, float(current.pressure or 0.0))
                    latest_compaction_reason = (
                        str(store.get("last_compaction_reason", ""))
                        or current.compaction_reason
                        or latest_compaction_reason
                    )
                else:
                    latest_compaction_reason = str(store.get("last_compaction_reason", "")) or latest_compaction_reason
                compaction_triggered = compaction_triggered or bool(store.get("last_compaction_triggered", False))
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
                "latest_pressure": latest_pressure,
                "latest_compaction_reason": latest_compaction_reason,
                "compaction_triggered": compaction_triggered,
                "generation_record_count": generation_status.get("record_count", 0),
                "generation_restore_count": generation_status.get("restore_count", 0),
                "generation_latest_generation_id": generation_status.get("latest_generation_id", ""),
                "generation_latest_aspiration": generation_status.get("latest_aspiration", ""),
                "generation_latest_note": generation_status.get("latest_note", ""),
                "generation_archive_path": self._generation_archive.base_dir if self._generation_archive else "",
                "entries": {
                    key: {
                        "has_packet": isinstance(store.get("current"), MemoryPacket),
                        "history_count": len(store.get("history", [])),
                        "reuse_count": int(store.get("reuse_count", 0)),
                        "invalidations": int(store.get("invalidations", 0)),
                        "calls": int(store.get("calls", 0)),
                        "last_outcome": str(store.get("last_outcome", "")),
                        "last_pressure": float(store.get("last_pressure", 0.0)),
                        "last_compaction_reason": str(store.get("last_compaction_reason", "")),
                        "last_compaction_triggered": bool(store.get("last_compaction_triggered", False)),
                    }
                    for key, store in self._stores.items()
                },
            }

    def has_active_packet(self, agent_name: str, task_family: str) -> bool:
        family = self._normalize_task_family(task_family)
        key = self._key(agent_name, family)
        with self._lock:
            store = self._stores.get(key)
            if not store:
                return False
            current = store.get("current")
            return isinstance(current, MemoryPacket) and current.status == "ACTIVE"

    def _capture_generation_record(
        self,
        current: MemoryPacket,
        reason: str,
        aspiration_prompt: str = "",
        raw_trace: str = "",
    ) -> None:
        if not self._generation_archive:
            return
        key = self._key(current.agent_name, current.task_family)
        with self._lock:
            store = self._stores.setdefault(key, self._empty_store())
            if store.get("last_captured_packet_id") == current.packet_id:
                return
            record = self._generation_archive.capture_packet(
                packet=current,
                reason=reason,
                aspiration_prompt=aspiration_prompt or current.aspiration_prompt,
                raw_trace=raw_trace,
                source_generation_id=str(store.get("generation_id", current.generation_id)),
            )
            if record:
                store["last_captured_packet_id"] = current.packet_id
                current.restore_count += 1
                store["generation_id"] = current.generation_id

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
                        "generation_id": current.generation_id,
                        "aspiration_prompt": current.aspiration_prompt,
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
                        "pressure": round(float(current.pressure or 0.0), 4),
                        "compaction_reason": current.compaction_reason,
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
        generation_id: str = "",
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
        signature = self._signature(task_family, task_prompt, failure_context)
        return MemoryPacket(
            packet_id=uuid.uuid4().hex[:12],
            agent_name=agent_name,
            task_family=task_family,
            generation_id=generation_id or uuid.uuid4().hex[:8],
            content=content,
            signature=signature,
            created_at=self._ts(),
            aspiration_prompt=self._compact_text(task_prompt, 320),
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
            "last_captured_packet_id": "",
            "calls": 0,
            "successes": 0,
            "failures": 0,
            "reuse_count": 0,
            "refresh_count": 0,
            "invalidations": 0,
            "last_outcome": "",
            "last_prompt": "",
            "generation_id": "",
        }

    def _key(self, agent_name: str, task_family: str) -> str:
        return f"{agent_name}::{task_family}"

    def _signature(
        self,
        task_family: str,
        task_prompt: str,
        failure_context: str,
    ) -> str:
        payload = "|".join(
            [
                task_family,
                self._compact_text(task_prompt, 360),
                self._compact_text(failure_context, 300),
            ]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _memory_pressure(
        self,
        task_prompt: str,
        failure_context: str,
        source_notes: List[str],
        current: Optional[MemoryPacket],
        limit: int,
    ) -> float:
        current_chars = len(current.content) if isinstance(current, MemoryPacket) else 0
        prompt_chars = len(self._compact_text(task_prompt, limit))
        failure_chars = len(self._compact_text(failure_context, limit))
        notes_chars = sum(len(self._compact_text(item, 220)) for item in source_notes[-6:])
        total_chars = current_chars + prompt_chars + failure_chars + notes_chars
        return round(min(1.5, total_chars / max(1, limit)), 4)

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
        self._sequence = itertools.count()
        self._inflight = 0
        self._last_call_at = 0.0
        self._throttle_hits = 0
        self._max_queue_depth = 0
        self._priority_waiting = 0
        self._swarm_waiting = 0
        self._last_lane = "swarm"
        self._waiters: List[_QueuedLocalCall] = []

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
        sequence = next(self._sequence)
        queued = _QueuedLocalCall(
            priority=0 if lane == "user" else 1,
            sequence=sequence,
            agent_name=agent_name,
            task_family=task_family,
            lane=lane,
        )
        with self._lock:
            while lane != "user" and self._swarm_waiting >= self.queue_limit:
                throttled = True
                self._throttle_hits += 1
                delay = self.backoff_seconds
                self._lock.wait(timeout=delay)
                waited += delay
            heapq.heappush(self._waiters, queued)
            if lane == "user":
                self._priority_waiting += 1
            else:
                self._swarm_waiting += 1
            self._max_queue_depth = max(self._max_queue_depth, self._inflight + len(self._waiters))
            self._lock.notify_all()
            while True:
                head = self._waiters[0] if self._waiters else None
                elapsed = time.time() - self._last_call_at
                interval_ready = not self._last_call_at or elapsed >= self.min_interval_seconds
                if head is queued and self._inflight < self.max_inflight and interval_ready:
                    heapq.heappop(self._waiters)
                    if lane == "user":
                        self._priority_waiting = max(0, self._priority_waiting - 1)
                    else:
                        self._swarm_waiting = max(0, self._swarm_waiting - 1)
                    self._inflight += 1
                    self._max_queue_depth = max(self._max_queue_depth, self._inflight + len(self._waiters))
                    self._last_call_at = time.time()
                    self._last_lane = lane
                    return LocalCallLease(
                        agent_name=agent_name,
                        task_family=task_family,
                        acquired_at=self._last_call_at,
                        waited_seconds=round(waited, 4),
                        throttled=throttled,
                        lane=lane,
                    )

                throttled = True
                self._throttle_hits += 1
                delay = self.backoff_seconds
                if self._inflight >= self.max_inflight:
                    delay = max(delay, self.min_interval_seconds)
                elif not interval_ready:
                    delay = max(delay, self.min_interval_seconds - elapsed)
                self._lock.wait(timeout=max(0.01, delay))
                waited += max(0.01, delay)

    def release(self, lease: LocalCallLease) -> None:
        with self._lock:
            self._inflight = max(0, self._inflight - 1)
            self._last_lane = lease.lane
            self._lock.notify_all()

    def status(self) -> Dict[str, object]:
        with self._lock:
            return {
                "inflight": self._inflight,
                "queue_depth": len(self._waiters) + self._inflight,
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
