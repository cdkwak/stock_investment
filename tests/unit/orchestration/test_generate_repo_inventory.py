from pathlib import Path

from scripts.maintenance.generate_repo_inventory import build_inventory, render_markdown


def test_inventory_is_bounded_and_does_not_read_data_files(tmp_path: Path) -> None:
    (tmp_path / "src" / "package").mkdir(parents=True)
    (tmp_path / "src" / "package" / "module.py").write_text("x = 1", encoding="utf-8")
    deep_data = tmp_path / "data" / "normalized" / "dataset" / "year=2026"
    deep_data.mkdir(parents=True)
    (deep_data / "data.parquet").write_bytes(b"must not be inventoried")
    (tmp_path / ".venv" / "ignored").mkdir(parents=True)

    inventory = build_inventory(tmp_path, max_depth=4)
    paths = [entry["path"] for entry in inventory["entries"]]

    assert "src/package/module.py" in paths
    assert "data/normalized" in paths
    assert "data/normalized/dataset" not in paths
    assert not any(path.startswith(".venv") for path in paths)
    assert inventory["classification"] == "GENERATED_LOCATION_INVENTORY_NOT_STATUS"


def test_markdown_labels_output_as_generated_not_status(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("", encoding="utf-8")
    rendered = render_markdown(build_inventory(tmp_path))

    assert "GENERATED_LOCATION_INVENTORY_NOT_STATUS" in rendered
    assert "README.md" in rendered
