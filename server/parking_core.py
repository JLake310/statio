"""주정차 위험 판단, 주변 주차장 검색, 빈자리 예측 공통 로직입니다.

빈자리 서버(server/app.py)와 조회 스크립트(datapipe/risk_lookup.py)가 함께 씁니다.
입력 DB:
    data/parking_risk.db   위험 지점과 주차장 정보 (datapipe/build_risk_db.py)
    data/seoul_parking.db  실시간 주차 대수 기록 (collector/seoul_parking_collector.py)
"""

import datetime
import math
import pathlib
import sqlite3
import statistics
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
RISK_DB = ROOT / "data" / "parking_risk.db"
LIVE_DB = ROOT / "data" / "seoul_parking.db"
CELL = 1000
KST = datetime.timezone(datetime.timedelta(hours=9))
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# 경고 우선순위입니다. 숫자가 작을수록 먼저 보여 줍니다. 앱 DB의 위험 종류 번호이기도 해요.
# 단속 잦은 자리는 실제 단속 기록이라 단속 카메라 바로 다음에 둬요(위험이 여럿이면 화면에는 앞의 3개만 보여요).
SEVERITY = {"cctv": 0, "bus_lane": 1, "hotspot": 2, "hydrant": 3, "crosswalk": 4, "bus_stop": 5, "intersection": 6,
            "school_zone": 7}
LABEL = {
    "cctv": "단속 CCTV", "bus_lane": "버스전용차로 CCTV", "hydrant": "소화전",
    "crosswalk": "횡단보도", "bus_stop": "버스정류소", "intersection": "교차로 모퉁이(추정)",
    "hotspot": "단속 잦은 자리", "school_zone": "어린이보호구역(추정)",
}
# 승용차 기준 과태료입니다(2026-10 지자체·서울시 안내로 확인). 같은 장소 2시간 이상 가산은 넣지 않아 화면에는 '추정'으로 보여요.
FINE_KRW = {"cctv": 40000, "bus_lane": 50000, "hydrant": 80000, "crosswalk": 40000, "bus_stop": 40000,
            "intersection": 40000, "hotspot": 40000, "school_zone": 120000}
MAX_RISK_RADIUS = 100  # 가장 넓은 판단 반경(어린이보호구역)

# 값이 멈춘 주차장: 수집 기간 하루 평균 이보다 적게 값이 바뀐 곳은 현장 값이 올라오지 않는 것으로 봐요.
# 앱은 이런 곳의 실시간 빈자리를 쓰지 않고(앱 DB 실시간 연계 끔), 빈자리 예측 학습에서도 빼요(ml/dataset.py).
FROZEN_CHANGES_PER_DAY = 2

PATTERN_WINDOW_MIN = 15   # 같은 시간대로 볼 앞뒤 범위
PATTERN_DAYS = 28         # 패턴에 쓰는 과거 기간
PATTERN_MIN_DAYS = 3      # 패턴 예측에 필요한 최소 일수
TREND_WINDOW_MIN = 30     # 추세 예측에 쓰는 최근 기간
TREND_MIN_SPAN_MIN = 10


def now_kst():
    return datetime.datetime.now(KST).replace(tzinfo=None)


