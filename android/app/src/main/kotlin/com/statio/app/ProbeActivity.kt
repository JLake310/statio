package com.statio.app

import ai.pleos.playground.navi.constants.CautionPointType
import ai.pleos.playground.navi.constants.NaviErrorCode
import ai.pleos.playground.navi.constants.NavigationStatus
import ai.pleos.playground.navi.data.CautionPoint
import ai.pleos.playground.navi.data.CurrentLocationInfo
import ai.pleos.playground.navi.data.DestinationArrivedInfo
import ai.pleos.playground.navi.data.DrivingInfo
import ai.pleos.playground.navi.data.RouteInfo
import ai.pleos.playground.navi.data.RouteStartInfo
import ai.pleos.playground.navi.data.RouteStateInfo
import ai.pleos.playground.navi.data.TripInfo
import ai.pleos.playground.navi.helper.NaviHelper
import ai.pleos.playground.navi.helper.listener.NaviHelperEventListener
import ai.pleos.playground.vehicle.Vehicle
import ai.pleos.playground.vehicle.api.model.area.DoorArea
import ai.pleos.playground.vehicle.api.model.area.SeatArea
import ai.pleos.playground.vehicle.constant.area.DoorValue
import ai.pleos.playground.vehicle.constant.area.SeatValue
import ai.pleos.playground.vehicle.constant.control.VehicleGear
import ai.pleos.playground.vehicle.listener.DoorPositionListener
import ai.pleos.playground.vehicle.listener.GearSelectionListener
import ai.pleos.playground.vehicle.listener.SeatOccupancyListener
import android.app.Activity
import android.car.Car
import android.car.VehiclePropertyIds
import android.car.hardware.CarPropertyValue
import android.car.hardware.property.CarPropertyManager
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.widget.ScrollView
import android.widget.TextView
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 에뮬레이터에서 차량·내비 이벤트가 실제로 들어오는지 확인하는 검증 화면입니다.
 *
 * 확인 항목
 * 1. 기어: Vehicle SDK `Gear`(VENDOR_GEAR_SELECTION)와 AAOS 표준 `GEAR_SELECTION` 중 무엇이 오는지
 * 2. 도어 열림·좌석 탑승 이벤트가 Vehicle SDK로 오는지
 * 3. NaviHelper `onCautionPointInfo`에 `NO_PARKING_ZONE`이 언제 오는지 (정차 중·경로 안내 중)
 *
 * adb로 명령을 보낼 수 있습니다.
 *   adb shell am start -n com.statio.app/.ProbeActivity --es cmd route --es lat 37.5724 --es lon 126.9769 --es name 광화문
 *   cmd: route, cancel, loc, state
 * 로그는 `adb logcat -s StatioProbe`로 봅니다.
 */
class ProbeActivity : Activity() {

    private lateinit var logView: TextView
    private lateinit var vehicle: Vehicle
    private lateinit var navi: NaviHelper
    private var car: Car? = null
    private var propertyManager: CarPropertyManager? = null
    private val main = Handler(Looper.getMainLooper())
    private val clock = SimpleDateFormat("HH:mm:ss.SSS", Locale.KOREA)
    private val lastByKey = mutableMapOf<String, String>()

