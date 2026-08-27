from datetime import date
import json
from pathlib import Path

from scripts.manual.collect.collect_krx_vkospi_daily import collect_one_finalized_date
from stock_data.contracts.vkospi_daily import KR_VKOSPI_DAILY, KR_VKOSPI_RAW_DAILY
from stock_data.providers.krx_mdc.vkospi import frames_from_history
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.validation.vkospi_daily import validate_vkospi_daily, validate_vkospi_raw_daily


BODY = json.dumps({"output": [{
    "TRD_DD": "2026/08/14", "CLSPRC_IDX": "55.31", "PRV_DD_CMPR": "0.03",
    "UPDN_RATE": "0.05", "OPNPRC_IDX": "54.00", "HGPRC_IDX": "56.00",
    "LWPRC_IDX": "53.00", "FLUC_TP_CD": "1",
}]}).encode()


class Response:
    status_code = 200
    content = BODY


class Session:
    def __init__(self):
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        return Response()


def test_first_validation_calls_once_then_physical_checkpoint_noop(tmp_path: Path):
    root = tmp_path
    rows = json.loads(BODY)["output"]
    raw, normalized = frames_from_history(
        rows, collected_at="2026-08-15T00:00:00+00:00", landing_reference="historical",
        response_sha256="a" * 64,
    )
    write_dataset_atomic(raw, root / "data/raw/kr_vkospi_daily", KR_VKOSPI_RAW_DAILY,
                         validate_vkospi_raw_daily)
    write_dataset_atomic(normalized, root / "data/normalized/kr_vkospi_daily", KR_VKOSPI_DAILY,
                         validate_vkospi_daily)
    state = root / "data/state/kr_vkospi_daily.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "status": "HISTORICAL_RAW_AND_NORMALIZED_COMPLETE_PIT_LIMITED",
        "last_accepted_market_date": "2026-08-14",
    }), encoding="utf-8")
    session = Session()
    first = collect_one_finalized_date(
        root, market_date=date(2026, 8, 14), finality_confirmed=True, session=session,
    )
    assert first["business_calls"] == 1 and first["status"] == "NOOP_IDEMPOTENT"
    assert session.calls == 1
    second = collect_one_finalized_date(
        root, market_date=date(2026, 8, 14), finality_confirmed=True, session=Session(),
    )
    assert second["business_calls"] == 0 and second["status"] == "NOOP_IDEMPOTENT"
    assert second["retained_rows"] == 1
