#!/usr/bin/env python3
"""서울시 시영주차장 실시간 주차 대수를 주기적으로 모아 SQLite에 저장합니다.

- 실시간 값: 서울 열린데이터광장 GetParkingInfo (시영주차장, 분 단위 갱신)
- 좌표: GetParkInfo (공영주차장 안내 정보)에서 주차장 코드로 맞춥니다.
- 인증키는 프로젝트 루트 .env의 SEOUL_OPEN_API_KEY에서 읽습니다.

사용법:
    python3 collector/seoul_parking_collector.py            # 5분 간격으로 계속 수집
    python3 collector/seoul_parking_collector.py --once     # 한 번만 수집
    python3 collector/seoul_parking_collector.py --stats    # 저장된 데이터 요약
"""

import argparse
import datetime
import json
import os
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "seoul_parking.db"
API_URL = "http://openapi.seoul.go.kr:8088/{key}/json/{svc}/{start}/{end}/"
PAGE_SIZE = 1000  # 1회 호출 최대 건수
NO_DATA_CODE = "INFO-200"
KST = datetime.timezone(datetime.timedelta(hours=9))
COORD_REFRESH_SEC = 24 * 60 * 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
    pklt_cd     TEXT PRIMARY KEY,
    name        TEXT,
    addr        TEXT,
    total       INTEGER,
    lat         REAL,
    lon         REAL,
    basic_fee   REAL,
    basic_min   REAL,
    add_fee     REAL,
    add_min     REAL,
    day_max     REAL,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS snapshots (
    pklt_cd            TEXT NOT NULL,
    source_updated_at  TEXT NOT NULL,
    collected_at       TEXT NOT NULL,
    total              INTEGER,
    parked             INTEGER,
    PRIMARY KEY (pklt_cd, source_updated_at)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_time ON snapshots (source_updated_at);
"""


def log(msg):
    print(f"[{datetime.datetime.now(KST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_env(path):
    """KEY=VALUE 형식의 .env를 읽어 환경변수에 없는 값만 채웁니다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def fetch_all(key, svc):
    """1,000건씩 나눠 호출해 전체 행을 돌려줍니다."""
    rows, start = [], 1
    while True:
        url = API_URL.format(key=key, svc=svc, start=start, end=start + PAGE_SIZE - 1)
        with urllib.request.urlopen(url, timeout=20) as resp:
            data = json.load(resp)
        body = data.get(svc)
        if body is None:
            result = data.get("RESULT", {})
            if result.get("CODE") == NO_DATA_CODE:
                return rows
            raise RuntimeError(f"{svc} 호출 실패: {result.get('CODE')} {result.get('MESSAGE')}")
        rows += body.get("row", [])
        if len(rows) >= body.get("list_total_count", 0):
            return rows
        start += PAGE_SIZE


def as_int(value):
    return None if value in (None, "") else int(float(value))


def collect_snapshots(conn, key):
    """실시간 연계된 주차장의 현재 주차 대수를 저장하고 주차장 기본 정보를 갱신합니다."""
    now = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    rows = fetch_all(key, "GetParkingInfo")
    linked = [r for r in rows if r.get("PRK_STTS_YN") == "1" and r.get("NOW_PRK_VHCL_UPDT_TM")]
    before = conn.total_changes
    for r in rows:
        conn.execute(
            """INSERT INTO lots (pklt_cd, name, addr, total, basic_fee, basic_min, add_fee, add_min, day_max, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(pklt_cd) DO UPDATE SET
                 name=excluded.name, addr=excluded.addr, total=excluded.total,
                 basic_fee=excluded.basic_fee, basic_min=excluded.basic_min,
                 add_fee=excluded.add_fee, add_min=excluded.add_min,
                 day_max=excluded.day_max, updated_at=excluded.updated_at""",
            (r["PKLT_CD"], r.get("PKLT_NM"), r.get("ADDR"), as_int(r.get("TPKCT")),
             r.get("BSC_PRK_CRG"), r.get("BSC_PRK_HR"), r.get("ADD_PRK_CRG"),
             r.get("ADD_PRK_HR"), r.get("DAY_MAX_CRG"), now),
        )
    lot_changes = conn.total_changes - before
    for r in linked:
        conn.execute(
            """INSERT OR IGNORE INTO snapshots (pklt_cd, source_updated_at, collected_at, total, parked)
               VALUES (?, ?, ?, ?, ?)""",
            (r["PKLT_CD"], r["NOW_PRK_VHCL_UPDT_TM"], now,
             as_int(r.get("TPKCT")), as_int(r.get("NOW_PRK_VHCL_CNT"))),
        )
    conn.commit()
    new_snapshots = conn.total_changes - before - lot_changes
    log(f"시영주차장 {len(rows)}곳 중 실시간 연계 {len(linked)}곳, 새 기록 {new_snapshots}건을 저장했습니다.")


def refresh_coordinates(conn, key):
    """공영주차장 안내 정보에서 주차장 코드별 첫 번째 유효 좌표를 가져와 채웁니다."""
    coords = {}
    for r in fetch_all(key, "GetParkInfo"):
        lat, lon = r.get("LAT"), r.get("LOT")
        if lat and lon and r["PKLT_CD"] not in coords:
            coords[r["PKLT_CD"]] = (float(lat), float(lon))
    updated = 0
    for code, (lat, lon) in coords.items():
        updated += conn.execute(
            "UPDATE lots SET lat = ?, lon = ? WHERE pklt_cd = ?", (lat, lon, code)
        ).rowcount
    conn.commit()
    missing = conn.execute("SELECT COUNT(*) FROM lots WHERE lat IS NULL").fetchone()[0]
    log(f"좌표를 {updated}곳에 채웠습니다. 좌표가 없는 주차장은 {missing}곳입니다.")


def print_stats(conn):
    lots = conn.execute("SELECT COUNT(*), COUNT(lat) FROM lots").fetchone()
    snap = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT pklt_cd), MIN(source_updated_at), MAX(source_updated_at) FROM snapshots"
    ).fetchone()
    print(f"주차장 {lots[0]}곳 (좌표 있음 {lots[1]}곳)")
    print(f"기록 {snap[0]}건, 주차장 {snap[1]}곳, 기간 {snap[2]} ~ {snap[3]}")


