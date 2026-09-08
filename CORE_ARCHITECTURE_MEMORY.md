# 🧠 TechB 핵심 아키텍처 및 기기별 역할 메모리

> ⚠️ **절대 변경 및 망각 금지 지침**
> 본 문서는 PC 워커와 실기기(폰팜) 워커의 근본적인 차이점 및 고속 크롤링 핵심 원칙을 영구 보존하기 위해 작성되었습니다.

---

## 1. 🖥️ PC 버전 (Stage 2: 1~5페이지 / 1~200위 초고속 탐색)

* **에이전트 프로필**: Chrome 데스크톱 + **`Nest Hub`** 에뮬레이션
  * `viewport`: `1024x600`, `devicePixelRatio: 2`
  * `userAgent`: `Mozilla/5.0 (Linux; Android) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.7977.64 Safari/537.36 CrKey/1.54.248666`
  * `clientHints`: `model: "Nest Hub"`, `formFactors: ["Tablet"]`, `platform: "Android"`, `platformVersion: "10"`
  * ⚠️ *임의로 모바일 기종명(SM-F711N 등)이나 가짜 Qualcomm WebGL을 스푸핑하면 네이버 WTM 차단(418)이 발생하므로 절대 금지.*

* **수집 방식 (Zero-Scroll & In-Browser API Fetch)**:
  * **스크롤 완전 불필요!**
  * 1페이지: 모바일 통검(`m.search.naver.com?ackey=...`) ➜ "가격비교 더보기" 경유 후 HTML SSR에 심겨진 `__NEXT_DATA__`에서 **0.001초 만에 즉시 추출**.
  * 2~5페이지: 브라우저 내부 세션(`credentials: 'include'`) 기반의 Next.js Data API (`/_next/data/{buildId}/search/all.json?pagingIndex=N&pagingSize=40`)로 **초고속 Data Fetch (0.5초/페이지, 총 3초 내외 완주)**.

* **수행 범위 및 한계**:
  * 1~5페이지(200위)까지는 **418 차단율 0%**로 완벽하게 수집됨.
  * 6페이지부터 네이버 WTM 연속 API 호출 임계치로 인해 418 차단이 발생하므로, **PC는 딱 1~5페이지(200위)까지만 검사**한다.
  * 200위 내에 없으면 즉시 `rank: 0`을 리턴하여 3단계 실기기 대기열로 인계한다.

---

## 2. 📱 실기기 버전 (Stage 3: 1~25페이지 / 1~1,000위 심층 전수 조사)

* **기기 환경**: 순정 안드로이드 기기 클러스터 (독립 모바일망 / Wi-Fi).
  * 기기 고유의 실제 네트워크와 리얼 브라우저 환경을 활용하므로 네이버의 고도화된 WTM 차단을 원천 회피.

* **수집 방식 (Zero-Scroll DOM 복제 물리 마우스 클릭)**:
  * **스크롤 완전 불필요!**
  * 네이버 모바일 쇼핑 페이지는 로드 시 하단 페이징 컨테이너(`div[class*="paginator"]`)가 이미 HTML DOM에 렌더링되어 있음.
  * 스크롤로 내려가지 않고, 하단 대상 페이징 버튼을 **`cloneNode(true)`로 화면 상단(`fixed`, `top: 120px`)에 완전 복제**한 뒤 리얼 물리 마우스 클릭 수행.
  * 상단 복제 버튼 클릭 즉시 SPA 라우팅 및 200 OK 원본 JSON 인터셉트로 실시간 수집.

* **수행 범위 및 한계**:
  * PC가 넘겨준 201위부터 최대 1,000위(25페이지)까지 심층 탐색 완주.
  * 1,000위까지 탐색 후에도 미발견 시 `rank: 1001`을 서버로 반환하여 최종 0위 확정.

---

## 3. 🚫 Zero-Scroll(노스크롤) 절대 원칙

1. 네이버 모바일 쇼핑은 초기 페이지 로드 시 상품 정보(`__NEXT_DATA__`)와 페이징 버튼 DOM이 이미 페이지 내에 전부 심겨져 있다.
2. 따라서 **PC든 실기기든 페이징 버튼을 누르기 위해 휠 스크롤(`window.scrollBy` 등)을 발생시키지 않는다.**
3. 즉시 DOM 탐색 ➜ 상단 복제(`cloneNode`) ➜ 클릭 방식으로 최고 속도와 안정성을 유지한다.