    private val doors = listOf(DoorValue.ROW_1_LEFT, DoorValue.ROW_1_RIGHT, DoorValue.ROW_2_LEFT, DoorValue.ROW_2_RIGHT, DoorValue.REAR)
    private val seats = listOf(SeatValue.ROW_1_LEFT, SeatValue.ROW_1_RIGHT, SeatValue.ROW_2_LEFT, SeatValue.ROW_2_RIGHT)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        logView = TextView(this).apply {
            textSize = 20f
            setPadding(40, 40, 40, 40)
            setTextIsSelectable(true)
        }
        setContentView(ScrollView(this).apply { addView(logView) })
        log("검증 시작")
        startVehicle()
        startCarProperty()
        startNavi()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleCommand(intent)
    }

    override fun onDestroy() {
        runCatching { propertyManager?.unregisterCallback(propertyCallback) }
        runCatching { navi.removeListener(naviListener); navi.release() }
        runCatching { vehicle.release() }
        car?.disconnect()
        super.onDestroy()
    }

    // ── Vehicle SDK ─────────────────────────────────────────

    private fun startVehicle() {
        vehicle = Vehicle(this)
        vehicle.initialize()
        waitForVehicle(attempt = 1)
    }

    /** 서비스 연결이 비동기라서 현재 기어를 읽을 수 있을 때까지 다시 시도합니다. */
    private fun waitForVehicle(attempt: Int) {
        vehicle.getGear().getCurrentGearSelectionState(
            { gear ->
                log("[SDK] 연결됨 (${attempt}번째 시도), 현재 기어 = $gear")
                main.post { registerVehicleListeners() }
            },
            { e ->
                if (attempt < 10) {
                    main.postDelayed({ waitForVehicle(attempt + 1) }, 1000)
                } else {
                    log("[SDK] 기어 조회 실패: ${e.javaClass.simpleName} ${e.message}")
                    main.post { registerVehicleListeners() }
                }
            },
        )
    }

    private fun registerVehicleListeners() {
        vehicle.getGear().registerGearSelection(object : GearSelectionListener {
            override fun onSelected(gear: VehicleGear?) = log("[SDK] 기어 이벤트 → $gear")
            override fun onFailed(e: Exception) = log("[SDK] 기어 등록 실패: ${e.javaClass.simpleName} ${e.message}")
        })

        vehicle.getDoor().checkDoorPositionCapabilities(
            { bounds -> log("[SDK] 도어 지원 영역: ${bounds.keys.map { it.values.toList() }}") },
            { e -> log("[SDK] 도어 지원 확인 실패: ${e.message}") },
        )
        for (door in doors) {
            vehicle.getDoor().registerDoorPosition(DoorArea(door), object : DoorPositionListener {
                override fun onPositionUpdated(position: Int?) = log("[SDK] 도어 $door 위치 → $position")
                override fun onFailed(e: Exception) = log("[SDK] 도어 $door 등록 실패: ${e.message}")
            })
        }

        vehicle.getSeat().checkSeatOccupancyCapabilities(
            { areas -> log("[SDK] 탑승 감지 지원 좌석: ${areas.map { it.values.toList() }}") },
            { e -> log("[SDK] 탑승 지원 확인 실패: ${e.message}") },
        )
        for (seat in seats) {
            vehicle.getSeat().getCurrentSeatOccupancy(SeatArea(seat),
                { occupied -> log("[SDK] 좌석 $seat 현재 탑승 = $occupied") },
                { e -> log("[SDK] 좌석 $seat 조회 실패: ${e.message}") })
            vehicle.getSeat().registerSeatOccupancy(SeatArea(seat), object : SeatOccupancyListener {
                override fun onSeatOccupancyUpdated(occupied: Boolean?) = log("[SDK] 좌석 $seat 탑승 → $occupied")
                override fun onFailed(e: Exception) = log("[SDK] 좌석 $seat 등록 실패: ${e.message}")
            })
        }
    }

    // ── AAOS 표준 속성 ────────────────────────────────────────

    private val aaosProperties = linkedMapOf(
        VehiclePropertyIds.GEAR_SELECTION to "GEAR_SELECTION",
        VehiclePropertyIds.CURRENT_GEAR to "CURRENT_GEAR",
        VehiclePropertyIds.PARKING_BRAKE_ON to "PARKING_BRAKE_ON",
        VehiclePropertyIds.PERF_VEHICLE_SPEED to "PERF_VEHICLE_SPEED",
        VENDOR_GEAR_SELECTION to "VENDOR_GEAR_SELECTION",
    )

    private val propertyCallback = object : CarPropertyManager.CarPropertyEventCallback {
        override fun onChangeEvent(value: CarPropertyValue<*>) =
            logChanged("aaos-${value.propertyId}", "[AAOS] ${aaosProperties[value.propertyId]} 이벤트 → ${value.value}")

        override fun onErrorEvent(propertyId: Int, areaId: Int) =
            log("[AAOS] ${aaosProperties[propertyId]} 오류 (area $areaId)")
    }

    private fun startCarProperty() {
        val connected = runCatching { Car.createCar(this) }.getOrNull()
        if (connected == null) {
            log("[AAOS] Car 연결 실패")
            return
        }
        car = connected
        val manager = connected.getCarManager(Car.PROPERTY_SERVICE) as CarPropertyManager
        propertyManager = manager
        for ((id, name) in aaosProperties) {
            try {
                if (manager.getCarPropertyConfig(id) == null) {
                    log("[AAOS] $name: 설정 없음 (지원 안 함 또는 권한 없음)")
                    continue
                }
                val current = runCatching { manager.getProperty<Any>(id, 0).value }
                    .getOrElse { "읽기 실패 ${it.javaClass.simpleName}" }
                log("[AAOS] $name 현재 = $current")
                manager.registerCallback(propertyCallback, id, CarPropertyManager.SENSOR_RATE_ONCHANGE)
            } catch (e: Exception) {
                log("[AAOS] $name: ${e.javaClass.simpleName} ${e.message}")
            }
        }
    }

    // ── NaviHelper ──────────────────────────────────────────

    private val naviListener = object : NaviHelperEventListener {
        override fun onCautionPointInfo(points: List<CautionPoint>) {
            val text = points.joinToString { "${it.type}(${it.remainDistance}m)" }
            logChanged("caution", "[NAVI] 주의 지점 ${points.size}개: $text")
            if (points.any { it.type == CautionPointType.NO_PARKING_ZONE }) log("[NAVI] ★ NO_PARKING_ZONE 수신")
        }

        override fun onCurrentLocationInfo(info: CurrentLocationInfo) =
            log("[NAVI] 현재 위치 ${info.latitude}, ${info.longitude} · ${info.address}")

        override fun onRouteStateInfo(info: RouteStateInfo) = log("[NAVI] 경로 상태 $info")
        override fun onRouteStarted(info: RouteStartInfo) = log("[NAVI] 경로 시작 $info")
        override fun onRouteCancelled() = log("[NAVI] 경로 취소")
        override fun onRouteEnded() = log("[NAVI] 경로 종료")
        override fun onDestinationArrived(info: DestinationArrivedInfo) = log("[NAVI] 목적지 도착 ${info.name} ${info.address}")
        override fun onNavigationStatus(status: NavigationStatus) = log("[NAVI] 내비 상태 $status")
        override fun onError(code: NaviErrorCode) = log("[NAVI] 오류 $code")
        override fun onCurrentRoadSpeedLimit(limit: Int) = logChanged("speed-limit", "[NAVI] 제한속도 $limit")
        override fun onTripInfo(info: TripInfo) = logChanged("trip", "[NAVI] 남은 거리 ${info.distance}m · ETA ${info.eta}")
        override fun onDrivingInfo(info: DrivingInfo) = logChanged("driving", "[NAVI] 주행 정보 ${info.destination}")
    }

    private fun startNavi() {
        navi = NaviHelper(this)
        navi.initialize(
            {
                log("[NAVI] 연결됨")
                navi.addListener(naviListener)
                navi.getRouteStateInfo()
                navi.getCurrentLocationInfo()
                handleCommand(intent)
            },
            { e -> log("[NAVI] 연결 실패: ${e.javaClass.simpleName} ${e.message}") },
        )
    }

    // ── adb 명령 ────────────────────────────────────────────

    private fun handleCommand(intent: Intent?) {
        when (intent?.getStringExtra("cmd")) {
            "route" -> {
                val lat = intent.getStringExtra("lat")?.toDoubleOrNull()
                val lon = intent.getStringExtra("lon")?.toDoubleOrNull()
                if (lat == null || lon == null) {
                    log("route 명령에는 lat, lon이 필요합니다")
                    return
                }
                val name = intent.getStringExtra("name") ?: "검증 목적지"
                log("[NAVI] 경로 요청 → $name ($lat, $lon)")
                navi.requestRoute(RouteInfo(latitude = lat, longitude = lon, poiName = name, poiId = "", address = "", poiSubId = ""))
            }
            "cancel" -> navi.cancelRoute()
            "loc" -> navi.getCurrentLocationInfo()
            "state" -> {
                navi.getRouteStateInfo()
                vehicle.getGear().getCurrentGearSelectionState({ log("[SDK] 현재 기어 = $it") }, { log("[SDK] 기어 조회 실패: ${it.message}") })
            }
            null -> Unit
            else -> log("모르는 명령: ${intent.getStringExtra("cmd")}")
        }
    }

    // ── 로그 ────────────────────────────────────────────────

    private fun log(message: String) {
        Log.i(TAG, message)
        main.post { logView.append("${clock.format(Date())}  $message\n") }
    }

    /** 자주 오는 이벤트는 값이 바뀔 때만 남깁니다. */
    private fun logChanged(key: String, message: String) {
        synchronized(lastByKey) {
            if (lastByKey[key] == message) return
            lastByKey[key] = message
        }
        log(message)
    }

    companion object {
        private const val TAG = "StatioProbe"
        /** Pleos 차량의 기어 속성 (Virtual Console 카탈로그 기준). */
        private const val VENDOR_GEAR_SELECTION = 0x21400400
    }
}
