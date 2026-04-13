# Smart Parking API - 프로젝트 분석 리서치

## 1. 시스템 개요

스마트 주차 관제 시스템의 **API 서버**로, CCTV 영상 기반 AI 주차면 탐지 결과를 수집하고, 주차장 상태를 모니터링하며, LED 전광판에 실시간 주차 정보를 표시하는 역할을 한다.

- **프레임워크**: FastAPI (Python 3.12, uvicorn)
- **ORM**: peewee-async (aiopg)
- **스케줄러**: APScheduler (백그라운드 워커)
- **패키지 매니저**: uv
- **렌더링**: Playwright (Chromium) + fabric.js (LED 컨텐츠 렌더링)

---

## 2. 필요한 컨테이너 / 외부 서비스 목록

### 2.1 Docker Compose에 정의된 컨테이너

| 컨테이너 | 이미지 | 포트 | 설명 |
|-----------|--------|------|------|
| `smart-parking-api` | `jdone/smart-parking-api:latest` | 5000 | 이 API 서버 자체 |

### 2.2 외부 의존 서비스 (별도 컨테이너/서버 필요)

| 서비스 | 현재 설정 주소 | 포트 | 프로토콜 | 역할 | 필수 여부 |
|--------|---------------|------|----------|------|-----------|
| **PostgreSQL** (+ PostGIS) | `192.168.0.201` | `55432` | TCP | 주 데이터베이스 (주차장, 주차면, CCTV, LED, 로그 등) | **필수** |
| **Redis** | `192.168.0.201` | `6379` | TCP | API 응답 캐시 (TTL 300초 기본) | **필수** |
| **Detector (주차면 탐지 AI)** | `192.168.200.37` | `3000` | HTTP | CCTV 영상 분석 → 주차면 점유 상태 반환 | **필수** |
| **NVR (영상 녹화 서버)** | `127.0.0.1` | `3000` | HTTP | CCTV 스냅샷(JPEG) 및 HLS 스트리밍 제공 | **필수** |
| **SPK API (통합플랫폼)** | `192.168.200.25` | `8080` | HTTP | 주차면 실제 점유 데이터 (비교 검증용) | 선택 |
| **Keycloak (인증 서버)** | `192.168.0.201` | `8888` | HTTP | OAuth2/OIDC 사용자 인증 (현재 `enabled: false`) | 선택 |
| **LED 전광판 (TCP)** | DB에 저장된 host/port | 각 장비별 | TCP (바이너리) | 주차 정보 표시 하드웨어 | 선택 |

### 2.3 에러 원인 분석: `Cannot connect to host 127.0.0.1:3000`

```
ERROR | worker.cctv_status - [cctv_status] CCTV(19) 스냅샷 이미지 요청 중 오류가 발생했습니다.
/ (ClientConnectorError) Cannot connect to host 127.0.0.1:3000 ssl:default
```

**원인**: `config.yaml`에서 NVR 설정이 `host: 127.0.0.1`, `port: 3000`으로 되어 있는데, 현재 시스템에 NVR 서버가 실행되지 않고 있다. `cctv_status` 워커가 60초마다 NVR에 스냅샷을 요청하지만, NVR이 없어서 TCP 연결 자체가 실패한다.

**해결 방법**:
- NVR 서버를 해당 주소에 구동하거나
- `config.yaml`의 `nvr.host`/`nvr.port`를 실제 NVR 서버 주소로 변경

---

## 3. 전체 데이터 흐름 (CCTV → 탐지 → DB → API)

### 3.1 아키텍처 다이어그램

