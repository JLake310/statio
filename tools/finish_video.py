#!/usr/bin/env python3
"""tools/demo.py로 찍은 시연 영상에 장면 자막과 앞뒤 소개 화면을 붙여 제출용 영상을 만듭니다.

    python3 tools/finish_video.py                                   # data/demo/statio_demo.mp4 → statio_demo_final.mp4
    python3 tools/finish_video.py data/demo/다른영상.mp4 --out 결과.mp4
    python3 tools/finish_video.py --no-teaser                       # 맨 앞 미리 보기 없이

- 순서는 미리 보기 → 소개 화면 → 본편 → 마무리 화면이에요. 미리 보기는 본편의 P단 경고 구간('P단 순간…' 자막부터
  0.5초 뒤부터 다음 장면 자막 전까지, 길어도 8초)을 잘라 맨 앞에 한 번 더 보여 줘요. 가장 강한 장면을 첫 몇 초에 보이게 하려고요.

- 자막은 같은 이름의 .srt를 읽어요. "1. …"처럼 번호로 시작하면 '장면 1' 표시를 붙여요.
- 소개 화면과 자막은 디자인 가이드 색과 글꼴로 Chrome 헤드리스가 그림(PNG)으로 그리고, ffmpeg가 영상에 얹어요.
  이 맥의 ffmpeg에는 자막 필터(libass)가 없어서 이렇게 해요.
- 자막은 화면 아래 작업 표시줄 자리에 얹어서 앱 화면을 가리지 않아요.
"""

import argparse
import html
import json
import pathlib
import re
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W, H, FPS = 1920, 1080, 30
INTRO_SEC, OUTRO_SEC, FADE_SEC = 3.5, 5.0, 0.4
# 녹화 화면은 자막 시각보다 0.2~0.4초 늦게 바뀌어요. 미리 보기는 판단 화면이 뜬 뒤(자막 0.5초 뒤)부터 잘라요.
TEASER_MAX_SEC, TEASER_SKIP_SEC = 8.0, 0.5

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body { width: 1920px; height: 1080px; }
body { font-family: "Apple SD Gothic Neo", "Pretendard", sans-serif; color: #131417; letter-spacing: -0.5px;
       -webkit-font-smoothing: antialiased; }
.card { width: 1920px; height: 1080px; background: #edeef2; display: flex; flex-direction: column;
        align-items: center; justify-content: center; text-align: center; }
.logo { width: 120px; height: 120px; border-radius: 30px; background: #131417; color: #fff; font-size: 68px;
        font-weight: 800; display: grid; place-items: center; }
