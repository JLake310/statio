#!/usr/bin/env python3
"""한 지점에 차를 세웠을 때의 주정차 위험과 근처 주차장(실시간 빈자리 포함)을 터미널에서 조회합니다.

판단 로직은 빈자리 서버와 같은 server/parking_core.py를 씁니다.

    python3 datapipe/risk_lookup.py 37.48352 127.03436
    python3 datapipe/risk_lookup.py 37.5724 126.9769 --when "2026-10-23 14:00" --eta 15
"""

import argparse
import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
import parking_core as core  # noqa: E402

BASIS = {"pattern": "지난 {days}일 같은 시간대", "trend": "최근 30분 추세", "current": "지금 값 그대로"}


def main():
    parser = argparse.ArgumentParser(description="주정차 위험과 근처 주차장 조회")
    parser.add_argument("lat", type=float)
    parser.add_argument("lon", type=float)
    parser.add_argument("--margin", type=float, default=5, help="GPS 오차 여유(m), 기본 5")
    parser.add_argument("--radius", type=float, default=700, help="주차장 검색 반경(m), 기본 700")
    parser.add_argument("--when", help="판단 시각 'YYYY-MM-DD HH:MM', 기본은 지금")
    parser.add_argument("--eta", type=float, help="도착까지 남은 분. 주면 도착 시점 예상 빈자리를 함께 보여 줍니다")
    args = parser.parse_args()

    now = core.now_kst()
    when = datetime.datetime.strptime(args.when, "%Y-%m-%d %H:%M") if args.when else now
    conn = core.connect(core.RISK_DB)
    live = core.LiveData()

    print(f"위치 {args.lat}, {args.lon} / 판단 시각 {when:%Y-%m-%d %H:%M}")
    risks = core.find_risks(conn, args.lat, args.lon, args.margin, when)
    if not risks:
        print("주정차 위험: 근처에서 알려진 단속·금지 지점을 찾지 못했습니다.")
    for r in risks:
        note = ""
        if r["kind"] == "school_zone":
            note = " ← 지금 주민신고 시간대입니다" if r["active_now"] else " (지금은 주민신고 시간대가 아닙니다)"
        print(f"  ⚠ {r['label']} {r['distance_m']}m · {r['name']} · {r['rule']}{note}")

    print(f"근처 주차장 (반경 {int(args.radius)}m, 좌표가 있는 곳만):")
    lots = core.find_lots(conn, live, args.lat, args.lon, args.radius, now, args.eta, limit=5)
    if not lots:
        print("  찾지 못했습니다.")
    for l in lots:
        rt = l["realtime"]
        status = f"빈자리 {rt['free']}면 · {rt['age_min']}분 전" if rt else "실시간 정보 없음"
        if rt and "predicted" in rt:
            p = rt["predicted"]
            status += f" → 도착 시 예상 {p['free']}면({BASIS[p['basis']].format(days=p['days'])})"
        fee = f"1시간 약 {l['fee']['per_hour_krw']:,}원" if l["fee"]["per_hour_krw"] is not None else "요금 정보 없음"
        print(f"  🅿 {l['name']}({l['category'] or '구분 없음'}) {l['distance_m']}m · {status} · {fee}")


if __name__ == "__main__":
    main()
