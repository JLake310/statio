#!/usr/bin/env python3
"""주차 위험 판단과 주차장 안내에 쓰는 서울 열린데이터광장 데이터를 내려받습니다.

결과는 data/raw/seoul/<데이터셋ID>_<서비스명>.json 으로 저장합니다.
인증키는 프로젝트 루트 .env의 SEOUL_OPEN_API_KEY에서 읽습니다.

    python3 datapipe/fetch_seoul_data.py               # 전부
    python3 datapipe/fetch_seoul_data.py OA-15534      # 고른 데이터셋만 (나머지 원본은 그대로 둬요)
"""

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "raw" / "seoul"
API_URL = "http://openapi.seoul.go.kr:8088/{key}/json/{svc}/{start}/{end}/"
PAGE_SIZE = 1000

# (데이터셋 ID, 서비스명, 설명)
DATASETS = [
    ("OA-20471", "TbOpendataFixedcctv", "불법주정차/전용차로 위반 단속 CCTV 위치"),
    ("OA-20448", "TgAcdt19", "어린이보호구역 대상 시설 위치"),
    ("OA-15554", "trafficSafetyA004AInfo", "횡단보도 (EPSG:5186 좌표)"),
    ("OA-15534", "trafficSafetyA008PInfo", "교차로 중심점 (EPSG:5186 좌표, 신호 교차로 위주)"),
    ("OA-12811", "SdeHydrantgtW", "소화용수 위치"),
    ("OA-15067", "busStopLocationXyInfo", "버스정류소 위치"),
    ("OA-13122", "GetParkInfo", "공영주차장 안내 정보"),
]


def load_key():
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("SEOUL_OPEN_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("SEOUL_OPEN_API_KEY가 없습니다. 프로젝트 루트의 .env를 확인해 주세요.")


def fetch_all(key, svc):
    rows, start = [], 1
    while True:
        url = API_URL.format(key=key, svc=svc, start=start, end=start + PAGE_SIZE - 1)
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = json.load(resp)
        body = data.get(svc)
        if body is None:
            result = data.get("RESULT", {})
            if result.get("CODE") == "INFO-200":
                return rows
            raise RuntimeError(f"{svc} 호출 실패: {result.get('CODE')} {result.get('MESSAGE')}")
        rows += body.get("row", [])
        if len(rows) >= body.get("list_total_count", 0):
            return rows
        start += PAGE_SIZE


def main():
    parser = argparse.ArgumentParser(description="서울 열린데이터광장 원본 데이터 내려받기")
    parser.add_argument("ids", nargs="*", help="받을 데이터셋 ID (비우면 전부)")
    args = parser.parse_args()
    unknown = set(args.ids) - {oa for oa, _, _ in DATASETS}
    if unknown:
        sys.exit(f"모르는 데이터셋 ID예요: {', '.join(sorted(unknown))}")
    key = load_key()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for oa, svc, desc in DATASETS:
        if args.ids and oa not in args.ids:
            continue
        rows = fetch_all(key, svc)
        (OUT_DIR / f"{oa}_{svc}.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        print(f"{oa} {desc}: {len(rows)}행을 저장했습니다.")
        time.sleep(0.3)


if __name__ == "__main__":
    main()
