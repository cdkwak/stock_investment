from stock_data.providers.cboe_vix_delayed_quote import CboeVixPayloadError, parse_payload
def test_requires_exact_identity_timestamp_delay_and_session():
 payload={"data":{"symbol":"_VIX","symbol_description":"CBOE Volatility Index","last_trade_price":"15.2","last_trade_time":"2026-08-21T03:00:00+00:00","is_delayed":True,"market_status":"OPEN"}}
 assert parse_payload(payload)["value"]==15.2
 payload["data"].pop("market_status")
 try: parse_payload(payload)
 except CboeVixPayloadError as error: assert str(error)=="SESSION_UNBOUND"
 else: raise AssertionError("must fail closed")
