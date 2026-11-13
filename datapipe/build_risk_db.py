#!/usr/bin/env python3
"""서울 원본 데이터로 주차 위험 지점과 공영주차장 정보를 담은 data/parking_risk.db를 만듭니다.

입력: datapipe/fetch_seoul_data.py가 저장한 data/raw/seoul/*.json
      data/raw/*주차장정보표준데이터*.csv (공공데이터포털에서 직접 받은 파일, 있으면 사용)
      data/raw/enforcement/OA-22190_*.csv (가장 최근 분기 단속 기록, 없으면 datapipe/enforcement.py가 받아요)
출력: data/parking_risk.db
    risk_points  : 종류, 이름, 위경도, 판단 반경(m), 규칙 설명, 격자 좌표
    parking_lots : 주차장 코드, 이름, 주소, 공영/민영, 위경도, 요금, 운영시간, 실시간 연계 여부, 출처

    python3 datapipe/build_risk_db.py
"""

import collections
import csv
import io
import json
import math
import pathlib
import re
import sqlite3

import enforcement as enf

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "seoul"
STD_PARKING_GLOB = "*주차장정보표준데이터*.csv"
DB_PATH = ROOT / "data" / "parking_risk.db"
CELL = 1000  # 위경도 × 1000 을 정수로 내린 값이 격자 한 칸입니다 (약 110m × 90m)

# 종류별 판단 반경과 안내 문구입니다. 반경은 도로교통법 제32조 기준이고, CCTV와 어린이보호구역은 추정치입니다.
# 교차로는 지점마다 반경이 달라요(intersection_radius). 여기 값은 크기를 추정하지 못했을 때의 기본값입니다.
KINDS = {
    "cctv":         (50,  "주정차 단속 CCTV 반경 50m (추정 단속 범위)"),
    "bus_lane":     (30,  "버스전용차로 단속 CCTV 반경 30m"),
    "bus_stop":     (10,  "버스정류소 10m 이내 주정차 금지 (주민신고 대상)"),
    "crosswalk":    (10,  "횡단보도 10m 이내 주정차 금지 (주민신고 대상)"),
    "hydrant":      (5,   "소화전 5m 이내 주정차 금지 (주민신고 대상)"),
    "intersection": (20,  "교차로 모퉁이 5m 이내 주정차 금지 (주민신고 대상, 교차로 크기는 횡단보도 위치로 추정)"),
    "hotspot":      (enf.HOTSPOT_RADIUS_M, "지난 분기 실제 단속이 잦았던 자리 반경 30m (서울시 단속 기록, 20m 안 10번 이상)"),
    # 어린이보호구역은 시설 주소 점만 있어요. 실제 단속 기록과 맞대 보니 30~300m 어느 반경도 단속 자리를 구별하지 못해서,
    # 주민신고 대상인 시설 앞 도로를 덮는 100m로 줄여 헛경고를 줄였어요(docs/단속기록_대조.md).
    "school_zone":  (100, "어린이보호구역 추정 (대상 시설 반경 100m, 시설 앞 도로 기준, 주민신고는 평일 08~20시)"),
}

# 교차로 모퉁이: 신호 교차로 중심점(서울 교통안전시설물 A008_P)만 있어서, 가까운 횡단보도까지를 교차로 반폭으로 봐요.
# 횡단보도는 교차로 가장자리에 그어지니까 '반폭 + 5m' 안이 모퉁이 5m 금지 구역이에요(도로교통법 제32조 2호).
CORNER_M = 5
CENTER_M = 5          # 중심점에서 이만큼 안의 횡단보도는 가장자리를 알려 주지 않아 반폭 추정에서 빼요
HALF_MAX_M, HALF_DEFAULT_M = 30, 15   # 반폭 위 한계, 40m 안에 쓸 횡단보도가 없을 때의 기본값
HALF_SEARCH_M = 40
CROSSING_DEG = 45     # 두 횡단보도 방향이 이만큼 벌어져 있으면 길이 서로 만나는 곳이에요
GRID_M = 50           # 횡단보도 찾기용 평면 격자 한 칸(m)

