# Pleos Connect SDK 데이터 명세 (공모전 앱 기준)

> 기준: Vehicle SDK v2.3.2 API Reference (document.pleos.ai), 2026-10-22에 조회 API 186개와 제어 API 55개를 하나씩 확인했어요.
> 실제 차종에서 지원되는지는 각 도메인의 `checkXxxCapabilities`로 확인해야 해요. 문서에 있다고 모든 차에서 값이 나오지는 않아요.

## 0. 권한 수준부터 보세요

| 보호 수준 | 의미 | 공모전 앱에서 |
|---|---|---|
| normal | 매니페스트에 선언하면 설치할 때 자동으로 허용돼요 | ✅ 쓸 수 있어요. 앱마켓 출시 때는 Pleos 앱마켓과 사전 협의·승인이 필요해요 |
| dangerous | 앱 실행 중에 사용자 동의를 받아야 해요 | ✅ 동의를 받으면 쓸 수 있어요 |
| signature\|privileged | 시스템·플랫폼 서명 앱만 쓸 수 있어요 | ❌ 사실상 못 써요 |

### ⚠️ Playground 실제 제공 상태 (2026-10-22, 사용자 프로젝트 화면 기준)
- **사용 가능:** Vehicle State(조회), NaviHelper
- **Coming Soon:** FusedLocation, TextToSpeech, SpeechToText, Vehicle Control
- 공식 제공 기준으로는 **조회 API와 NaviHelper만** 확실하게 쓸 수 있어요. 아래 Sample App의 제어 호출은 에뮬레이터에서 기술적으로 동작한다는 뜻이고, 공식 제공은 아니에요.

### 참고: 공식 Sample App이 선언하는 공개 권한
Pleos 공식 'Vehicle SDK Sample App 사용 가이드'에 일반 앱이 선언하는 공개 권한 목록이 있어요. 이 샘플 앱은 에뮬레이터에서 `setDoorPosition` 같은 **제어 API를 실제로 호출**해요. 이 권한들은 실행 중 승인 창 없이 매니페스트 선언만으로 동작해요.

- **조회:** CAR_CLIMATE, CAR_DOORS, CAR_DRIVE, CAR_ENERGY, CAR_INFO, CAR_LIGHTS, CAR_MILEAGE, CAR_MIRRORS, CAR_POWERTRAIN, CAR_SAFETY, CAR_SEATS, CAR_SPEED, CAR_TOLL, CAR_TIRES, CAR_WINDOWS, READ_CAR_DISPLAY_UNITS, READ_CAR_STEERING, READ_ADAS_STATES, READ_WINDSHIELD_WIPERS
- **제어:** CONTROL_CAR_CLIMATE, CONTROL_CAR_DOORS, CONTROL_CAR_DRIVE, CONTROL_CAR_ENERGY, CONTROL_CAR_INFO, CONTROL_CAR_LIGHTS, CONTROL_CAR_MIRRORS, CONTROL_CAR_SAFETY, CONTROL_CAR_SEATS, CONTROL_CAR_TOLL, CONTROL_CAR_WINDOWS, CONTROL_WINDSHIELD_WIPERS
- **제외:** "PRIVATE_* permission은 예제에 포함하지 않습니다." → 경고등 API가 여기에 해당해요.
- **종료:** LLM SDK는 2026-10-19 에뮬레이터 v2.1.0 릴리스로 제공이 끝났어요. AI 연동은 Gleo Interface로 안내하고 있어요.

### ❌ 서드파티가 못 쓰는 것
| 항목 | API | 권한 |
|---|---|---|
| **경고등 전체** (DAW `DRIVER_ATTENTION`, TPMS, 엔진, 오일 압력 등 90여 종) | `getTelltalesInfo`, `registerTelltales` | `PRIVATE_CAR_SAFETY` (signature\|privileged) |
| 스마트글라스 켜고 끄기 | `setSmartGlassState` | signature\|privileged |
| 운전자 모니터링 (졸음 KSS 단계, 전방 주시) | AAOS `DRIVER_DROWSINESS_ATTENTION_STATE` 등 | `READ_DRIVER_MONITORING_STATES` (signature\|privileged) |
| 실내 카메라 영상 | 없음 | — |

