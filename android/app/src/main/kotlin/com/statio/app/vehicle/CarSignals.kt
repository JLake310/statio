package com.statio.app.vehicle

import ai.pleos.playground.navi.constants.CautionPointType
import ai.pleos.playground.navi.constants.NaviErrorCode
import ai.pleos.playground.navi.data.CautionPoint
import ai.pleos.playground.navi.data.CurrentLocationInfo
import ai.pleos.playground.navi.data.DestinationArrivedInfo
import ai.pleos.playground.navi.data.DestinationInfo
import ai.pleos.playground.navi.data.DrivingInfo
import ai.pleos.playground.navi.data.RouteInfo
import ai.pleos.playground.navi.data.RouteStartInfo
import ai.pleos.playground.navi.helper.NaviHelper
import ai.pleos.playground.navi.helper.listener.NaviHelperEventListener
import ai.pleos.playground.vehicle.Vehicle
import ai.pleos.playground.vehicle.api.model.area.DoorArea
import ai.pleos.playground.vehicle.constant.area.DoorValue
import ai.pleos.playground.vehicle.constant.control.VehicleGear
import ai.pleos.playground.vehicle.listener.DoorPositionListener
import ai.pleos.playground.vehicle.listener.GearSelectionListener
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log

/**
 * 차에서 오는 신호(기어, 운전석 도어)와 내비(현재 위치, 목적지, 경로 안내, 주정차 금지 구간)를 한곳에서 받습니다.
 * 콜백은 모두 메인 스레드로 옵니다.
 *
 * 에뮬레이터 검증 결과(docs/VehicleSDK_명세.md): 기어·도어는 Vehicle SDK로 바로 오고,
 * 좌석 탑승 값은 반대로 와서 쓰지 않습니다. 하차는 운전석 도어 열림으로 판단합니다.
 */
class CarSignals(context: Context, private val listener: Listener) {

    enum class Gear { PARK, REVERSE, NEUTRAL, DRIVE, UNKNOWN }

    data class NaviLocation(val lat: Double, val lon: Double, val address: String?)

    /** 내비에 정해진 목적지입니다. 남은 시간은 초, 남은 거리는 미터입니다. */
    data class Destination(val lat: Double, val lon: Double, val name: String, val address: String?, val remainSec: Int, val remainM: Int)

    interface Listener {
        fun onGearChanged(gear: Gear, previous: Gear?)
        fun onDriverDoorChanged(open: Boolean)
        fun onConnectionChanged(vehicleReady: Boolean, naviReady: Boolean)
        fun onRouteStarted(name: String?) {}
        fun onDestinationChanged(destination: Destination) {}
        fun onRemainingChanged(remainSec: Int, remainM: Int) {}
        fun onRouteCleared() {}
        fun onNaviError(message: String) {}
    }

    private val app = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private var vehicle: Vehicle? = null
    private var navi: NaviHelper? = null
    private var stopped = false

    var gear: Gear? = null
        private set
    var driverDoorOpen = false
        private set
    var vehicleReady = false
        private set
    var naviReady = false
        private set
    var lastLocation: NaviLocation? = null
        private set
    var destination: Destination? = null
        private set

    /** 내비가 바로 앞(30m 이내)의 주정차 금지 구간을 마지막으로 알려 준 시각입니다(elapsedRealtime). */
    private var noParkingZoneAt = 0L
    private val pendingLocation = mutableListOf<(NaviLocation?) -> Unit>()

    fun start() {
        stopped = false
        startVehicle()
        startNavi()
    }

    fun stop() {
        stopped = true
        main.removeCallbacksAndMessages(null)
        runCatching { vehicle?.getGear()?.unregisterGearSelection(gearListener) }
        runCatching { vehicle?.getDoor()?.unregisterDoorPosition(DRIVER_DOOR, doorListener) }
        runCatching { vehicle?.release() }
        runCatching { navi?.removeListener(naviListener); navi?.release() }
        vehicle = null
        navi = null
    }

    /** 내비에 현재 위치를 묻습니다. 2초 안에 답이 없으면 마지막으로 받은 위치를 줍니다. */
    fun requestLocation(callback: (NaviLocation?) -> Unit) {
        val helper = navi
        if (helper == null || !naviReady) {
            callback(lastLocation)
            return
        }
        pendingLocation += callback
        runCatching { helper.getCurrentLocationInfo() }.onFailure { flushLocation() }
        main.postDelayed({ flushLocation() }, LOCATION_TIMEOUT_MS)
    }

    /** 최근 1분 안에 내비가 바로 앞의 주정차 금지 구간을 알려 줬는지입니다. */
    fun recentlyPassedNoParkingZone(): Boolean =
        noParkingZoneAt > 0 && SystemClock.elapsedRealtime() - noParkingZoneAt < NO_PARKING_MEMORY_MS

    fun cancelRoute() {
        runCatching { navi?.cancelRoute() }
    }

    fun routeTo(lat: Double, lon: Double, name: String) {
        val helper = navi ?: return listener.onNaviError("내비와 연결되지 않았어요")
        runCatching {
            helper.requestRoute(RouteInfo(latitude = lat, longitude = lon, poiName = name, poiId = "", address = "", poiSubId = ""))
        }.onFailure { listener.onNaviError("경로를 요청하지 못했어요") }
    }

    // ── Vehicle SDK ─────────────────────────────────────────

    private val gearListener = object : GearSelectionListener {
        override fun onSelected(gear: VehicleGear?) {
            main.post { updateGear(gear.toGear()) }
        }

        override fun onFailed(e: Exception) {
            Log.w(TAG, "기어 수신 실패: ${e.message}")
        }
    }

