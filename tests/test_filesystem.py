from pathlib import Path

from app.utils.filesystem import data_root, make_run_id, prepare_run_paths


def test_data_root_defaults_to_engine_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CICD_ENGINE_DATA_DIR", raising=False)
    assert data_root(tmp_path) == tmp_path


def test_run_paths_use_configured_data_root(monkeypatch, tmp_path: Path) -> None:
    engine_dir = tmp_path / "engine"
    mounted_root = tmp_path / "mounted-data"
    monkeypatch.setenv("CICD_ENGINE_DATA_DIR", str(mounted_root))

    run_id = make_run_id(engine_dir)
    paths = prepare_run_paths(engine_dir, run_id)

    assert paths["run_dir"].parent == mounted_root / "runs"
    assert paths["repo_dir"] == mounted_root / "workspace" / run_id / "repo"
    assert paths["logs_dir"].is_dir()
