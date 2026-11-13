#!/usr/bin/env python3
"""Statio 시연 장면을 에뮬레이터에서 자동으로 재생하고 화면을 녹화합니다.

장면 (약 1분 10초)
  1. 도착 전 비교: 주행 중 목적지(광화문)를 정하면 차 안 AI가 근처 주차장의 도착 때 빈자리와 확률을 예측해요 → 닫기
     자막의 AI 예측 숫자는 앱 로그(StatioAI)에서 그대로 가져와요
  2. 길가 P단: 광화문 세종대로 길가에 세우면 단속 CCTV·단속 잦은 자리 경고 → '주차장으로 안내'
     경고 자막의 거리·과태료·단속 횟수는 앱 로그(StatioDemo의 check 줄)에서 그대로 가져와요
  3. 주차장 P단: 세종로 공영주차장에 세우고 운전석 문을 열면 층 선택(B2)과 휴대폰 QR
  4. 월간 리포트: 방금 경고를 받고 주차장으로 옮긴 기록이 이번 달 리포트에 쌓여요
     녹화 전에 리포트 기록을 비워서, 영상의 숫자는 이 시연에서 실제로 쌓인 기록만 보여 줘요

준비
  - Pleos Connect 에뮬레이터 실행, `adb forward tcp:8088 tcp:8088`, 앱 설치
  - 내비 옆 분할 창으로 찍으려면 --split (또는 화면 오른쪽 핸들을 미리 가운데로 끌어 두세요)

    python3 tools/demo.py                  # 녹화하면서 재생 → data/demo/statio_demo_날짜.mp4 와 .srt(장면 자막)
    python3 tools/demo.py --no-record      # 녹화 없이 재생만
    python3 tools/demo.py --split          # 내비 옆 분할 창으로 맞추고 시작
    python3 tools/demo.py --pace 1.5       # 장면 사이 대기를 1.5배로

동작 방식
  - 차 신호(기어·속도·도어)는 Virtual Console로 바꿔요(tools/vconsole.py).
  - 앱은 화면이 바뀔 때 로그(StatioDemo)에 'screen=…'을 남기고, locate 명령을 받으면 버튼의 화면 좌표를 알려 줘요.
    스크립트는 그 좌표를 실제 터치로 눌러요. 내비 지도가 계속 움직여 화면 덤프(uiautomator)가 자주 실패해서 이렇게 해요.
  - 녹화가 끝나면 장면별 확인용 캡처도 같은 폴더에 남겨요.
"""

import argparse
import datetime
import math
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import vconsole as vc  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "demo"
APP = "com.statio.app"
MAIN = f"{APP}/.MainActivity"
LOG_TAG = "StatioDemo"
AI_TAG = "StatioAI"
APP_DB = ROOT / "android" / "app" / "src" / "main" / "assets" / "statio.db"
ADB = shutil.which("adb") or str(pathlib.Path.home() / "Library/Android/sdk/platform-tools/adb")
REMOTE_VIDEO = "/sdcard/statio_demo.mp4"
DESTINATION = ("37.5724", "126.9769", "광화문")


# ── adb와 앱 ────────────────────────────────────────────

