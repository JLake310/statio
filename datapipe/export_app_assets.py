#!/usr/bin/env python3
"""앱에 넣을 데이터와 Kotlin 로직 대조용 정답 사례를 만듭니다.

입력:
    data/parking_risk.db   위험 지점과 주차장 정보 (datapipe/build_risk_db.py)
    data/seoul_parking.db  실시간 빈자리 기록 (collector/seoul_parking_collector.py) → 시간대 패턴표, 값이 멈춘 주차장
출력:
    android/app/src/main/assets/statio.db              앱 내장 DB
    android/app/src/test/resources/golden_cases.json   server/parking_core.py로 계산한 정답 사례

    python3 datapipe/export_app_assets.py
    python3 datapipe/export_app_assets.py --keep-patterns   # 위험 지점·주차장만 새로 넣고 패턴표는 지금 앱 DB 것을 그대로 써요

--keep-patterns는 빈자리 예측 모델(ml/export_model.py)을 다시 만들지 않고 위험 지점만 바꿀 때 써요.
모델의 대조 사례가 지금 앱 DB 패턴표로 계산돼 있어서, 패턴표가 바뀌면 모델도 다시 내보내야 해요.

앱 DB는 크기를 줄이려고 좌표를 정수(위경도 × 1,000,000)로, 위험 종류를 숫자로 저장합니다.
위험 종류 번호는 server/parking_core.py의 SEVERITY 순서와 같습니다.
"""

import argparse
import datetime
import json
import math
import pathlib
import random
import sqlite3
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import parking_core as core  # noqa: E402

ASSET_DB = ROOT / "android" / "app" / "src" / "main" / "assets" / "statio.db"
GOLDEN = ROOT / "android" / "app" / "src" / "test" / "resources" / "golden_cases.json"

