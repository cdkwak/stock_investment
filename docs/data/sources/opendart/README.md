# OpenDART

## Status

- Project status: `PILOT` for free-issue corporate-action observations.
- Accepted use: bounded disclosure searches and specific corporate-action endpoints.

## Official reference

- [OpenDART API guide](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS001)

Checked-in pilot endpoints:

- `https://opendart.fss.or.kr/api/list.json`
- `https://opendart.fss.or.kr/api/fricDecsn.json`
- `https://opendart.fss.or.kr/api/pifricDecsn.json`

## Authentication

- Environment variable: `OPENDART_API_KEY`
- API query key: `crtfc_key`

## Safe read example

```python
import os
import requests

LIST_URL = "https://opendart.fss.or.kr/api/list.json"
params = {
    "crtfc_key": os.environ["OPENDART_API_KEY"],
    "corp_code": "<8-digit-corp-code>",
    "bgn_de": "20260101",
    "end_de": "20260819",
    "page_no": 1,
    "page_count": 100,
}
response = requests.get(LIST_URL, params=params, timeout=(3.05, 10))
response.raise_for_status()
payload = response.json()
if not isinstance(payload, dict) or "status" not in payload:
    raise ValueError("OpenDART status is missing")
```

Never print the full URL. Validate OpenDART `status` and `message`, pagination,
corporation identity, receipt number, event dates, and documented no-data codes.

## Project route

- Provider: `src/stock_data/providers/opendart_free_issue.py`
- Pilot: `scripts/manual/pilot/pilot_opendart_free_issue.py`
- Identity policy: [Corporate Action Canonical Identity](../../research/active/CORPORATE_ACTION_CANONICAL_IDENTITY.md)

## Boundaries

- A disclosure's presence today is not proof that a complete historical PIT event set existed earlier.
- Do not merge issuer codes by name or ticker alone.
- Do not infer event status or effective date from an unrelated disclosure field.
