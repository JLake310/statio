#!/usr/bin/env python3
"""Pleos 에뮬레이터 Virtual Console을 터미널에서 조작합니다.

콘솔은 웹 화면이 열려 있다는 신호(heartbeat)가 있어야 값을 받습니다. 이 스크립트는 실행하는 동안
그 신호를 대신 보냅니다. 먼저 `adb forward tcp:8088 tcp:8088`이 되어 있어야 합니다.

    python3 tools/vconsole.py gear P            # P·R·N·D
    python3 tools/vconsole.py speed 60          # km/h, 0이면 정지
    python3 tools/vconsole.py door fl open      # fl·fr·rl·rr, open·close
    python3 tools/vconsole.py seat driver off   # driver·passenger·rear_left·rear_right, on·off (콘솔 기준)
    python3 tools/vconsole.py state
    python3 tools/vconsole.py scenario park     # 주행 → 정지 → P단 → 운전석 문 열림 → 하차

앱에서 받는 좌석 탑승 값은 콘솔 값과 반대로 옵니다(docs/VehicleSDK_명세.md 6장).
"""

import argparse
import json
import threading
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8088"
GEARS = {"P": "Park", "R": "Reverse", "N": "Neutral", "D": "Drive"}
DOORS = ("fl", "fr", "rl", "rr")
SEATS = ("driver", "passenger", "rear_left", "rear_right")


def request(method, path):
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} 실패: HTTP {e.code} {e.read().decode(errors='ignore')[:200]}")
    except urllib.error.URLError as e:
        raise SystemExit(f"콘솔에 연결하지 못했습니다 ({e.reason}). adb forward tcp:8088 tcp:8088을 확인하세요.")
    return json.loads(body) if body else {}


class Heartbeat:
    """콘솔 웹 화면처럼 1초마다 세션 신호를 보냅니다."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            try:
                request("POST", "/browser/session?client=vconsole-cli")
            except SystemExit:
                pass
            self._stop.wait(0.8)

    def __enter__(self):
        self._thread.start()
        time.sleep(1.5)  # 콘솔이 활성 상태로 바뀔 시간
        return self

    def __exit__(self, *exc):
        self._stop.set()
        request("POST", "/browser/session?client=vconsole-cli&closing=1")


def set_gear(g):
    request("PUT", f"/vehicle/gear?gear={GEARS[g]}")
    print(f"기어 {g}")


def set_speed(kph):
    pedal = 30 if kph > 0 else 0
    request("PUT", f"/vehicle/controls?speed={kph}&accelerator={pedal}&brake=0&steering=0")
    print(f"속도 {kph} km/h")


def set_door(door, is_open):
    request("PUT", f"/vehicle/body?door_{door}={'true' if is_open else 'false'}")
    print(f"도어 {door} {'열림' if is_open else '닫힘'}")


def set_seat(seat, occupied):
    request("PUT", f"/vehicle/seat?kind=occupied&area={seat}&value={'true' if occupied else 'false'}")
    print(f"좌석 {seat} {'탑승' if occupied else '비움'} (콘솔 기준)")


def print_state():
    s = request("GET", "/vehicle/state")
    gear = (s.get("gear", {}).get("actual") or {}).get("value")
    print(f"콘솔 활성: {s.get('browser', {}).get('active')}")
    print(f"기어: {gear} · 속도: {s.get('controls', {}).get('speed_kph')} km/h")
    print(f"도어: {s.get('body', {}).get('doors')}")
    print(f"탑승: {s.get('seat', {}).get('occupied')}")


def scenario_park(pause):
    """Statio 핵심 장면: 주행하다 멈추고 P단에 둔 뒤 운전자가 내립니다."""
    set_seat("driver", True)
    set_gear("D")
    set_speed(30)
    time.sleep(pause)
    set_speed(0)
    time.sleep(pause)
    set_gear("P")
    time.sleep(pause)
    set_door("fl", True)
    time.sleep(pause)
    set_seat("driver", False)
    time.sleep(pause)
    set_door("fl", False)


def main():
    parser = argparse.ArgumentParser(description="Pleos Virtual Console 조작")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("gear").add_argument("value", choices=list(GEARS))
    sub.add_parser("speed").add_argument("kph", type=int)
    p = sub.add_parser("door")
    p.add_argument("door", choices=DOORS)
    p.add_argument("action", choices=("open", "close"))
    p = sub.add_parser("seat")
    p.add_argument("seat", choices=SEATS)
    p.add_argument("action", choices=("on", "off"))
    sub.add_parser("state")
    p = sub.add_parser("scenario")
    p.add_argument("name", choices=("park",))
    p.add_argument("--pause", type=float, default=2.0, help="단계 사이 대기(초), 기본 2")
    args = parser.parse_args()

    if args.cmd == "state":
        print_state()
        return
    with Heartbeat():
        if args.cmd == "gear":
            set_gear(args.value)
        elif args.cmd == "speed":
            set_speed(args.kph)
        elif args.cmd == "door":
            set_door(args.door, args.action == "open")
        elif args.cmd == "seat":
            set_seat(args.seat, args.action == "on")
        elif args.cmd == "scenario":
            scenario_park(args.pause)
        time.sleep(0.5)  # 마지막 값이 앱까지 전달될 시간


if __name__ == "__main__":
    main()
