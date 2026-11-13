#!/usr/bin/env python3
"""제출용 release APK를 만들어 apk/팀명_Statio_release.apk로 넣습니다.

    python3 tools/build_release.py                 # 단위 테스트 → release 빌드 → 서명 확인 → apk/에 복사
    python3 tools/build_release.py --skip-tests    # 테스트 없이 빌드만
    python3 tools/build_release.py --team 다른팀명

- 파일 이름은 공모전 제출물 안내의 `팀명_앱이름_release.apk` 규칙을 따라요.
- 서명 키와 비밀번호는 .env의 RELEASE_* 값을 써요(android/app/build.gradle.kts가 읽어요). 값이 없으면 멈춰요.
- .env에 서울 API 키(SEOUL_OPEN_API_KEY)가 없으면 실시간 빈자리 없이 동작하는 APK가 되니 경고해요.
"""

import argparse
import hashlib
import os
import pathlib
import re
import shutil
import subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android"
BUILT = ANDROID / "app" / "build" / "outputs" / "apk" / "release" / "app-release.apk"
APK_DIR = ROOT / "apk"
TEAM, APP_NAME = "", "Statio"  # 제출할 때는 --team에 팀명(개인은 성명)을 넣어요(공식 제출 안내)
JBR = "/Applications/Android Studio.app/Contents/jbr/Contents/Home"
SDK = pathlib.Path(os.environ.get("ANDROID_HOME") or pathlib.Path.home() / "Library" / "Android" / "sdk")


def filled_env_keys():
    """.env에서 값이 채워진 키 이름만 돌려줘요. 값은 읽지 않아요."""
    env = ROOT / ".env"
    if not env.exists():
        return set()
    keys = set()
    for line in env.read_text(encoding="utf-8").splitlines():
        name, sep, value = line.strip().partition("=")
        if sep and not name.startswith("#") and value.strip():
            keys.add(name)
    return keys | {k for k in ("SEOUL_OPEN_API_KEY", "RELEASE_STORE_FILE") if os.environ.get(k)}


def build_tool(name):
    found = sorted((SDK / "build-tools").glob(f"*/{name}"))
    if not found:
        raise SystemExit(f"Android SDK build-tools에서 {name}을(를) 찾지 못했어요 ({SDK})")
    return str(found[-1])


def main():
    parser = argparse.ArgumentParser(description="제출용 release APK 만들기")
    parser.add_argument("--team", default=TEAM, help="파일 이름 앞에 붙일 팀명, 비우면 Statio_release.apk")
    parser.add_argument("--skip-tests", action="store_true", help="단위 테스트를 건너뛰어요")
    args = parser.parse_args()

    keys = filled_env_keys()
    if "RELEASE_STORE_FILE" not in keys:
        raise SystemExit("서명 키가 없어요. .env.example을 보고 .env에 RELEASE_* 값을 채워 주세요")
    if "SEOUL_OPEN_API_KEY" not in keys:
        print("경고: 서울 API 키가 없어서 실시간 빈자리 없이 동작하는 APK가 만들어져요")

    env = dict(os.environ)
    env.setdefault("JAVA_HOME", JBR)
    env["PATH"] = f"{env['JAVA_HOME']}/bin:{env.get('PATH', '')}"  # apksigner도 java를 찾아 써요
    tasks = ([] if args.skip_tests else [":app:testDebugUnitTest"]) + [":app:assembleRelease"]
    subprocess.run(["./gradlew", "--quiet", *tasks], cwd=ANDROID, env=env, check=True)

    verify = subprocess.run([build_tool("apksigner"), "verify", "--print-certs", str(BUILT)],
                            capture_output=True, text=True, env=env)
    if verify.returncode != 0:
        raise SystemExit(f"서명 확인에 실패했어요: {verify.stderr.strip() or verify.stdout.strip()}")
    badging = subprocess.run([build_tool("aapt2"), "dump", "badging", str(BUILT)],
                             capture_output=True, text=True, check=True).stdout
    if "application-debuggable" in badging:
        raise SystemExit("디버그 가능한 APK가 만들어졌어요. release 설정을 확인해 주세요")
    version = re.search(r"versionName='([^']*)'", badging).group(1)

    APK_DIR.mkdir(exist_ok=True)
    for old in APK_DIR.glob("*_release.apk"):  # 팀명이 바뀌었을 때 예전 파일이 남지 않게 해요
        old.unlink()
    dest = APK_DIR / ((f"{args.team}_" if args.team else "") + f"{APP_NAME}_release.apk")
    shutil.copy2(BUILT, dest)

    signer = next((line.split(": ", 1)[1] for line in verify.stdout.splitlines() if "certificate DN" in line), "?")
    print(f"완성: {dest.relative_to(ROOT)} (버전 {version}, {dest.stat().st_size / 1_048_576:.1f}MB)")
    print(f"서명: {signer}")
    print(f"SHA-256: {hashlib.sha256(dest.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    main()