---

## 1. Vehicle SDK 조회 (모두 normal)

`get…`은 현재 값을 한 번 조회하고, `register…`는 값이 바뀔 때마다 받아요. 결과는 모두 비동기 콜백으로 와요.

### 주행·조작
| 항목 | API | 값 | 권한 |
|---|---|---|---|
| **기어** | `getCurrentGearSelectionState`, `registerGearSelection` | VehicleGear (`GEAR_PARK`·`GEAR_REVERSE`·`GEAR_NEUTRAL`·`GEAR_DRIVE`·`GEAR_1`\~`9`·`GEAR_UNKNOWN`). 내부적으로 Pleos 속성 `VENDOR_GEAR_SELECTION`(0x21400400)을 읽어요. **API 레퍼런스 표에는 없고 SDK 클래스(`api/Gear`)에서 찾았어요.** 에뮬레이터에서 동작 확인 | CAR_DRIVE 선언 상태에서 동작 (최소 권한은 미확인) |
| **조향각** | `getCurrentSteeringWheelAngle`, `registerSteeringWheelAngle` | Float, **전륜(앞바퀴) 조향각**, 도(°), 왼쪽이 음수. **핸들 회전각이 아니에요** | CAR_DRIVE |
| 브레이크 페달 | `getCurrentBrakePedalStatus`, `registerBrakePedalStatus` | Boolean, 밟았는지 여부만. **밟은 정도는 없어요** | CAR_DRIVE |
| 방향지시등 | `getCurrentTurnSignalState`, `registerTurnSignalState` | TurnSignalValue (OFF / LEFT / RIGHT) | CAR_LIGHTS |
| 크루즈 상태 | `getCruiseControlState`, `registerCruiseControlState` | CruiseControl (OTHER, ENABLED, ACTIVATED, USER_OVERRIDE, SUSPENDED, FORCED_DEACTIVATION_WARNING) | CAR_DRIVE |
| 크루즈 목표 속도 | `getCruiseControlTargetSpeed`, `registerCruiseControlTargetSpeed` | Float, m/s | CAR_DRIVE |
| 드라이브 모드 | `getCurrentDrivingModeState`, `registerDrivingModeState` | DrivingModeType (에코·컴포트·스포츠 등) | CAR_DRIVE |

### 주행 거리·시간
| 항목 | API | 값 | 권한 |
|---|---|---|---|
| 누적 주행거리 | `getCurrentDistanceDriven`, `registerDistanceDriven` | Float, km | CAR_DRIVE |
| 이번 주행 거리 | `getTripCurrentDrivingDistance`, `registerTripCurrentDrivingDistance` | Float, m | CAR_DRIVE |
| **이번 주행 시간** | `getTripCurrentDrivingTime`, `registerTripCurrentDrivingTime` | Int, **분** | CAR_DRIVE |
| 누적 트립 거리·시간 | `getTripCumulativeDrivingDistance` / `…Time` | m / 분 | CAR_DRIVE |
| 주유 후 거리·시간 | `getTripSinceRefillDrivingDistance` / `…Time` | m / 분 | CAR_DRIVE |

### 에너지
| 항목 | API | 값 | 권한 |
|---|---|---|---|
| 연료량 | `getFuelPercent`, `registerFuelPercent` | Int, % | CAR_ENERGY |
| 연비 (현재·순간·누적·주유 후) | `getCurrentFuelEconomy` 등 | Float, L/100km | CAR_ENERGY |
| EV 배터리 잔량 | `getEvBatteryStateOfCharge`, `registerEvBatteryStateOfCharge` | Int, % | CAR_ENERGY |
| EV 전비 (현재·순간·누적·충전 후) | `getCurrentEvEfficiency` 등 | Float, km/kWh | CAR_ENERGY |
| EV 충전 중 여부, 충전 전류 제한 | `getEvChargeSwitchState`, `getEvChargeCurrentDrawLimit` | Boolean / Float(A) | CAR_ENERGY |

