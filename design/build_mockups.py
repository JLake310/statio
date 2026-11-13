#!/usr/bin/env python3
"""Statio 화면 시안 HTML을 만들고 Chrome 헤드리스로 PNG를 뽑습니다.

    python3 design/build_mockups.py

결과: design/html/*.html, design/png/*.png
주차장 이름·거리·빈자리·요금은 datapipe/risk_lookup.py로 조회한 광화문 실제 값이고,
'도착 시 예상'과 월간 리포트 숫자는 예시입니다.
"""

import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent
HTML_DIR, PNG_DIR = ROOT / "html", ROOT / "png"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def page(body, width, height):
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<link rel="stylesheet" href="../mock.css"><style>html,body{{width:{width}px;height:{height}px}}</style></head>
<body>{body}</body></html>"""


def status_bar():
    return """<div class="status"><span class="dot"></span><span class="dot"></span><span class="dot"></span><b style="color:#131417">14:02</b></div>"""


def gnb():
    icons = "".join('<div class="ico"></div>' for _ in range(3))
    return f"""<div class="gnb"><span class="temp">22.0°</span>{icons}<div class="ico launcher">S</div>{icons}<span class="temp">22.0°</span></div>"""


def driving_view(speed, gear):
    gears = "".join(f'<span class="{"on" if g == gear else ""}">{g}</span>' for g in "PRND")
    moving = gear == "D"
    lane = """<path d="M404 1208 L404 420" stroke="#0064ff" stroke-width="120" stroke-linecap="round" opacity=".18"/>""" if moving else ""
    sign = '<div class="dv-sign">50</div>' if moving else ""
    return f"""<div class="driving">
<svg class="dv-road" viewBox="0 0 808 1208">
  <path d="M120 1208 L330 300 L478 300 L688 1208 Z" fill="#e0e1e6"/>
  <path d="M276 1208 L362 300" stroke="#fff" stroke-width="6" stroke-dasharray="40 40"/>
  <path d="M532 1208 L446 300" stroke="#fff" stroke-width="6" stroke-dasharray="40 40"/>
  {lane}
  <rect x="354" y="900" width="100" height="170" rx="30" fill="#313236"/>
  <rect x="372" y="930" width="64" height="44" rx="10" fill="#686a72"/>
</svg>
<div class="dv-top"><span class="dv-speed">{speed}</span><span class="dv-unit">km/h</span></div>
<div class="dv-gear">{gears}</div>
<div class="dv-range">512km</div>
{sign}
</div>"""


def nav_window(tbt_main, tbt_sub, eta_left, eta_right, arrived=False):
    route = "M420 1184 L420 760 L600 760 L600 430" if not arrived else "M420 1184 L420 760 L600 760"
    pin_y = 430 if not arrived else 760
    return f"""<div class="win">
<svg class="map" viewBox="0 0 840 1184">
  <rect width="840" height="1184" fill="#e8ebef"/>
  <g fill="#f7f8fa">
    <rect x="40" y="80" width="300" height="300" rx="18"/><rect x="460" y="80" width="340" height="280" rx="18"/>
    <rect x="40" y="460" width="300" height="240" rx="18"/><rect x="660" y="460" width="140" height="240" rx="18"/>
    <rect x="40" y="820" width="300" height="300" rx="18"/><rect x="480" y="840" width="320" height="300" rx="18"/>
  </g>
  <path d="M400 0 V1184 M0 420 H840 M0 760 H840 M600 0 V1184" stroke="#fff" stroke-width="44"/>
  <path d="{route}" fill="none" stroke="#0064ff" stroke-width="22" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="600" cy="{pin_y}" r="26" fill="#fe3d16"/><circle cx="600" cy="{pin_y}" r="10" fill="#fff"/>
  <path d="M420 1080 l-26 52 l26 -14 l26 14 z" fill="#131417"/>