def main():
    parser = argparse.ArgumentParser(description="서울시 시영주차장 실시간 주차 대수 수집기")
    parser.add_argument("--once", action="store_true", help="한 번만 수집하고 끝냅니다")
    parser.add_argument("--interval", type=int, default=300, help="수집 간격(초), 기본 300")
    parser.add_argument("--db", type=pathlib.Path, default=DEFAULT_DB, help="SQLite 파일 경로")
    parser.add_argument("--stats", action="store_true", help="저장된 데이터 요약만 보여 줍니다")
    args = parser.parse_args()

    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.executescript(SCHEMA)
    if args.stats:
        print_stats(conn)
        return

    load_env(ROOT / ".env")
    key = os.environ.get("SEOUL_OPEN_API_KEY")
    if not key:
        sys.exit("SEOUL_OPEN_API_KEY가 없습니다. 프로젝트 루트의 .env를 확인해 주세요.")

    last_coord_refresh = 0.0
    while True:
        try:
            collect_snapshots(conn, key)
            if time.time() - last_coord_refresh > COORD_REFRESH_SEC:
                refresh_coordinates(conn, key)
                last_coord_refresh = time.time()
        except (urllib.error.URLError, TimeoutError, RuntimeError, ValueError) as e:
            log(f"수집에 실패했습니다. 다음 주기에 다시 시도합니다: {e}")
        if args.once:
            break
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            log("수집을 멈췄습니다.")
            break


if __name__ == "__main__":
    main()
