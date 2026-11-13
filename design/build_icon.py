#!/usr/bin/env python3
"""Statio 앱 아이콘을 만들어 Android 리소스로 넣습니다.

앱 머리글의 'S' 로고(basic_900 둥근 사각형에 흰 글자)와 같은 모양이에요.
Chrome 헤드리스로 1024px 원본을 그리고, macOS `sips`로 해상도별 크기로 줄여요.

    python3 design/build_icon.py

만드는 것
  - mipmap-*/ic_launcher.png, ic_launcher_round.png   예전 방식 아이콘 (48dp)
  - mipmap-*/ic_launcher_foreground.png               적응형 아이콘 앞면 (108dp, 가운데 66dp 안에 글자)
  - mipmap-anydpi-v26/ic_launcher*.xml                적응형 아이콘 (배경색 + 앞면)
  - design/png/icon_1024.png                          원본 (문서·발표용)
"""

import pathlib
import subprocess
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
RES = ROOT / "android" / "app" / "src" / "main" / "res"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
BG = "#131417"  # basic_900
FONT = '"Apple SD Gothic Neo", "Pretendard", sans-serif'

LEGACY = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
ADAPTIVE = {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}

# shape: 'square'(둥근 사각형), 'round'(원), 'glyph'(배경 없이 글자만, 적응형 앞면용)
SHAPES = {
    "square": ("border-radius:230px;background:%s;" % BG, 560),
    "round": ("border-radius:50%%;background:%s;" % BG, 540),
    "glyph": ("background:transparent;", 400),  # 108dp 중 약 42dp 높이 → 가운데 66dp 안에 들어가요
}


def render(shape: str, out: pathlib.Path):
    style, font_px = SHAPES[shape]
    html = f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1024px;height:1024px;background:transparent}}
.i{{width:1024px;height:1024px;{style}display:flex;align-items:center;justify-content:center;
  color:#fff;font-family:{FONT};font-weight:800;font-size:{font_px}px;line-height:1;letter-spacing:-8px}}
</style></head><body><div class="i"><span>S</span></div></body></html>"""
    with tempfile.TemporaryDirectory() as tmp:
        page = pathlib.Path(tmp) / "icon.html"
        page.write_text(html, encoding="utf-8")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tmp}/profile",
                        "--window-size=1024,1024", "--default-background-color=00000000",
                        f"--screenshot={out}", page.as_uri()], check=True, capture_output=True, timeout=60)


def resize(src: pathlib.Path, size: int, out: pathlib.Path):
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["sips", "-z", str(size), str(size), str(src), "--out", str(out)], check=True, capture_output=True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        masters = {}
        for shape in SHAPES:
            masters[shape] = pathlib.Path(tmp) / f"{shape}.png"
            render(shape, masters[shape])
        for density, size in LEGACY.items():
            resize(masters["square"], size, RES / f"mipmap-{density}" / "ic_launcher.png")
            resize(masters["round"], size, RES / f"mipmap-{density}" / "ic_launcher_round.png")
        for density, size in ADAPTIVE.items():
            resize(masters["glyph"], size, RES / f"mipmap-{density}" / "ic_launcher_foreground.png")
        (ROOT / "design" / "png").mkdir(parents=True, exist_ok=True)
        resize(masters["square"], 1024, ROOT / "design" / "png" / "icon_1024.png")

    anydpi = RES / "mipmap-anydpi-v26"
    anydpi.mkdir(parents=True, exist_ok=True)
    adaptive = """<?xml version="1.0" encoding="utf-8"?>
<!-- design/build_icon.py가 만들어요 -->
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@color/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
"""
    (anydpi / "ic_launcher.xml").write_text(adaptive, encoding="utf-8")
    (anydpi / "ic_launcher_round.xml").write_text(adaptive, encoding="utf-8")
    (RES / "values" / "ic_launcher.xml").write_text(f"""<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">{BG}</color>
</resources>
""", encoding="utf-8")
    print("아이콘을 만들었어요:", ", ".join(sorted(p.name for p in RES.glob("mipmap-*"))))


if __name__ == "__main__":
    main()