def adb(*args, check=True):
    result = subprocess.run([ADB, *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"adb {' '.join(args)} 실패: {result.stderr.strip()}")
    return result.stdout


def app(*extras):
    """앱에 시연 명령을 보냅니다. 예: app('--es', 'cmd', 'reset')"""
    adb("shell", "am", "start", "-n", MAIN, *extras)


def app_log():
    return adb("logcat", "-d", "-s", f"{LOG_TAG}:I", check=False)


def after(action, pattern, timeout=15.0):
    """로그를 비우고 action을 한 뒤, 앱 로그에 pattern이 나올 때까지 기다려 일치 결과를 돌려줘요."""
    adb("logcat", "-c", check=False)
    action()
    end = time.time() + timeout
    while time.time() < end:
        m = re.search(pattern, app_log())
        if m:
            return m
        time.sleep(0.3)
    raise SystemExit(f"앱 로그에서 '{pattern}'을(를) 보지 못했어요")


def check_captions():
    """방금 P단 판단 로그로 경고 자막을 만들어요. 단속 잦은 자리가 있으면 실제 단속 횟수도 한 줄 더해요."""
    m = re.search(r"check fine=(\d+) risks=(\S.*)", app_log())
    if not m:
        return ["P단 순간, 단속 위험과 과태료를 바로 알려요"]
    risks = [r.split("|", 3) for r in m.group(2).strip().split(";")]
    _, label, dist, _ = risks[0]
    out = [f"P단 순간, {label} {dist}m · 과태료 {int(m.group(1)) // 10000}만 원을 바로 알려요"]
    hot = next((r for r in risks if r[0] == "hotspot"), None)
    # 단속 잦은 자리 이름: '224번 단속 (25년 4분기)' 또는 '55번 · 평일 18~21시 (25년 4분기)'
    m = re.match(r"^([\d,]+)번(?: 단속)?(?: · (.+?))? \((.+)\)$", hot[3]) if hot else None
    if m:
        count, peak, period = m.groups()
        more = f"주로 {peak}에 몰려요" if peak else "지난 단속 기록으로 미리 알아요"
        out.append(f"실제로 {period}에 {count}번 단속된 자리예요. {more}")
    return out


def ai_caption(dest_lat, dest_lon):
    """방금 화면에 쓴 AI 예측 로그에서 맨 위 카드(빈자리 확률 50% 이상 중 목적지에서 가장 가까운 곳)를 골라 자막을 만들어요."""
    lines = adb("logcat", "-d", "-s", f"{AI_TAG}:I", check=False)
    preds = {m.group(1): (int(m.group(2)), int(m.group(3)), float(m.group(4)))
             for m in re.finditer(r"ai lot=(\S+) eta=[\d.]+ now=(\d+) base=\S+ ai=(\d+) p=([\d.]+)", lines)}
    if not preds:
        return None
    with sqlite3.connect(APP_DB) as conn:
        rows = conn.execute(f"SELECT id, name, lat_e6, lon_e6 FROM parking_lots WHERE id IN ({','.join('?' * len(preds))})", list(preds)).fetchall()

    def dist(lat_e6, lon_e6):
        dy = (lat_e6 / 1e6 - dest_lat) * 111_000
        dx = (lon_e6 / 1e6 - dest_lon) * 111_000 * math.cos(math.radians(dest_lat))
        return math.hypot(dx, dy)

    lot_id, name, *_ = min(rows, key=lambda r: (preds[r[0]][2] < 0.5, dist(r[2], r[3])))
    _, ai, p = preds[lot_id]
    name = re.sub(r"\(.\)$", "", name).strip()
    count = "만차" if ai == 0 else f"약 {ai:,}면"
    return f"AI 예측: {name}, 도착 때 {count} · 빈자리 확률 {min(max(round(p * 100), 1), 99)}%"


def tap(target):
    """앱에 버튼 좌표를 물어 실제 터치로 눌러요. target은 뷰 id(btn_route) 또는 text:B2."""
    m = after(lambda: app("--es", "cmd", "locate", "--es", "target", target),
              rf"locate {re.escape(target)} (\d+|none)(?: (\d+))?", timeout=8)
    if m.group(1) == "none":
        raise SystemExit(f"화면에서 {target}을(를) 찾지 못했어요")
    adb("shell", "input", "tap", m.group(1), m.group(2))


def app_bounds():
    """Statio 창의 화면 좌표 (왼쪽, 위, 오른쪽, 아래)입니다."""
    out = adb("shell", "dumpsys", "window", "windows")
    block = out[out.find(f"{APP}/{APP}.MainActivity}}"):]
    m = re.search(r"\bframe=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", block)
    return tuple(map(int, m.groups())) if m else None


def app_width():
    """Statio 창의 폭(px)입니다. 분할 창이면 약 840, 전체 창이면 약 1680입니다."""
    b = app_bounds()
    return b[2] - b[0] if b else 0


def ensure_split():
    """내비 옆 분할 창으로 맞춥니다. 화면 오른쪽 끝 핸들을 가운데로 끌어요."""
    if 0 < app_width() < 1200:
        return
    adb("shell", "input", "swipe", "2547", "719", "1700", "719", "600")
    time.sleep(2)
    if not 0 < app_width() < 1200:
        print("분할 창으로 바꾸지 못했어요. 화면 오른쪽 핸들(⋮)을 가운데로 직접 끌어 주세요.")


# ── 녹화와 자막 ────────────────────────────────────────────

class Recorder:
    """adb screenrecord로 녹화하고, 장면 설명을 녹화 시작 기준 시각으로 모아 .srt 자막을 씁니다."""

    def __init__(self, out: pathlib.Path, enabled: bool):
        self.out, self.enabled = out, enabled
        self.proc = None
        self.started = time.time()
        self.captions = []  # (시작 초, 문장)

    def start(self):
        if self.enabled:
            adb("shell", "rm", "-f", REMOTE_VIDEO, check=False)
            self.proc = subprocess.Popen([ADB, "shell", "screenrecord", "--size", "1920x1080", "--bit-rate", "12000000",
                                          "--time-limit", "180", REMOTE_VIDEO],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
        self.started = time.time()

    def caption(self, text):
        t = time.time() - self.started
        self.captions.append((t, text))
        print(f"[{t:5.1f}s] {text}")

    def snapshot(self, name):
        with open(self.out.with_name(f"{self.out.stem}_{name}.png"), "wb") as f:
            subprocess.run([ADB, "exec-out", "screencap", "-p"], stdout=f, check=False)

    def stop(self):
        end = time.time() - self.started
        if self.enabled and self.proc:
            adb("shell", "pkill", "-INT", "screenrecord", check=False)
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            time.sleep(1.5)  # mp4 마무리 기록을 기다려요
            adb("pull", REMOTE_VIDEO, str(self.out))
            adb("shell", "rm", "-f", REMOTE_VIDEO, check=False)
        lines = []
        for i, (t, text) in enumerate(self.captions):
            t2 = self.captions[i + 1][0] if i + 1 < len(self.captions) else end
            lines += [str(i + 1), f"{srt_time(t)} --> {srt_time(t2)}", text, ""]
        self.out.with_suffix(".srt").write_text("\n".join(lines), encoding="utf-8")


def srt_time(sec):
    ms = int(round(sec * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


# ── 장면 ────────────────────────────────────────────────

def prepare():
    """녹화 전에 차와 앱을 시작 상태로 돌려요: P단 정차, 문 닫힘, 운전석 탑승, 광화문 시연 위치, 경로 없음."""
    vc.set_speed(0)
    vc.set_gear("P")
    for door in vc.DOORS:
        vc.set_door(door, False)
    vc.set_seat("driver", True)
    app("--es", "cmd", "report_clear")
    after(lambda: app("--es", "scenario", "gwanghwamun", "--es", "cmd", "reset"), r"screen=IDLE")
    time.sleep(2)
    app()  # 경로를 취소하면 내비 앱이 앞으로 나와서, Statio를 다시 앞으로 가져와요
    time.sleep(2)


def run_scenes(rec: Recorder, pace: float):
    def hold(sec):
        time.sleep(sec * pace)

    # 장면 1. 도착 전 비교
    rec.caption("1. 주행 중, 목적지를 광화문으로 정해요")
    vc.set_gear("D")
    vc.set_speed(40)
    hold(2)
    after(lambda: app("--es", "cmd", "route", "--es", "lat", DESTINATION[0], "--es", "lon", DESTINATION[1],
                      "--es", "name", DESTINATION[2]), r"screen=ARRIVAL", timeout=25)
    detail = ai_caption(float(DESTINATION[0]), float(DESTINATION[1]))
    rec.caption("차 안의 AI가 도착 때 주차장 빈자리를 예측해요")
    hold(1)
    rec.snapshot("1_arrival")
    hold(3)
    if detail:
        rec.caption(detail)
        hold(4)
    rec.caption("서버 없이, 지난 기록을 학습한 모델이 차 안에서 바로 계산해요")
    hold(3)
    tap("btn_arrival_close")
    hold(1)

    # 장면 2. 길가 P단
    rec.caption("2. 잠깐이면 괜찮겠지? 세종대로 길가에 세워요")
    vc.set_speed(0)
    hold(2.5)  # 자막을 읽을 시간을 줘요
    after(lambda: vc.set_gear("P"), r"screen=CHECK")
    warn, *more = check_captions()
    rec.caption(warn)
    hold(1)
    rec.snapshot("2_check")
    hold(3)
    for line in more:
        rec.caption(line)
        hold(3.5)
    rec.caption("빈자리 있는 공영주차장으로 바로 안내해요")
    tap("btn_route")
    hold(4)

    # 장면 3. 주차장 P단과 하차
    rec.caption("3. 세종로 공영주차장으로 옮겨서 세워요")
    vc.set_gear("D")
    vc.set_speed(20)
    hold(3)
    vc.set_speed(0)
    hold(1)
    after(lambda: vc.set_gear("P"), r"screen=CHECK")
    rec.caption("주차장 안이라 단속 걱정이 없어요")
    hold(3)
    after(lambda: vc.set_door("fl", True), r"screen=PARKED")
    rec.caption("내리면 층을 한 번 누르고, QR로 휴대폰 지도에 위치를 넘겨요")
    hold(2)
    tap("text:B2")
    hold(1)
    rec.snapshot("3_parked")
    hold(4)
    vc.set_door("fl", False)
    hold(1)

    # 장면 4. 월간 리포트
    tap("btn_parked_done")
    hold(1)
    rec.caption("4. 이번 달 리포트에 방금 기록이 쌓였어요")
    m = after(lambda: tap("report_card"), r"report saved=(\d+) warnings=(\d+) moves=(\d+)")
    saved, warnings, moves = (int(x) for x in m.groups())
    hold(2)
    rec.snapshot("4_report")
    rec.caption(f"단속 위험 경고 {warnings}번 중 {moves}번 주차장으로 옮겨, 과태료 대신 주차비만 냈어요")
    hold(4)
    rec.caption(f"예상 절감액 {man_won(saved)}. 기록은 차 안에만 남아요")
    hold(4)


def man_won(krw):
    """34,840 → '3만 4천 원' (앱 화면과 같은 표기)"""
    man, cheon = krw // 10_000, krw % 10_000 // 1_000
    text = " ".join(x for x in (f"{man}만" if man else "", f"{cheon}천" if cheon else "") if x)
    return f"{text} 원" if text else f"{krw:,}원"


def main():
    parser = argparse.ArgumentParser(description="Statio 시연 자동 재생·녹화")
    parser.add_argument("--no-record", action="store_true", help="녹화 없이 재생만 해요")
    parser.add_argument("--split", action="store_true", help="내비 옆 분할 창으로 맞추고 시작해요")
    parser.add_argument("--pace", type=float, default=1.0, help="장면 사이 대기 배율, 기본 1.0")
    parser.add_argument("--out", type=pathlib.Path, help="mp4 저장 경로 (기본 data/demo/statio_demo_날짜.mp4)")
    args = parser.parse_args()

    if "device" not in adb("get-state", check=False):
        raise SystemExit("에뮬레이터가 연결돼 있지 않아요 (adb devices 확인)")
    out = args.out or OUT_DIR / f"statio_demo_{datetime.datetime.now():%Y%m%d_%H%M}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    rec = Recorder(out, enabled=not args.no_record)
    touches = adb("shell", "settings", "get", "system", "show_touches", check=False).strip()

    with vc.Heartbeat():
        prepare()
        if args.split:
            ensure_split()
        adb("shell", "settings", "put", "system", "show_touches", "1", check=False)  # 영상에 누른 곳이 보이게
        try:
            rec.start()
            run_scenes(rec, args.pace)
        finally:
            rec.stop()
            adb("shell", "settings", "put", "system", "show_touches", touches if touches in ("0", "1") else "0", check=False)

    if rec.enabled:
        print(f"녹화: {out}")
    print(f"자막: {out.with_suffix('.srt').name}, 장면 캡처: {out.stem}_*.png")


if __name__ == "__main__":
    main()