</svg>
<div class="tbt"><div class="arrow">↱</div><div><div class="t-l">{tbt_main}</div><div class="b-s c3">{tbt_sub}</div></div></div>
<div class="eta"><span class="t-s">{eta_left}</span><span class="b-m c3">{eta_right}</span></div>
</div>"""


def app_head(chip=""):
    return f"""<div class="app-head"><div class="logo">S</div><div class="name">Statio</div>{chip}</div>"""


def screen(driving, windows):
    return f"""<div class="screen">{status_bar()}{driving}<div class="apparea">{windows}</div>{gnb()}</div>"""


def lot_card(name, meta, free, free_cls, updated, pred, fee, pick=False, ratio=None):
    bar = f'<div class="bar" style="margin-top:18px"><i style="width:{ratio}%"></i></div>' if ratio is not None else ""
    return f"""<div class="card{' pick' if pick else ''}">
  <div class="row"><div><div class="t-s">{name}</div><div class="b-s c3">{meta}</div></div><div class="spacer"></div>
    <div class="free"><div class="h-s {free_cls}">{free}</div><div class="lb-s c3">{updated}</div></div></div>
  {bar}
  <div class="row" style="margin-top:14px"><span class="b-s c2">{pred}</span><div class="spacer"></div><span class="b-s c2">{fee}</span></div>
</div>"""


# ① 도착 전: 빈자리 있는 주차장으로 (주행 중, 세로 창)
s1_app = f"""<div class="win"><div class="app">
{app_head('<span class="chip info">실시간</span>')}
<div><div class="t-l">광화문 근처 주차장</div><div class="b-m c3">도착 6분 전 · 빈자리가 많은 순서예요</div></div>
{lot_card("세종로 공영주차장", "143m · 공영", "1,109면", "ok", "1분 전 기준", "도착 시 예상 <b class='ok'>넉넉</b>", "1시간 약 5,160원", pick=True, ratio=88)}
{lot_card("서울글로벌센터 공영주차장", "496m · 공영", "33면", "ok", "1분 전 기준", "도착 시 예상 20~30면", "1시간 약 8,400원", ratio=40)}
{lot_card("다동쉼터 공영주차장", "559m · 공영", "정보 없음", "c4", "실시간 미제공", "운영 08:00-22:00", "1시간 약 7,100원")}
<div class="btns foot"><div class="btn filled">세종로 공영주차장으로 안내</div></div>
</div></div>"""
S1 = screen(driving_view(34, "D"), nav_window("300m", "세종대로 방면", "14:08 도착", "1.2km") + s1_app)

# ② P단 순간: 단속 경고 (주차, 가로 창)
s2_app = f"""<div class="win land"><div class="app" style="flex-direction:row;gap:40px">
<div style="flex:1;display:flex;flex-direction:column;gap:24px">
{app_head('<span class="chip bad">주차 전 확인</span>')}
<div class="warn">
  <div class="row"><div class="ico">!</div><div class="h-s bad">여기는 단속 위험이 있어요</div></div>
  <div class="b-l c2" style="margin:16px 0 8px">세종대로 · 차를 세운 위치 기준</div>
  <div class="risk"><span class="k"></span><span class="t-s">주정차 단속 CCTV</span><span class="b-m c3">세종대왕 동상 앞</span><div class="spacer"></div><span class="t-m bad">27m</span></div>
  <div class="risk"><span class="k mid"></span><span class="t-s">주정차 금지 구간</span><span class="b-m c3">내비 지도 기준</span><div class="spacer"></div><span class="t-m" style="color:#ff7a3d">해당</span></div>
</div>
<div class="card" style="flex:1;padding:0;overflow:hidden;position:relative">
  <svg viewBox="0 0 900 420" preserveAspectRatio="xMidYMid slice" style="width:100%;height:100%;display:block">
    <rect width="900" height="420" fill="#e8ebef"/>
    <g fill="#f7f8fa"><rect x="20" y="20" width="300" height="150" rx="16"/><rect x="380" y="20" width="500" height="150" rx="16"/>
      <rect x="20" y="250" width="300" height="150" rx="16"/><rect x="380" y="250" width="500" height="150" rx="16"/></g>
    <path d="M0 210 H900 M350 0 V420" stroke="#fff" stroke-width="56"/>
    <circle cx="250" cy="200" r="95" fill="rgba(254,61,22,.12)" stroke="#fe3d16" stroke-width="3" stroke-dasharray="10 8"/>
    <rect x="236" y="186" width="28" height="28" rx="6" fill="#fe3d16"/>
    <path d="M170 228 h40 v-26 h-40 z" fill="#131417"/>
    <path d="M210 230 C 330 300, 520 300, 640 330" fill="none" stroke="#02c265" stroke-width="7" stroke-dasharray="4 14" stroke-linecap="round"/>
    <circle cx="660" cy="330" r="34" fill="#02c265"/><text x="660" y="343" text-anchor="middle" font-size="36" font-weight="800" fill="#fff">P</text>
  </svg>
  <div class="row" style="position:absolute;left:24px;bottom:20px;gap:24px">
    <span class="chip">■ 내 차</span><span class="chip bad">● 단속 CCTV 범위</span><span class="chip" style="color:#02a457">P 세종로 공영주차장 · 걸어서 2분</span>
  </div>
