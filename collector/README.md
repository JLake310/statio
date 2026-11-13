# 서울 실시간 주차 수집기

서울시 시영주차장의 실시간 주차 대수를 5분마다 받아 `data/seoul_parking.db`(SQLite)에 쌓아요. 이 데이터로 '도착 시점 예상 빈자리'의 시간대별 패턴을 만들어요.

## 준비
- 프로젝트 루트의 `.env`에 `SEOUL_OPEN_API_KEY`를 넣어 주세요. 형식은 `.env.example`을 참고하세요.
- Python 3 기본 라이브러리만 써서 따로 설치할 게 없어요.

## 실행
```bash
python3 collector/seoul_parking_collector.py            # 5분 간격으로 계속 수집
python3 collector/seoul_parking_collector.py --once     # 한 번만 수집
python3 collector/seoul_parking_collector.py --stats    # 저장된 데이터 요약
```

## 저장 구조
| 테이블 | 내용 |
|---|---|
| `lots` | 주차장 코드, 이름, 주소, 총 주차면, 좌표, 요금 |
| `snapshots` | 주차장 코드, 서울시 갱신 시각, 수집 시각, 총 주차면, 현재 주차 대수 |

같은 주차장의 같은 갱신 시각 값은 한 번만 저장해요. 빈자리는 `total - parked`로 계산해요.
