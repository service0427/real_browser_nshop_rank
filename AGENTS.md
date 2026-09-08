# 📘 TechB 네이버 순위 수집 & 분산 태스크 API 통합 가이드

본 문서는 **1단계(서버 고속 패킷) ➡️ 2단계(PC 브라우저 워커) ➡️ 3단계(완전 실기기 워커)**로 이어지는 **3단계 계층형 순위 수집 파이프라인(3-Tier Pipeline)**의 API 할당 및 리턴 규격과 외부 조회 API 명세서입니다.

---

## 🧠 [핵심 원칙 및 아키텍처 메모리 (절대 변경 및 망각 금지)]

### 1. 🖥️ PC 버전 (Stage 2: 1~5페이지 / 1~200위 초고속 탐색)
- **에이전트 프로필**: 반드시 크롬 에뮬레이터 **`Nest Hub`** (`1024x600`, `CrKey/1.54.248666`, Android 10) 고정!
  - ⚠️ 임의로 기종명을 변경하거나 WebGL을 가짜 스푸핑하면 네이버 WTM 지문 불일치로 418 차단이 발생하므로 절대 금지.
- **수집 방식 (초고속 Zero-Scroll)**:
  - 스크롤을 사용할 필요가 전혀 없다! 1페이지는 HTML SSR에 심겨진 `__NEXT_DATA__`에서 0.001초 만에 즉시 파싱.
  - 2~5페이지(200위)는 브라우저 내부 세션(`credentials: 'include'`) 기반의 Next.js Data API (`/_next/data/{buildId}/search/all.json?pagingIndex=N`)로 초고속(페이지당 0.5초, 총 3초 내외) Data Fetch 처리.
- **역할 및 범위 한계**:
  - 1~5페이지까지는 418 차단이 0%로 완벽 작동함.
  - 6페이지부터 WTM 세션 연속 호출 한도로 418 차단이 발생하므로, **PC는 딱 1~5페이지(200위)까지만 처리**하고 200위 밖이면 즉시 `rank: 0`을 리턴하여 3단계 실기기로 인계한다.

### 2. 📱 실기기 버전 (Stage 3: 1~25페이지 / 1~1,000위 심층 전수 조사)
- **기기 환경**: 순정 안드로이드 실기기 클러스터 (모바일망 / 독립 Wi-Fi).
- **수집 방식 (Zero-Scroll DOM 복제 물리 클릭)**:
  - 스크롤을 하지 않아도 네이버 모바일 쇼핑 페이지 로드 시 하단 페이징 DOM 버튼이 이미 렌더링되어 있다!
  - 스크롤 지연을 완전히 배제하기 위해, 하단 페이징 DOM 버튼을 `cloneNode(true)`로 화면 상단(`fixed`, `top: 120px`)에 완전 복제 생성하고 리얼 물리 마우스 클릭을 수행한다.
- **역할 및 범위 한계**:
  - 201위부터 최대 1,000위(25페이지)까지 심층 탐색 완주.
  - 1,000위 밖이면 `rank: 1001`을 서버로 전송하여 최종 0위를 확정한다.

### 3. 🚫 스크롤 금지 원칙 (Zero-Scroll Principle)
- 네이버 쇼핑 모바일은 HTML 내 `__NEXT_DATA__` 및 페이징 DOM이 초기 로드 시 전부 심겨져 있으므로, **페이징을 위해 임의의 휠 스크롤(`window.scrollBy` 등)을 발생시키지 않는다**.

---

## 🏗️ 1. 3단계 계층형 수집 파이프라인 개요

```
[전체 슬롯 (약 3,500개)]
   │
   ▼
[1단계] 서버 고속 패킷 탐색 (m.search.naver.com 1~20위)
   ├─► 🟢 1~20위 포착 ──────────────► [최종 순위 확정] (외부 PUSH: N등)
   └─► ⚪ 20위 밖 (200 OK 수신) ──────► 2단계 PC 워커 대기열 진입
          │
          ▼
[2단계] PC 브라우저 워커 (worker=pc, 1~200위 고속 탐색)
   ├─► 🟢 21~200위 포착 ─────────────► [최종 순위 확정] (외부 PUSH: N등)
   └─► ⚪ 200위 밖 (rank=0 리턴) ───► 3단계 실기기 대기열 자동 인계 (외부 싱크 NULL 유지)
          │
          ▼
[3단계] 완전 실기기 워커 (worker=mobile, 201~1,000위 심층 탐색)
   ├─► 🟢 201~1,000위 포착 ──────────► [최종 순위 확정] (외부 PUSH: N등)
   └─► ⚪ 1,000위 밖 (rank=1001 리턴) ─► ⭐️ [최종 0위 확정 검수 완료] (외부 PUSH: 0등 확정 전송)
```