.name { font-size: 44px; font-weight: 800; margin-top: 22px; }
.h { font-size: 76px; font-weight: 800; line-height: 1.2; margin-top: 56px; }
.sub { font-size: 34px; color: #44464e; margin-top: 30px; }
.steps { display: flex; gap: 18px; margin-top: 56px; align-items: center; font-size: 30px; font-weight: 700; color: #44464e; }
.step { background: #fff; border-radius: 999px; padding: 16px 30px; }
.arrow { color: #a4a8ae; }
.stats { display: flex; gap: 28px; margin-top: 56px; }
.stat { width: 460px; background: #fff; border-radius: 32px; padding: 40px 36px; text-align: left; }
.stat b { display: block; font-size: 56px; font-weight: 800; }
.stat span { display: block; font-size: 28px; color: #686a72; margin-top: 12px; line-height: 1.35; }
.next { margin-top: 48px; font-size: 32px; font-weight: 700; color: #0064ff; }
.note { margin-top: 28px; font-size: 24px; color: #686a72; }
.ok { color: #02a457; } .bad { color: #fe3d16; } .ai { color: #0064ff; }
.cap { position: absolute; left: 0; right: 0; bottom: 18px; display: flex; justify-content: center; }
.cap div { background: rgba(19, 20, 23, .86); color: #fff; border-radius: 22px; padding: 14px 30px; font-size: 38px;
           font-weight: 700; display: flex; align-items: center; gap: 18px; box-shadow: 0 6px 24px rgba(0, 0, 0, .25); }
.cap i { font-style: normal; font-size: 26px; font-weight: 800; background: #02c265; color: #fff; border-radius: 999px; padding: 6px 16px; }
"""

INTRO = """<div class="card">
  <div class="logo">S</div><div class="name">Statio</div>
  <div class="h">차를 세우는 순간,<br>"여기 세워도 돼요?"</div>
  <div class="sub">Pleos Connect 주차 비서 · <b class="ai">온디바이스 AI 빈자리 예측</b> · 1차 버전 에뮬레이터 시연</div>
  <div class="steps"><span class="step"><b class="ai">AI</b> 도착 때 빈자리 예측</span><span class="arrow">→</span>
    <span class="step">P단 단속 경고</span><span class="arrow">→</span><span class="step">하차 위치·층 기록</span>
    <span class="arrow">→</span><span class="step">월간 리포트</span></div>
</div>"""

OUTRO = """<div class="card">
  <div class="logo">S</div>
  <div class="h" style="margin-top:40px">서버 없이, AI 예측까지 차 안에서 동작해요</div>
  <div class="stats">
    <div class="stat"><b class="ai">{ai_head}</b><span>{ai_body}</span></div>
    <div class="stat"><b class="bad">18만여 곳</b><span>서울 단속 CCTV·단속 잦은 자리<br>교차로 모퉁이 등 주민신고 구역</span></div>
    <div class="stat"><b class="ok">109곳</b><span>서울시 시영주차장<br>실시간 빈자리를 차에서 바로</span></div>
  </div>
  <div class="next">2차 PT에서 더 보여 드려요 · 6주 학습 AI 정확도 공개 · 대안 주차장 1,100곳으로 확대</div>
  <div class="note">{ai_note}단속 위험은 공공데이터 위치로 추정한 안내이며 실제 단속 여부를 보장하지 않아요.</div>
</div>"""


def outro():
    """마무리 화면입니다. AI 카드 숫자는 ml/reports/latest.json(ml/train_eval.py 결과)에서 읽어요."""
    report = ROOT / "ml" / "reports" / "latest.json"
    try:
        r = json.loads(report.read_text(encoding="utf-8"))
        model, now, days = r["full"]["rule"]["recall"], r["full"]["now_full"]["recall"], r["info"]["span_days"]
        return OUTRO.format(ai_head=f"만차 {model * 100:.0f}% 포착", ai_body=f"도착 때 만차가 될 주차장<br>지금 만차만 볼 때는 {now * 100:.0f}%",
                            ai_note=f"AI 수치는 {days:.0f}일치 기록을 학습·시험으로 나눠 잰 예비 결과예요. ")
    except (OSError, KeyError, ValueError, TypeError, ZeroDivisionError):
        return OUTRO.format(ai_head="AI 예측", ai_body="도착 때 빈자리와<br>빈자리 확률을 차 안에서", ai_note="")


def render(body: str, out: pathlib.Path, transparent=False):
    page = f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "page.html"
        src.write_text(page, encoding="utf-8")
        args = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}/profile",
                f"--window-size={W},{H}", f"--screenshot={out}", src.as_uri()]
        if transparent:
            args.insert(-1, "--default-background-color=00000000")
        subprocess.run(args, check=True, capture_output=True, timeout=60)


def caption_body(text: str) -> str:
    m = re.match(r"^(\d+)\.\s*(.*)$", text)
    label = f"<i>장면 {m.group(1)}</i>" if m else ""
    return f'<div class="cap"><div>{label}{html.escape(m.group(2) if m else text)}</div></div>'


def parse_srt(path: pathlib.Path):
    def sec(t):
        h, m, s = t.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    items = []
    for block in path.read_text(encoding="utf-8").strip().split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) >= 3:
            start, end = (sec(x.strip()) for x in lines[1].split("-->"))
            items.append((start, end, " ".join(lines[2:])))
    return items


def teaser_window(captions):
    """맨 앞에 먼저 보여 줄 P단 경고 구간 (시작, 끝)입니다. 경고 자막과 이어지는 '실제로 …번 단속된 자리' 자막까지 담아요."""
    for i, (start, _, text) in enumerate(captions):
        if text.startswith("P단 순간"):
            end = start + TEASER_MAX_SEC
            for later, _, more in captions[i + 1:]:
                if not more.startswith("실제로"):
                    end = min(end, later)
                    break
            return start + TEASER_SKIP_SEC, end
    return None


def main():
    parser = argparse.ArgumentParser(description="시연 영상에 자막과 앞뒤 소개 붙이기")
    parser.add_argument("video", nargs="?", type=pathlib.Path, default=ROOT / "data" / "demo" / "statio_demo.mp4")
    parser.add_argument("--srt", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--no-teaser", action="store_true", help="맨 앞 P단 경고 미리 보기를 빼요")
    args = parser.parse_args()
    srt = args.srt or args.video.with_suffix(".srt")
    out = args.out or args.video.with_name(f"{args.video.stem}_final.mp4")
    captions = parse_srt(srt) if srt.exists() else []
    teaser = None if args.no_teaser else teaser_window(captions)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        render(INTRO, tmp / "intro.png")
        render(outro(), tmp / "outro.png")
        for i, (_, _, text) in enumerate(captions):
            render(caption_body(text), tmp / f"cap{i}.png", transparent=True)

        inputs = ["-i", str(args.video),
                  "-loop", "1", "-framerate", str(FPS), "-t", str(INTRO_SEC), "-i", str(tmp / "intro.png"),
                  "-loop", "1", "-framerate", str(FPS), "-t", str(OUTRO_SEC), "-i", str(tmp / "outro.png")]
        for i in range(len(captions)):
            inputs += ["-loop", "1", "-framerate", str(FPS), "-i", str(tmp / f"cap{i}.png")]

        graph = [f"[0:v]fps={FPS},scale={W}:{H},setsar=1,format=yuv420p[m0]"]
        for i, (start, end, _) in enumerate(captions):
            graph.append(f"[m{i}][{i + 3}:v]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})':shortest=1[m{i + 1}]")
        last = f"m{len(captions)}"
        parts = "[intro][main][outro]"
        if teaser:
            # 자막을 얹은 본편에서 P단 경고 구간을 한 번 더 잘라 맨 앞에 둬요.
            a, b = teaser
            graph += [f"[{last}]split=2[full][cut]",
                      f"[cut]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,"
                      f"fade=t=out:st={b - a - FADE_SEC:.3f}:d={FADE_SEC},format=yuv420p[teaser]"]
            last, parts = "full", "[teaser][intro][main][outro]"
        graph += [
            f"[{last}]fade=t=in:st=0:d={FADE_SEC},format=yuv420p[main]",
            f"[1:v]scale={W}:{H},setsar=1,{f'fade=t=in:st=0:d={FADE_SEC},' if teaser else ''}"
            f"fade=t=out:st={INTRO_SEC - FADE_SEC}:d={FADE_SEC},format=yuv420p[intro]",
            f"[2:v]scale={W}:{H},setsar=1,fade=t=in:st=0:d={FADE_SEC},format=yuv420p[outro]",
            f"{parts}concat=n={parts.count('[')}:v=1:a=0[v]",
        ]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(graph), "-map", "[v]",
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(out)], check=True)

    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                         capture_output=True, text=True).stdout.strip()
    lead = f", 맨 앞 미리 보기 {teaser[1] - teaser[0]:.1f}초" if teaser else ""
    print(f"완성: {out} ({float(dur):.1f}초, 자막 {len(captions)}개{lead})")


if __name__ == "__main__":
    main()