</div>
<div class="lb-m c3">서울시 단속 CCTV 위치 공공데이터 기반 추정 안내예요. 단속 여부를 보장하지 않아요.</div>
</div>
<div style="width:700px;display:flex;flex-direction:column;gap:24px;padding-top:80px">
<div class="row" style="gap:20px">
  <div class="card" style="flex:1"><div class="b-m c3">여기 세우면</div><div class="h-m bad">40,000원</div><div class="lb-m c3">과태료 (승용차)</div></div>
  <div class="card" style="flex:1"><div class="b-m c3">주차장에 세우면</div><div class="h-m ok">860원</div><div class="lb-m c3">10분 기준</div></div>
</div>
{lot_card("세종로 공영주차장", "143m · 공영 · 걸어서 2분", "1,109면", "ok", "1분 전 기준", "5분 430원", "", pick=True, ratio=88)}
{lot_card("서울글로벌센터 공영주차장", "496m · 공영", "33면", "ok", "1분 전 기준", "10분 1,400원", "", ratio=40)}
<div class="btns foot"><div class="btn filled">주차장으로 안내</div><div class="btn basic">잠깐만 세울게요</div></div>
</div>
</div></div>"""
S2 = screen(driving_view(0, "P"), s2_app)

# ③ 주차장 도착: 층 선택과 위치 기록 (주차, 세로 창)
qr = "".join(f'<rect x="{(i % 7) * 18}" y="{(i // 7) * 18}" width="16" height="16" rx="2" fill="#131417"/>' for i in range(49) if (i * 37 + 11) % 5 < 3)
s3_app = f"""<div class="win"><div class="app">
{app_head('<span class="chip">14:02 주차</span>')}
<div><div class="t-l">세종로 공영주차장에 세웠어요</div><div class="b-m c3">몇 층이에요? 한 번만 눌러 주세요</div></div>
<div class="floors"><div>B1</div><div class="sel">B2</div><div>B3</div><div>B4</div><div>1F</div><div class="b-l" style="font-size:36px">모름</div></div>
<div class="card row" style="gap:28px">
  <svg class="qr" viewBox="0 0 126 126">{qr}</svg>
  <div><div class="t-s">휴대폰으로 위치 받기</div><div class="b-s c3">카메라로 찍으면 주차 위치와<br>요금 안내가 휴대폰에 저장돼요</div></div>
</div>
<div class="card">
  <div class="row"><span class="t-s">요금 알림</span><div class="spacer"></div><span class="chip info">켜짐</span></div>
  <div class="b-m c2" style="margin-top:10px">2시간을 넘으면 1만 원을 넘어요.<br>1시간 50분이 되면 휴대폰으로 알려 드릴게요.</div>
</div>
<div class="row foot"><span class="b-m c3">요금</span><div class="spacer"></div><span class="t-s">5분 430원 · 1시간 약 5,160원</span></div>
</div></div>"""
S3 = screen(driving_view(0, "P"), nav_window("도착", "세종로 공영주차장", "주차장 도착", "14:02", arrived=True) + s3_app)

# ④ 휴대폰 위치 카드
S4 = """<div class="phone">
<div style="font-size:15px;font-weight:800;color:#0064ff">Statio</div>
<div style="font-size:26px;font-weight:800;line-height:1.3">내 차는 여기 있어요</div>
<div class="pc"><div style="font-size:15px;color:#686a72">세종로 공영주차장</div>
  <div style="font-size:64px;font-weight:800;line-height:1.1">B2</div>
  <div style="font-size:14px;color:#686a72">종로구 세종로 80-1</div></div>
