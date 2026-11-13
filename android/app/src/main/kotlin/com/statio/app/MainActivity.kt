package com.statio.app

import android.app.Activity
import android.car.Car
import android.car.drivingstate.CarUxRestrictions
import android.car.drivingstate.CarUxRestrictionsManager
import android.content.Intent
import android.content.res.Configuration
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.View
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.TextView
import com.statio.app.core.LogEvent
import com.statio.app.core.LogKind
import com.statio.app.core.Lot
import com.statio.app.core.LotRow
import com.statio.app.core.ParkingCheck
import com.statio.app.core.RiskLevel
import com.statio.app.core.distanceM
import com.statio.app.core.feeFor
import com.statio.app.core.monthlyReport
import com.statio.app.core.recommendedIndex
import com.statio.app.ui.Arrival
import com.statio.app.ui.ArrivalRenderer
import com.statio.app.ui.CheckRenderer
import com.statio.app.ui.Format
import com.statio.app.ui.ParkedRenderer
import com.statio.app.ui.ReportRenderer
import com.statio.app.vehicle.CarSignals
import com.statio.app.vehicle.CarSignals.Gear
import java.time.Duration
import java.time.YearMonth
import java.time.format.DateTimeFormatter
import kotlin.math.roundToInt

/**
 * Statio 메인 화면입니다. 화면은 다섯 가지예요.
 * - 대기: 연결 상태, 마지막 주차 위치, 이번 달 리포트 요약, 시연 위치
 * - 도착 전 비교(시안 01): 내비에 목적지가 정해지면 근처 주차장과 도착 시점 예상 빈자리
 * - P단 판단(시안 02): 기어가 P로 바뀐 자리의 단속 위험과 대안 주차장
 * - 위치 기록(시안 03): 운전석 문이 열리면 주차 위치, 층, 휴대폰 QR
 * - 월간 리포트(시안 05): 기기 안에 쌓은 경고·이동 기록으로 계산한 이번 달 절감액
 *
 * 내비 옆 분할 창(세로)과 전체 창(가로)을 오가면 레이아웃만 바꿔 같은 상태를 다시 그립니다.
 * 주행 중 제한(CarUxRestrictions)에 '설정 금지'가 걸리면 시연 위치 선택을 잠급니다.
 *
 * adb로 시연을 돕는 명령을 보낼 수 있습니다.
 *   --es scenario gwanghwamun|wonhyoro|jongmyo|real   시연 위치
 *   --es cmd check                                     P단 판단을 바로 실행
 *   --es cmd route --es lat 37.5724 --es lon 126.9769 --es name 광화문   운전자가 목적지를 정한 것처럼 경로 요청
 *   --es cmd parked                                    마지막 주차 기록 화면
 *   --es cmd idle                                      대기 화면
 *   --es cmd report                                    이번 달 리포트 화면
 *   --es cmd report_clear                              리포트 기록 비우기 (시연 시작 전)
 *   --es cmd reset                                     경로 취소, 판단·비교 결과 지우고 대기 화면 (시연 시작 전)
 *   --es cmd locate --es target btn_route              버튼의 화면 좌표를 로그로 알려 줌 (text:B2처럼 글자로도 찾음)
 * 화면이 바뀔 때와 locate 결과는 로그 태그 StatioDemo로 남겨서 tools/demo.py가 읽어요.
 */
class MainActivity : Activity(), CarSignals.Listener {

    private enum class Screen { IDLE, CHECK, ARRIVAL, PARKED, REPORT }

    private lateinit var app: StatioApp
    private lateinit var signals: CarSignals
    private val main = Handler(Looper.getMainLooper())
    private var tone: ToneGenerator? = null

    // 화면 상태입니다. 창 크기가 바뀌어 레이아웃을 다시 만들 때도 그대로 남습니다.
    private var screen = Screen.IDLE
    private var check: ParkingCheck? = null
    private var checkPlace = ""
    private var arrival: Arrival? = null
    private var arrivalNearShown = false
    private var record: ParkingRecord? = null
    /** Statio가 안내한 주차장입니다. 시연 위치를 쓸 때는 다음 P단을 이 주차장에 도착한 것으로 봅니다. */
    private var routedLot: LotRow? = null
    /** P단 경고를 받고 대안 주차장으로 안내했을 때의 과태료 추정입니다. 그 주차장에 세우면 리포트에 '주차장으로 옮김'으로 남겨요. */
    private var warnedFine: Int? = null
    private var checking = false
    private var car: Car? = null
    private var uxManager: CarUxRestrictionsManager? = null
    /** 주행 중이라 설정 성격의 화면(시연 위치 선택)을 막아야 하는지입니다. */
    private var setupLocked = false
    private var status: Pair<String, Chip> = "차량 연결 중" to Chip.NORMAL