### 탑승·안전
| 항목 | API | 값 | 권한 |
|---|---|---|---|
| **좌석별 탑승 여부** | `getCurrentSeatOccupancy`, `registerSeatOccupancy` | Boolean, SeatArea별 | CAR_SEATS |
| **좌석별 안전벨트 착용** | `getCurrentSeatBeltBuckledState`, `registerSeatBeltBuckledState` | Boolean, SeatArea별 | CAR_SEATS |
| 안전벨트 미착용 경고 | `hasSeatBeltNotBuckledWarning`, `registerSeatBeltNotBuckledWarning` | Boolean, SeatArea별 | CAR_SAFETY |
| 에어백 전개 여부 | `hasSeatAirbagBeenDeployed`, `registerSeatAirbagDeployed` | Boolean, SeatArea별 | CAR_SAFETY |
| 에어백 이상 | `hasSeatAirbagEnabled` | Boolean (true면 이상) | CAR_SAFETY |
| 엔진오일·브레이크액·변속기오일 수준 | `getEngineOilLevelState` 등 | OilLevelState | CAR_SAFETY |
| 와셔액 부족 | `hasLowWasherFluidWarning` | Boolean | CAR_SAFETY |
| 엔진 출력 저하 경고 | `hasPowerDownWarningIndicator` | Boolean | CAR_SAFETY |
| 타이어 | `getLowTirePressure` | Float, kPa. **실제 공기압이 아니라 '저압 기준값'이에요** | CAR_SAFETY |

### 실내 환경
| 항목 | API | 값 | 권한 |
|---|---|---|---|
| **내기순환 여부** | `getHvacRecirculationState`, `registerHvacRecirculationState` | Boolean | CAR_CLIMATE |
| 자동 내기 전환 | `getHvacAutoRecirculationState` | Boolean | CAR_CLIMATE |
| **실내 온도** | `getHvacTemperatureCurrent`, `registerHvacTemperatureCurrent` | Float, °C | CAR_CLIMATE |
| 설정 온도 | `getHvacTemperature`, `registerHvacTemperature` | Float, °C | CAR_CLIMATE |
| 에어컨·오토·공조 전원·듀얼·공기청정 | `getHvacAcState` 등 | Boolean | CAR_CLIMATE |
| 풍량·풍향 | `getHvacFanSpeed`, `getHvacFanDirection` | Int / FanDirectionType | CAR_CLIMATE |
| 성에 제거 | `getHvacDefrosterState`, `getHvacMaxDefrostState` | Boolean | CAR_CLIMATE |
| 시트 열선·통풍 | `getHvacSeatTemperature`, `getHvacSeatVentilationState` | Int, SeatArea별 | CAR_SEATS |
| 핸들 열선 | `getHvacSteeringWheelHeatState` | Int | CAR_CLIMATE |
| 사이드미러 열선 | `getHvacSideMirrorHeatState` | Int | CAR_MIRRORS |

