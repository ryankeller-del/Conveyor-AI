"""Tests for memory.py."""

import json
import tempfile
from pathlib import Path
from conveyor.core.memory import LocalMemory, MemoryPacket, MemoryState


class TestLocalMemory:
    def test_store_and_count(self):
        mem = LocalMemory(max_packets=10)
        mem.store_packet("coder", "code-refactor", "Move types.py to core")
        status = mem.get_pressure_status()
        assert status["local_memory_packet_count"] == 1
        assert status["latest_local_memory_agent"] == "coder"
        assert status["latest_local_memory_task_family"] == "code-refactor"

    def test_lookup_by_family(self):
        mem = LocalMemory()
        mem.store_packet("coder", "code", "packet 1")
        mem.store_packet("judge", "code", "packet 2")
        mem.store_packet("chat", "chat", "packet 3")

        results = mem.lookup_by_family("code")
        assert len(results) == 2
        assert all(p.task_family == "code" for p in results)

    def test_reuse_tracking(self):
        mem = LocalMemory()
        mem.store_packet("coder", "test", "write tests")
        packet = mem.lookup_by_family("test")[0]
        mem.increment_reuse(packet)
        mem.increment_reuse(packet)
        assert mem.state.reuse_count == 2
        assert packet.reuse_count == 2

    def test_invalidation(self):
        mem = LocalMemory()
        mem.store_packet("coder", "old", "stale content")
        packet = mem.lookup_by_family("old")[0]
        mem.invalidate(packet, "obsolete")
        assert packet.invalidated
        assert mem.state.invalidations == 1
        # Lookup should not return invalidated packet
        assert len(mem.lookup_by_family("old")) == 0

    def test_pressure_calculation(self):
        mem = LocalMemory(max_packets=10)
        for i in range(10):
            mem.store_packet("agent", f"family-{i}", f"content {i}")
        pressure = mem.calculate_pressure()
        # packet_count=10, invalidations=0, denominator=20
        # pressure = 10/20 = 0.5
        assert abs(pressure - 0.5) < 0.01

    def test_pressure_capped_at_1(self):
        mem = LocalMemory(max_packets=5)
        for i in range(20):
            mem.store_packet("agent", f"family-{i}", f"content {i}")
        pressure = mem.calculate_pressure()
        assert pressure <= 1.0

    def test_compaction_trigger(self):
        mem = LocalMemory(max_packets=10, compaction_ratio=0.5)
        for i in range(15):
            mem.store_packet("agent", f"family-{i}", f"content {i}")
        assert mem.check_compaction_trigger() is True

    def test_generation_memory(self):
        mem = LocalMemory()
        mem.record_generation("gen-1", "refactor types")
        mem.record_generation("gen-2", "add tests")
        gen_status = mem.get_generation_status()
        assert gen_status["generation_memory_records"] == 2
        assert gen_status["generation_memory_latest_generation_id"] == "gen-2"

    def test_generation_restore(self):
        mem = LocalMemory()
        mem.record_generation("gen-1", "aspiration")
        record = mem.restore_generation("gen-1")
        assert record is not None
        assert record.aspiration == "aspiration"
        assert mem.state.generation_restores == 1

    def test_generation_restore_missing(self):
        mem = LocalMemory()
        result = mem.restore_generation("nonexistent")
        assert result is None

    def test_persist_and_reload(self):
        mem = LocalMemory(max_packets=5)
        mem.store_packet("coder", "code", "test packet")
        mem.record_generation("g1", "test gen")

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = Path(f.name)
        try:
            mem.persist(path)
            assert path.is_file()
            with open(path) as fh:
                data = json.load(fh)
            assert data["packets"][0]["agent_name"] == "coder"
            assert data["generations"][0]["generation_id"] == "g1"
            assert data["state"]["packet_count"] == 1
        finally:
            path.unlink(missing_ok=True)


class TestMemoryState:
    def test_defaults(self):
        state = MemoryState()
        assert state.pressure == 0.0
        assert state.packet_count == 0
        assert state.max_packets == 100
        assert state.compaction_ratio == 0.7

    def test_active_packet_count(self):
        state = MemoryState(packet_count=10, invalidations=3)
        assert state.active_packet_count == 7
