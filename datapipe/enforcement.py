"""서울시 불법주정차 단속 기록(서울 열린데이터광장 OA-22190)을 받고 읽고, '단속 잦은 자리'를 만듭니다.

build_risk_db.py(앱에 넣을 단속 잦은 자리)와 check_enforcement.py(실제 단속 기록 대조)가 함께 씁니다.
원본은 분기별 CSV(단속일, 단속시간, 구주소, 도로명, 경도, 위도)이고 공공누리 1유형이에요.
단속 방식(고정 CCTV·이동 단속·주민신고) 구분은 없어요.
"""

import collections
import csv
import datetime
import math
import pathlib
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "enforcement"
DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false"
# 분기 → 서울 열린데이터광장 파일 번호(데이터셋 화면의 downloadFile 번호). 새 분기가 올라오면 여기에 더해요.
QUARTERS = {"2025Q1": 10, "2025Q2": 11, "2025Q3": 12, "2025Q4": 15}
SEOUL = (37.4, 37.72, 126.75, 127.2)

# 단속 잦은 자리: 한 분기 동안 20m 칸 안에서 10번 이상 단속된 곳의 평균 위치, 판단 반경 30m.
# 지난 분기로 만들고 다음 분기 단속과 맞대 정했어요(docs/단속기록_대조.md).
HOTSPOT_CELL_M = 20
HOTSPOT_MIN_COUNT = 10
HOTSPOT_RADIUS_M = 30
# 단속이 몰리는 시간대: 그 자리 단속의 50% 이상이 한 3시간에 몰렸을 때만 보여 줘요. 90% 이상이 평일이면 '평일'을 붙여요.
# 지난 분기로 정한 시간대에 다음 분기 단속의 37%가 들었어요(서울 전체 최다 시간대를 똑같이 쓰면 25%).
HOTSPOT_PEAK_HOURS = 3
HOTSPOT_PEAK_SHARE = 0.5
HOTSPOT_WEEKDAY_SHARE = 0.9

M_PER_DEG_LAT = 111_320


def download(quarter):
    """그 분기 CSV 경로입니다. 없으면 받아서 data/raw/enforcement/에 둡니다(약 60MB)."""
    path = RAW_DIR / f"OA-22190_{quarter}.csv"
    if path.exists():
        return path
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    body = urllib.parse.urlencode({"infId": "OA-22190", "infSeq": 1, "seq": QUARTERS[quarter]}).encode()
    print(f"{quarter} 단속 기록을 받는 중이에요 (약 60MB)")
    with urllib.request.urlopen(urllib.request.Request(DOWNLOAD_URL, data=body), timeout=300) as r:
        data = r.read()
    tmp = path.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.rename(path)
    return path


def load_records(path):
    """(위도, 경도, 단속 시각) 목록과 뺀 행 수입니다. 좌표가 없거나 서울 밖인 행은 뺍니다."""
    out, skipped = [], 0
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            try:
                lon, lat = float(row[4]), float(row[5])
                when = datetime.datetime.strptime(row[0].strip('"') + row[1].strip()[:8], "%Y%m%d%H:%M:%S")
            except (ValueError, IndexError):
                skipped += 1
                continue
            if SEOUL[0] < lat < SEOUL[1] and SEOUL[2] < lon < SEOUL[3]:
                out.append((lat, lon, when))
            else:
                skipped += 1
    return out, skipped


def quarter_label(quarter):
    """'2025Q4' → '25년 10~12월'"""
    year, q = quarter.split("Q")
    first = (int(q) - 1) * 3 + 1
    return f"{year[2:]}년 {first}~{first + 2}월"


def quarter_short(quarter):
    """'2025Q4' → '25년 4분기'"""
    year, q = quarter.split("Q")
    return f"{year[2:]}년 {q}분기"


def peak_window(times):
    """단속이 가장 많은 연속 3시간입니다. times는 (평일인지, 시) 목록이고 (평일 비율, 시작 시, 그 3시간 비율)을 돌려줘요."""
    hours = collections.Counter(h for _, h in times)
    start = max(range(24), key=lambda h: sum(hours[(h + k) % 24] for k in range(HOTSPOT_PEAK_HOURS)))
    share = sum(hours[(start + k) % 24] for k in range(HOTSPOT_PEAK_HOURS)) / len(times)
    return sum(1 for weekday, _ in times if weekday) / len(times), start, share


def peak_label(times):
    """'평일 14~17시'처럼 단속이 몰리는 시간대입니다. 뚜렷하게 몰리지 않으면 None이에요."""
    weekday, start, share = peak_window(times)
    if share < HOTSPOT_PEAK_SHARE:
        return None
    hours = f"{start}~{(start + HOTSPOT_PEAK_HOURS) % 24}시"
    return f"평일 {hours}" if weekday >= HOTSPOT_WEEKDAY_SHARE else hours


def _cell(lat, lon):
    return int(lat * M_PER_DEG_LAT / HOTSPOT_CELL_M), int(lon * M_PER_DEG_LAT * math.cos(math.radians(37.55)) / HOTSPOT_CELL_M)


def _distance_m(lat1, lon1, lat2, lon2):
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    return 6371000 * math.hypot(x, math.radians(lat2 - lat1))


def hotspots(records):
    """단속 잦은 자리 [(위도, 경도, 반경 안 단속 수, 단속이 몰리는 시간대 또는 None)]입니다. 단속 수가 많은 순서예요.

    20m 칸마다 단속을 모아 10번 이상인 칸의 평균 위치를 자리로 삼고, 화면에 보여 줄 수와 시간대는
    그 자리 반경 30m 안에 든 칸들의 단속으로 계산해요(칸 평균 위치 기준).
    """
    cells = collections.defaultdict(lambda: [0, 0.0, 0.0, []])
    for lat, lon, when in records:
        c = cells[_cell(lat, lon)]
        c[0] += 1
        c[1] += lat
        c[2] += lon
        c[3].append((when.weekday() < 5, when.hour))
    centers = {k: (n, la / n, lo / n, times) for k, (n, la, lo, times) in cells.items()}
    reach = math.ceil(HOTSPOT_RADIUS_M / HOTSPOT_CELL_M) + 1
    out = []
    for (a, b), (n, lat, lon, _) in centers.items():
        if n < HOTSPOT_MIN_COUNT:
            continue
        near = [centers[(x, y)] for x in range(a - reach, a + reach + 1) for y in range(b - reach, b + reach + 1)
                if (x, y) in centers and _distance_m(lat, lon, *centers[(x, y)][1:3]) <= HOTSPOT_RADIUS_M]
        times = [t for c in near for t in c[3]]
        out.append((lat, lon, len(times), peak_label(times)))
    return sorted(out, key=lambda h: -h[2])