def distance_m(lat1, lon1, lat2, lon2):
    """짧은 거리용 평면 근사 거리(m)입니다."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000 * math.hypot(x, y)


def cell_range(lat, lon, meters):
    dlat = meters / 111000 * CELL
    dlon = meters / (111000 * math.cos(math.radians(lat))) * CELL
    cl, co = int(lat * CELL), int(lon * CELL)
    return cl - math.ceil(dlat), cl + math.ceil(dlat), co - math.ceil(dlon), co + math.ceil(dlon)


def connect(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ── 주정차 위험 ─────────────────────────────────────────────

def find_risks(conn, lat, lon, margin_m, when):
    """반경 안의 위험 지점을 종류별로 가장 가까운 한 곳씩 돌려줍니다. 반경은 지점마다 저장된 값(교차로는 크기별)을 써요."""
    lat0, lat1, lon0, lon1 = cell_range(lat, lon, MAX_RISK_RADIUS + margin_m)
    rows = conn.execute(
        """SELECT kind, name, lat, lon, radius_m, rule FROM risk_points
           WHERE cell_lat BETWEEN ? AND ? AND cell_lon BETWEEN ? AND ?""",
        (lat0, lat1, lon0, lon1),
    ).fetchall()
    nearest = {}
    for r in rows:
        d = distance_m(lat, lon, r["lat"], r["lon"])
        if d <= r["radius_m"] + margin_m and (r["kind"] not in nearest or d < nearest[r["kind"]]["distance_m"]):
            nearest[r["kind"]] = {"kind": r["kind"], "label": LABEL[r["kind"]], "name": r["name"],
                                  "distance_m": round(d), "rule": r["rule"], "lat": r["lat"], "lon": r["lon"]}
    weekday_day = when.weekday() < 5 and 8 <= when.hour < 20
    for risk in nearest.values():
        risk["active_now"] = weekday_day if risk["kind"] == "school_zone" else True
        risk["fine_krw_estimate"] = FINE_KRW[risk["kind"]]
    return sorted(nearest.values(), key=lambda r: SEVERITY[r["kind"]])


def risk_level(risks):
    """high: 지금 단속·신고 대상, caution: 시간대에 따라 대상, none: 알려진 위험 없음."""
    if any(r["active_now"] for r in risks):
        return "high"
    return "caution" if risks else "none"


# ── 요금 ────────────────────────────────────────────────

def fee_for(minutes, basic_fee, basic_min, add_fee, add_min, day_max=None):
    """기본시간·추가단위 요금으로 주차 시간별 요금을 계산합니다. 정보가 없으면 None입니다."""
    if basic_fee is None or not basic_min:
        return None
    fee = basic_fee
    if minutes > basic_min and add_min:
        fee += math.ceil((minutes - basic_min) / add_min) * (add_fee or 0)
    if day_max:
        fee = min(fee, day_max)
    return int(fee)


# ── 실시간 빈자리 ────────────────────────────────────────────

class LiveData:
    """수집기 DB를 읽습니다. 최신 값은 짧게 캐시해 요청마다 전체를 읽지 않게 합니다."""

    def __init__(self, path=LIVE_DB, ttl_sec=60):
        self.path, self.ttl_sec = path, ttl_sec
        self._cache, self._cached_at = {}, 0.0
        self._lock = threading.Lock()

    def _conn(self):
        return connect(self.path) if pathlib.Path(self.path).exists() else None

    def latest(self):
        with self._lock:
            if time.time() - self._cached_at < self.ttl_sec:
                return self._cache
            conn = self._conn()
            if conn is None:
                return {}
            rows = conn.execute(
                """SELECT s.pklt_cd, s.total, s.parked, s.source_updated_at FROM snapshots s
                   JOIN (SELECT pklt_cd, MAX(source_updated_at) AS t FROM snapshots GROUP BY pklt_cd) m
                     ON s.pklt_cd = m.pklt_cd AND s.source_updated_at = m.t"""
            ).fetchall()
            conn.close()
            self._cache = {r["pklt_cd"]: (r["total"], r["parked"], r["source_updated_at"]) for r in rows}
            self._cached_at = time.time()
            return self._cache

    def history(self, code, since):
        conn = self._conn()
        if conn is None:
            return []
        rows = conn.execute(
            """SELECT source_updated_at, total, parked FROM snapshots
               WHERE pklt_cd = ? AND source_updated_at >= ? ORDER BY source_updated_at""",
            (code, since.strftime(TIME_FORMAT)),
        ).fetchall()
        conn.close()
        return [(datetime.datetime.strptime(r[0], TIME_FORMAT), r[1], r[2]) for r in rows]


def frozen_lots(live_conn):
    """수집 DB(snapshots)에서 값이 멈춘 주차장 코드입니다. 총 주차면이 0인 기록은 빼고 주차 대수 변화를 세요."""
    rows = live_conn.execute(
        "SELECT pklt_cd, collected_at, total, parked FROM snapshots WHERE total > 0 ORDER BY pklt_cd, collected_at").fetchall()
    if not rows:
        return set()
    times = [datetime.datetime.strptime(r[1], TIME_FORMAT) for r in (min(rows, key=lambda r: r[1]), max(rows, key=lambda r: r[1]))]
    days = max((times[1] - times[0]).total_seconds() / 86400, 1.0)
    last, changes = {}, {}
    for lot, _, total, parked in rows:
        parked = min(max(parked, 0), total)
        changes[lot] = changes.get(lot, 0) + (lot in last and last[lot] != parked)
        last[lot] = parked
    return {lot for lot, n in changes.items() if n / days < FROZEN_CHANGES_PER_DAY}


def predict_free(history, now, eta_min, current_free, total):
    """도착 시점 빈자리를 예측합니다.

    1) pattern: 지난 4주 중 같은 요일 유형(평일/주말)·같은 시간대(±15분)의 날짜별 중앙값을 다시 중앙값으로 묶습니다.
       서로 다른 날짜가 3일 이상 있어야 씁니다.
    2) trend: 데이터가 부족하면 최근 30분 변화 속도를 도착 시간만큼 이어 붙입니다.
    3) current: 그것도 없으면 지금 값을 그대로 씁니다.
    """
    target = now + datetime.timedelta(minutes=eta_min)
    weekend = target.weekday() >= 5
    target_min = target.hour * 60 + target.minute
    by_day = {}
    for t, tot, parked in history:
        if t.date() >= now.date() or (t.weekday() >= 5) != weekend:
            continue
        diff = abs(t.hour * 60 + t.minute - target_min)
        if min(diff, 1440 - diff) <= PATTERN_WINDOW_MIN:
            by_day.setdefault(t.date(), []).append(tot - parked)
    if len(by_day) >= PATTERN_MIN_DAYS:
        value = statistics.median(statistics.median(v) for v in by_day.values())
        return {"free": max(0, min(total, round(value))), "basis": "pattern", "days": len(by_day)}

    recent = [(t, tot - parked) for t, tot, parked in history if t >= now - datetime.timedelta(minutes=TREND_WINDOW_MIN)]
    if len(recent) >= 3:
        span = (recent[-1][0] - recent[0][0]).total_seconds() / 60
        if span >= TREND_MIN_SPAN_MIN:
            slope = (recent[-1][1] - recent[0][1]) / span
            value = current_free + slope * eta_min
            return {"free": max(0, min(total, round(value))), "basis": "trend", "days": 0}
    return {"free": current_free, "basis": "current", "days": 0}


# ── 주변 주차장 ────────────────────────────────────────────

def lot_tier(lot):
    """추천 순서 칸: 0 빈자리 있음, 1 실시간 정보 없음, 2 만차. 상태를 모르는 곳이 만차로 알려진 곳보다 나아요(앱과 같은 규칙)."""
    if lot["realtime"] is None:
        return 1
    return 0 if lot["realtime"]["free"] > 0 else 2


def find_lots(conn, live, lat, lon, radius_m, now, eta_min=None, limit=10):
    """반경 안의 주차장을 '실시간 빈자리 있음 → 실시간 정보 없음 → 만차' 순서로, 같은 칸 안에서는 가까운 순서로 돌려줍니다."""
    lat0, lat1, lon0, lon1 = cell_range(lat, lon, radius_m)
    rows = conn.execute(
        """SELECT pklt_cd, name, addr, category, kind, total, lat, lon, basic_fee, basic_min, add_fee, add_min, day_max,
                  weekday_hours, weekend_hours, holiday_hours, realtime
           FROM parking_lots WHERE lat IS NOT NULL AND cell_lat BETWEEN ? AND ? AND cell_lon BETWEEN ? AND ?""",
        (lat0, lat1, lon0, lon1),
    ).fetchall()
    latest = live.latest()
    lots = []
    for r in rows:
        d = distance_m(lat, lon, r["lat"], r["lon"])
        if d > radius_m:
            continue
        fees = (r["basic_fee"], r["basic_min"], r["add_fee"], r["add_min"], r["day_max"])
        lot = {
            "id": r["pklt_cd"], "name": r["name"], "address": r["addr"], "category": r["category"], "type": r["kind"],
            "lat": r["lat"], "lon": r["lon"], "distance_m": round(d),
            "fee": {"basic_krw": r["basic_fee"], "basic_min": r["basic_min"], "add_krw": r["add_fee"], "add_min": r["add_min"],
                    "day_max_krw": r["day_max"] or None, "per_10min_krw": fee_for(10, *fees), "per_hour_krw": fee_for(60, *fees)},
            "hours": {"weekday": r["weekday_hours"], "weekend": r["weekend_hours"], "holiday": r["holiday_hours"]},
            "realtime": None,
        }
        if r["realtime"] and r["pklt_cd"] in latest:  # 실시간 연계가 꺼진 곳(앱 DB에서는 값이 멈춘 곳 포함)은 실시간 값을 안 써요
            total, parked, updated = latest[r["pklt_cd"]]
            free = max(0, total - parked)
            age = (now - datetime.datetime.strptime(updated, TIME_FORMAT)).total_seconds() / 60
            lot["realtime"] = {"free": free, "total": total, "updated_at": updated, "age_min": round(age)}
            if eta_min is not None:
                since = now - datetime.timedelta(days=PATTERN_DAYS)
                lot["realtime"]["predicted"] = predict_free(live.history(r["pklt_cd"], since), now, eta_min, free, total)
        lots.append(lot)
    # 같은 좌표에 여러 주차장이 있을 때도 순서가 일정하도록 마지막에 코드 순으로 정렬합니다(앱과 같은 규칙).
    lots.sort(key=lambda l: (lot_tier(l), l["distance_m"], l["id"]))
    return lots[:limit]


def check_parking(risk_conn, live, lat, lon, when, margin_m=5, radius_m=700, limit=5):
    """P단 순간의 판단: 위험 목록, 위험 단계, 대안 주차장을 한 번에 돌려줍니다."""
    risks = find_risks(risk_conn, lat, lon, margin_m, when)
    alternatives = find_lots(risk_conn, live, lat, lon, radius_m, now_kst(), limit=limit)
    return {
        "location": {"lat": lat, "lon": lon},
        "checked_at": when.strftime(TIME_FORMAT),
        "risk_level": risk_level(risks),
        "risks": risks,
        "fine_krw_estimate": max((r["fine_krw_estimate"] for r in risks if r["active_now"]), default=0),
        "alternatives": alternatives,
        "notice": "공공데이터 기반 추정 정보이며 단속 여부를 보장하지 않습니다.",
    }
