from argparse import Namespace
from pathlib import Path

from run_rehearsal import run_rehearsal


def test_standalone_rehearsal_launcher_writes_reports(tmp_path: Path):
    args = Namespace(
        profile="healthy",
        goal="Validate offline rehearsal launcher",
        target_files="app_v3.py",
        language="general",
        stage="BOOTSTRAP",
        root=str(tmp_path),
        apply_if_better=False,
    )

    result = run_rehearsal(args)

    assert result["rehearsal_id"]
    assert Path(result["report_path"]).exists()
    assert Path(result["manifest_path"]).exists()
    assert Path(result["trace_path"]).exists()
    assert result["stage_current"]
    assert result["stage_next"]
