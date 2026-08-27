# LS t8462 official-KRX screen gate — 2026-08-17

Status: **REVIEW_REQUIRED_AUTHENTICATED_SCREEN_UNAVAILABLE**

This is validation-only access evidence. It does not collect KRX data, change
any Raw/Normalized/Canonical dataset, or alter an LS semantic classification.

## Purpose and retained comparator

The intended comparison was the retained LS Open API `t8462` run
`20260814T165922Z_da488bc5fd024f559b0ef70f6d340e1f`, specifically the
KOSPI200-futures `U` response for the already retained dates 2026-01-02,
2026-07-31, 2026-08-13, and 2026-08-14.  The source run audit SHA-256 is
`9c2c61812c6f81a641e347bbf448a014cf1df78e57e2c186911d680d9d684ea3`; the
KOSPI200-futures `U` response SHA-256 is
`7f19ef75cde8d89dc4d4c74f1cc9fccd38b5de98ab6a0b45c42a0a3a800f76e9`.

The requested official comparator is KRX Data Marketplace Basic Statistics
screen **[15007] 투자자별 거래실적** (`MDC0201050302`), whose visible KRX search
result describes it as `통계 > 기본 통계 > 파생상품 > 거래실적 > 투자자별 거래실적`.

## Observed gate

The selected browser's KRX root visibly presented `로그인`; no authenticated
identity was displayed. The public site search located the official [15007]
screen, but opening that result opened the KRX login page before any screen
selector, unit, date, investor row, or data value became visible.

No credentials were entered, no account setting was changed, and no screen data
query or download was made. This is an access observation for this browser
session only; it does not contradict or replace any retained authenticated KRX
evidence.

## Result

| Item | Result |
|---|---|
| Official screen data queries | 0 |
| Downloads | 0 |
| KRX/LS Landing writes | 0 |
| Raw/Normalized/Canonical writes | 0 |
| Numeric cross-check | Not performed |
| D/N/U conclusion | None |

Because no same-grain official display values were visible, the existing
`t8462` statuses remain unchanged: `U` has only its existing bounded
multi-date amount/session inference, while `D` and `N` remain unresolved. No
unit or option-aggregate conclusion is added.

The machine-readable companion is
`artifacts/semantic_validation/ls_t8462_krx_authenticated_screen_gate_20260817.json`.