```
┌──────────────────────────────────────────────────────────────────────┐
│                        물리 인프라 계층                                │
├──────────────┬───────────────────┬───────────────────────────────────┤
│  CCTV 카메라  │   NVR 서버         │   LED 전광판 (TCP)                 │
│  (물리장비)    │  127.0.0.1:3000   │   (각 장비별 host:port)            │
└──────┬───────┴────────┬──────────┴──────────────┬────────────────────┘
       │ 영상 스트림       │ JPEG/HLS 제공             │ 바이너리 이미지 수신
       ▼                 │                          │
┌──────────────────────┐ │                          │
│  Detector (AI 서버)   │ │                          │
│  192.168.200.37:3000 │ │                          │
│                      │ │                          │
│  CCTV 영상 → AI 분석  │ │                          │
│  → 주차면 점유 판단    │ │                          │
└──────────┬───────────┘ │                          │
           │              │                          │
           ▼              ▼                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    Smart Parking API (이 서버)                        │
│                    127.0.0.1:5000                                     │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    Background Workers                           │ │
│  │                                                                 │ │
│  │  ① space_status (5초)  ──→ Detector API 폴링                    │ │
│  │  ② cctv_status (60초)  ──→ NVR 스냅샷 요청                      │ │
│  │  ③ led_tcp_sender (1초) ──→ LED 전광판 이미지 전송               │ │
│  │  ④ data_compare (매시 정각) ──→ SPK vs Detector 비교             │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌─────────────────┐  ┌──────────────┐  ┌────────────────────────┐  │
│  │  REST API (/v1)  │  │ WebSocket    │  │  Swagger/ReDoc Docs    │  │
│  │  - parking       │  │ - LED 실시간  │  │  - /docs               │  │
│  │  - cctv          │  │   업데이트    │  │  - /redoc              │  │
│  │  - led           │  │              │  │                        │  │
│  │  - dashboard     │  │              │  │                        │  │
│  │  - file          │  │              │  │                        │  │
│  └────────┬────────┘  └──────────────┘  └────────────────────────┘  │
│           │                                                          │
└───────────┼──────────────────────────────────────────────────────────┘
            │
     ┌──────┴──────┐        ┌──────────────┐
     │ PostgreSQL  │        │    Redis      │
     │ + PostGIS   │        │  캐시 서버     │
     │ :55432      │        │  :6379        │
     └─────────────┘        └──────────────┘
```

### 3.2 상세 데이터 흐름

#### FLOW 1: 주차면 상태 감지 (핵심 파이프라인)

```
[CCTV 카메라] ──영상 스트림──→ [NVR] ──HLS/RTSP──→ [Detector AI 서버]
                                                         │
                                                    AI가 영상 분석
                                                    주차면별 점유 판단
                                                         │
                                                         ▼
                                              Detector REST API
                                              GET /parking/{seq}/space/list
                                                         │
                                                         │ (5초마다 폴링)
                                                         ▼
                                              [space_status 워커]
                                                         │
                                              ┌──────────┴──────────┐
                                              │                     │
                                              ▼                     ▼
                                      SpaceInfo 테이블         SpaceLog 테이블
                                      (현재 상태 갱신)          (변경 이력 기록)
                                              │
                                              ▼
                                     [REST API / WebSocket]
                                              │
                                              ▼
                                     [클라이언트 (웹/앱)]
```

**상세 단계**:

1. **CCTV → NVR**: 물리 CCTV 카메라가 영상을 NVR 서버로 전송
2. **NVR → Detector**: Detector AI 서버가 NVR의 HLS 스트림을 소비하여 실시간 영상 분석
3. **Detector 내부 처리**: AI 모델이 주차면별 점유 여부를 판단하고 내부 상태 업데이트
4. **API 서버 → Detector 폴링** (`space_status` 워커, 5초 간격):
   - `GET http://192.168.200.37:3000/parking/{parking_seq}/space/list` 호출
   - 응답 형태:
     ```json
     [
       {
         "unique_id": "1-1",
         "type": "common",       // common | disabled | compact
         "is_parked": true,       // 점유 여부
         "status_updated_at": "2026-04-09T16:00:00"
       }
     ]
     ```
5. **DB 비교 및 업데이트**:
   - 기존 `SpaceInfo` 테이블과 비교
   - `updated_at`이 DB보다 최신이고 `is_parked` 상태가 변경된 경우만 업데이트
   - 새로운 주차면이면 INSERT, 사라진 주차면이면 DELETE
   - 변경 시 `SpaceLog` 테이블에 이력 기록
6. **Redis 캐시**: API 응답 시 `@redis_cache` 데코레이터로 캐시 (TTL 300초)
7. **클라이언트 제공**: REST API (`GET /v1/parking/{seq}/status`)로 최종 데이터 제공

#### FLOW 2: CCTV 상태 모니터링

