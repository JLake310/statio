#!/usr/bin/env python3
"""Statio 빈자리 서버입니다. 차량 앱이 주변 주차장, 실시간·예상 빈자리, 주정차 위험을 조회합니다.

    python3 server/app.py                  # http://127.0.0.1:8787
    python3 server/app.py --port 9000

Pleos 에뮬레이터에서는 호스트 PC를 http://10.0.2.2:8787 로 부릅니다.
API 설명은 server/README.md를 보세요.
"""

import argparse
import contextlib
import datetime
import json
import pathlib
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import parking_core as core  # noqa: E402

live = None


def risk_db():
    """SQLite 연결은 스레드 사이에 나눠 쓰지 않도록 요청마다 새로 열고 닫습니다(읽기 전용이라 가볍습니다)."""
    return contextlib.closing(core.connect(core.RISK_DB))


class BadRequest(Exception):
    pass


def number(params, name, default=None, lo=None, hi=None):
    raw = params.get(name, [None])[0]
    if raw is None:
        if default is None:
            raise BadRequest(f"{name} 값이 필요합니다.")
        return default
    try:
        value = float(raw)
    except ValueError:
        raise BadRequest(f"{name} 값이 숫자가 아닙니다: {raw}")
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        raise BadRequest(f"{name} 값은 {lo}~{hi} 범위여야 합니다.")
    return value


def location(params):
    return number(params, "lat", lo=33, hi=39), number(params, "lon", lo=124, hi=132)


def handle_nearby(params):
    lat, lon = location(params)
    radius = number(params, "radius", 700, 50, 3000)
    eta = params.get("eta_min", [None])[0]
    eta_min = number(params, "eta_min", lo=0, hi=180) if eta is not None else None
    limit = int(number(params, "limit", 10, 1, 50))
    now = core.now_kst()
    with risk_db() as conn:
        lots = core.find_lots(conn, live, lat, lon, radius, now, eta_min, limit)
    return {"location": {"lat": lat, "lon": lon}, "generated_at": now.strftime(core.TIME_FORMAT), "lots": lots}


def handle_check(params):
    lat, lon = location(params)
    when_raw = params.get("when", [None])[0]
    try:
        when = datetime.datetime.strptime(when_raw, "%Y-%m-%dT%H:%M") if when_raw else core.now_kst()
    except ValueError:
        raise BadRequest("when 은 YYYY-MM-DDTHH:MM 형식이어야 합니다.")
    margin, radius = number(params, "margin", 5, 0, 30), number(params, "radius", 700, 50, 3000)
    with risk_db() as conn:
        return core.check_parking(conn, live, lat, lon, when, margin_m=margin, radius_m=radius)


def handle_lot(lot_id, params):
    with risk_db() as conn:
        row = conn.execute("SELECT * FROM parking_lots WHERE pklt_cd = ?", (lot_id,)).fetchone()
    if row is None:
        return None
    hours = int(number(params, "hours", 24, 1, 168))
    since = core.now_kst() - datetime.timedelta(hours=hours)
    series = [{"at": t.strftime(core.TIME_FORMAT), "free": max(0, tot - parked), "total": tot}
              for t, tot, parked in live.history(lot_id, since)]
    lot = {k: row[k] for k in row.keys() if k not in ("cell_lat", "cell_lon")}
    return {"lot": lot, "series": series}


def handle_health(_params):
    latest = live.latest()
    last = max((v[2] for v in latest.values()), default=None)
    return {"status": "ok", "realtime_lots": len(latest), "latest_update": last}


class Handler(BaseHTTPRequestHandler):
    server_version = "StatioServer/0.1"

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(url.query)
        routes = {"/health": handle_health, "/v1/lots/nearby": handle_nearby, "/v1/parking/check": handle_check}
        try:
            if url.path in routes:
                return self.send_json(200, routes[url.path](params))
            if url.path.startswith("/v1/lots/"):
                result = handle_lot(urllib.parse.unquote(url.path[len("/v1/lots/"):]), params)
                if result is not None:
                    return self.send_json(200, result)
                return self.send_json(404, {"error": "주차장을 찾지 못했습니다."})
            return self.send_json(404, {"error": "없는 경로입니다."})
        except BadRequest as e:
            return self.send_json(400, {"error": str(e)})

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{core.now_kst():%H:%M:%S}] {self.address_string()} {fmt % args}\n")


def main():
    global live
    parser = argparse.ArgumentParser(description="Statio 빈자리 서버")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()

    if not core.RISK_DB.exists():
        sys.exit("data/parking_risk.db가 없습니다. 먼저 python3 datapipe/build_risk_db.py 를 실행해 주세요.")
    live = core.LiveData()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Statio 빈자리 서버를 시작했습니다: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("서버를 멈췄습니다.")


if __name__ == "__main__":
    main()
