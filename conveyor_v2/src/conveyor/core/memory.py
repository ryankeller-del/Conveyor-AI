"""Memory management — local memory packets and generation memory.

Tracks memory packets, reuse counts, invalidations, and pressure
calculation. Provides the data structures and bookkeeping logic
for the controller's memory status aggregation.

Legacy source: SwarmController's memory-related fields (30+ status keys).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MemoryPacket:
    """A single memory unit stored by an agent.

    Matches the legacy concept of "local memory packets" — each packet
    is labelled with the agent that created it and the task family it
    belongs to, enabling cache-lookup by family.
    """
    agent_name: str
    task_family: str
    content: str
    created_at: float = field(default_factory=time.time)
    reuse_count: int = 0
    invalidated: bool = False


@dataclass
class GenerationMemoryRecord:
    """A single generation aspiration/note pair.

    Tracks what the swarm was trying to achieve in a given generation.
    Used for cross-generation learning.
    """
    generation_id: str
    aspiration: str
    note: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class MemoryState:
    """Aggregated memory statistics.

    Matches the status() keys for memory:
      local_memory_packet_count, local_memory_reuse_count,
      local_memory_invalidations, local_memory_pressure,
      local_memory_compaction_triggered, etc.
    """
    packet_count: int = 0
    reuse_count: int = 0
    invalidations: int = 0
    pressure: float = 0.0
    compaction_triggered: bool = False
    latest_pressure: float = 0.0
    latest_compaction_reason: str = ""
    latest_note: str = ""
    latest_agent: str = ""
    latest_task_family: str = ""

    max_packets: int = 100
    compaction_ratio: float = 0.7

    generation_records: int = 0
    generation_restores: int = 0
    latest_generation_id: str = ""
    latest_aspiration: str = ""
    latest_generation_note: str = ""
    memory_path: str = ""

    @property
    def active_packet_count(self) -> int:
        """Count of non-invalidated packets."""
        return self.packet_count - self.invalidations


class LocalMemory:
    """Manages local memory packets for the swarm.

    Thread-safe: all mutations are simple attribute updates
    suitable for asyncio.to_thread calls.
    """

    def __init__(self, max_packets: int = 100, compaction_ratio: float = 0.7) -> None:
        self.state = MemoryState(
            max_packets=max_packets,
            compaction_ratio=compaction_ratio,
        )
        self._packets: list[MemoryPacket] = []
        self._generation_records: list[GenerationMemoryRecord] = []

    def store_packet(self, agent_name: str, family: str, content: str) -> None:
        """Store a new memory packet."""
        packet = MemoryPacket(
            agent_name=agent_name,
            task_family=family,
            content=content,
        )
        self._packets.append(packet)
        self.state.packet_count = len(self._packets)
        self.state.latest_agent = agent_name
        self.state.latest_task_family = family
        self.state.latest_note = _truncate(content, 120)

    def lookup_by_family(self, family: str) -> list[MemoryPacket]:
        """Find non-invalidated packets matching a task family."""
        return [
            p for p in self._packets
            if p.task_family == family and not p.invalidated
        ]

    def increment_reuse(self, packet: MemoryPacket) -> None:
        """Mark a packet as reused."""
        packet.reuse_count += 1
        self.state.reuse_count += 1

    def invalidate(self, packet: MemoryPacket, reason: str = "") -> None:
        """Invalidate a packet (mark as stale)."""
        if not packet.invalidated:
            packet.invalidated = True
            self.state.invalidations += 1

    def calculate_pressure(self) -> float:
        """Calculate memory pressure as a 0.0-1.0 ratio.

        Pressure = (invalidations + active_packets) / (max_packets * 2)
        This means pressure approaches 1.0 when the buffer is full
        and many invalidations have occurred.
        """
        denominator = max(self.state.max_packets * 2, 1)
        numerator = self.state.packet_count + self.state.invalidations
        pressure = min(numerator / denominator, 1.0)
        self.state.pressure = pressure
        self.state.latest_pressure = pressure
        return pressure

    def check_compaction_trigger(self) -> bool:
        """Check if compaction should be triggered.

        Returns True if pressure exceeds compaction_ratio.
        """
        pressure = self.calculate_pressure()
        triggered = pressure >= self.state.compaction_ratio
        self.state.compaction_triggered = triggered
        return triggered

    def get_pressure_status(self) -> dict[str, Any]:
        """Return a status dict matching the legacy memory status keys."""
        self.calculate_pressure()
        return {
            "local_memory_packet_count": self.state.packet_count,
            "local_memory_reuse_count": self.state.reuse_count,
            "local_memory_invalidations": self.state.invalidations,
            "local_memory_pressure": self.state.pressure,
            "local_memory_compaction_triggered": self.state.compaction_triggered,
            "latest_local_memory_pressure": self.state.latest_pressure,
            "latest_local_memory_compaction_reason": self.state.latest_compaction_reason,
            "latest_local_memory_note": self.state.latest_note,
            "latest_local_memory_agent": self.state.latest_agent,
            "latest_local_memory_task_family": self.state.latest_task_family,
        }

    # --- Generation memory ---

    def record_generation(self, generation_id: str, aspiration: str, note: str = "") -> None:
        """Record a generation aspiration."""
        record = GenerationMemoryRecord(
            generation_id=generation_id,
            aspiration=aspiration,
            note=note,
        )
        self._generation_records.append(record)
        self.state.generation_records = len(self._generation_records)
        self.state.latest_generation_id = generation_id
        self.state.latest_aspiration = aspiration
        self.state.latest_generation_note = note

    def restore_generation(self, generation_id: str) -> GenerationMemoryRecord | None:
        """Look up a generation record by ID.

        Returns the record if found, incrementing restore count.
        """
        for record in self._generation_records:
            if record.generation_id == generation_id:
                self.state.generation_restores += 1
                return record
        return None

    def get_generation_status(self) -> dict[str, Any]:
        """Return generation memory status keys."""
        return {
            "generation_memory_records": self.state.generation_records,
            "generation_memory_restores": self.state.generation_restores,
            "generation_memory_latest_generation_id": self.state.latest_generation_id,
            "generation_memory_latest_aspiration": self.state.latest_aspiration,
            "generation_memory_latest_note": self.state.latest_generation_note,
            "generation_memory_path": self.state.memory_path,
        }

    def persist(self, path: Path) -> None:
        """Write current memory state to a JSON file."""
        data = {
            "packets": [
                {k: v for k, v in vars(p).items() if k != "raw"}
                for p in self._packets
            ],
            "generations": [
                {k: v for k, v in vars(g).items()}
                for g in self._generation_records
            ],
            "state": {
                k: v for k, v in vars(self.state).items()
                if not k.startswith("_") and not callable(v)
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        self.state.memory_path = str(path)


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."
