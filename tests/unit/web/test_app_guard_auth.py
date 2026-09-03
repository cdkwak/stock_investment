from __future__ import annotations

from fastapi.testclient import TestClient

from stock_web.app import create_app
from stock_web.auth import SESSION_COOKIE_NAME, set_pin
from tests.unit.web import new_temp_root

TAILNET_CLIENT = ("100.107.40.4", 50000)
TAILNET_RELAY = {"X-Forwarded-For": "100.86.222.47"}


def _client(root, *, address=TAILNET_CLIENT) -> TestClient:
    return TestClient(
        create_app(root),
        base_url="https://dashboard.test",
        follow_redirects=False,
        client=address,
    )


def test_absent_pin_preserves_tailnet_access() -> None:
    client = _client(new_temp_root())
    assert client.get("/", headers=TAILNET_RELAY).status_code == 200


def test_pin_guard_redirects_html_rejects_api_and_allows_static() -> None:
    root = new_temp_root()
    set_pin(root, "2468", iterations=1_000)
    client = _client(root)

    page = client.get("/stocks?symbol=005930", headers=TAILNET_RELAY)
    api = client.get("/api/home", headers=TAILNET_RELAY)
    static = client.get("/static/auth.css", headers=TAILNET_RELAY)

    assert page.status_code == 303
    assert page.headers["location"] == "/login?next=%2Fstocks%3Fsymbol%3D005930"
    assert api.status_code == 401
    assert api.json() == {"error": "pin_required"}
    assert static.status_code == 200


def test_correct_pin_sets_secure_cookie_and_unlocks_tailnet_page() -> None:
    root = new_temp_root()
    set_pin(root, "2468", iterations=1_000)
    client = _client(root)

    login = client.post(
        "/login",
        headers=TAILNET_RELAY,
        data={"pin": "2468", "next": "/stocks?symbol=005930"},
    )

    assert login.status_code == 303
    assert login.headers["location"] == "/stocks?symbol=005930"
    set_cookie = login.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=2592000" in set_cookie
    assert client.get("/", headers=TAILNET_RELAY).status_code == 200

    logout = client.post("/logout", headers=TAILNET_RELAY)
    assert logout.status_code == 303
    assert "max-age=0" in logout.headers["set-cookie"].lower()


def test_wrong_pin_five_times_locks_client() -> None:
    root = new_temp_root()
    set_pin(root, "2468", iterations=1_000)
    client = _client(root)

    for _ in range(4):
        response = client.post(
            "/login", headers=TAILNET_RELAY, data={"pin": "nope", "next": "/"},
        )
        assert response.status_code == 401
        assert "PIN이 맞지 않습니다" in response.text

    locked = client.post(
        "/login", headers=TAILNET_RELAY, data={"pin": "nope", "next": "/"},
    )
    assert locked.status_code == 429
    assert "잠시 후 다시 시도하세요" in locked.text
    still_locked = client.post(
        "/login", headers=TAILNET_RELAY, data={"pin": "2468", "next": "/"},
    )
    assert still_locked.status_code == 429


def test_loopback_is_never_redirected_and_public_is_still_forbidden() -> None:
    root = new_temp_root()
    set_pin(root, "2468", iterations=1_000)

    assert _client(root, address=("127.0.0.1", 50000)).get("/").status_code == 200
    public = _client(root, address=("8.8.8.8", 50000))
    assert public.get("/").status_code == 403
    assert public.get("/static/auth.css").status_code == 403


def test_tampered_cookie_is_rejected_and_external_next_is_not_allowed() -> None:
    root = new_temp_root()
    set_pin(root, "2468", iterations=1_000)
    client = _client(root)
    login = client.post(
        "/login",
        headers=TAILNET_RELAY,
        data={"pin": "2468", "next": "https://example.com/escape"},
    )
    assert login.headers["location"] == "/"
    original = client.cookies.get(SESSION_COOKIE_NAME)
    assert original

    tampered = _client(root)
    rejected = tampered.get(
        "/",
        headers={**TAILNET_RELAY, "Cookie": f"{SESSION_COOKIE_NAME}={original}0"},
    )
    assert rejected.status_code == 303
    assert rejected.headers["location"] == "/login?next=%2F"


def test_login_page_has_required_korean_copy() -> None:
    root = new_temp_root()
    set_pin(root, "2468", iterations=1_000)

    page = _client(root).get("/login", headers=TAILNET_RELAY)

    assert page.status_code == 200
    assert "대시보드 잠금" in page.text
    assert "로컬 PC에서는 PIN이 필요하지 않습니다" in page.text
    assert '>열기</button>' in page.text