KIND_CODE = {kind: code for kind, code in core.SEVERITY.items()}
# 이름이 관리번호뿐이라 화면에 쓸 수 없는 종류는 이름을 빼서 크기를 줄입니다.
DROP_NAME = {"crosswalk"}
# 지점마다 반경이 다른 종류입니다. 나머지는 앱이 종류별 기본 반경(Kotlin RiskKind)을 써서 radius_m을 비워 둡니다.
POINT_RADIUS = {"intersection"}
SLOTS = 96  # 하루를 15분 단위로 나눈 칸 수
SLOT_MIN = 1440 // SLOTS

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE risk_points (
    kind      INTEGER NOT NULL,
    lat_e6    INTEGER NOT NULL,
    lon_e6    INTEGER NOT NULL,
    name      TEXT,
    radius_m  INTEGER,           -- 지점별 판단 반경(m). 비어 있으면 종류 기본값
    cell_lat  INTEGER NOT NULL,
    cell_lon  INTEGER NOT NULL
);
CREATE TABLE parking_lots (
    id             TEXT PRIMARY KEY,
    name           TEXT,
    addr           TEXT,
    category       TEXT,
    type           TEXT,
    total          INTEGER,
    lat_e6         INTEGER NOT NULL,
    lon_e6         INTEGER NOT NULL,
    basic_fee      INTEGER,
    basic_min      INTEGER,
    add_fee        INTEGER,
    add_min        INTEGER,
    day_max        INTEGER,
    weekday_hours  TEXT,
    weekend_hours  TEXT,
    holiday_hours  TEXT,
    realtime       INTEGER,
    cell_lat       INTEGER NOT NULL,
    cell_lon       INTEGER NOT NULL
);
CREATE TABLE patterns (
    lot_id    TEXT NOT NULL,
    day_type  INTEGER NOT NULL,  -- 0 평일, 1 주말
    slot      INTEGER NOT NULL,  -- 0~95, 15분 단위 칸의 시작 시각
    free      INTEGER NOT NULL,  -- 날짜별 중앙값의 중앙값
    days      INTEGER NOT NULL,  -- 값을 낸 서로 다른 날짜 수
    PRIMARY KEY (lot_id, day_type, slot)
) WITHOUT ROWID;
"""


def e6(value):
    return int(round(value * 1_000_000))


def as_int(value):
    return None if value is None else int(round(value))


def export_risks(src, dst):
    rows = src.execute("SELECT kind, name, lat, lon, radius_m, cell_lat, cell_lon FROM risk_points").fetchall()
    dst.executemany(
        "INSERT INTO risk_points VALUES (?, ?, ?, ?, ?, ?, ?)",
        ((KIND_CODE[r["kind"]], e6(r["lat"]), e6(r["lon"]), None if r["kind"] in DROP_NAME else r["name"],
          int(r["radius_m"]) if r["kind"] in POINT_RADIUS else None, r["cell_lat"], r["cell_lon"]) for r in rows),
    )
    dst.execute("CREATE INDEX idx_risk_cell ON risk_points (cell_lat, cell_lon)")
    return len(rows)


def export_lots(src, dst, frozen):
    """주차장을 넣습니다. 값이 멈춘 주차장(frozen)은 실시간 연계를 꺼서 앱이 실시간 빈자리 대신 '실시간 정보 없음'으로 보여 줘요."""
    rows = src.execute(
        """SELECT pklt_cd, name, addr, category, kind, total, lat, lon, basic_fee, basic_min, add_fee, add_min, day_max,
                  weekday_hours, weekend_hours, holiday_hours, realtime, cell_lat, cell_lon
           FROM parking_lots WHERE lat IS NOT NULL"""
    ).fetchall()
    dst.executemany(
        "INSERT INTO parking_lots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ((r["pklt_cd"], r["name"], r["addr"], r["category"], r["kind"], r["total"], e6(r["lat"]), e6(r["lon"]),
          as_int(r["basic_fee"]), as_int(r["basic_min"]), as_int(r["add_fee"]), as_int(r["add_min"]),
          as_int(r["day_max"]), r["weekday_hours"], r["weekend_hours"], r["holiday_hours"],
          0 if r["pklt_cd"] in frozen else r["realtime"] or 0,
          r["cell_lat"], r["cell_lon"]) for r in rows),
    )
    dst.execute("CREATE INDEX idx_lot_cell ON parking_lots (cell_lat, cell_lon)")
    return len(rows)


def build_patterns(live_path):
    """parking_core.predict_free의 pattern 규칙을 15분 칸마다 미리 계산합니다.

    칸 시작 시각 앞뒤 15분 안의 기록을 날짜별 중앙값으로 묶고, 그 값들의 중앙값을 냅니다.
    최근 28일만 씁니다. 앱은 days가 3 이상인 칸만 예측에 씁니다.
    """
    if not live_path.exists():
        return []
    conn = sqlite3.connect(f"file:{live_path}?mode=ro", uri=True)
    latest = conn.execute("SELECT MAX(source_updated_at) FROM snapshots").fetchone()[0]
    if latest is None:
        return []
    since = datetime.datetime.strptime(latest, core.TIME_FORMAT) - datetime.timedelta(days=core.PATTERN_DAYS)
    rows = conn.execute(
        "SELECT pklt_cd, source_updated_at, total, parked FROM snapshots WHERE source_updated_at >= ?",
        (since.strftime(core.TIME_FORMAT),),
    ).fetchall()
    conn.close()

    # (주차장, 평일/주말, 칸) → 날짜 → 빈자리 목록
    cells = {}
    for code, updated, total, parked in rows:
        if total is None or parked is None:
            continue
        t = datetime.datetime.strptime(updated, core.TIME_FORMAT)
        minute = t.hour * 60 + t.minute
        day_type = 1 if t.weekday() >= 5 else 0
        for slot in range(SLOTS):
            diff = abs(minute - slot * SLOT_MIN)
            if min(diff, 1440 - diff) <= core.PATTERN_WINDOW_MIN:
                cells.setdefault((code, day_type, slot), {}).setdefault(t.date(), []).append(total - parked)
    patterns = []
    for (code, day_type, slot), by_day in cells.items():
        value = statistics.median(statistics.median(v) for v in by_day.values())
        patterns.append((code, day_type, slot, round(value), len(by_day)))
    return patterns


def current_patterns():
    """지금 앱 DB의 패턴표를 그대로 읽습니다(--keep-patterns)."""
    if not ASSET_DB.exists():
        sys.exit(f"{ASSET_DB.relative_to(ROOT)}가 없어 패턴표를 이어 쓸 수 없어요. --keep-patterns 없이 실행해 주세요.")
    with sqlite3.connect(f"file:{ASSET_DB}?mode=ro", uri=True) as conn:
        return conn.execute("SELECT lot_id, day_type, slot, free, days FROM patterns").fetchall()


class NoLive:
    """정답 사례는 실시간 값 없이 계산합니다. 실시간 값은 매번 바뀌어서 대조할 수 없기 때문입니다."""

    def latest(self):
        return {}

    def history(self, code, since):
        return []


def golden_cases(src):
    """대조 지점: 고정 지점 2곳 + 위험 지점·주차장 근처에서 고른 무작위 지점."""
    rng = random.Random(7)
    points = [("광화문 세종대로", 37.57256, 126.97829), ("위험 없음(한강 위)", 37.5170, 126.9960)]
    for kind, count, max_offset in [("cctv", 10, 40), ("hydrant", 6, 8), ("crosswalk", 6, 12), ("bus_stop", 4, 12),
                                    ("bus_lane", 2, 30), ("intersection", 10, 30), ("school_zone", 6, 90),
                                    ("hotspot", 6, 25)]:
        rows = src.execute("SELECT lat, lon FROM risk_points WHERE kind = ? ORDER BY id", (kind,)).fetchall()
        for lat, lon in rng.sample(rows, count):
            dist, angle = rng.uniform(0, max_offset), math.radians(rng.uniform(0, 360))
            dlat = dist * math.cos(angle) / 111_320
            dlon = dist * math.sin(angle) / (111_320 * math.cos(math.radians(lat)))
            points.append((f"{kind} 근처", round(lat + dlat, 6), round(lon + dlon, 6)))
    lots = src.execute("SELECT lat, lon FROM parking_lots WHERE lat IS NOT NULL ORDER BY pklt_cd").fetchall()
    for lat, lon in rng.sample(lots, 4):
        points.append(("주차장 근처", round(lat + rng.uniform(-0.002, 0.002), 6), round(lon + rng.uniform(-0.002, 0.002), 6)))

    whens = ["2026-10-22T14:00", "2026-10-24T14:00", "2026-10-22T21:00"]  # 평일 낮, 주말 낮, 평일 밤
    now = datetime.datetime(2026, 10, 22, 14, 0)
    cases = []
    for i, (label, lat, lon) in enumerate(points):
        when_text = whens[i % len(whens)]
        when = datetime.datetime.strptime(when_text, "%Y-%m-%dT%H:%M")
        risks = core.find_risks(src, lat, lon, 5, when)
        lots = core.find_lots(src, NoLive(), lat, lon, 700, now, limit=5)
        cases.append({
            "label": label, "lat": lat, "lon": lon, "when": when_text, "margin_m": 5, "radius_m": 700,
            "risk_level": core.risk_level(risks),
            "risks": [{"kind": r["kind"], "distance_m": r["distance_m"], "active_now": r["active_now"],
                       "fine_krw": r["fine_krw_estimate"]} for r in risks],
            "lots": [{"id": l["id"], "distance_m": l["distance_m"], "per_10min_krw": l["fee"]["per_10min_krw"],
                      "per_hour_krw": l["fee"]["per_hour_krw"]} for l in lots],
        })
    return cases


def main():
    parser = argparse.ArgumentParser(description="앱 내장 DB와 정답 사례 만들기")
    parser.add_argument("--keep-patterns", action="store_true", help="패턴표는 지금 앱 DB 것을 그대로 써요")
    args = parser.parse_args()

    patterns = current_patterns() if args.keep_patterns else build_patterns(core.LIVE_DB)
    src = core.connect(core.RISK_DB)
    ASSET_DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = ASSET_DB.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    dst = sqlite3.connect(tmp)
    dst.executescript(SCHEMA)
    n_risks = export_risks(src, dst)
    frozen = set()
    if core.LIVE_DB.exists():
        with sqlite3.connect(core.LIVE_DB) as live:
            frozen = core.frozen_lots(live)
    n_lots = export_lots(src, dst, frozen)
    n_frozen = sum(1 for (lot,) in src.execute("SELECT pklt_cd FROM parking_lots WHERE lat IS NOT NULL") if lot in frozen)
    dst.executemany("INSERT INTO patterns VALUES (?, ?, ?, ?, ?)", patterns)
    built_at = core.now_kst().strftime(core.TIME_FORMAT)
    dst.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("version", built_at), ("risk_points", str(n_risks)), ("parking_lots", str(n_lots)),
        ("patterns", str(len(patterns))), ("pattern_min_days", str(core.PATTERN_MIN_DAYS)),
        ("frozen_lots", str(n_frozen)),
    ])
    dst.commit()
    dst.execute("VACUUM")
    dst.close()
    tmp.replace(ASSET_DB)

    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    cases = golden_cases(src)
    GOLDEN.write_text(json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    src.close()

    usable = sum(1 for p in patterns if p[4] >= core.PATTERN_MIN_DAYS)
    print(f"앱 DB: {ASSET_DB.relative_to(ROOT)} ({ASSET_DB.stat().st_size / 1e6:.1f}MB, 버전 {built_at})")
    print(f"  위험 지점 {n_risks:,}곳, 주차장 {n_lots:,}곳, 패턴 칸 {len(patterns):,}개 (예측에 쓸 수 있는 칸 {usable:,}개"
          f"{', 지금 앱 DB 것 그대로' if args.keep_patterns else ''})")
    print(f"  값이 멈춘 주차장 {len(frozen)}곳(앱 DB에 있는 곳 {n_frozen}곳)은 실시간 빈자리를 쓰지 않아요"
          f"(수집 기간 하루 평균 {core.FROZEN_CHANGES_PER_DAY}번 미만 바뀜)")
    print(f"정답 사례: {GOLDEN.relative_to(ROOT)} ({len(cases)}개 지점)")


if __name__ == "__main__":
    main()
