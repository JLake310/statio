#!/usr/bin/env python3
"""실제 불법 주정차 단속 기록과 위험 지점 DB를 맞대서, Statio가 단속 자리에서 미리 경고했을지 셉니다.

입력: 서울시 불법주정차 단속 정보 분기별 CSV(datapipe/enforcement.py가 없으면 받아요)
      data/parking_risk.db (datapipe/build_risk_db.py)
판단: 앱·server/parking_core.find_risks와 같은 규칙이에요. 위험 지점마다 '판단 반경 + 여유 5m' 안이면 경고,
      어린이보호구역은 평일 08~20시에만 '단속 위험', 그 밖에는 '주의'예요.
비교: 같은 단속 기록을 100~300m 아무 방향으로 옮긴 지점에서도 셉니다. 옮긴 지점은 건물 안처럼 차를 세울 수 없는
      자리도 섞여서, 차이는 '단속이 몰리는 자리를 얼마나 더 잘 짚는지'의 대략적인 크기로만 봐요.
단속 잦은 자리: DB에 든 단속 잦은 자리는 맞대는 분기의 기록으로 만든 것일 수 있어서 기본 대조에서는 빼요.
      대신 --hotspots로 '지난 분기 기록으로 만들고 다음 분기 단속과 맞대는' 시험을 따로 해요.

    python3 datapipe/check_enforcement.py                          # 2025년 4분기, 지금 위험 지점
    python3 datapipe/check_enforcement.py 2025Q3 2025Q4
    python3 datapipe/check_enforcement.py --hotspots 2025Q3:2025Q4 2025Q1:2025Q4   # 자리와 몰리는 시간대 검증
    python3 datapipe/check_enforcement.py --school-radii 2025Q3 2025Q4   # 어린이보호구역 반경 비교
"""

import argparse
import collections
import math
import random
import sqlite3
import sys

import enforcement as enf

sys.path.insert(0, str(enf.ROOT / "server"))
from parking_core import SEVERITY, distance_m  # noqa: E402  앱과 같은 거리 계산·우선순위를 써요

RISK_DB = enf.ROOT / "data" / "parking_risk.db"
MARGIN_M = 5      # 앱과 같은 여유 거리
CELL = 1000       # 위경도 × 1000 격자 (약 110m × 90m)
SCHOOL, HOTSPOT = "school_zone", "hotspot"
SCHOOL_RADII = [30, 50, 75, 100, 150, 200, 300]
KINDS = [k for k in sorted(SEVERITY, key=SEVERITY.get) if k != HOTSPOT]


class RiskGrid:
    """위험 지점을 격자에 담아 둡니다. 어린이보호구역(100m)만 한 칸 더 멀리까지 찾아요."""

    def __init__(self, points):
        self.near, self.far = collections.defaultdict(list), collections.defaultdict(list)
        for kind, lat, lon, radius in points:
            grid = self.far if kind == SCHOOL else self.near
            grid[(int(lat * CELL), int(lon * CELL))].append((kind, lat, lon, radius))

    @classmethod
    def from_db(cls, db):
        return cls(sqlite3.connect(db).execute(
            "SELECT kind, lat, lon, radius_m FROM risk_points WHERE kind != ?", (HOTSPOT,)))

    def kinds_at(self, lat, lon):
        """반경 안에 든 위험 종류와 가장 가까운 거리입니다."""
        hit = {}
        cl, co = int(lat * CELL), int(lon * CELL)
        for grid, reach in ((self.near, 1), (self.far, 2)):
            for a in range(cl - reach, cl + reach + 1):
                for b in range(co - reach, co + reach + 1):
                    for kind, plat, plon, radius in grid.get((a, b), ()):
                        d = distance_m(lat, lon, plat, plon)
                        if d <= radius + MARGIN_M and d < hit.get(kind, math.inf):
                            hit[kind] = d
        return hit


def tally(grid, points, hot=None):
    """경고 비율을 셉니다. points는 (위도, 경도, 시각) 목록이고, hot을 주면 단속 잦은 자리도 함께 봐요."""
    c = collections.Counter()
    for lat, lon, when in points:
        hit = grid.kinds_at(lat, lon)
        if hot is not None and hot.kinds_at(lat, lon):
            hit[HOTSPOT] = 0
        if not hit:
            continue
        school_active = when.weekday() < 5 and 8 <= when.hour < 20
        c["any"] += 1
        c["high"] += any(k != SCHOOL or school_active for k in hit)
        c["no_school"] += any(k not in (SCHOOL, HOTSPOT) for k in hit)
        c["no_school_with_hot"] += any(k != SCHOOL for k in hit)
        for k in hit:
            c[k] += 1
    return {k: v / len(points) for k, v in c.items()} | {"n": len(points)}