### 차체·조명
| 항목 | API | 값 | 권한 |
|---|---|---|---|
| 도어 열림 정도 | `getCurrentDoorPosition`, `registerDoorPosition` | Int, DoorArea별 (1·2·3열 좌우, HOOD, REAR) | CAR_DOORS |
| 도어 잠금 | `getDoorLockState`, `registerDoorLockState` | Boolean | CAR_DOORS |
| 창문 열림 정도 | `getCurrentWindowPosition`, `registerWindowPosition` | Int, WindowArea별 | CAR_WINDOWS |
| 창문 잠금·블라인드·스마트글라스 | `getCurrentWindowLockState` 등 | — | CAR_WINDOWS |
| 와이퍼 상태·스위치 | `getCurrentWiperState`, `getCurrentWiperSwitch` | WiperState / WiperSwitch | CAR_WINDOWS |
| 전조등(상향·하향)·안개등·미등 | `getCurrentHighBeamLightState` 등 | LightState | CAR_LIGHTS |
| 실내등·독서등 | `getCurrentCabinLightState` 등 | LightState | CAR_LIGHTS |
| 앰비언트 라이트 상태·밝기·색 | `getAmbientLightState`, `getAmbientLightBrightness`, `getAmbientLightBrightnessColor` | LightState / Int / ARGB | CAR_LIGHTS |
| 시트 위치 (앞뒤·높이·등받이 각도 등) | `getCurrentSeatForeAftPosition` 등 | Int, SeatArea별 | CAR_SEATS |

### 기타
| 항목 | API | 값 | 권한 |
|---|---|---|---|
| 차대번호(VIN) | `getVINInfo` | String | CAR_INFO |
| 온도 표시 단위 | `getCurrentTemperatureDisplayUnit` | TemperatureUnit | CAR_INFO |
| 하이패스 안내 음량 | `getCurrentEtcsVoiceGuidanceVolume` | Int | CAR_TOLL |

---

## 2. Vehicle SDK 제어 (대부분 normal)

| 도메인 | API | 권한 |
|---|---|---|
| 공조 | `setHvacRecirculation`, `setHvacAutoRecirculation`, `setHvacTemperature`, `setHvacFanSpeed`, `setHvacFanDirection`, `setHvacAc`, `setHvacMaxAc`, `setHvacAuto`, `setHvacPower`, `setHvacDual`, `setHvacAirCleaner`, `setHvacDefroster`, `setHvacMaxDefrost`, `setHvacSteeringWheelHeat` | CONTROL_CAR_CLIMATE |
| 시트 | `setHvacSeatTemperature`, `setHvacSeatVentilation`, `moveSeat…` (위치 11종) | CONTROL_CAR_SEATS |
| 앰비언트 라이트 | `setAmbientLightSwitch`, `setAmbientLightBrightness`, `setAmbientLightBrightnessColor` | **CAR_LIGHTS** (조회 권한과 같아요) |
| 기타 조명 | `setCabinLightSwitch`, `setReadingLightSwitch`, 전조등·안개등·미등 스위치 | CONTROL_CAR_LIGHTS |
| 창문·와이퍼 | `setWindowPosition`, `setWindowLock`, `moveWindowBlind`, `setSmartGlassLevel`, `setSmartGlassZoneState`, `setWiperSwitch` | CONTROL_CAR_WINDOWS |
| 도어 | `setDoorPosition`, `setDoorLock`, `setGloveBoxLock` | CONTROL_CAR_DOORS |
| EV 충전 | `setEvChargeSwitch`, `setEvChargePercentLimit`, `setEvChargePortOpen`, `setEvChargeCurrentDrawLimit` | CONTROL_CAR_ENERGY |
| 사이드미러 열선 | `setHvacSideMirrorHeat` | CONTROL_CAR_MIRRORS |
| 하이패스 음량 | `setEtcsVoiceGuidanceVolume` | CONTROL_CAR_TOLL |

> **주의:** 기술적으로는 normal이라 에뮬레이터에서 동작할 가능성이 높아요. 하지만 Pleos 정책상 퍼미션은 앱마켓과 사전 협의·승인된 범위에서만 쓸 수 있고, 'SDK 사용 신청' 문서에는 Vehicle Control SDK가 "준비 중"으로 나와 있어요. **에뮬레이터 시연은 가능하지만, 실제 출시는 승인 여부에 달려 있다**고 보시면 돼요. 주행 중에는 차량 설정류 UI가 주행 규제 대상이에요.