> 🛡️ **기기 유휴 방지 안전장치 (Auto-Fallback)**:
> - **PC만 켜진 경우**: 2단계 작업이 끝나면 아직 실기기가 검사 안 한 3단계 대기 슬롯까지 PC 워커가 이어서 계속 검사 지원!
> - **실기기만 켜진 경우**: 2단계 PC 작업 유무와 상관없이 실기기가 전체 0위 슬롯을 즉시 1,000위까지 검사!
> - **둘 다 켜진 경우**: 1-2-3단계 황금 분업으로 초고속 병렬 처리!

---

## 🔌 2. 분산 태스크 큐 API (Port: 9003)

- **서버 기본 주소**: `http://114.207.112.172:9003`
- **통신 프로토콜**: HTTP REST (JSON)
- **독점 락(Lease)**: 작업 할당 시 기본 **5분(300초)** 동안 타 기기에 중복 할당 방지 (서버 고정)

---

### [2-1] 작업 할당 (Task Lease)

대기 중인 0순위 작업 1개를 가져오며 서버 내부에서 5분간 독점 락을 겁니다.

- **Method**: `GET`
- **URL**: `http://114.207.112.172:9003/api/v1/task`

#### 요청 쿼리 파라미터
| 파라미터명 | 타입 | 필수 여부 | 기본값 | 설명 |
| :--- | :---: | :---: | :---: | :--- |
| **`worker`** | `string` | **필수 권장** | `all` | **`pc`**: 2단계 PC 브라우저 워커 (1차 패킷 0위 슬롯 우선 할당 / 소진 시 실기기 슬롯 Fallback)<br>**`mobile`**: 3단계 실기기 워커 (PC 200위 밖 슬롯 최우선 할당 / 소진 시 일반 0위 슬롯 Fallback)<br>**`all`**: 공용 |
| **`service`** | `string` | 선택 | `shop` | `shop` (쇼핑) 또는 `place` (플레이스) |

#### 응답 (200 OK - 작업 있음)
```json
{
  "success": true,
  "has_task": true,
  "task_id": 61031864,
  "service": "shop",
  "worker": "pc",
  "keyword": "주차장코너보호대",
  "keyword_total_count": 8,
  "keyword_remaining_count": 5,
  "total_remaining_tasks": 850,
  "target": "89264888169",
  "naver_search_url": "https://m.search.shopping.naver.com/search/all?query=주차장코너보호대"
}
```
- `keyword_total_count`: 오늘자 전체 중 이 검색어에 등록된 **유니크 상품 총 개수** (단독이면 1, 다수면 2 이상)
- `keyword_remaining_count`: 이 검색어에서 **아직 브라우저 검사가 남은 유니크 상품 수**
- `total_remaining_tasks`: 해당 워커가 처리해야 할 **전체 0순위 잔여 작업 수**

#### 응답 (200 OK - 대기 작업 없음)
```json
{
  "success": true,
  "has_task": false,
  "worker": "pc",
  "message": "현재 대기 중인 [pc] 0순위 작업이 없습니다."
}
```

---

### [2-2] 순위 결과 제출 및 상태 리턴 (Task Return)

탐색 결과(순위 포착, 200위 밖, 1,000위 밖 최종 0위) 또는 클라이언트 오류/차단을 리턴합니다.

- **Method**: `POST`
- **URL**: `http://114.207.112.172:9003/api/v1/task/return`
- **Header**: `Content-Type: application/json`

---

#### 🟢 케이스 1: 순위 포착 (1 ~ 1,000위 발견 시)
2단계(PC) 또는 3단계(실기기)에서 상품을 찾았을 때 전송합니다.

```json
{
  "task_id": 61031864,
  "service": "shop",
  "worker": "pc",
  "rank": 14,
  "product": {
    "productName": "고탄성 주차장 코너 보호대",
    "mallName": "디테일애드",
    "lowPrice": 12900,
    "imageUrl": "https://shopping-phinf.pstatic.net/main_8926488/89264888169.jpg",
    "reviewCount": 42,
    "scoreInfo": 4.85,
    "nvMid": "89264888169",
    "brand": "디테일애드",
    "category": "생활/건강>안전용품>코너가드"
  }
}
```

##### 응답 (200 OK)
```json
{
  "success": true,
  "message": "[주차장코너보호대] 순위 포착 완료 (14위)",
  "task_id": 61031864,
  "worker": "pc",
  "rank": 14,
  "browser_rank": 14,
  "saved_metadata": true,
  "updated_at": "2026-09-03 16:00:00"
}
```

---

#### ⚪ 케이스 2: 2단계 PC 워커 200위 밖 (3단계 실기기 대기 유지)
PC 워커가 200위(약 5페이지)까지 탐색했으나 상품이 없을 때 전송합니다.

```json
{
  "task_id": 61031864,
  "service": "shop",
  "worker": "pc",
  "rank": 0
}
```

##### 응답 (200 OK)
```json
{
  "success": true,
  "message": "[주차장코너보호대] PC 200위 검사 완료 (200위 밖 -> 실기기 1,000위 심층 대기)",
  "task_id": 61031864,
  "worker": "pc",
  "rank": 0,
  "browser_rank": 0,
  "saved_metadata": false,
  "updated_at": "2026-09-03 16:00:00"
}
```
> 📌 **서버 동작**: `rank`를 0위로 섣불리 확정하지 않고 **실기기 대기열로 자동 전이**하며, **외부 마케팅 DB 싱크 시 `NULL` 상태를 유지**합니다.

