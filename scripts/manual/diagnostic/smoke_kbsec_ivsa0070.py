"""One-shot, read-only IVSA0070 smoke. Never prints secrets or raw responses."""
from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

from stock_data.pipelines.kbsec_snapshot import store_kb_market_summary_response
from stock_data.providers.kbsec.client import (
    KBSecBusinessError,
    KBSecClient,
    KBSecHTTPError,
    KBSecResponseError,
)
from stock_data.providers.kbsec.market_summary import normalize_market_summary


ROOT = Path(__file__).resolve().parents[3]
REQUIRED = ("KBSEC_BASE_URL", "KBSEC_APP_KEY", "KBSEC_APP_SECRET")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-only", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", override=False)
    readiness = {name: bool(os.getenv(name, "").strip()) for name in REQUIRED}
    if not all(readiness.values()):
        print(json.dumps({"status": "LIVE_NOT_READY", "credentials": readiness}))
        return 2

    client = KBSecClient()
    try:
        client.access_token()
    except KBSecHTTPError as error:
        details = error.details
        print(json.dumps({"status": "TOKEN_HTTP_ERROR",
            "http_status": details.http_status if details else None,
            "content_type": details.content_type if details else None,
            "response_is_json": details.response_is_json if details else None,
            "error_code": details.error_code if details else None,
            "error_message": details.error_message if details else None,
            "result_code": details.result_code if details else None,
            "result_message": details.result_message if details else None,
            "process_code": details.process_code if details else None,
            "process_message": details.process_message if details else None,
            "text_excerpt": details.text_excerpt if details else None}, ensure_ascii=False))
        return 4
    except KBSecBusinessError as error:
        print(json.dumps({"status": "TOKEN_REJECTED", "http_status": error.http_status,
            "result_code": error.result_code, "result_message": error.result_message,
            "process_code": error.process_code, "process_message": error.process_message}, ensure_ascii=False))
        return 3
    if args.token_only:
        print(json.dumps({"status": "TOKEN_OK"}))
        return 0

    fixture = json.loads((ROOT / "tests/fixtures/kbsec_ivsa0070.json").read_text(encoding="utf-8"))
    collected_at = datetime.now(timezone.utc)
    try:
        response = client.market_summary()  # cached token; exactly one IVSA0070 request
    except KBSecHTTPError as error:
        details = error.details
        print(json.dumps({"status": "IVSA0070_HTTP_ERROR",
            "http_status": details.http_status if details else None,
            "content_type": details.content_type if details else None,
            "response_is_json": details.response_is_json if details else None,
            "result_code": details.result_code if details else None,
            "result_message": details.result_message if details else None,
            "process_code": details.process_code if details else None,
            "process_message": details.process_message if details else None}, ensure_ascii=False))
        return 5
    except KBSecBusinessError as error:
        print(json.dumps({"status": "IVSA0070_REJECTED", "http_status": error.http_status,
            "result_code": error.result_code, "result_message": error.result_message,
            "process_code": error.process_code, "process_message": error.process_message}, ensure_ascii=False))
        return 6
    except KBSecResponseError as error:
        print(json.dumps({"status": "IVSA0070_SCHEMA_ERROR", "error": str(error)[:160]}, ensure_ascii=False))
        return 7
    live_body = response.data_body
    fixture_body = fixture["dataBody"]
    frames = normalize_market_summary(response, collected_at=collected_at)

    numeric_failure = None
    try:
        counts = store_kb_market_summary_response(ROOT, response=response, collected_at=collected_at)
    except Exception as error:
        numeric_failure = type(error).__name__ + ": " + str(error)[:160]
        raise

    null_fields = {
        name: sorted(frame.columns[frame.isna().any()].tolist())
        for name, frame in frames.items()
    }
    section_counts = {name: len(live_body.get(name, [])) if isinstance(live_body.get(name, []), list) else None
                      for name in ("out2", "out3", "out4", "out5")}
    report = {
        "status": "OK", "http_status": response.http_status,
        "result_code": response.result_code, "process_code": response.process_code,
        "result_message": response.result_message,
        "process_message": response.process_message,
        "inq_dy_tm": str(live_body.get("inq_dy_tm", "")),
        "market_date": next(iter(frames.values())).iloc[0]["market_date"],
        "sections_present": {name: name in live_body for name in ("out2", "out3", "out4", "out5")},
        "section_counts": section_counts, "normalized_counts": counts,
        "extra_live_fields": sorted(set(live_body) - set(fixture_body)),
        "fixture_only_fields": sorted(set(fixture_body) - set(live_body)),
        "null_fields": null_fields, "numeric_parsing_failure": numeric_failure,
        "inq_dy_tm_length": len(str(live_body.get("inq_dy_tm", ""))),
    }
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
