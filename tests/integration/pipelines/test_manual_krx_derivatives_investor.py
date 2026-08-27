from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_data.audit.manual_krx_derivatives_investor import (
    audit_retained_inventory,
    build_inventory,
    retain_inventory,
)


def _write(root: Path, name: str, rows: list[str]) -> None:
    inbox = root / "docs/data/sources/krx/manual_inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    text = "일자,기관 합계,기타법인,개인,외국인 합계,전체\n" + "\n".join(rows) + "\n"
    (inbox / name).write_bytes(text.encode("cp949"))


def test_inventory_and_idempotent_retention(tmp_path: Path) -> None:
    _write(tmp_path, "data_선물순매수.csv", ['"1999/04/27","1.0","2.0","-3.0","0.0","0.0"'])
    _write(tmp_path, "data_2.csv", [
        '"1999/04/28","2.0","3.0","-5.0","0.0","0.0"',
        '"1999/04/27","1.0","2.0","-3.0","0.0","0.0"',
    ])
    report = build_inventory(tmp_path)
    assert report["file_count"] == 2
    assert report["physical_rows"] == 3
    assert report["unique_dates"] == 2
    assert report["overlap_date_count"] == 1
    assert report["target_assessment"]["normalized_writes"] is False
    first = retain_inventory(tmp_path)
    second = retain_inventory(tmp_path)
    assert first["status"] == "CREATED"
    assert second["status"] == "ALREADY_RECORDED"
    manifest = json.loads((tmp_path / first["manifest"]).read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 2
    assert all((tmp_path / item["landing_file"]).read_bytes() for item in manifest["files"])
    audit = audit_retained_inventory(tmp_path, first["inventory_sha256"], write=True)
    assert audit["result"] == "PASS_LANDING_ONLY_TARGET_INPUT_INCOMPLETE"
    assert (tmp_path / audit["audit_path"]).is_file()


def test_conflicting_overlap_fails(tmp_path: Path) -> None:
    _write(tmp_path, "a.csv", ['"1999/04/27","1","2","-3","0","0"'])
    _write(tmp_path, "b.csv", ['"1999/04/27","2","2","-4","0","0"'])
    with pytest.raises(ValueError, match="conflicting overlapping dates"):
        build_inventory(tmp_path)


@pytest.mark.parametrize(
    "body,error",
    [
        (b"", "header"),
        ("일자,기관 합계\n1999/04/27,1\n".encode("cp949"), "header"),
        ("일자,기관 합계,기타법인,개인,외국인 합계,전체\n1999/04/27,1,2,3,4,NaN\n".encode("cp949"), "non-finite"),
    ],
)
def test_malformed_files_fail(tmp_path: Path, body: bytes, error: str) -> None:
    inbox = tmp_path / "docs/data/sources/krx/manual_inbox"
    inbox.mkdir(parents=True)
    (inbox / "bad.csv").write_bytes(body)
    with pytest.raises(ValueError, match=error):
        build_inventory(tmp_path)