```
[cctv_status 워커] ──60초마다──→ [NVR 서버]
        │                          │
        │   GET /live/video{seq}.jpg?user=admin&password=admin
        │                          │
        │                    JPEG 스냅샷 반환
        │                          │
        ▼                          ▼
  스냅샷 이미지 분석 (numpy + PIL)
        │
        ├── 모든 픽셀이 rgb(16,16,68) 또는 rgb(68,16,16)
        │   → 카메라 연결 끊김 (NVR이 보내는 "No Signal" 색상)
        │   → status = ERROR
        │
        ├── 정상 이미지
        │   → status = NORMAL
        │
        ├── HTTP 연결 실패 (현재 에러)
        │   → status = ERROR
        │   → "Cannot connect to host 127.0.0.1:3000"
        │
        └── 상태 변경 시
            → CctvInfo 테이블 업데이트
            → FacilityLog 테이블에 로그 기록
```

**NVR 스냅샷 URL 패턴**:
```
{protocol}://{host}:{port}/live/video{cctv_seq}.jpg?user={username}&password={password}
```
예: `http://127.0.0.1:3000/live/video19.jpg?user=admin&password=admin`

**NVR "No Signal" 감지**:
- NVR은 카메라가 연결 해제되면 단색 이미지를 반환함
- `rgb(16, 16, 68)` 또는 `rgb(68, 16, 16)` = 카메라 미연결
- numpy로 모든 픽셀이 이 색인지 확인: `np.all(img_array == [16, 16, 68])`

#### FLOW 3: LED 전광판 표시

```
[led_tcp_sender 워커] ──1초마다──→
        │
        ├── 1. DB에서 주차면 현황 조회 (SpaceInfo)
        │
        ├── 2. 변수 매핑 생성
        │   ${1:remain_space} → 잔여 주차면 수
        │   ${1:total_space}  → 총 주차면 수
        │   ${1:name}         → 주차장 이름
        │   ... (타입별 세분화)
        │
        ├── 3. 컨텐츠 렌더링
        │   ├── 32x48 LED: PIL ImageDraw로 직접 BMP 생성
        │   └── 기타: Playwright + fabric.js로 캡처 → BMP
        │
        ├── 4. 만차 감지 (잔여 ≤ 2대)
        │   → "만차" 텍스트 (빨간색, 폰트 크기 축소)
        │
        ├── 5. 스케줄 확인 (ON/OFF 시간)
        │   → OFF 시간: 검은색 이미지 전송
        │
        └── 6. TCP 소켓으로 이미지 전송
            → STX(0xAA) + BUF_RST(0xB0) + SND_IMG(0xB1) + 이미지 데이터 + ETX
```

#### FLOW 4: 데이터 비교 검증 (매시 정각)

```
[data_compare 워커] ──매시 정각──→
        │
        ├── 1. DB SpaceInfo 조회 (Detector 기반 데이터)
        │
        ├── 2. SPK API 호출 (http://192.168.200.25:8080/spk/spkApi/usages/{seq})
        │   → 실제 센서/루프 기반 점유 데이터
        │
        ├── 3. 주차면별 비교
        │   Detector is_parked vs SPK is_parked
        │
        └── 4. Excel 파일 생성 (resources/data_compare/)
            → 시트별 주차장
            → 인식률(%) 계산
            → 색상 코딩 (빨강=점유, 초록=비점유, 파랑=일치)
```

---

## 4. 데이터베이스 스키마

### 4.1 테이블 구조

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   ParkingInfo    │     │    SpaceInfo      │     │    SpaceLog      │
├─────────────────┤     ├──────────────────┤     ├──────────────────┤
│ seq (PK, auto)  │◄────│ parking_seq (FK)  │     │ seq (PK, auto)   │
│ name            │     │ seq (PK, varchar) │◄────│ space_seq        │
│ address         │     │ type (enum int)   │     │ parking_seq      │
│ location (5179) │     │ is_parked (bool)  │     │ is_parked        │
│ created_at      │     │ updated_at        │     │ changed_at       │
│ updated_at      │     └──────────────────┘     └──────────────────┘
└─────────────────┘
        │
        │
        ▼
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│    CctvInfo      │     │     LedInfo       │     │  FacilityLog     │
├─────────────────┤     ├──────────────────┤     ├──────────────────┤
│ seq (PK, auto)  │     │ seq (PK, auto)   │     │ seq (PK, auto)   │
│ name            │     │ name             │     │ facility_type     │
│ parking_seq     │     │ parking_seq      │     │ facility_seq      │
│ status          │     │ location (5179)  │     │ parking_seq       │
│ created_at      │     │ type (tcp/hdmi)  │     │ level             │
│ updated_at      │     │ host / port      │     │ message           │
└─────────────────┘     │ width / height   │     │ created_at        │
                        │ status           │     └──────────────────┘
                        │ schedule (JSON)  │
                        │ content (JSON)   │     ┌──────────────────┐
                        │ event_content    │     │ LedHdmiTemplate  │
                        │ created/updated  │     ├──────────────────┤
                        └──────────────────┘     │ seq (PK, auto)   │
                                                 │ name / desc      │
                                                 │ zones (JSON)     │
                                                 │ medias (JSON)    │
                                                 │ created_at       │
                                                 └──────────────────┘
