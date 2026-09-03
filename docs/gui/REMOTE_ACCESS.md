# 웹 대시보드 원격 접속

웹 대시보드는 로컬 PC의 loopback 주소와 Tailscale 주소(`100.64.0.0/10`,
`fd7a:115c:a1e0::/48`)에서만 요청을 받습니다. 공개 주소는 PIN 설정 여부와
관계없이 `403`으로 거부됩니다. `tailscale serve`를 쓰는 경우 전달 경로의 모든
주소가 사설 허용 범위일 때만 `X-Forwarded-For`를 신뢰합니다.
세션 쿠키는 `Secure`이므로 원격 기기는 `tailscale serve` 등의 HTTPS 주소로
접속해야 로그인 상태가 유지됩니다.

## PIN 설정

프로젝트 루트에서 다음 명령을 실행하고 4~12자의 PIN을 프롬프트에 입력합니다.
입력값은 화면에 표시되지 않습니다.

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe scripts\manual\web_pin.py set
```

자동화에서는 PIN 한 줄을 표준 입력으로 전달하는 `set --pin-stdin`을 사용할 수
있지만, 명령줄 인수나 로그에는 PIN을 넣지 마십시오. 설정과 교체는
`data/local/web_pin.json`에 PBKDF2-SHA256 해시만 원자적으로 저장합니다. PIN을
해제하려면 다음 명령을 실행합니다.

```powershell
.venv\Scripts\python.exe scripts\manual\web_pin.py clear
```

PIN 파일이 없으면 기존 동작과 동일합니다. 파일이 있으면 loopback이 아닌
Tailscale 클라이언트의 HTML 요청은 로그인 화면으로 이동하고 API 요청은
`401 {"error":"pin_required"}`를 반환합니다. 로그인 세션은 설치별 로컬 비밀로
서명한 `Secure`, `HttpOnly`, `SameSite=Lax` 쿠키이며 30일간 유효합니다. 실패가
클라이언트별 5회 누적되면 해당 대시보드 프로세스에서 10분간 로그인이
잠깁니다. 대시보드를 재시작하지 않아도 PIN 설정·교체·해제가 반영됩니다.

## 보호 범위

PIN은 Tailscale에서 들어오는 **읽기 화면과 읽기 API**에 추가 잠금을 제공합니다.
Tailscale 자체의 기기 인증이나 ACL을 대체하지 않으며, 공개 인터넷 접속을
허용하지도 않습니다. 로컬 PC(loopback)는 PIN을 요구하지 않습니다. 쓰기
엔드포인트는 PIN 로그인 여부와 무관하게 기존처럼 loopback 전용이므로,
Tailscale 클라이언트에서는 계속 사용할 수 없습니다.