---

## 3. Vehicle SDK에 없는 것: AAOS 표준 속성 (CarPropertyManager)

Pleos 문서에 따르면 Vehicle SDK에 없는 값은 AAOS `CarPropertyManager`로 읽고, 차량 지원 여부를 직접 확인해야 해요.

| 항목 | AAOS 권한 | 보호 수준 |
|---|---|---|
| **차량 속도** | `android.car.permission.CAR_SPEED` | dangerous (사용자 동의) |
| **기어 (P·R·N·D)** | `android.car.permission.CAR_POWERTRAIN` | normal |
| 주행 상태 (주차·공회전·주행) | `android.car.permission.CAR_DRIVING_STATE_3P` | dangerous |
| 조향각 (AAOS 표준) | `android.car.permission.READ_CAR_STEERING_3P` | dangerous |
| 외기 온도 | `android.car.permission.CAR_EXTERIOR_ENVIRONMENT` | normal |
| 주행 중 UI 제한 | `CarUxRestrictionsManager` | 권한 불필요 |

> Vehicle SDK v2.3.2에서 `VehicleSpeedListener`가 삭제됐어요. 속도는 AAOS 표준으로 받아야 해요.

**에뮬레이터 확인 결과 (2026-10-22, `android/` 검증 앱)**
| 속성 | 결과 |
|---|---|
| `GEAR_SELECTION`, `CURRENT_GEAR` | ✅ 읽기·이벤트 모두 동작해요. P=4, D=8. Vehicle SDK 기어 이벤트와 같은 순간에 와요 |
| `PERF_VEHICLE_SPEED` | ✅ m/s로 와요(60km/h → 16.67). `CAR_SPEED`는 dangerous라 앱 실행 중에 동의를 받아야 해요 |
| `PARKING_BRAKE_ON` | ✅ 읽혀요 (Virtual Console에 조작 항목은 없어요) |
| `VENDOR_GEAR_SELECTION` (0x21400400) | ❌ 서드파티 앱에는 설정이 안 보여요. Vehicle SDK `Gear`로 받아야 해요 |

> `pleos.car.permission.CAR_POWERTRAIN`이라는 Pleos 권한은 없어요. AAOS 기어 속성은 `android.car.permission.CAR_POWERTRAIN`(normal)이에요. 이 속성을 컴파일하려면 `useLibrary("android.car")`와 매니페스트 `<uses-library android:name="android.car" android:required="false"/>`가 필요해요.

---

## 4. NaviHelper SDK (사용 가능)

| 구분 | 내용 |
|---|---|
| 경로 요청 | `requestRoute`, `requestRouteWithWaypoints`, `addWaypoint`, `removeWaypoint`, `requestTrip`, `requestReRoute`, `cancelRoute`, `changeRouteOption`, `requestNavigationScreen` |
| 조회 | `getCurrentLocationInfo` (주소·위경도·방향), `getDestinationInfo`, `getWaypointInfo`, `getRouteStateInfo` (경로 안내 중 여부·내비 모드·교통 상태), `getTBTInfo`, `getBookmarkInfo` (집·회사·충전소), `getRecentDestinationInfo`, `getChargerOperatorInfo` |
| 이벤트 | `onCurrentRoadSpeedLimit` (현재 도로 제한속도), `onCautionPointInfo` (어린이보호구역·사고 다발·낙석·안개·야생동물·결빙 위험 등), `onCameraAlert` (단속 카메라·과속 여부), `onTripInfo` (남은 거리·ETA·도착 시 예상 배터리), `onDestinationArrived`, `onWayPointArrived`, `onRouteStarted`, `onRouteCancelled`, `onReRouteEnded`, `onNavigationStatus` |
| 내비 모드 | NOT_WORKING, ROUTE_GUIDING, SAFE_GUIDING, **SIMULATION_GUIDING (모의 주행)** |
| 없는 것 | **장소(POI) 검색 API는 없어요.** 휴게소·주차장 같은 장소 데이터는 외부에서 가져와야 해요 |
| 권한 (모두 normal) | 조회(`get…Info`)는 `pleos.car.permission.NAVI_ROUTE`, 경로 요청·취소는 `NAVI_ROUTE_SEARCH`도 필요해요. **선언하지 않으면 `SecurityException`으로 앱이 바로 종료돼요** (2026-10-22 에뮬레이터에서 겪음) |

