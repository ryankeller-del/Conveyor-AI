import json
from pathlib import Path

from swarm_core.failure_memory import FailureMemory
from swarm_core.reporting import DailyReportGenerator


def test_failure_memory_logs_and_recalls_similar(tmp_path: Path):
    memory = FailureMemory(str(tmp_path / "learn"))
    memory.log_failure(
        prompt="build flask endpoint with auth",
        code="def endpoint(): pass",
        error_message="NameError: request is not defined",
        wave_name="BASELINE",
        target_file="app_v3.py",
        fix_summary="Import request from flask and validate headers",
    )

    guidance = memory.format_guidance("flask endpoint auth request handling", limit=2)
    assert "PAST FAILURE WARNINGS" in guidance
    assert "Prevention" in guidance


def test_failure_memory_appends_rule(tmp_path: Path):
    memory = FailureMemory(str(tmp_path / "learn"))
    memory.append_rule("Always validate None before dereference")
    rules_path = tmp_path / "learn" / "failure_rules.md"
    assert rules_path.exists()
    assert "Always validate None" in rules_path.read_text(encoding="utf-8")


def test_daily_report_generator_writes_report(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('x')\n", encoding="utf-8")
    (repo / "app_v2.py").write_text("print('y')\n", encoding="utf-8")
    (repo / "app_v3.py").write_text("print('z')\n", encoding="utf-8")
    (repo / "swarm_core").mkdir()
    (repo / "tests").mkdir()

    # Initialize a minimal git repo for diff-based report generation.
    import subprocess

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=bot", "-c", "user.email=bot@example.com", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    (repo / "app_v3.py").write_text("print('z2')\n", encoding="utf-8")

    report_path = DailyReportGenerator(str(repo)).generate()
    report_file = Path(report_path)
    assert report_file.exists()
    content = report_file.read_text(encoding="utf-8")
    assert "Version app_v3.py" in content
    assert "Daily Improvement Report" in content