SCHEMA = """
DROP TABLE IF EXISTS risk_points;
DROP TABLE IF EXISTS parking_lots;
CREATE TABLE risk_points (
    id        INTEGER PRIMARY KEY,
    kind      TEXT NOT NULL,
    name      TEXT,
    lat       REAL NOT NULL,
    lon       REAL NOT NULL,
    radius_m  REAL NOT NULL,
    rule      TEXT NOT NULL,
    cell_lat  INTEGER NOT NULL,
    cell_lon  INTEGER NOT NULL
);
CREATE INDEX idx_risk_cell ON risk_points (kind, cell_lat, cell_lon);
CREATE TABLE parking_lots (
    pklt_cd        TEXT PRIMARY KEY,
    name           TEXT,
    addr           TEXT,
    category       TEXT,
    kind           TEXT,
    oper           TEXT,
    total          INTEGER,
    lat            REAL,
    lon            REAL,
    basic_fee      REAL,
    basic_min      REAL,
    add_fee        REAL,
    add_min        REAL,
    day_max        REAL,
    weekday_hours  TEXT,
    weekend_hours  TEXT,
    holiday_hours  TEXT,
    realtime       INTEGER,
    source         TEXT,
    cell_lat       INTEGER,
    cell_lon       INTEGER
);
CREATE INDEX idx_lot_cell ON parking_lots (cell_lat, cell_lon);
"""

# EPSG:5186 (Korea 2000 / Central Belt 2010, GRS80) 역변환 상수입니다.
A, F = 6378137.0, 1 / 298.257222101
E2 = 2 * F - F * F
EP2 = E2 / (1 - E2)
LAT0, LON0, K0, FE, FN = math.radians(38), math.radians(127), 1.0, 200000.0, 600000.0


def meridian_arc(phi):
    return A * ((1 - E2 / 4 - 3 * E2**2 / 64 - 5 * E2**3 / 256) * phi
                - (3 * E2 / 8 + 3 * E2**2 / 32 + 45 * E2**3 / 1024) * math.sin(2 * phi)
                + (15 * E2**2 / 256 + 45 * E2**3 / 1024) * math.sin(4 * phi)
                - (35 * E2**3 / 3072) * math.sin(6 * phi))


def tm5186_to_wgs84(x, y):
    """EPSG:5186 평면좌표(x=동, y=북)를 위경도로 바꿉니다. GRS80과 WGS84 차이는 무시합니다."""
    m = meridian_arc(LAT0) + (y - FN) / K0
    mu = m / (A * (1 - E2 / 4 - 3 * E2**2 / 64 - 5 * E2**3 / 256))
    e1 = (1 - math.sqrt(1 - E2)) / (1 + math.sqrt(1 - E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu)
            + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
            + (151 * e1**3 / 96) * math.sin(6 * mu)
            + (1097 * e1**4 / 512) * math.sin(8 * mu))
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1, t1 = EP2 * cos1**2, tan1**2
    n1 = A / math.sqrt(1 - E2 * sin1**2)
    r1 = A * (1 - E2) / (1 - E2 * sin1**2) ** 1.5
    d = (x - FE) / (n1 * K0)
    lat = phi1 - (n1 * tan1 / r1) * (d**2 / 2
                                     - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * EP2) * d**4 / 24
                                     + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * EP2 - 3 * c1**2) * d**6 / 720)
    lon = LON0 + (d - (1 + 2 * t1 + c1) * d**3 / 6
                  + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * EP2 + 24 * t1**2) * d**5 / 120) / cos1
    return math.degrees(lat), math.degrees(lon)


def in_seoul(lat, lon):
    return 37.40 <= lat <= 37.72 and 126.75 <= lon <= 127.20


