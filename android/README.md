# Statio Android 앱

Pleos Connect 차량용 앱이에요. 운영 서버 없이 APK 하나로 동작해요.

| 화면 | 언제 | 내용 |
|---|---|---|
| 도착 전 비교 (시안 01) | 내비에 목적지가 정해지면, 도착 10분 전에 한 번 더 | 목적지 근처 주차장 3곳의 실시간 빈자리, **AI가 예측한 도착 때 빈자리·빈자리 확률**, 만차 위험이면 빨간색 '도착 때 만차 위험', 1시간 요금, 고른 주차장으로 안내. 순서는 '자리 있을 곳 → 실시간 정보 없음 → 만차(만차 위험)'이고, 안내 버튼은 만차 위험인 곳을 가리키지 않아요(모두 만차면 숨김). 목적지에서 가장 가까운 주차장이 만차 위험이면 마지막 칸에 남겨 보여 줘요 |
| P단 판단 (시안 02) | 기어가 P로 바뀌면 | 단속 CCTV·단속 잦은 자리·소화전·횡단보도·버스정류소·교차로 모퉁이·어린이보호구역 판단, 과태료 추정, 대안 주차장, 약도 |
| 위치 기록 (시안 03) | P단에서 운전석 문이 열리면 | "OO에 세웠어요", 층 선택, 휴대폰으로 넘기는 QR(카카오맵 링크), 다음 요금 구간 안내 |
| 대기 | 그 밖 | 기어·위치·실시간 연결 상태, 마지막 주차 위치, 시연 위치 선택 |

- 내비 옆 **세로 분할 창(폭 약 840dp)** 과 **가로 전체 창** 레이아웃이 따로 있어요(`layout/`, `layout-w1200dp/`). 창 크기가 바뀌면 다시 만들지 않고 같은 상태를 다시 그려요.
- **주행 중에도 화면이 유지돼요.** 매니페스트의 `distractionOptimized`로 주행 중 표시 가능 앱이 되고, 주행 중 제한(`CarUxRestrictions`)에 '설정 금지'가 걸리면 시연 위치 선택만 잠가요.
- 에뮬레이터 화면은 `screenshots/`에 있어요.

## 환경
| 항목 | 버전 |
|---|---|
| Android Gradle Plugin | 9.4.1 (Kotlin은 AGP 내장) |
| Gradle | 9.8.2 (wrapper) |
| JDK | Android Studio 내장 JBR 25 |
| compileSdk / targetSdk / minSdk | 37 / 34 / 31 |
| 라이브러리 | Pleos Vehicle 2.3.2, NaviHelper 3.0.5, zxing core 3.5.3(QR) |

## 빌드와 설치
Android Studio에서 `android/` 폴더를 열면 돼요. 터미널에서는 이렇게 해요.
```bash
cd android
JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" ./gradlew :app:assembleDebug :app:testDebugUnitTest
~/Library/Android/sdk/platform-tools/adb install -r -g app/build/outputs/apk/debug/app-debug.apk
```
- **제출용 release APK:** 저장소 루트에서 `python3 tools/build_release.py`를 실행하면 테스트, 서명한 release 빌드, 서명 확인을 거쳐 `apk/Statio_release.apk`로 넣어요. 서명 키는 `.env`의 `RELEASE_*` 값이고, 값이 없으면 멈춰요. 축소(R8)는 꺼 두었어요.
- release와 디버그 빌드는 서명이 달라서, 바꿔 설치할 때는 `adb uninstall com.statio.app`을 먼저 해요.
- **서울 API 키:** 빌드할 때 환경변수 `SEOUL_OPEN_API_KEY`, 없으면 저장소 루트 `.env`에서 읽어 `BuildConfig`에 넣어요. 키가 없으면 실시간 빈자리 없이 동작해요. 키는 APK 안에 들어가니 정식 출시 때는 키를 숨기는 프록시가 필요해요.
- **휴대폰 QR:** 카카오맵 링크(좌표와 '내 차 · 주차장 · 층')만 담아요. Statio 서버나 웹페이지는 쓰지 않아요(카드 페이지 `web/card/`는 보류).
- 앱 내장 DB `app/src/main/assets/statio.db`는 `python3 datapipe/export_app_assets.py`로 만들어요. 테스트도 이 파일을 읽어요.

