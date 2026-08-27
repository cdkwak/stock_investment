"""Private, read-only Toss holdings snapshot contract.

The contract deliberately excludes every account identifier and authentication
field.  Currency buckets are never merged without an explicit FX contract.
"""

TOSS_ACCOUNT_SNAPSHOT_SCHEMA_VERSION = 2
TOSS_ACCOUNT_SOURCE = "tossinvest_open_api"
TOSS_ACCOUNT_SOURCE_SPEC_VERSION = "1.2.14"
TOSS_ACCOUNT_OPERATION = "getHoldings"
TOSS_BUYING_POWER_OPERATION = "getBuyingPower"
TOSS_ACCOUNT_CURRENCIES = ("KRW", "USD")

TOSS_ACCOUNT_FORBIDDEN_KEYS = frozenset({
    "accountNo",
    "accountSeq",
    "account_number",
    "account_seq",
    "authorization",
    "access_token",
    "client_id",
    "client_secret",
    "token",
})

__all__ = [
    "TOSS_ACCOUNT_CURRENCIES",
    "TOSS_ACCOUNT_FORBIDDEN_KEYS",
    "TOSS_ACCOUNT_OPERATION",
    "TOSS_BUYING_POWER_OPERATION",
    "TOSS_ACCOUNT_SNAPSHOT_SCHEMA_VERSION",
    "TOSS_ACCOUNT_SOURCE",
    "TOSS_ACCOUNT_SOURCE_SPEC_VERSION",
]