    private val doorListener = object : DoorPositionListener {
        override fun onPositionUpdated(position: Int?) {
            main.post {
                val open = (position ?: 0) > 0
                if (open != driverDoorOpen) {
                    driverDoorOpen = open
                    listener.onDriverDoorChanged(open)
                }
            }
        }

        override fun onFailed(e: Exception) {
            Log.w(TAG, "도어 수신 실패: ${e.message}")
        }
    }

    private fun startVehicle() {
        val v = Vehicle(app)
        vehicle = v
        v.initialize()
        waitForVehicle(v, attempt = 1)
    }

    /** 서비스 연결이 비동기라서 현재 기어를 읽을 수 있을 때까지 다시 시도합니다. */
    private fun waitForVehicle(v: Vehicle, attempt: Int) {
        v.getGear().getCurrentGearSelectionState(
            { current ->
                main.post {
                    if (stopped) return@post
                    vehicleReady = true
                    updateGear(current.toGear())
                    v.getGear().registerGearSelection(gearListener)
                    v.getDoor().registerDoorPosition(DRIVER_DOOR, doorListener)
                    notifyConnection()
                }
            },
            { e ->
                if (attempt < VEHICLE_RETRIES) {
                    main.postDelayed({ if (!stopped) waitForVehicle(v, attempt + 1) }, 1000)
                } else {
                    Log.w(TAG, "Vehicle SDK 연결 실패: ${e.message}")
                    main.post { notifyConnection() }
                }
            },
        )
    }

    private fun updateGear(next: Gear) {
        val previous = gear
        if (previous == next) return
        gear = next
        listener.onGearChanged(next, previous)
    }

    private fun VehicleGear?.toGear(): Gear = when (this) {
        VehicleGear.GEAR_PARK -> Gear.PARK
        VehicleGear.GEAR_REVERSE -> Gear.REVERSE
        VehicleGear.GEAR_NEUTRAL -> Gear.NEUTRAL
        null, VehicleGear.GEAR_UNKNOWN -> Gear.UNKNOWN
        else -> Gear.DRIVE // GEAR_DRIVE, GEAR_1~9
    }

    // ── NaviHelper ──────────────────────────────────────────

    private val naviListener = object : NaviHelperEventListener {
        override fun onCurrentLocationInfo(info: CurrentLocationInfo) {
            main.post {
                lastLocation = NaviLocation(info.latitude, info.longitude, info.address)
                flushLocation()
            }
        }

        override fun onCautionPointInfo(points: List<CautionPoint>) {
            if (points.any { it.type == CautionPointType.NO_PARKING_ZONE && it.remainDistance <= NO_PARKING_NEAR_M }) {
                noParkingZoneAt = SystemClock.elapsedRealtime()
            }
        }

        override fun onRouteStarted(info: RouteStartInfo) {
            main.post {
                listener.onRouteStarted(info.name)
                runCatching { navi?.getDestinationInfo() }
            }
        }

        override fun onDestinationChanged() {
            main.post { runCatching { navi?.getDestinationInfo() } }
        }

        override fun onDestinationInfo(info: DestinationInfo) {
            main.post {
                if (info.latitude == 0.0 && info.longitude == 0.0) return@post // 경로가 없을 때 오는 빈 값
                val next = Destination(info.latitude, info.longitude, info.name.ifBlank { "목적지" }, info.address,
                    info.remainTime, info.remainDistance)
                val previous = destination
                destination = next
                if (previous == null || previous.lat != next.lat || previous.lon != next.lon) listener.onDestinationChanged(next)
            }
        }

        override fun onDrivingInfo(info: DrivingInfo) {
            val state = info.destination
            main.post {
                destination = destination?.copy(remainSec = state.duration, remainM = state.distance)
                if (destination != null) listener.onRemainingChanged(state.duration, state.distance)
            }
        }

        override fun onRouteCancelled() {
            main.post { clearRoute() }
        }

        override fun onDestinationArrived(info: DestinationArrivedInfo) {
            main.post { clearRoute() }
        }

        override fun onError(code: NaviErrorCode) {
            main.post { listener.onNaviError("내비 오류: $code") }
        }
    }

    private fun startNavi() {
        val helper = NaviHelper(app)
        navi = helper
        helper.initialize(
            {
                main.post {
                    if (stopped) return@post
                    helper.addListener(naviListener)
                    naviReady = true
                    notifyConnection()
                    runCatching { helper.getCurrentLocationInfo() }
                }
            },
            { e ->
                Log.w(TAG, "NaviHelper 연결 실패: ${e.message}")
                main.post { notifyConnection() }
            },
        )
    }

    private fun clearRoute() {
        if (destination == null) return
        destination = null
        listener.onRouteCleared()
    }

    private fun flushLocation() {
        if (pendingLocation.isEmpty()) return
        val callbacks = pendingLocation.toList()
        pendingLocation.clear()
        callbacks.forEach { it(lastLocation) }
    }

    private fun notifyConnection() = listener.onConnectionChanged(vehicleReady, naviReady)

    companion object {
        private const val TAG = "CarSignals"
        private val DRIVER_DOOR = DoorArea(DoorValue.ROW_1_LEFT)
        private const val VEHICLE_RETRIES = 10
        private const val LOCATION_TIMEOUT_MS = 2_000L
        private const val NO_PARKING_NEAR_M = 30
        private const val NO_PARKING_MEMORY_MS = 60_000L
    }
}
