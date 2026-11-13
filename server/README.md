# Statio 빈자리 서버

주변 주차장, 실시간·예상 빈자리, P단 순간의 주정차 위험을 돌려주는 API예요. Python 기본 라이브러리만 써서 따로 설치할 게 없어요.

> **2026-10-22 구조 결정:** 제출하는 앱은 이 서버 없이 APK 하나로 동작해요. 이 서버와 `parking_core.py`는 앱에 넣을 데이터를 만들고, Kotlin으로 옮긴 로직이 같은 답을 내는지 대조하는 기준으로 써요. 개발 중 에뮬레이터에서 바로 붙여 볼 때도 쓸 수 있어요.

## 실행
```bash
python3 server/app.py                 # http://127.0.0.1:8787
python3 server/app.py --port 9000
python3 -m unittest discover -s server   # 단위 테스트
```
- 먼저 `data/parking_risk.db`(`python3 datapipe/build_risk_db.py`)가 있어야 해요.
- 실시간 값은 수집기 DB `data/seoul_parking.db`를 읽어요. 수집기가 돌고 있어야 값이 최신이에요.
- **Pleos 에뮬레이터에서는 호스트 PC를 `http://10.0.2.2:8787`로 불러요.**

## API

### `GET /health`
서버 상태와 실시간 데이터의 마지막 갱신 시각을 돌려줘요.
```json
{"status": "ok", "realtime_lots": 109, "latest_update": "2026-10-22 02:45:37"}
```

### `GET /v1/parking/check` — P단 순간 판단
| 파라미터 | 설명 |
|---|---|
| `lat`, `lon` | 차를 세운 위치 (필수) |
| `when` | 판단 시각 `YYYY-MM-DDTHH:MM`. 없으면 지금이에요. 어린이보호구역 신고 시간대 판단에 써요 |
| `margin` | GPS 오차 여유(m), 기본 5 |
| `radius` | 대안 주차장 검색 반경(m), 기본 700 |

응답 예시 (광화문 세종대로, 평일 14:00):
```json
{
  "risk_level": "high",
  "risks": [{"kind": "cctv", "label": "단속 CCTV", "name": "kt광화문지사 건너편(세종대왕동상)",
             "distance_m": 27, "rule": "주정차 단속 CCTV 반경 50m (추정 단속 범위)",
             "active_now": true, "fine_krw_estimate": 40000, "lat": 37.5726, "lon": 126.9767}],
  "fine_krw_estimate": 40000,
  "alternatives": [{"id": "171721", "name": "세종로 공영주차장(시)", "distance_m": 143,
                    "fee": {"per_10min_krw": 860, "per_hour_krw": 5160, "...": "..."},
                    "realtime": {"free": 1109, "total": 1260, "updated_at": "2026-10-22 02:45:23", "age_min": 1}}],
  "notice": "공공데이터 기반 추정 정보이며 단속 여부를 보장하지 않습니다."
}
```
- `risk_level`: `high`는 지금 단속·신고 대상, `caution`은 시간대에 따라 대상(어린이보호구역 밖 시간), `none`은 알려진 위험 없음이에요.
- `fine_krw_estimate`: 지금 적용되는 위험 중 가장 큰 과태료 추정치예요(승용차 기준, 공식 금액 확인 필요).

### `GET /v1/lots/nearby` — 도착 전 주차장 비교
| 파라미터 | 설명 |
|---|---|
| `lat`, `lon` | 목적지 위치 (필수) |
| `eta_min` | 도착까지 남은 분. 주면 `realtime.predicted`에 도착 시점 예상 빈자리가 들어가요 |
| `radius` | 검색 반경(m), 기본 700 |
| `limit` | 최대 개수, 기본 10 |

정렬은 실시간 빈자리가 있는 곳이 먼저, 그다음은 가까운 순서예요. `realtime`이 `null`이면 실시간 정보가 없는 주차장이에요.

`realtime.predicted.basis`
| 값 | 뜻 |
|---|---|
| `pattern` | 지난 4주 중 같은 요일 유형(평일/주말)·같은 시간대(±15분)의 날짜별 중앙값. 서로 다른 날짜가 3일 이상이면 써요 |
| `trend` | 데이터가 부족할 때 최근 30분 변화 속도를 도착 시간만큼 이어 붙여요 |
| `current` | 그것도 없으면 지금 값이에요 |

### `GET /v1/lots/{id}` — 주차장 상세와 빈자리 추이
`hours`(기본 24, 최대 168) 동안의 빈자리 기록을 `series`로 돌려줘요.

## 구조
| 파일 | 역할 |
|---|---|
| `parking_core.py` | 위험 판단, 요금 계산, 주변 주차장 검색, 빈자리 예측. `datapipe/risk_lookup.py`도 이 모듈을 써요 |
| `app.py` | HTTP 서버 (`ThreadingHTTPServer`) |
| `test_parking_core.py` | 요금·예측·위험 단계·거리 단위 테스트 |

## 남은 일
- 휴대폰 위치 카드 API(주차 기록 저장, QR 링크, 카드 페이지)
- 2주 이상 데이터가 쌓이면 `pattern` 예측 정확도 검증
- 외부에 열 때 HTTPS와 호출 키 적용