---

#### ⭐️ 케이스 3: 3단계 완전 실기기 1,000위 밖 (최종 0위 확정 검수 완료)
실기기 워커가 1,000위(약 25페이지)까지 전수 탐색했으나 상품이 없을 때 전송합니다.

```json
{
  "task_id": 61031864,
  "service": "shop",
  "worker": "mobile",
  "rank": 1001
}
```
*(또는 `worker: "mobile", rank: 0` 전송 시 서버가 자동으로 1001등 최종 0위로 확정)*

##### 응답 (200 OK)
```json
{
  "success": true,
  "message": "[주차장코너보호대] 실기기 1,000위 전수 조사 완료 -> 최종 0위(순위권 밖) 확정",
  "task_id": 61031864,
  "worker": "mobile",
  "rank": 0,
  "browser_rank": 1001,
  "saved_metadata": false,
  "updated_at": "2026-09-03 16:00:00"
}
```
> 📌 **서버 동작**: 실기기 1,000위 검수 완료(`browser_rank = 1001`)를 마킹하며, **외부 마케팅 DB로 PUSH할 때 확실하게 `0등`으로 확정 전송**합니다!

---

#### 🚨 케이스 4: 클라이언트 차단 / 에러 발생 (Re-queue)
기기 차단(418), 브라우저 크래시, 통신 오류 발생 시 전송합니다.

```json
{
  "task_id": 61031864,
  "status": "BLOCKED",
  "error_message": "HTTP 418 차단 감지"
}
```

##### 응답 (200 OK)
```json
{
  "success": true,
  "requeued": true,
  "message": "[주차장코너보호대] 차단/오류 보고 접수. 순번을 맨 뒤로 이동했습니다.",
  "task_id": 61031864,
  "error_reported": "HTTP 418 차단 감지"
}
```

---

## 🔄 3. 외부 마케팅 DB 동기화(PUSH) 정책

외부 마케팅 DB(`ad_slots`, `rank_history`)로 10분 주기 증분 PUSH될 때 적용되는 안전 정책입니다:

| 순위 상태 | 조건 | 외부 DB PUSH 반영 값 | 설명 |
| :---: | :--- | :---: | :--- |
| **실제 순위** | `1 <= rank <= 1000` | **`N등`** | 어떠한 단계에서든 순위 포착 시 즉시 반영 |
| **진행 중 (임시)** | `rank == 0` & 실기기 1001 미검증 | **`스킵 (NULL 유지)`** | 1~2단계 진행 중에는 섣부른 0위 푸시 방지 |
| **최종 0위 확정** | `rank == 1001` (실기기 검수 완료) | **`0등` (확정)** | 실기기가 1,000위 전수 조사를 마쳤을 때만 0등 푸시 |

---

## 🌐 4. 외부 순위 실시간 조회 API (Port: 9000)

외부 대행사 및 시스템에서 현재 DB의 최종 확정 순위를 조회할 때 사용합니다.

- **Endpoint**: `GET http://114.207.112.172:9000/api/v1/rank/shop`
- **Query Parameters**:
  - `keyword` (필수): 검색 키워드
  - `target` (필수): 카탈로그 MID 또는 스마트스토어 상품번호

#### 응답 예시
```json
{
  "rank": 14,
  "checked_at": "2026-09-03 16:00:00",
  "product": {
    "product_name": "고탄성 주차장 코너 보호대",
    "store_name": "디테일애드",
    "price": 12900,
    "image_url": "https://shopping-phinf.pstatic.net/main_8926488/89264888169.jpg",
    "review_count": 42,
    "score": 4.85,
    "updated_at": "2026-09-03 16:00:00"
  },
  "history": {
    "16:00:00.000": 14
  }
}
```

---

## 🔍 5. 스마트스토어 상품 상세 분석 API (Port: 9002)

스토어 상품의 카탈로그 MID(`syncNvMid`) 및 메타데이터를 정밀 조회할 때 사용합니다.

- **Endpoint**: `GET http://114.207.112.172:9002/api/v1/product/inspect`
- **Query Parameters**:
  - `url` (필수): 스마트스토어/브랜드스토어 상품 URL
  - `refresh` (선택, 기본: `false`): `true` 설정 시 24시간 캐시 무시하고 실시간 재수집

#### 응답 예시
```json
{
  "success": true,
  "product_id": 84418330317,
  "sync_nv_mid": "58509155869",
  "product_name": "고탄성 주차장 코너 보호대",
  "store_name": "디테일애드",
  "sale_price": 12900,
  "discounted_price": 12900,
  "discount_ratio": 0,
  "category": "생활/건강>안전용품>코너가드",
  "review_count": 42,
  "score": 4.85,
  "image_url": "https://shopping-phinf.pstatic.net/main_8926488/89264888169.jpg"
}
```