def load(prefix):
    path = next(RAW_DIR.glob(f"{prefix}_*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def crosswalk_grid(rows):
    """횡단보도 좌표(EPSG:5186, m)를 GRID_M 칸으로 묶어 둡니다."""
    grid = collections.defaultdict(list)
    for r in rows:
        x, y = to_float(r.get("XCRD")), to_float(r.get("YCRD"))
        if x and y:
            grid[(int(x // GRID_M), int(y // GRID_M))].append((x, y))
    return grid


def intersection_radius(x, y, crosswalks):
    """교차로 반폭 + 모퉁이 5m를 정수 m로 돌려줍니다. 길 중간의 횡단보도 신호로 보이면 None입니다.

    - 반폭: 중심점 5m 밖에서 가장 가까운 횡단보도까지(최대 30m). 40m 안에 없으면 15m로 둬요.
    - 중심점 바로 위에 횡단보도가 있고 주변 횡단보도가 모두 한 방향으로 늘어서 있으면 길 중간 횡단보도 신호예요.
      모퉁이가 없고 횡단보도 10m 규칙이 이미 덮어서 빼요.
    """
    gx, gy = int(x // GRID_M), int(y // GRID_M)
    near = sorted((math.hypot(cx - x, cy - y), math.degrees(math.atan2(cy - y, cx - x)))
                  for dx in (-1, 0, 1) for dy in (-1, 0, 1) for cx, cy in crosswalks.get((gx + dx, gy + dy), ()))
    near = [(d, a) for d, a in near if d <= HALF_SEARCH_M]
    outside = [(d, a) for d, a in near if d >= CENTER_M]
    if near and near[0][0] < CENTER_M and not crossing(outside):
        return None
    half = min(outside[0][0], HALF_MAX_M) if outside else HALF_DEFAULT_M
    return round(half + CORNER_M)


def crossing(points):
    """방향이 CROSSING_DEG 이상 벌어진 횡단보도 두 개가 있는지 봅니다. 마주 보는 방향(180°)은 같은 줄로 쳐요."""
    angles = [a % 180 for _, a in points]
    return any(min(abs(a - b), 180 - abs(a - b)) >= CROSSING_DEG for i, a in enumerate(angles) for b in angles[i + 1:])


def intersection_name(name):
    """'구암지구대(연등)2'처럼 붙은 신호 운영 표시와 번호를 뗍니다."""
    return re.sub(r"\(.*?\)\d*", "", name or "").strip() or None


def risk_rows():
    """원본 데이터를 (종류, 이름, 위도, 경도, 반경)으로 바꿉니다. 반경이 None이면 종류 기본값입니다.
    같은 종류의 같은 좌표는 한 번만 넣습니다."""
    seen = set()

    def emit(kind, name, lat, lon, radius=None):
        if lat is None or lon is None or not in_seoul(lat, lon):
            return None
        key = (kind, round(lat, 5), round(lon, 5))
        if key in seen:
            return None
        seen.add(key)
        return (kind, name, lat, lon, radius)

    for r in load("OA-20471"):
        kind = {"불법주정차구역": "cctv", "버스전용차로": "bus_lane"}.get(r.get("GRNDS_SE"))
        if kind:
            row = emit(kind, r.get("CRDN_BRNCH_NM") or r.get("FIX_CCTV_ADDR"), to_float(r.get("LAT")), to_float(r.get("LOT")))
            if row:
                yield row
    for r in load("OA-15067"):
        if r.get("STOPS_TYPE") != "한강선착장":
            row = emit("bus_stop", r.get("STOPS_NM"), to_float(r.get("YCRD")), to_float(r.get("XCRD")))
            if row:
                yield row
    crosswalks = load("OA-15554")
    for r in crosswalks:
        x, y = to_float(r.get("XCRD")), to_float(r.get("YCRD"))
        if x and y:
            row = emit("crosswalk", r.get("CRSWK_MNG_NO1"), *tm5186_to_wgs84(x, y))
            if row:
                yield row
    for r in load("OA-12811"):
        row = emit("hydrant", r.get("NEW_ADRS"), to_float(r.get("LAT")), to_float(r.get("LOT")))
        if row:
            yield row
    for r in load("OA-20448"):
        row = emit("school_zone", r.get("FCLT_NM"), to_float(r.get("YCRD")), to_float(r.get("XCRD")))
        if row:
            yield row
    grid = crosswalk_grid(crosswalks)
    for r in load("OA-15534"):
        x, y = to_float(r.get("XCRD")), to_float(r.get("YCRD"))
        radius = intersection_radius(x, y, grid) if x and y else None
        if radius:
            row = emit("intersection", intersection_name(r.get("INTR_NM")), *tm5186_to_wgs84(x, y), radius)
            if row:
                yield row
    # 단속 잦은 자리는 가장 최근 분기 단속 기록으로 만들어요. 이름을 화면에 그대로 쓰는데, 분할 창에서는 뒤가 잘려서
    # 중요한 순서(단속 수 → 몰리는 시간대 → 분기)로 적어요. 예: '224번 · 평일 14~17시 (25년 4분기)'
    quarter = max(enf.QUARTERS)
    for lat, lon, count, peak in enf.hotspots(enf.load_records(enf.download(quarter))[0]):
        name = f"{count:,}번 · {peak}" if peak else f"{count:,}번 단속"
        row = emit("hotspot", f"{name} ({enf.quarter_short(quarter)})", lat, lon)
        if row:
            yield row


def hours(start, end):
    return f"{start}-{end}" if start and end else None


LOT_COLUMNS = ["pklt_cd", "name", "addr", "category", "kind", "oper", "total", "lat", "lon",
               "basic_fee", "basic_min", "add_fee", "add_min", "day_max",
               "weekday_hours", "weekend_hours", "holiday_hours", "realtime", "source"]


def seoul_lots():
    """서울 공영주차장 안내 정보를 주차장 코드 하나당 하나로 합칩니다. 좌표는 유효한 좌표들의 평균입니다."""
    groups = collections.defaultdict(list)
    for r in load("OA-13122"):
        groups[r["PKLT_CD"]].append(r)
    for code, rows in groups.items():
        r = rows[0]
        pts = [(to_float(x.get("LAT")), to_float(x.get("LOT"))) for x in rows]
        pts = [(la, lo) for la, lo in pts if la and lo and in_seoul(la, lo)]
        yield {
            "pklt_cd": code, "name": r.get("PKLT_NM"), "addr": r.get("ADDR"), "category": "공영",
            "kind": r.get("PKLT_KND_NM"), "oper": r.get("OPER_SE_NM"),
            "total": int(to_float(r.get("TPKCT")) or 0),
            "lat": sum(p[0] for p in pts) / len(pts) if pts else None,
            "lon": sum(p[1] for p in pts) / len(pts) if pts else None,
            "basic_fee": to_float(r.get("PRK_CRG")), "basic_min": to_float(r.get("PRK_HM")),
            "add_fee": to_float(r.get("ADD_CRG")), "add_min": to_float(r.get("ADD_UNIT_TM_MNT")),
            "day_max": to_float(r.get("DLY_MAX_CRG")),
            "weekday_hours": hours(r.get("WD_OPER_BGNG_TM"), r.get("WD_OPER_END_TM")),
            "weekend_hours": hours(r.get("WE_OPER_BGNG_TM"), r.get("WE_OPER_END_TM")),
            "holiday_hours": hours(r.get("LHLDY_BGNG"), r.get("LHLDY")),
            "realtime": 1 if r.get("PRK_NOW_INFO_PVSN_YN") == "1" else 0, "source": "seoul",
        }


def read_csv_any(path):
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp949"):
        try:
            return list(csv.DictReader(io.StringIO(raw.decode(enc))))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"인코딩을 알 수 없습니다: {path}")


def std_lots():
    """전국주차장정보표준데이터 중 서울 주차장을 같은 형식으로 바꿉니다. 파일이 없으면 아무것도 돌려주지 않습니다.

    관리번호가 비었거나 겹치는 행이 있어 순번을 붙여 코드를 유일하게 만듭니다.
    """
    seen = collections.Counter()
    for path in sorted(RAW_DIR.parent.glob(STD_PARKING_GLOB)):
        for r in read_csv_any(path):
            addr = r.get("소재지지번주소") or r.get("소재지도로명주소") or ""
            if not addr.startswith("서울"):
                continue
            lat, lon = to_float(r.get("위도")), to_float(r.get("경도"))
            if lat is not None and lon is not None and not in_seoul(lat, lon):
                lat = lon = None
            code = "STD-" + (r.get("주차장관리번호") or "unknown")
            seen[code] += 1
            if seen[code] > 1:
                code = f"{code}#{seen[code]}"
            yield {
                "pklt_cd": code, "name": r.get("주차장명"), "addr": addr,
                "category": r.get("주차장구분"), "kind": r.get("주차장유형"), "oper": r.get("요금정보"),
                "total": int(to_float(r.get("주차구획수")) or 0), "lat": lat, "lon": lon,
                "basic_fee": to_float(r.get("주차기본요금")), "basic_min": to_float(r.get("주차기본시간")),
                "add_fee": to_float(r.get("추가단위요금")), "add_min": to_float(r.get("추가단위시간")),
                "day_max": to_float(r.get("1일주차권요금")),
                "weekday_hours": hours(r.get("평일운영시작시각"), r.get("평일운영종료시각")),
                "weekend_hours": hours(r.get("토요일운영시작시각"), r.get("토요일운영종료시각")),
                "holiday_hours": hours(r.get("공휴일운영시작시각"), r.get("공휴일운영종료시각")),
                "realtime": 0, "source": "std",
            }


def norm_addr(addr):
    """'서울특별시 종로구 훈정동 2-0' 과 '종로구 훈정동 2' 를 같은 키로 만듭니다."""
    a = re.sub(r"^서울(특별시|시)?\s*", "", addr or "")
    a = re.sub(r"\(.*?\)|번지|\s", "", a)
    return re.sub(r"-0$", "", a)


def norm_name(name):
    n = re.sub(r"\(.*?\)|\s", "", name or "")
    return re.sub(r"(공영)?주차장$|공영$", "", n)


def gu_of(addr):
    m = re.search(r"([가-힣]+구)", addr or "")
    return m.group(1) if m else ""


BUS_ONLY = re.compile(r"관광버스|버스전용")


def merge_lots(seoul, std):
    """서울 공영주차장에 표준데이터 좌표를 채우고, 짝이 없는 표준데이터 주차장(민영 포함)을 덧붙입니다."""
    by_addr = {norm_addr(s["addr"]): s for s in std if s["addr"]}
    by_name = {(gu_of(s["addr"]), norm_name(s["name"])): s for s in std if s["name"]}
    matched, filled = set(), 0
    for lot in seoul:
        s = by_addr.get(norm_addr(lot["addr"])) or by_name.get((gu_of(lot["addr"]), norm_name(lot["name"])))
        if not s:
            continue
        matched.add(s["pklt_cd"])
        if lot["lat"] is None and s["lat"] is not None:
            lot["lat"], lot["lon"] = s["lat"], s["lon"]
            filled += 1
    extra = [s for s in std if s["pklt_cd"] not in matched and s["lat"] is not None]
    # 관광버스 전용 주차장은 승용차가 쓸 수 없어 안내 대상에서 뺍니다.
    lots = [lot for lot in seoul + extra if not BUS_ONLY.search(lot["name"] or "")]
    return lots, filled, len(matched), len(extra)


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    counts = collections.Counter()
    radii = collections.defaultdict(list)
    for kind, name, lat, lon, point_radius in risk_rows():
        radius, rule = KINDS[kind]
        radius = point_radius or radius
        conn.execute(
            "INSERT INTO risk_points (kind, name, lat, lon, radius_m, rule, cell_lat, cell_lon) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (kind, name, lat, lon, radius, rule, int(lat * CELL), int(lon * CELL)),
        )
        counts[kind] += 1
        radii[kind].append(radius)

    seoul, std = list(seoul_lots()), list(std_lots())
    lots, filled, matched, extra = merge_lots(seoul, std)
    placeholders = ",".join("?" * (len(LOT_COLUMNS) + 2))
    conn.executemany(
        f"INSERT INTO parking_lots ({', '.join(LOT_COLUMNS)}, cell_lat, cell_lon) VALUES ({placeholders})",
        [[lot[c] for c in LOT_COLUMNS]
         + ([int(lot["lat"] * CELL), int(lot["lon"] * CELL)] if lot["lat"] is not None else [None, None])
         for lot in lots],
    )
    conn.commit()

    for kind, n in counts.most_common():
        r = sorted(radii[kind])
        spread = f" (반경 {r[0]}~{r[-1]}m, 중앙값 {r[len(r) // 2]}m)" if r[0] != r[-1] else ""
        print(f"위험 지점 {kind}: {n}곳{spread}")
    print(f"서울 공영주차장 {len(seoul)}곳, 표준데이터 서울 주차장 {len(std)}곳 "
          f"(서울 공영주차장과 짝 {matched}곳, 좌표 보충 {filled}곳, 새로 추가 {extra}곳)")
    with_coord = sum(1 for lot in lots if lot["lat"] is not None)
    print(f"주차장 합계 {len(lots)}곳 (좌표 있음 {with_coord}곳, 실시간 연계 표시 {sum(l['realtime'] for l in lots)}곳)")
    print(f"저장 위치: {DB_PATH}")


if __name__ == "__main__":
    main()
