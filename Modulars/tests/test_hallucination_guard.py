from pathlib import Path

from swarm_core.hallucination_guard import HallucinationGuard


def test_hallucination_guard_detects_unknown_symbols_and_apis(tmp_path: Path):
    target = tmp_path / "module_a.py"
    target.write_text(
        "def known_func():\n    return 1\n",
        encoding="utf-8",
    )

    guard = HallucinationGuard(str(tmp_path))
    result = guard.evaluate(
        target_file=str(target),
        code=(
            "def runner():\n"
            "    known_func()\n"
            "    MissingSymbol()\n"
            "    GhostApi.do_magic()\n"
        ),
        prompt="implement robust behavior",
        doc_grounding_enabled=False,
    )

    assert result.confidence < 1.0
    assert any("MissingSymbol" in symbol for symbol in result.unknown_symbols)
    assert any("GhostApi.do_magic" in api for api in result.unknown_apis)
    assert len(result.alerts) >= 1


def test_hallucination_guard_doc_grounding_flag(tmp_path: Path):
    target = tmp_path / "module_b.py"
    target.write_text("def f():\n    return 2\n", encoding="utf-8")

    guard = HallucinationGuard(str(tmp_path))
    result = guard.evaluate(
        target_file=str(target),
        code="def f2():\n    return f()\n",
        prompt="According to official docs, this should be best.",
        doc_grounding_enabled=True,
    )

    assert result.missing_doc_grounding is True
    assert result.confidence < 1.0