```

### 4.2 SpaceInfo.seq 형식

`{parking_seq}-{unique_code}` 예: `1-1`, `1-2`, `2-1`

### 4.3 ParkingSpaceType (주차면 타입)

| 값 | 의미 |
|----|------|
| 0 | COMMON (일반) |
| 1 | DISABLED (장애인) |
| 2 | ELECTRIC (전기차) |
| 3 | WOMAN (여성) |
| 8 | COMPACT (경차) |

---

## 5. 워커 스케줄 요약

| 워커 | 주기 | 대상 서비스 | 역할 |
|------|------|------------|------|
| `space_status` | 5초 | Detector (`192.168.200.37:3000`) | 주차면 점유 상태 수집 |
| `cctv_status` | 60초 | NVR (`127.0.0.1:3000`) | CCTV 연결 상태 확인 |
| `led_tcp_sender` | 1초 | LED 장비 (각 장비별 TCP) | 전광판 이미지 전송 |
| `data_compare` | 매시 정각 (cron) | SPK (`192.168.200.25:8080`) | 탐지 정확도 검증 |

---

## 6. API 엔드포인트 구조

```
/health                          → 헬스체크
/v1/dashboard/                   → 대시보드 요약
/v1/parking/{seq}/status         → 주차장 상태 (주차면, CCTV, LED)
/v1/parking/{seq}/fclt_logs      → 시설물 로그 조회
/v1/parking/{seq}/space          → 주차면 수동 상태 변경
/v1/cctv/preview/cctv{seq}/...   → HLS 스트리밍 프록시
/v1/led/                         → LED CRUD, 컨텐츠 관리
/v1/file/                        → 미디어 파일 업로드/다운로드
/ws/                             → WebSocket (LED 실시간 업데이트)
```

---

## 7. Redis 캐시 구조

- **라이브러리**: `redis.asyncio`
- **연결 설정**: `host=192.168.0.201`, `port=6379`, `max_connections=10`
- **캐시 키 패턴**: `{function_name}:{args}:{kwargs}`
- **기본 TTL**: 300초 (5분)
- **사용 위치**: `@redis_cache` 데코레이터로 API 응답 캐싱
- **동작**: 캐시 히트 시 Redis에서 JSON 역직렬화 후 반환, 미스 시 함수 실행 → 결과 JSON 직렬화 후 저장

---

## 8. 핵심 연동 포인트 정리

### 8.1 Detector ↔ NVR 관계

**중요**: Detector와 NVR은 서로 다른 역할이지만 같은 포트(3000)를 사용하고 있어 혼동하기 쉽다.

| 구분 | Detector | NVR |
|------|----------|-----|
| **역할** | AI 영상 분석 → 주차면 점유 판단 | CCTV 영상 녹화/스트리밍/스냅샷 |
| **주소** | `192.168.200.37:3000` | `127.0.0.1:3000` |
| **이 서버가 호출하는 API** | `GET /parking/{seq}/space/list` | `GET /live/video{seq}.jpg` |
| **호출 주기** | 5초 (space_status) | 60초 (cctv_status) |
| **데이터 방향** | Detector → API 서버 (주차면 상태) | NVR → API 서버 (스냅샷 이미지) |

### 8.2 데이터가 분석되는 과정 (NVR 없이는 불가능한가?)

**Detector 서버는 NVR과 독립적으로 동작할 수 있다.** 핵심 흐름:

1. **Detector가 CCTV 영상을 직접 수신** (NVR의 HLS 스트림 또는 CCTV RTSP 직접 연결)
2. **AI 분석 수행** → 내부적으로 주차면 상태 관리
3. **API 서버는 Detector API만 호출**하면 주차면 데이터를 받을 수 있음

따라서 **NVR이 없어도 Detector가 정상이면 주차면 탐지는 작동한다.** NVR은 다음 용도로만 사용:
- `cctv_status` 워커: CCTV 카메라 연결 상태 모니터링 (스냅샷으로 확인)
- CCTV HLS 프록시: 클라이언트에게 실시간 영상 스트리밍 제공

### 8.3 최소 필수 인프라

주차면 탐지 기능만 동작시키려면:

```
[필수]
1. PostgreSQL + PostGIS  → 데이터 저장
2. Redis                 → API 응답 캐시
3. Detector AI 서버       → 주차면 점유 데이터