**에뮬레이터 확인 결과 (2026-10-22)**
- `initialize(onSuccess, onFailed)` 후 `addListener`를 하면 이벤트가 와요. 에뮬레이터 내비 서비스는 3.0.4예요.
- `getCurrentLocationInfo` → 37.4127, 127.0947 '수정구 시흥동'. **이 위치는 고정이에요.** Virtual Console 속도 조작이나 `adb emu geo fix`(안드로이드 GPS)로 움직이지 않아요.
- `requestRoute(RouteInfo(lat, lon, poiName, poiId, address, poiSubId))` → 약 7초 뒤 `onRouteStarted`(소요 시간·거리·통행료), `onNavigationStatus(ROUTE_DRIVE)`, `onRouteStateInfo(ROUTE_GUIDING)`가 와요. `cancelRoute` → `onRouteCancelled`, `FREE_DRIVE`.
- `onCautionPointInfo`는 정차 중(경로 없음·경로 안내 중 모두) 5분 동안 한 번도 오지 않았어요. 위치가 움직이지 않아 주행 중 수신은 확인하지 못했어요.
- 모의 주행(SIMULATION_GUIDING)을 시작하는 API나 설정 메뉴는 찾지 못했어요.
- `onRouteStarted` 뒤에 `getDestinationInfo`를 부르면 `onDestinationInfo`로 목적지 좌표·이름·주소("종로구 세종로")·남은 거리(m)·남은 시간(초)이 와요. 남은 시간은 `onDrivingInfo`의 `destination.duration`(초)으로도 와요.
- 경로를 바꿔 요청하면 새 목적지로 `onRouteStarted`가 다시 와요. 이 정보로 Statio가 안내한 주차장인지 구분해요.
- Pleos 화면 오른쪽 끝 핸들(⋮)을 끌면 앱 영역이 두 칸(각 폭 약 841dp, 세로)으로 나뉘어 내비와 나란히 띄울 수 있어요.

## 5. FusedLocation · TTS · STT

| SDK | 내용 |
|---|---|
| FusedLocation | 위도·경도·고도·방향·신뢰도·시각, `getLastKnownLocation`, **지오펜스** (`addProximityAlert`, 진입·이탈 이벤트) |
| TextToSpeech | 음성 합성. 서버 연결이 필요해 Playground 프로젝트의 클라이언트 ID·시크릿이 필요해요 |
| SpeechToText | 음성 인식. 온디바이스 또는 서버 추론 (`InferenceSource`). 클라이언트 ID·시크릿이 필요해요 |

> 'SDK 사용 신청' 문서에는 FusedLocation·TTS·STT도 "준비 중"으로 나와 있어요. 다만 10/19 릴리스 노트에 버전이 있고 공모전도 공식 SDK로 안내하고 있어서, 문서가 오래됐을 가능성이 커요. 설명회에서 확인이 필요해요.

---

## 6. 에뮬레이터 Virtual Console에서 바꿀 수 있는 값

