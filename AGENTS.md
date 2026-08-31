# 📌 Project Antigravity Engineering Guidelines & Rules

## 🚨 1. 절대 주의사항: HEADLESS 모드 사용 금지 (STRICTLY NO HEADLESS)
- **금지 이유**: 네이버의 WTM 보안 게이트웨이 및 봇 탐지 시스템은 Headless 환경(`navigator.webdriver`, Canvas/WebGL 핑거프린트 결여, 가상 디스플레이)을 즉시 감지하여 **HTTP 418 차단**, **로그인 강제 리다이렉트**, **빈 `__NEXT_DATA__` 응답**을 발생시킵니다.
- **필수 실행 규칙**:
  - 반드시 **우분투 데스크톱 Real GUI 화면 모드**(`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0`)로 구동해야 합니다.
  - 브라우저는 실제 GUI 윈도우로 구동되어 네이버의 봇 탐지를 우회합니다.
  - 향후 코드 수정, 리팩토링, 기능 추가 시 **어떠한 경우에도 `--headless` 플래그를 추가하거나 기본값으로 적용하지 마십시오.**

---

## 🏗️ 2. 아키텍처 및 모듈 분리 원칙 (Modular Architecture)
1. **Core Engine (`core/engine/`)**:
   - `supervisor.py`: N-Thread 멀티 워커 클러스터 오케스트레이터, 서킷 브레이커, 10분 유휴 스마트 대기.
   - `task_runner.py`: 단일 워커 라이프사이클 (임대 -> Fast Requeue 중복 검사 -> 캐시 조회 -> 크롤링 -> 결과 반환).
2. **Crawler Subsystem (`services/crawler/`)**:
   - `browser_process.py`: Chrome 프로세스 실행/종료 및 디스플레이 환경 바인딩.
   - `cdp_controller.py`: CDP 세션 주입, 세로형(430x780) 모바일 뷰포트 & 트래픽 계측.
   - `dom_navigator.py`: ackey 모바일 통검 진입, 가격비교 더보기 클릭, **실제 DOM 복제(Clone) 마우스 물리 클릭 페이징**.
   - `data_extractor.py`: `__NEXT_DATA__` SSR 파싱, 광고 필터링, 100% 타겟 ID 매칭.
   - `__init__.py`: 크롤링 통합 Facade.
3. **Runtime Data Isolation (`services/runtime/`)**:
   - 모든 동적 생성 데이터(프로필 풀 `profiles/`, 템플릿 `master_profile/`, 캐시 `keyword_cache/`, `browser_cache/`, 로그 `logs/`)는 반드시 `services/runtime/` 하위에 위치해야 합니다.
   - 프로젝트 루트 디렉토리에는 불필요한 런타임 폴더나 레거시 파일이 생성되지 않도록 청결을 유지하십시오.

---

## ⚡ 3. 성능 및 차단 회피 핵심 메커니즘
- **Non-blocking Fast Requeue**: 동일 키워드가 여러 워커에 중복 할당되면 브라우저를 띄워 대기하지 않고 0.001초 만에 `BLOCKED` 상태로 서버 큐 맨 뒤로 반납하여 즉시 다음 작업을 가져옵니다.
- **DOM 복제 물리 클릭**: 네이버 모바일 쇼핑 하단 페이지 버튼을 상단에 복제하여 마우스 물리 좌표로 클릭함으로써 페이지네이션 차단을 완벽하게 우회합니다.
- **Smart Standby (10분 대기)**: 서버 잔여 태스크가 0개일 때는 크롬 브라우저를 모두 닫고 600초간 유휴 대기하여 서버와 로컬 자원을 보호합니다.