## 시연 영상 (자동 재생·녹화)
```bash
python3 tools/demo.py --split --out data/demo/statio_demo_ai.mp4                # 내비 옆 분할 창으로 네 장면을 재생하며 녹화
python3 tools/finish_video.py data/demo/statio_demo_ai.mp4                    # 앞뒤 소개 화면과 장면 자막을 붙여 statio_demo_ai_final.mp4 (약 80초)
python3 tools/demo.py --no-record                                # 녹화 없이 재생만
```
- `finish_video.py`는 소개 화면·자막을 Chrome으로 그림을 만들어 ffmpeg로 얹어요(이 맥의 ffmpeg에 자막 필터가 없어서). 자막은 작업 표시줄 자리에 얹어 앱 화면을 가리지 않아요.
- 장면: ① 주행 중 목적지 설정 → 도착 전 주차장 비교와 AI 예측 ② 길가 P단 → 단속 경고 → 주차장으로 안내 ③ 주차장 P단 → 하차 → 층 선택·QR ④ 대기 화면 → 월간 리포트(방금 쌓인 경고·이동 기록과 예상 절감액). 약 65초예요. 시작할 때 리포트 기록을 비워요.
- 장면 ①의 AI 예측 자막은 앱 로그(`StatioAI`)의 실제 예측값으로 만들고, 마무리 화면의 '만차 N% 포착'은 `ml/reports/latest.json`에서 읽어요. 장면 ④ 자막은 앱 로그의 리포트 숫자로 만들어요.
- 장면 설명은 같은 이름의 `.srt` 자막 파일로, 장면별 캡처는 `_1_arrival.png` 같은 이름으로 함께 남아요.
- 영상에 누른 곳이 보이도록 녹화하는 동안만 터치 표시를 켜요.
- 스크립트는 앱이 남기는 로그(`StatioDemo`)로 화면 전환을 기다리고, 앱이 알려 준 버튼 좌표를 실제로 눌러요. 내비 지도가 계속 움직여 화면 덤프가 자주 실패하기 때문이에요.

## 직접 시연하기
```bash
python3 tools/vconsole.py gear D && python3 tools/vconsole.py gear P   # P단 → 판단 화면
python3 tools/vconsole.py door fl open                                  # 하차 → 위치 기록 화면
```
- 에뮬레이터 내비 위치는 수정구 시흥동에 고정이라, 대기 화면의 '시연 위치'에서 P단 판단에 쓸 위치를 골라요.
- 시연 위치를 쓰는 중에 Statio가 주차장으로 안내했으면, 다음 P단은 그 주차장에 도착한 것으로 봐요. 그래서 '목적지 → 비교 → 안내 → P단 → 하차' 흐름을 한 번에 보여 줄 수 있어요.
- 내비 옆에 나란히 놓으려면 화면 오른쪽 끝 핸들(⋮)을 가운데로 끌어요.

| 시연 위치 | 결과 |
|---|---|
| 광화문 세종대로 | 단속 CCTV 11m·단속 잦은 자리(224번 단속, 25년 4분기) 11m, 과태료 4만 원 추정, 세종로 공영주차장 232m |
| 용산 원효로 | 소화전 7m·횡단보도 3m, 과태료 8만 원 추정, 용산주차빌딩 271m |
| 종묘 앞 | 알려진 위험 없음, 종묘주차장 40m (주차장 바로 앞이라 안내 버튼은 숨겨요) |

adb 명령(`adb shell am start -n com.statio.app/.MainActivity ...`)
| 인자 | 동작 |
|---|---|
| `--es scenario gwanghwamun` | 시연 위치 (`wonhyoro`, `jongmyo`, `real`) |
| `--es cmd route --es lat 37.5724 --es lon 126.9769 --es name 광화문` | 운전자가 목적지를 정한 것처럼 경로 요청 → 도착 전 비교 |
| `--es cmd check` | P단 판단을 바로 실행 |
| `--es cmd check --es lat 37.55168 --es lon 127.00115 --es place 국립극장앞` | 시연 위치와 상관없이 그 좌표로 P단 판단을 한 번 실행 (예: 교차로 모퉁이 확인) |
| `--es cmd parked` | 마지막 주차 기록 화면 |
| `--es cmd idle` | 대기 화면 |
| `--es cmd report` | 이번 달 리포트 화면 |
| `--es cmd report_clear` | 리포트 기록(경고·이동·주차)을 비워요 (시연 시작 전, `tools/demo.py`가 씀) |
| `--es cmd reset` | 경로 취소, 판단·비교 결과를 지우고 대기 화면 (시연 시작 전) |
| `--es cmd locate --es target btn_route` | 버튼의 화면 좌표를 로그(`StatioDemo`)로 알려 줘요. `text:B2`처럼 글자로도 찾아요 |