    private lateinit var checkRenderer: CheckRenderer
    private lateinit var arrivalRenderer: ArrivalRenderer
    private lateinit var parkedRenderer: ParkedRenderer
    private lateinit var reportRenderer: ReportRenderer

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = application as StatioApp
        record = ParkingRecord.load(this)
        signals = CarSignals(this, this)
        inflate()
        signals.start()
        connectUxRestrictions()
        handleCommand(intent)
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        inflate() // values-w1200dp·layout-w1200dp 선택이 바뀌므로 레이아웃을 새로 만들어요.
    }

    override fun onResume() {
        super.onResume()
        main.post(liveTicker)
    }

    override fun onPause() {
        main.removeCallbacks(liveTicker)
        super.onPause()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleCommand(intent)
    }

    override fun onDestroy() {
        runCatching { uxManager?.unregisterListener() }
        car?.disconnect()
        signals.stop()
        tone?.release()
        super.onDestroy()
    }

    // ── 레이아웃과 화면 전환 ─────────────────────────────────────

    private fun inflate() {
        setContentView(R.layout.activity_main)
        checkRenderer = CheckRenderer(this, findViewById(R.id.check))
        arrivalRenderer = ArrivalRenderer(this, findViewById(R.id.arrival)) { index ->
            arrival = arrival?.copy(selected = index)
            render()
        }
        parkedRenderer = ParkedRenderer(this, findViewById(R.id.parked)) { floor -> chooseFloor(floor) }
        reportRenderer = ReportRenderer(findViewById(R.id.report))

        findViewById<View>(R.id.btn_check_now).setOnClickListener { runCheck() }
        findViewById<View>(R.id.btn_dismiss).setOnClickListener { show(Screen.IDLE) }
        findViewById<View>(R.id.btn_route).setOnClickListener {
            val c = check ?: return@setOnClickListener
            c.bestAlternative?.let { routeToLot(it.row, fine = c.fineKrw.takeIf { c.level == RiskLevel.HIGH && it > 0 }) }
        }
        findViewById<View>(R.id.btn_arrival_route).setOnClickListener {
            val a = arrival ?: return@setOnClickListener
            a.selectedLot?.let { lot -> logAvoidedFull(a, lot); routeToLot(lot.row) }
        }
        findViewById<View>(R.id.btn_arrival_close).setOnClickListener { show(Screen.IDLE) }
        findViewById<View>(R.id.btn_parked_done).setOnClickListener { show(Screen.IDLE) }
        findViewById<View>(R.id.record_card).setOnClickListener { if (record != null) show(Screen.PARKED) }
        findViewById<View>(R.id.report_card).setOnClickListener { show(Screen.REPORT) }
        findViewById<View>(R.id.btn_report_close).setOnClickListener { show(Screen.IDLE) }

        renderScenarios()
        signals.gear?.let { findViewById<TextView>(R.id.gear_value).text = GEAR_LABEL.getValue(it) }
        renderConnection()
        render()
        setStatus(status.first, status.second)
    }

    private fun show(next: Screen) {
        screen = next
        Log.i(DEMO_TAG, "screen=${next.name}")
        when (next) {
            Screen.IDLE -> setStatus(if (signals.vehicleReady) "P단을 기다리는 중" else "차량 연결 중", Chip.INFO)
            Screen.PARKED -> record?.let { setStatus("${it.parkedAt.format(CLOCK)} 주차", Chip.NORMAL) }
            Screen.ARRIVAL -> setStatus("실시간", Chip.INFO)
            Screen.REPORT -> setStatus("${StatioApp.nowSeoul().monthValue}월", Chip.NORMAL)
            Screen.CHECK -> Unit // 판단 결과에 따라 evaluate()에서 정해요.
        }
        render()
    }

    private fun render() {
        if (screen == Screen.CHECK && check == null) screen = Screen.IDLE
        if (screen == Screen.ARRIVAL && arrival == null) screen = Screen.IDLE
        if (screen == Screen.PARKED && record == null) screen = Screen.IDLE
        when (screen) {
            Screen.CHECK -> checkRenderer.render(check!!, checkPlace, signals.recentlyPassedNoParkingZone())
            Screen.ARRIVAL -> arrivalRenderer.render(arrival!!)
            Screen.PARKED -> parkedRenderer.render(record!!)
            Screen.REPORT -> thisMonthReport().let { r ->
                // 시연 스크립트(tools/demo.py)가 이 줄로 리포트 자막을 만들어요.
                Log.i(DEMO_TAG, "report saved=${r.savedKrw} warnings=${r.warnings} moves=${r.moves}")
                reportRenderer.render(r)
            }
            Screen.IDLE -> { renderRecordCard(); renderReportCard() }
        }
        findViewById<View>(R.id.idle).visibility = visibleIf(screen == Screen.IDLE)
        findViewById<View>(R.id.check).visibility = visibleIf(screen == Screen.CHECK)
        findViewById<View>(R.id.arrival).visibility = visibleIf(screen == Screen.ARRIVAL)
        findViewById<View>(R.id.parked).visibility = visibleIf(screen == Screen.PARKED)
        findViewById<View>(R.id.report).visibility = visibleIf(screen == Screen.REPORT)
    }

    private fun visibleIf(b: Boolean) = if (b) View.VISIBLE else View.GONE

    // ── 차량·내비 신호 ───────────────────────────────────────

    override fun onGearChanged(gear: Gear, previous: Gear?) {
        findViewById<TextView>(R.id.gear_value).text = GEAR_LABEL.getValue(gear)
        when {
            gear == Gear.PARK && previous != null && previous != Gear.PARK -> runCheck()
            // 다시 출발하면 닫고, 목적지가 있으면 도착 전 비교로 돌아가요.
            gear != Gear.PARK && (screen == Screen.CHECK || screen == Screen.PARKED || screen == Screen.REPORT) -> show(if (arrival != null) Screen.ARRIVAL else Screen.IDLE)
        }
    }

    override fun onDriverDoorChanged(open: Boolean) {
        if (open && signals.gear == Gear.PARK) recordParking()
    }

    override fun onConnectionChanged(vehicleReady: Boolean, naviReady: Boolean) {
        renderConnection()
        if (!checking && (screen == Screen.IDLE || status.first == "차량 연결 중")) {
            if (vehicleReady) setStatus("P단을 기다리는 중", Chip.INFO) else setStatus("차량 신호를 받지 못했어요", Chip.BAD)
        }
    }

    override fun onRouteStarted(name: String?) = setStatus("${name ?: "목적지"}으로 안내 중", Chip.INFO)

    override fun onDestinationChanged(destination: CarSignals.Destination) {
        val lot = routedLot
        if (lot != null && distanceM(destination.lat, destination.lon, lot.lat, lot.lon) < SAME_PLACE_M) {
            setStatus("${Format.shortName(lot.name)}으로 가는 중", Chip.OK)
            return
        }
        routedLot = null
        arrivalNearShown = false
        // 목적지를 정했다는 건 다시 출발한다는 뜻이라 지난 주차 기록 화면 위에도 띄워요. P단 경고는 가리지 않아요.
        computeArrival(destination, showWhenReady = screen != Screen.CHECK)
    }

    override fun onRemainingChanged(remainSec: Int, remainM: Int) {
        val dest = signals.destination ?: return
        // 도착 10분 전에 한 번 더 띄워요. 처음 띄운 화면을 닫았어도 이때는 다시 보여 줘요.
        if (remainSec in 1..ARRIVAL_REMIND_SEC && !arrivalNearShown && routedLot == null) {
            arrivalNearShown = true
            computeArrival(dest, showWhenReady = screen == Screen.IDLE || screen == Screen.ARRIVAL)
        }
    }

    override fun onRouteCleared() {
        arrival = null
        if (screen == Screen.ARRIVAL) show(Screen.IDLE)
    }

    override fun onNaviError(message: String) = setStatus(message, Chip.BAD)

    // ── 도착 전 비교 ─────────────────────────────────────────

    private fun computeArrival(dest: CarSignals.Destination, showWhenReady: Boolean) {
        val now = StatioApp.nowSeoul()
        val count = resources.getInteger(R.integer.arrival_lot_count)
        app.worker.execute {
            val lots = runCatching {
                app.advisor.nearbyLots(dest.lat, dest.lon, now, etaMin = dest.remainSec / 60.0, limit = count, keepNearestFull = true)
            }
            main.post {
                lots.onFailure { setStatus("주차장을 찾지 못했어요: ${it.javaClass.simpleName}", Chip.BAD) }
                val found = lots.getOrNull().orEmpty()
                if (found.isEmpty()) {
                    arrival = null
                    setStatus("${dest.name} 근처에 안내할 주차장이 없어요", Chip.NORMAL)
                    return@post
                }
                val keep = arrival?.takeIf { it.destination.lat == dest.lat && it.destination.lon == dest.lon }?.selectedLot?.row?.id
                // 운전자가 고른 곳이 있으면 그대로, 아니면 만차(만차 위험)가 아닌 첫 곳을 가리켜요. 모두 만차면 안내 버튼을 숨겨요(-1).
                val picked = found.indexOfFirst { it.row.id == keep }.takeIf { it >= 0 } ?: recommendedIndex(found) ?: -1
                arrival = Arrival(dest, found, now, selected = picked)
                if (showWhenReady) show(Screen.ARRIVAL)
            }
        }
    }

    private fun routeToLot(lot: LotRow, fine: Int? = null) {
        routedLot = lot
        warnedFine = fine
        arrival = null // 주차장을 골랐으니 비교는 끝났어요. 다시 출발해도 이 비교 화면으로 돌아가지 않아요.
        signals.routeTo(lot.lat, lot.lon, lot.name)
        show(Screen.IDLE)
        setStatus("${Format.shortName(lot.name)}으로 경로를 요청했어요", Chip.INFO) // 대기 화면 기본 문구를 덮어써요
    }

    // ── P단 판단 ───────────────────────────────────────────

    /** P단 판단입니다. [at]을 주면(adb 확인용) 시연 위치와 상관없이 그 좌표로 한 번 판단해요. */
    private fun runCheck(at: Triple<Double, Double, String>? = null) {
        if (checking) return
        checking = true
        setStatus("세운 자리를 확인하는 중", Chip.INFO)
        val scenario = Scenario.selected(this)
        val arrivedLot = routedLot
        when {
            at != null -> evaluate(at.first, at.second, at.third)
            // 시연 위치를 쓰는 중이면 Statio가 안내한 주차장에 도착한 것으로 봐요.
            scenario != null && arrivedLot != null -> evaluate(arrivedLot.lat, arrivedLot.lon, Format.shortName(arrivedLot.name))
            scenario != null -> evaluate(scenario.lat, scenario.lon, scenario.place)
            else -> signals.requestLocation { loc ->
                if (loc == null) {
                    checking = false
                    setStatus("현재 위치를 받지 못했어요", Chip.BAD)
                } else {
                    evaluate(loc.lat, loc.lon, loc.address ?: "현재 위치")
                }
            }
        }
    }

    private fun evaluate(lat: Double, lon: Double, place: String) {
        val now = StatioApp.nowSeoul()
        app.worker.execute {
            val result = runCatching { app.advisor.check(lat, lon, now) }
            main.post {
                checking = false
                result.onSuccess {
                    check = it
                    checkPlace = place
                    // 시연 스크립트(tools/demo.py)가 이 줄로 경고 자막을 만들어요. screen=CHECK보다 먼저 남겨요.
                    Log.i(DEMO_TAG, "check fine=${it.fineKrw} risks=" +
                        it.risks.joinToString(";") { r -> "${r.kind.code}|${r.kind.label}|${r.distanceM}|${r.name.orEmpty()}" })
                    if (it.level == RiskLevel.HIGH && it.fineKrw > 0) {
                        ParkingLog.add(this, LogEvent(LogKind.WARN, now, place, fineKrw = it.fineKrw))
                    }
                    show(Screen.CHECK)
                    val level = if (it.level == RiskLevel.NONE && signals.recentlyPassedNoParkingZone()) RiskLevel.CAUTION else it.level
                    when (level) {
                        RiskLevel.HIGH -> { setStatus("주차 전 확인", Chip.BAD); beep() }
                        RiskLevel.CAUTION -> setStatus("주차 전 확인", Chip.NORMAL)
                        RiskLevel.NONE -> setStatus("주차해도 좋아요", Chip.OK)
                    }
                }.onFailure { setStatus("판단하지 못했어요: ${it.javaClass.simpleName}", Chip.BAD) }
            }
        }
    }

    // ── 위치 기록 ───────────────────────────────────────────

    /** 운전석 문이 열린 순간 = 운전자가 내리는 순간이라 위치를 기록하고 층을 묻습니다. */
    private fun recordParking() {
        val c = check?.takeIf { Duration.between(it.checkedAt, StatioApp.nowSeoul()).toMinutes() < RECENT_CHECK_MIN }
        val (lat, lon, place) = when {
            c != null -> Triple(c.lat, c.lon, checkPlace)
            else -> Scenario.selected(this)?.let { Triple(it.lat, it.lon, it.place) }
                ?: signals.lastLocation?.let { Triple(it.lat, it.lon, it.address ?: "현재 위치") }
                ?: return setStatus("위치를 몰라 기록하지 못했어요", Chip.BAD)
        }
        val now = StatioApp.nowSeoul()
        app.worker.execute {
            val lot = runCatching { app.advisor.lotAt(lat, lon) }.getOrNull()
            main.post {
                val next = ParkingRecord(lat, lon, lot?.let { Format.shortName(it.name) } ?: place, now, lot)
                record = next
                logParking(next, arrivedLot = routedLot)
                routedLot = null
                warnedFine = null
                ParkingRecord.save(this, next)
                setStatus("${now.format(CLOCK)} 주차", Chip.NORMAL)
                show(Screen.PARKED)
            }
        }
    }

    private fun chooseFloor(floor: String?) {
        val current = record ?: return
        val next = current.copy(floor = floor)
        record = next
        ParkingRecord.save(this, next)
        render()
    }

    // ── 월간 리포트 ─────────────────────────────────────────

    private fun thisMonthReport() = monthlyReport(ParkingLog.load(this), YearMonth.from(StatioApp.nowSeoul()))

    /** 주차를 기록하고, 경고를 받고 안내한 주차장에 세웠으면 '주차장으로 옮김'도 남겨요(아낀 돈 = 과태료 − 1시간 요금). */
    private fun logParking(record: ParkingRecord, arrivedLot: LotRow?) {
        val lot = record.lot
        ParkingLog.add(this, LogEvent(LogKind.PARK, record.parkedAt, record.place, inLot = lot != null))
        val fine = warnedFine
        if (fine != null && lot != null && arrivedLot != null && lot.id == arrivedLot.id) {
            ParkingLog.add(this, LogEvent(LogKind.MOVE, record.parkedAt, record.place, fineKrw = fine, feeKrw = feeFor(60, lot) ?: 0))
        }
    }

    /** 목적지에 더 가까운 주차장이 만차(만차 위험)라 다른 주차장으로 안내하면 '만차 피함'으로 남겨요. */
    private fun logAvoidedFull(a: Arrival, picked: Lot) {
        val avoided = a.lots.any { !it.recommendable && it.distanceM < picked.distanceM }
        if (avoided) ParkingLog.add(this, LogEvent(LogKind.AVOID_FULL, StatioApp.nowSeoul(), Format.shortName(picked.row.name)))
    }

    private fun renderReportCard() {
        findViewById<TextView>(R.id.report_card_value).text = ReportRenderer.summary(thisMonthReport())
    }

    // ── 대기 화면 ───────────────────────────────────────────

    private fun renderScenarios() {
        val list = findViewById<LinearLayout>(R.id.scenario_list)
        list.removeAllViews()
        val selected = Scenario.selected(this)
        val portrait = !resources.getBoolean(R.bool.wide)
        (listOf<Scenario?>(null) + Scenario.presets).forEachIndexed { i, scenario ->
            val item = TextView(this, null, 0, if (portrait) R.style.Text_BodyM else R.style.Text_BodyL).apply {
                text = scenario?.label ?: "실제 내비 위치"
                val v = if (portrait) dp(16) else dp(22)
                setPadding(dp(28), v, dp(28), v)
                setBackgroundResource(if (scenario == selected) R.drawable.bg_card_pick else R.drawable.bg_button_basic)
                setTypeface(typeface, if (scenario == selected) android.graphics.Typeface.BOLD else android.graphics.Typeface.NORMAL)
                isEnabled = !setupLocked
                alpha = if (setupLocked && scenario != selected) 0.4f else 1f
                setOnClickListener {
                    Scenario.select(this@MainActivity, scenario)
                    routedLot = null
                    renderScenarios()
                    renderConnection()
                }
            }
            list.addView(item, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { if (i > 0) topMargin = dp(10) })
        }
        if (setupLocked) {
            val note = TextView(this, null, 0, R.style.Text_LabelM).apply { text = "주행 중에는 바꿀 수 없어요" }
            list.addView(note, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { topMargin = dp(12) })
        }
    }

    /** 주행 중 제한을 받아요. 이 앱은 주행 중 표시 가능(distractionOptimized)이라 화면은 막히지 않고, 설정만 잠가요. */
    private fun connectUxRestrictions() {
        val connected = runCatching { Car.createCar(this) }.getOrNull() ?: return
        car = connected
        val manager = connected.getCarManager(Car.CAR_UX_RESTRICTION_SERVICE) as? CarUxRestrictionsManager ?: return
        uxManager = manager
        manager.registerListener { restrictions -> main.post { applyUxRestrictions(restrictions) } }
        manager.currentCarUxRestrictions?.let { applyUxRestrictions(it) }
    }

    private fun applyUxRestrictions(restrictions: CarUxRestrictions) {
        val locked = restrictions.isRequiresDistractionOptimization &&
            restrictions.activeRestrictions and CarUxRestrictions.UX_RESTRICTIONS_NO_SETUP != 0
        if (locked == setupLocked) return
        setupLocked = locked
        renderScenarios()
    }

    private fun renderConnection() {
        findViewById<TextView>(R.id.vehicle_state).text = if (signals.vehicleReady) "Vehicle SDK 연결됨" else "Vehicle SDK 연결 중"
        val scenario = Scenario.selected(this)
        val location = findViewById<TextView>(R.id.location_value)
        val source = findViewById<TextView>(R.id.location_source)
        if (scenario != null) {
            location.text = scenario.label
            source.text = "시연 위치 사용 중"
        } else {
            location.text = signals.lastLocation?.address ?: "–"
            source.text = if (signals.naviReady) "내비 현재 위치" else "내비 연결 중"
        }
    }

    private fun renderRecordCard() {
        val r = record
        findViewById<View>(R.id.record_card).visibility = visibleIf(r != null)
        if (r != null) {
            findViewById<TextView>(R.id.record_value).text =
                listOfNotNull(r.place, r.floor, r.parkedAt.format(DATE_TIME)).joinToString(" · ")
        }
    }

    /** 1분마다 실시간 빈자리 연결 상태를 갱신하고, 도착 전 비교 화면이면 빈자리도 새로 받아요. */
    private val liveTicker = object : Runnable {
        override fun run() {
            refreshLive()
            if (screen == Screen.ARRIVAL) arrival?.let { computeArrival(signals.destination ?: it.destination, showWhenReady = false) }
            main.postDelayed(this, 60_000)
        }
    }

    private fun refreshLive() {
        if (!app.live.hasKey) {
            findViewById<TextView>(R.id.live_value).text = "꺼짐"
            findViewById<TextView>(R.id.live_note).text = "API 키 없이 빌드됐어요"
            return
        }
        app.worker.execute {
            val latest = app.live.latest()
            val newest = latest.values.maxOfOrNull { it.updatedAt }
            val error = app.live.lastError
            main.post {
                val value = findViewById<TextView>(R.id.live_value)
                val note = findViewById<TextView>(R.id.live_note)
                if (latest.isEmpty()) {
                    value.text = "받지 못했어요"
                    note.text = error ?: "응답 없음"
                } else {
                    value.text = "${latest.size}곳"
                    val age = newest?.let { Duration.between(it, StatioApp.nowSeoul()).toMinutes() } ?: 0
                    note.text = "${age}분 전 기준"
                }
            }
        }
    }

    // ── 공통 ────────────────────────────────────────────────

    private enum class Chip(val background: Int, val color: Int) {
        NORMAL(R.drawable.bg_chip, R.color.text_2),
        INFO(R.drawable.bg_chip_info, R.color.positive),
        OK(R.drawable.bg_chip_ok, R.color.active_text),
        BAD(R.drawable.bg_chip_bad, R.color.negative),
    }

    private fun setStatus(text: String, chip: Chip) {
        status = text to chip
        val view = findViewById<TextView>(R.id.status_chip)
        view.text = text
        view.setBackgroundResource(chip.background)
        view.setTextColor(getColor(chip.color))
    }

    private fun handleCommand(intent: Intent?) {
        intent ?: return
        intent.getStringExtra("scenario")?.let { id ->
            Scenario.select(this, Scenario.byId(id))
            routedLot = null
            renderScenarios()
            renderConnection()
        }
        when (intent.getStringExtra("cmd")) {
            "check" -> {
                val lat = intent.getStringExtra("lat")?.toDoubleOrNull()
                val lon = intent.getStringExtra("lon")?.toDoubleOrNull()
                runCheck(if (lat != null && lon != null) Triple(lat, lon, intent.getStringExtra("place") ?: "지정 위치") else null)
            }
            "idle" -> show(Screen.IDLE)
            "parked" -> show(Screen.PARKED)
            "report" -> show(Screen.REPORT)
            "report_clear" -> { ParkingLog.clear(this); render() }
            "locate" -> intent.getStringExtra("target")?.let { locateForDemo(it) }
            "reset" -> {
                signals.cancelRoute()
                routedLot = null
                warnedFine = null
                arrival = null
                check = null
                arrivalNearShown = false
                show(Screen.IDLE)
            }
            "route" -> {
                val lat = intent.getStringExtra("lat")?.toDoubleOrNull()
                val lon = intent.getStringExtra("lon")?.toDoubleOrNull()
                if (lat != null && lon != null) signals.routeTo(lat, lon, intent.getStringExtra("name") ?: "목적지")
            }
        }
    }

    /** 시연 스크립트가 누를 곳의 화면 좌표(가운데)를 로그로 알려 줘요. 화면 덤프 없이 실제 터치로 누를 수 있어요. */
    private fun locateForDemo(target: String) {
        window.decorView.post {
            val view = if (target.startsWith("text:")) {
                findShownText(window.decorView, target.removePrefix("text:"))
            } else {
                resources.getIdentifier(target, "id", packageName).takeIf { it != 0 }?.let { findViewById<View>(it) }
            }
            if (view != null && view.isShown) {
                val loc = IntArray(2)
                view.getLocationOnScreen(loc)
                Log.i(DEMO_TAG, "locate $target ${loc[0] + view.width / 2} ${loc[1] + view.height / 2}")
            } else {
                Log.i(DEMO_TAG, "locate $target none")
            }
        }
    }

    private fun findShownText(root: View, text: String): View? = when {
        root is TextView && root.isShown && root.text.toString() == text -> root
        root is ViewGroup -> (0 until root.childCount).firstNotNullOfOrNull { findShownText(root.getChildAt(it), text) }
        else -> null
    }

    private fun beep() {
        val generator = tone ?: runCatching { ToneGenerator(AudioManager.STREAM_NOTIFICATION, 80) }.getOrNull()?.also { tone = it }
        generator?.startTone(ToneGenerator.TONE_PROP_BEEP2, 300)
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).roundToInt()

    companion object {
        private const val DEMO_TAG = "StatioDemo"
        private const val SAME_PLACE_M = 50.0
        private const val ARRIVAL_REMIND_SEC = 600
        private const val RECENT_CHECK_MIN = 30L
        private val CLOCK = DateTimeFormatter.ofPattern("HH:mm")
        private val DATE_TIME = DateTimeFormatter.ofPattern("M월 d일 HH:mm")
        private val GEAR_LABEL = mapOf(Gear.PARK to "P", Gear.REVERSE to "R", Gear.NEUTRAL to "N", Gear.DRIVE to "D", Gear.UNKNOWN to "–")
    }
}