[선택]
4. NVR                   → CCTV 상태 모니터링 + 영상 스트리밍
5. SPK API               → 탐지 정확도 검증
6. Keycloak              → 사용자 인증 (현재 비활성)
7. LED 전광판             → 물리 전광판 표시
```

---

## 9. 설정 파일 참조

### config.yaml 주요 항목

```yaml
scheduler:
  space_status_interval: 5    # Detector 폴링 주기 (초)
  cctv_status_interval: 60    # NVR 상태 확인 주기 (초)

detector:
  base_url: http://192.168.200.37:3000
  api_key:                     # Bearer 토큰 (선택)

nvr:
  proxy: true                  # API 서버를 통한 HLS 프록시 여부
  protocol: http
  host: 127.0.0.1              # ← 현재 에러 원인 (NVR 미실행)
  port: 3000
  username: admin
  password: admin

database:
  db_type: postgresql
  host: 192.168.0.201
  port: 55432
  name: smart_parking

redis:
  host: 192.168.0.201
  port: 6379

led:
  parking_full_threshold: 2    # 잔여 N대 이하 → 만차 표시
  parking_full_color: "#FF0000"

auth:
  enabled: false               # 인증 비활성화 상태
```

---

## 10. 파일 구조 요약

```
smart-parking-api/
├── app/
│   ├── main.py                          # FastAPI 앱, lifespan, 워커 등록
│   ├── config.py                        # 설정 로딩 (YAML + ENV)
│   ├── api/v1/endpoints/
│   │   ├── parking.py                   # 주차장 상태/로그 API
│   │   ├── cctv.py                      # HLS 프록시 API
│   │   ├── led.py                       # LED 관리 API
│   │   ├── dashboard.py                 # 대시보드 API
│   │   └── file.py                      # 파일 업로드 API
│   ├── workers/
│   │   ├── service.py                   # 워커 서비스 lifecycle
│   │   ├── scheduler.py                 # APScheduler 래퍼
│   │   └── tasks/
│   │       ├── base.py                  # 태스크 데코레이터 (로깅, 타이밍)
│   │       ├── space_status.py          # 주차면 상태 수집 (Detector)
│   │       ├── cctv_status.py           # CCTV 상태 확인 (NVR)
│   │       ├── led_tcp_sender.py        # LED 이미지 전송
│   │       └── data_compare.py          # 탐지 정확도 비교 (SPK)
│   ├── models/
│   │   ├── database.py                  # DB 모델 (Peewee ORM)
│   │   └── api/common.py               # 공통 API 모델 (Pydantic)
│   ├── utils/
│   │   ├── database.py                  # DB 연결 관리
│   │   ├── redis.py                     # Redis 캐시 유틸
│   │   ├── led_tcp_manager.py           # LED TCP 연결 관리
│   │   ├── ws_client_manager.py         # WebSocket 클라이언트 관리
│   │   ├── fabric_capture.py            # Playwright fabric.js 렌더링
│   │   └── modules/
│   │       ├── detector_api.py          # Detector API 클라이언트
│   │       ├── spk_api.py               # SPK API 클라이언트
│   │       └── led_tcp.py               # LED TCP 프로토콜 구현
│   └── ws/                              # WebSocket 엔드포인트
├── configs/
│   ├── config.yaml                      # 메인 설정 파일
│   └── .env                             # 환경 변수
├── docker-compose.yml                   # 컨테이너 정의
├── Dockerfile                           # 이미지 빌드
├── scripts/launch.sh                    # 서버 시작 스크립트
├── fonts/                               # LED 렌더링용 한글 폰트
├── resources/                           # 업로드 파일, 비교 결과 등
└── logs/                                # 애플리케이션 로그
```