| 분류 | 항목 |
|---|---|
| Motion | 기어(P·R·N·D), 브레이크 페달(0\~100%), 누적 주행거리, 조향각(-540\~540°), 핸들 열선 |
| ADAS | 크루즈 상태, 크루즈 목표 속도 |
| Convenience | 하이패스, 도어 열림·잠금, 트렁크·프렁크, 실내등·독서등, 앰비언트 밝기·색, 외부 조명, 상향등, 비상등, 제동등, 방향지시등, 창문 위치·잠금, **좌석 탑승**, 와이퍼, 글로브박스 |
| Safety | 경고등 (단, 앱에서 경고등을 읽을 권한은 없어요) |
| Energy | 주행 가능 거리, 배터리 에너지·SOC·용량, 충전 전류·목표 충전량·충전 스위치·충전 포트 |
| Climate | 공조 전원, 에어컨, 오토, 듀얼, **내기순환**, 풍량, 풍향, 성에 제거, **목표 온도**, 시트 열선·통풍, 공기청정 |

> Virtual Console 목록에 '이번 주행 시간'은 없어요. 차량 속도는 목록에는 없지만 페달·HTTP API(`/vehicle/controls`)로 바꿀 수 있어요.

### Virtual Console을 스크립트로 조작하기 (2026-10-22 확인)
- `adb forward tcp:8088 tcp:8088` 후 `http://127.0.0.1:8088/`에서 콘솔이 열려요. 조작은 모두 HTTP `PUT`이에요. `tools/vconsole.py`로 쓸 수 있어요.
- **콘솔 웹 화면이 열려 있다는 신호가 있어야 값이 바뀌어요.** 신호가 없으면 `{"error":"emulator_inactive"}`(503)가 와요. 웹 화면은 `POST /browser/session?client=<id>`를 1초마다 보내고, 3초 동안 없으면 비활성으로 봐요.

| 조작 | 요청 | 앱에서 받은 값 |
|---|---|---|
| 기어 | `PUT /vehicle/gear?gear=Park` (Park·Reverse·Neutral·Drive) | SDK `GEAR_PARK`, AAOS `GEAR_SELECTION` 4 |
| 속도 | `PUT /vehicle/controls?speed=60&accelerator=30&brake=0&steering=0` | AAOS `PERF_VEHICLE_SPEED` 16.67 (m/s) |
| 도어 | `PUT /vehicle/body?door_fl=true` (`door_fl`·`door_fr`·`door_rl`·`door_rr`) | SDK `registerDoorPosition` ROW_1_LEFT 1 (닫힘 0) |
| 좌석 탑승 | `PUT /vehicle/seat?kind=occupied&area=driver&value=true` (driver·passenger·rear_left·rear_right) | SDK `registerSeatOccupancy` **false (반대로 와요)** |

- 지원 영역: 도어는 1·2열 좌우·HOOD·REAR, 탑승 감지는 1열 좌우와 2열 좌·중·우예요.
- 좌석 탑승은 콘솔 값과 SDK 값이 반대예요. 운전석·조수석·2열 좌측 모두 같았어요. 실차에서 의미를 확인해야 해요.
- 내비 위치는 콘솔로 바꿀 수 없어요(4장 참고).

### 주행 중 화면 제한 (2026-10-22 확인)
- 콘솔에서 기어 D, 속도 40km/h로 두면 `CarDrivingStateService` 상태가 2(주행 중)가 되고, `CarUxRestrictionsManagerService`가 'DO 필수, 제한 0x50(영상 금지 0x10 + 설정 금지 0x40)'을 걸어요.
- 매니페스트 액티비티에 `<meta-data android:name="distractionOptimized" android:value="true"/>`를 넣은 Statio는 **사이드로드한 앱인데도 주행 중에 화면이 그대로 유지돼요.**
- 이 설정이 없는 앱(에뮬레이터에 깔린 Token Sample 앱)을 주행 중에 열면 "주행 중에는 사용할 수 없습니다" 화면으로 막혀요. `mEnableActivityBlocking:true`예요.
- Statio는 '설정 금지'가 걸리면 시연 위치 선택을 잠가요(`MainActivity.applyUxRestrictions`). 출시 때 앱마켓 심사에서 주행 중 표시를 어떻게 허용하는지는 따로 확인해야 해요.
