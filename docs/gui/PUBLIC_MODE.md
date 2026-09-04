# 웹 대시보드 게스트 모드

게스트 모드는 가족에게 시장·종목·검증 화면을 보여 주기 위한 별도 실행 모드다.
공개 인터넷 모드가 아니며, 기존 loopback/Tailscale 네트워크 제한과 설정된 PIN
잠금은 그대로 적용된다.

## 실행

기존 비공개 인스턴스(`127.0.0.1:8787`)를 그대로 둔 채 프로젝트 루트의 다른
PowerShell에서 다음 명령을 실행한다.

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe -m stock_web --public --port 8790
```

`--public`은 프로세스에 `STOCK_WEB_PUBLIC_MODE=1`을 설정한다. 같은 효과가 필요한
프로세스 관리자는 이 환경 변수를 직접 설정할 수 있다. 앱에서는
`app.state.public_mode`가 `True`가 된다. `.claude/launch.json`의 `guest` 구성도
같은 명령을 사용한다.

## 휴대전화에서 열기

휴대전화를 같은 tailnet에 연결한 뒤, 대시보드 PC에서 게스트 포트만 별도 HTTPS
Serve 항목으로 연결한다.

```powershell
tailscale serve --bg --https=8790 8790
tailscale serve status
```

상태 출력에 표시되는 `https://<기기>.<tailnet>.ts.net:8790/` 주소를 휴대전화에서
연다. PIN 파일이 있으면 먼저 로그인해야 한다. `tailscale funnel`이나 공인 주소
노출은 사용하지 않는다. 게스트 모드도 일반 사설 LAN 주소는 허용하지 않는다.

## 개인정보 경계

- `/account`는 안내문만 표시하며 계좌 API, 현금흐름, 순자산, 매매일지, 수동
  입력 및 모든 API 쓰기 요청은 `404 {"error":"guest mode"}`로 거부한다.
- 홈은 `sections.account = {"guest": true}`만 제공하고 보유량 기반 규칙과 보유
  표시는 만들지 않는다.
- 종목 화면은 `config/public_watchlist.json`의 고정 목록을 읽기 전용으로 사용한다.
  사용자 관심목록과 조건 파일은 읽거나 쓰지 않는다.
- 게스트 홈 캐시는 비공개 캐시와 다른 메모리 키를 사용한다. 스캐너도 사용자
  조건/파일 캐시를 사용하지 않으며 `artifacts/local_user` 또는 `data/local`에
  캐시를 기록하지 않는다.
- 오늘 브리핑·일정과 Research 현재 상태, 그리고 PIN 검증은 명시적인 예외로
  유지된다. 데이터 운영·자격증명 화면은 게스트에게 표시하지 않는다.