def shifted(points, rng):
    out = []
    for lat, lon, when in points:
        d, a = rng.uniform(100, 300), rng.uniform(0, 2 * math.pi)
        out.append((lat + d * math.cos(a) / 111320, lon + d * math.sin(a) / (111320 * math.cos(math.radians(lat))), when))
    return out


def unique_spots(records):
    """약 1m 단위로 같은 자리는 한 번만 셉니다."""
    return [(lat, lon, w) for (lat, lon), w in {(round(la, 5), round(lo, 5)): w for la, lo, w in records}.items()]


def kind_report(quarters, grid, rng):
    print("| 대상 | 건수 | 경고 범위 안 | 그 시각 '단속 위험' | 어린이보호구역 빼고 | " + " | ".join(KINDS) + " |")
    print("|---" * (5 + len(KINDS)) + "|")
    for quarter in quarters:
        records, skipped = enf.load_records(enf.download(quarter))
        top = collections.Counter((round(lat, 5), round(lon, 5)) for lat, lon, _ in records)
        top_share = sum(c for _, c in top.most_common(len(top) // 100)) / len(records)
        for title, points in ((f"{quarter} 단속 기록", records), (f"{quarter} 단속 자리(중복 뺌)", unique_spots(records)),
                              (f"{quarter} 100~300m 옮긴 자리", shifted(records, rng))):
            s = tally(grid, points)
            print(f"| {title} | {s['n']:,} | {s.get('any', 0):.1%} | {s.get('high', 0):.1%} | {s.get('no_school', 0):.1%} | "
                  + " | ".join(f"{s.get(k, 0):.1%}" for k in KINDS) + " |")
        print(f"<!-- {quarter}: 좌표 없음·서울 밖 {skipped:,}건 제외, 단속 자리 {len(top):,}곳, "
              f"상위 1% 자리가 단속의 {top_share:.0%} -->")


def hotspot_report(pairs, grid, rng):
    """지난 분기(만들기)로 만든 단속 잦은 자리가 다음 분기(맞대기) 단속을 얼마나 미리 짚었는지 셉니다."""
    print("| 만들기 → 맞대기 | 단속 잦은 자리 | 대상 | 건수 | 단속 잦은 자리 안 | 기존(어린이보호구역 빼고) | 기존 + 단속 잦은 자리 | 경고 범위 안(전체) |")
    print("|---|---|---|---|---|---|---|---|")
    for pair in pairs:
        train, test = pair.split(":")
        spots = enf.hotspots(enf.load_records(enf.download(train))[0])
        hot = RiskGrid((HOTSPOT, lat, lon, enf.HOTSPOT_RADIUS_M - MARGIN_M) for lat, lon, *_ in spots)  # 반경 30m 그대로
        records = enf.load_records(enf.download(test))[0]
        for title, points in (("단속 기록", records), ("단속 자리(중복 뺌)", unique_spots(records)),
                              ("100~300m 옮긴 자리", shifted(records, rng))):
            s = tally(grid, points, hot)
            print(f"| {train} → {test} | {len(spots):,}곳 | {title} | {s['n']:,} | {s.get(HOTSPOT, 0):.1%} | "
                  f"{s.get('no_school', 0):.1%} | {s.get('no_school_with_hot', 0):.1%} | {s.get('any', 0):.1%} |")


def hotspot_time_report(pairs):
    """지난 분기로 정한 '단속이 몰리는 3시간'에 다음 분기 그 자리 단속이 얼마나 드는지 셉니다.

    비교: 하루 중 3시간(12.5%), 서울 전체에서 단속이 가장 많은 3시간을 모든 자리에 똑같이 쓸 때.
    """
    print("| 만들기 → 맞대기 | 대상 자리 | 다음 분기 단속 | 그 자리 시간대 안 | 서울 전체 최다 시간대 안 | 하루 중 3시간 |")
    print("|---|---|---|---|---|---|")
    w = enf.HOTSPOT_PEAK_HOURS
    for pair in pairs:
        train, test = (enf.load_records(enf.download(q))[0] for q in pair.split(":"))
        spots = [(lat, lon) for lat, lon, _, _ in enf.hotspots(train)]
        grid = collections.defaultdict(list)
        for i, (lat, lon) in enumerate(spots):
            grid[(int(lat * CELL), int(lon * CELL))].append(i)

        def times_by_spot(records):
            out = collections.defaultdict(list)
            for lat, lon, when in records:
                cl, co = int(lat * CELL), int(lon * CELL)
                for i in {i for a in (cl - 1, cl, cl + 1) for b in (co - 1, co, co + 1) for i in grid.get((a, b), ())}:
                    if distance_m(lat, lon, *spots[i]) <= enf.HOTSPOT_RADIUS_M:
                        out[i].append((when.weekday() < 5, when.hour))
            return out

        before, after = times_by_spot(train), times_by_spot(test)
        hours = collections.Counter(h for t in before.values() for _, h in t)
        city = max(range(24), key=lambda h: sum(hours[(h + k) % 24] for k in range(w)))
        for title, keep in (("전체", lambda share: True), ("몰림 뚜렷(앱이 시간대를 보여 주는 자리)", lambda share: share >= enf.HOTSPOT_PEAK_SHARE)):
            own = cityhit = total = n = 0
            for i, t in before.items():
                _, start, share = enf.peak_window(t)
                if not keep(share):
                    continue
                n += 1
                later = after.get(i, [])
                total += len(later)
                own += sum(1 for _, h in later if (h - start) % 24 < w)
                cityhit += sum(1 for _, h in later if (h - city) % 24 < w)
            print(f"| {pair.replace(':', ' → ')} | {title} {n:,}곳 | {total:,} | {own / total:.1%} | "
                  f"{cityhit / total:.1%} ({city}~{(city + w) % 24}시) | {w / 24:.1%} |")


def school_radius_report(quarters, seed, sample=120_000):
    """어린이보호구역 반경을 바꿔 가며 단속 기록과 옮긴 자리가 시설 반경 안에 드는 비율을 셉니다(분기마다 표본)."""
    conn = sqlite3.connect(RISK_DB)
    schools = collections.defaultdict(list)
    for lat, lon in conn.execute("SELECT lat, lon FROM risk_points WHERE kind = ?", (SCHOOL,)):
        schools[(int(lat * CELL), int(lon * CELL))].append((lat, lon))
    others = RiskGrid(conn.execute("SELECT kind, lat, lon, radius_m FROM risk_points WHERE kind NOT IN (?, ?)", (SCHOOL, HOTSPOT)))

    def nearest(lat, lon):
        cl, co = int(lat * CELL), int(lon * CELL)
        return min((distance_m(lat, lon, a, b) for x in range(cl - 4, cl + 5) for y in range(co - 4, co + 5)
                    for a, b in schools.get((x, y), ())), default=math.inf)

    rng = random.Random(seed)
    records = []
    for quarter in quarters:
        recs = enf.load_records(enf.download(quarter))[0]
        records += rng.sample(recs, min(sample, len(recs)))
    rows = {name: [(nearest(lat, lon), bool(others.kinds_at(lat, lon))) for lat, lon, _ in points]
            for name, points in (("record", records), ("shifted", shifted(records, rng)))}
    print(f"| 반경 | 단속 기록 안 | 옮긴 자리 안 | 배수 | 어린이보호구역만 걸린 옮긴 자리 |  ({', '.join(quarters)} 표본 {len(records):,}건)")
    print("|---|---|---|---|---|")
    for r in SCHOOL_RADII:
        share = {k: sum(d <= r + MARGIN_M for d, _ in v) / len(v) for k, v in rows.items()}
        only = sum(d <= r + MARGIN_M and not o for d, o in rows["shifted"]) / len(rows["shifted"])
        print(f"| {r}m | {share['record']:.1%} | {share['shifted']:.1%} | {share['record'] / share['shifted']:.2f} | {only:.1%} |")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("quarters", nargs="*", help=f"분기 ({', '.join(enf.QUARTERS)})")
    parser.add_argument("--hotspots", nargs="+", default=[], metavar="만들기:맞대기",
                        help="단속 잦은 자리 시험. 예: 2025Q3:2025Q4")
    parser.add_argument("--school-radii", action="store_true", help="어린이보호구역 반경별 비교(분기는 앞에 적어요)")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if not args.quarters and not args.hotspots:
        args.quarters = ["2025Q4"]
    if args.school_radii:
        school_radius_report(args.quarters, args.seed)
        return
    for quarter in args.quarters:
        if quarter not in enf.QUARTERS:
            parser.error(f"{quarter}: 받을 수 있는 분기는 {', '.join(enf.QUARTERS)}예요")
    for pair in args.hotspots:
        if any(q not in enf.QUARTERS for q in pair.split(":")) or pair.count(":") != 1:
            parser.error(f"{pair}: '2025Q3:2025Q4'처럼 적어 주세요 ({', '.join(enf.QUARTERS)})")

    grid = RiskGrid.from_db(RISK_DB)
    rng = random.Random(args.seed)
    if args.quarters:
        kind_report(args.quarters, grid, rng)
    if args.hotspots:
        hotspot_report(args.hotspots, grid, rng)
        hotspot_time_report(args.hotspots)


if __name__ == "__main__":
    main()