<svg viewBox="0 0 350 160" style="border-radius:18px;background:#e8ebef">
  <path d="M0 60 H350 M120 0 V160 M260 0 V160" stroke="#fff" stroke-width="16"/>
  <circle cx="190" cy="92" r="14" fill="#fe3d16"/><circle cx="190" cy="92" r="5" fill="#fff"/></svg>
<div class="pc">
  <div class="prow"><span style="color:#686a72">주차한 시각</span><b>14:02</b></div>
  <div class="prow"><span style="color:#686a72">지난 시간</span><b>1시간 12분</b></div>
  <div class="prow"><span style="color:#686a72">지금까지 요금</span><b>약 6,450원</b></div>
</div>
<div style="background:#fff4e5;color:#8a4b00;border-radius:16px;padding:14px 16px;font-size:14px;font-weight:700">2시간을 넘으면 요금이 1만 원을 넘어요. 1시간 50분이 되면 알려 드릴게요.</div>
<div class="pbtn">주차장까지 길찾기</div>
</div>"""

# ⑤ 월간 리포트 (주차, 가로 창, 예시 데이터)
weeks = [("1주", 2, 2), ("2주", 1, 1), ("3주", 2, 1), ("4주", 1, 1)]
cols = "".join(
    f'<div class="col"><div class="pair" style="height:420px"><i style="height:{w * 190}px;width:84px;background:#fe3d16"></i>'
    f'<i style="height:{m * 190}px;width:84px;background:#02c265"></i></div><span class="b-s c3">{label}</span></div>'
    for label, w, m in weeks)
s5_app = f"""<div class="win land"><div class="app">
{app_head('<span class="chip">10월</span>')}
<div><div class="t-l">이번 달 Statio가 아껴 드린 것</div><div class="b-m c3">2026년 10월 · 주차 23번</div></div>
<div class="stats">
  <div class="stat"><div class="b-m c3">예상 절감액</div><div class="h-m ok">18만 원</div><div class="lb-m c3">과태료 − 주차비</div></div>
  <div class="stat"><div class="b-m c3">단속 위험 경고</div><div class="h-m">6번</div><div class="lb-m c3">그중 5번 주차장으로 이동</div></div>
  <div class="stat"><div class="b-m c3">만차 피함</div><div class="h-m">3번</div><div class="lb-m c3">도착 전 다른 주차장 안내</div></div>
  <div class="stat"><div class="b-m c3">자주 간 주차장</div><div class="t-m" style="margin-top:14px">세종로 공영주차장</div><div class="lb-m c3">7번</div></div>
</div>
<div class="card" style="flex:1;display:flex;flex-direction:column;gap:18px">
  <div class="row"><span class="t-s">주별 단속 위험 경고와 이동</span><div class="spacer"></div>
    <div class="legend lb-m c3"><span><i style="background:#fe3d16"></i>경고</span><span><i style="background:#02c265"></i>주차장으로 이동</span></div></div>
  <div class="chart" style="height:480px;margin-top:auto">{cols}</div>
</div>
</div><div class="note lb-s c4">시안용 예시 데이터</div></div>"""
S5 = screen(driving_view(0, "P"), s5_app)

SCREENS = [
    ("01_도착전_주차장비교", S1, 2560, 1440, 1),
    ("02_P단_단속경고", S2, 2560, 1440, 1),
    ("03_층선택_위치기록", S3, 2560, 1440, 1),
    ("04_휴대폰_위치카드", S4, 390, 844, 3),
    ("05_월간리포트", S5, 2560, 1440, 1),
]


def main():
    HTML_DIR.mkdir(exist_ok=True)
    PNG_DIR.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as profile:
        for name, body, w, h, scale in SCREENS:
            html = HTML_DIR / f"{name}.html"
            html.write_text(page(body, w, h), encoding="utf-8")
            subprocess.run(
                [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                 f"--window-size={w},{h}", f"--force-device-scale-factor={scale}", "--virtual-time-budget=5000",
                 f"--screenshot={PNG_DIR / (name + '.png')}", html.as_uri()],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True,
            )
            print(f"{name}.png 를 만들었습니다.")


if __name__ == "__main__":
    main()
