from datetime import datetime, timezone
from stock_data.providers.naver_mobile_home_observation import observation_for, parse_rows

NOW = datetime(2026, 8, 21, 5, 14, 30, tzinfo=timezone.utc)
HTML = b'''<a href="/domestic/index/KOSPI/total" data-nlog-click-cid="KOSPI">KOSPI 6,938.86 8. 21. 14:14 \xec\x8b\xa4\xec\x8b\x9c\xea\xb0\x84</a><a href="/domestic/index/KOSDAQ/total" data-nlog-click-cid="KOSDAQ">KOSDAQ 802.27 8. 21. 14:14 \xec\x8b\xa4\xec\x8b\x9c\xea\xb0\x84</a><a href="/marketindex/exchange/FX_USDKRW" data-nlog-click-cid="FX_USDKRW">USD 1,381.7 8. 21. 14:12 \xec\x8b\xa4\xec\x8b\x9c\xea\xb0\x84</a><a href="/marketindex/metals/GCcv1" data-nlog-click-cid="GCcv1">Gold 4,594.1 8. 21. 00:04 10\xeb\xb6\x84 \xec\xa7\x80\xec\x97\xb0</a><a href="/marketindex/energy/CLcv1" data-nlog-click-cid="CLcv1">WTI 86.2 8. 21. 00:04 10\xeb\xb6\x84 \xec\xa7\x80\xec\x97\xb0</a>'''

def test_independent_ssr_identity_gates() -> None:
    rows = parse_rows(HTML, recovered_at=NOW)
    assert rows['KOSPI']['accepted'] and rows['KOSDAQ']['accepted'] and rows['FX_USDKRW']['accepted']
    assert rows['GCcv1']['reason'] == 'VISIBLE_CONTRACT_UNIT_MISSING'
    assert rows['CLcv1']['reason'] == 'VISIBLE_CONTRACT_UNIT_MISSING'

def test_observation_is_display_only_and_api_zero_source() -> None:
    row = parse_rows(HTML, recovered_at=NOW)['KOSPI']; source = observation_for('KOSPI', row, recovered_at=NOW)
    assert source.value.unit == 'index points' and source.provenance.request_count == 1 and source.value.display_only and not source.value.pit_safe

def test_post_close_requires_direct_close_state_and_labels_finality() -> None:
    html = b'<a href="/domestic/index/KOSPI/total" data-nlog-click-cid="KOSPI">KOSPI 6,900.00 8. 21. 15:30 \xec\x9e\xa5\xeb\xa7\x88\xea\xb0\x90</a>'
    row = parse_rows(html, recovered_at=datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc), required_status="POST_CLOSE")['KOSPI']
    assert row['accepted'] and observation_for('KOSPI', row, recovered_at=datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)).value.finality.value == 'POST_CLOSE_SNAPSHOT'