- 경로 안내는 내비 고정 위치에서 출발해서 거리가 길게 나와요(에뮬레이터 한계).
- 아이콘을 바꿨는데 작업 표시줄에 예전 아이콘이 남으면 에뮬레이터를 재부팅하세요. Pleos 홈 화면이 최근 앱 칸의 아이콘을 따로 저장해 둬요.

## 구조
| 위치 | 역할 |
|---|---|
| `core/` | 위험 판단·요금·주차장 순위·빈자리 예측·요금 구간. `server/parking_core.py`를 옮긴 것이고 Android에 기대지 않아요 |
| `data/AssetParkingData.kt` | 앱 내장 DB를 앱 저장소로 복사해 읽어요(설치·업데이트 때, 복사본이 비었거나 깨졌을 때 다시 복사) |
| `core/SpacePredictor.kt` | 빈자리 예측 모델(assets/predictor.json, `ml/export_model.py`가 만들어요)로 도착 시 빈자리와 빈자리 있을 확률을 차 안에서 계산해요. 1차 버전부터 화면에 보여요(`AI_PREDICTION_ON_SCREEN`) |
| `data/SeoulLiveSource.kt` | 서울시 `GetParkingInfo` 호출, 1분 캐시, 예측용 최근 기록 |
| `vehicle/CarSignals.kt` | Vehicle SDK 기어·운전석 도어, NaviHelper 위치·목적지·남은 시간·경로·주정차 금지 구간 |
| `MainActivity.kt` | 네 화면 전환과 상태 |
| `ui/CheckRenderer.kt`, `ArrivalRenderer.kt`, `ParkedRenderer.kt`, `RiskMapView.kt` | 화면별 그리기, 약도 |
| `ParkingRecord.kt`, `PhoneCard.kt`, `ui/QrCode.kt` | 주차 기록(층·주차장·요금 포함), 카카오맵 링크와 QR 그림 |
| `Scenario.kt` | 시연 위치 |
| `ProbeActivity.kt` | 차량·내비 신호 검증 화면 (adb로만 열어요) |

## 테스트 (28개)
| 테스트 | 내용 |
|---|---|
| `ParkingCoreTest` | 요금, 요금 구간(1만 원을 넘는 시간), 예측(패턴·추세·현재값), 위험 단계, 어린이보호구역 시간대, 거리, 추천 순서 3단계와 안내 대상(만차·AI 만차 위험 건너뛰기), 가장 가까운 만차 주차장 남기기 |
| `GoldenCasesTest` | 앱 내장 DB + Kotlin 로직이 파이썬 결과와 40개 지점에서 같은지 (위험 종류·거리 ±1m·과태료·주차장·요금) |
| `SpacePredictorTest` | 빈자리 예측 모델이 파이썬(scikit-learn)과 250개 상황에서 같은 입력·빈자리·확률을 내는지, 모델이 모르는 주차장은 건너뛰는지 |
| `SeoulLiveSourceTest` | 서울 API 응답 읽기, 데이터 없음, 키 오류 |

에뮬레이터에서는 화면의 QR을 zxing으로 다시 읽어 링크가 맞는지도 확인했어요(2026-10-22).

## 권한
| 권한 | 쓰는 곳 |
|---|---|
| `pleos.car.permission.CAR_DRIVE` | Vehicle SDK 기어 (이 권한으로 동작 확인, 최소 권한은 미확인) |
| `pleos.car.permission.CAR_DOORS` | 도어 열림 |
| `pleos.car.permission.CAR_SEATS` | 좌석 탑승 (검증 화면만) |
| `pleos.car.permission.NAVI_ROUTE` | NaviHelper 조회(위치·목적지). 없으면 앱이 바로 종료돼요 |
| `pleos.car.permission.NAVI_ROUTE_SEARCH` | NaviHelper 경로 요청·취소 |
| `android.permission.INTERNET` | 서울시 실시간 API |
| `android.car.permission.CAR_POWERTRAIN`, `CAR_SPEED` | AAOS 기어·속도 (검증 화면만) |

검증 결과는 `../docs/VehicleSDK_명세.md` 3·4·6장에 있어요.

## 남은 일
- 패턴표가 쌓이면 '도착 시 예상'이 '지금 기준'에서 '지난 N일 같은 시간대'로 바뀌어요
