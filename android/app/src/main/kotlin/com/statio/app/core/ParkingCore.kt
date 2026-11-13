package com.statio.app.core

import java.time.DayOfWeek
import java.time.Duration
import java.time.LocalDateTime
import kotlin.math.ceil
import kotlin.math.roundToInt

/**
 * 주정차 위험 판단, 요금 계산, 주변 주차장 순위, 빈자리 예측입니다.
 * server/parking_core.py를 옮긴 것이고, 같은 답을 내는지 단위 테스트(GoldenCasesTest)로 대조합니다.
 */

const val PATTERN_MIN_DAYS = 3        // 패턴 예측에 필요한 최소 일수
const val TREND_WINDOW_MIN = 30L      // 추세 예측에 쓰는 최근 기간
const val TREND_MIN_SPAN_MIN = 10.0
const val SLOTS_PER_DAY = 96          // 패턴표의 15분 칸 수
private const val SLOT_MIN = 1440 / SLOTS_PER_DAY

// ── 주정차 위험 ─────────────────────────────────────────────

/** 반경 안의 위험 지점을 종류별로 가장 가까운 한 곳씩, 우선순위 순서로 돌려줍니다. 반경은 지점마다의 값(교차로는 크기별)이에요. */
fun evaluateRisks(candidates: List<RiskPoint>, lat: Double, lon: Double, marginM: Double, at: LocalDateTime): List<Risk> {
    val nearest = HashMap<RiskKind, Pair<RiskPoint, Double>>()
    for (point in candidates) {
        val d = distanceM(lat, lon, point.lat, point.lon)
        if (d <= point.radiusM + marginM && (nearest[point.kind]?.second ?: Double.MAX_VALUE) > d) {
            nearest[point.kind] = point to d
        }
    }
    val weekdayDaytime = at.dayOfWeek !in setOf(DayOfWeek.SATURDAY, DayOfWeek.SUNDAY) && at.hour in 8 until 20
    return nearest.values
        .map { (p, d) ->
            val active = if (p.kind == RiskKind.SCHOOL_ZONE) weekdayDaytime else true
            Risk(p.kind, p.name, d.roundToInt(), p.lat, p.lon, activeNow = active, radiusM = p.radiusM)
        }
        .sortedBy { it.kind.ordinal }
}

fun riskLevel(risks: List<Risk>): RiskLevel = when {
    risks.any { it.activeNow } -> RiskLevel.HIGH
    risks.isNotEmpty() -> RiskLevel.CAUTION
    else -> RiskLevel.NONE
}

// ── 요금 ────────────────────────────────────────────────

/** 기본시간·추가단위 요금으로 주차 시간별 요금을 계산합니다. 정보가 없으면 null입니다. */
fun feeFor(minutes: Int, basicFee: Int?, basicMin: Int?, addFee: Int?, addMin: Int?, dayMax: Int? = null): Int? {
    if (basicFee == null || basicMin == null || basicMin == 0) return null
    var fee = basicFee
    if (minutes > basicMin && addMin != null && addMin > 0) {
        fee += ceil((minutes - basicMin).toDouble() / addMin).toInt() * (addFee ?: 0)
    }
    if (dayMax != null && dayMax > 0) fee = minOf(fee, dayMax)
    return fee
}

fun feeFor(minutes: Int, lot: LotRow): Int? = feeFor(minutes, lot.basicFee, lot.basicMin, lot.addFee, lot.addMin, lot.dayMax)

/** 요금이 [thresholdKrw]를 처음 넘는 주차 시간(분)입니다. 하루 안에 넘지 않거나 요금 정보가 없으면 null입니다. */
fun minutesUntilFeeExceeds(thresholdKrw: Int, lot: LotRow): Int? =
    (1..1440).firstOrNull { (feeFor(it, lot) ?: return null) > thresholdKrw }

/** 1시간 요금보다 큰 첫 번째 기준 금액(1만·2만·3만·5만 원)과 그 금액을 넘는 시간입니다. 안내할 게 없으면 null입니다. */
fun nextFeeStep(lot: LotRow): Pair<Int, Int>? {
    val hourly = feeFor(60, lot) ?: return null
    val step = listOf(10_000, 20_000, 30_000, 50_000).firstOrNull { it > hourly } ?: return null
    return minutesUntilFeeExceeds(step, lot)?.let { step to it }
}

// ── 빈자리 예측 ────────────────────────────────────────────

/** 패턴표에서 [target] 시각이 속하는 칸입니다. 칸은 시작 시각 앞뒤 15분의 기록으로 만들어져 있습니다. */
fun patternSlot(target: LocalDateTime): Pair<Int, Int> {
    val dayType = if (target.dayOfWeek == DayOfWeek.SATURDAY || target.dayOfWeek == DayOfWeek.SUNDAY) 1 else 0
    val minute = target.hour * 60 + target.minute
    return dayType to ((minute.toDouble() / SLOT_MIN).roundToInt() % SLOTS_PER_DAY)
}

/** 실시간 API를 받을 때마다 쌓는 한 주차장의 최근 기록입니다. */
data class LiveSample(val at: LocalDateTime, val free: Int)

/**
 * 도착 시점 빈자리를 예측합니다.
 * 1) pattern: 같은 요일 유형·같은 시간대 칸의 값이 서로 다른 날짜 3일 이상으로 만들어졌으면 씁니다.
 * 2) trend: 데이터가 부족하면 최근 30분 변화 속도를 도착 시간만큼 이어 붙입니다.
 * 3) current: 그것도 없으면 지금 값을 그대로 씁니다.
 */
fun predictFree(pattern: PatternCell?, recent: List<LiveSample>, now: LocalDateTime, etaMin: Double, currentFree: Int, total: Int): Prediction {
    if (pattern != null && pattern.days >= PATTERN_MIN_DAYS) {
        return Prediction(pattern.free.coerceIn(0, total), PredictionBasis.PATTERN, pattern.days)
    }
    val window = recent.filter { !it.at.isBefore(now.minusMinutes(TREND_WINDOW_MIN)) }.sortedBy { it.at }
    if (window.size >= 3) {
        val spanMin = Duration.between(window.first().at, window.last().at).seconds / 60.0
        if (spanMin >= TREND_MIN_SPAN_MIN) {
            val slope = (window.last().free - window.first().free) / spanMin
            val value = (currentFree + slope * etaMin).roundToInt()
            return Prediction(value.coerceIn(0, total), PredictionBasis.TREND, 0)
        }
    }
    return Prediction(currentFree, PredictionBasis.CURRENT, 0)
}

// ── 주변 주차장 ────────────────────────────────────────────

/**
 * 주차장을 '도착 때 자리 있을 곳 → 실시간 정보 없는 곳 → 만차(만차 위험)' 순서로, 같은 칸 안에서는 가까운 순서로 고릅니다.
 * AI 예측이 화면에 켜져 있으면 만차 위험 경고로, 아니면 지금 빈자리로 봅니다(server/parking_core.py는 지금 빈자리만 봐요).
 *
 * [keepNearestFull]이면 목적지에서 가장 가까운 주차장이 만차(만차 위험)라 목록에서 밀려났을 때 마지막 칸에 남겨요.
 * 운전자가 목적지 주차장이 왜 안 보이는지 헷갈리지 않고, '만차 위험'이라는 정보도 볼 수 있게요(도착 전 비교 화면만 써요).
 */
fun rankLots(
    candidates: List<LotRow>,
    live: Map<String, LiveCount>,
    lat: Double,
    lon: Double,
    radiusM: Double,
    now: LocalDateTime,
    limit: Int,
    predict: ((LotRow, LiveCount) -> Prediction)? = null,
    keepNearestFull: Boolean = false,
): List<Lot> {
    val all = candidates
        .mapNotNull { row ->
            val d = distanceM(lat, lon, row.lat, row.lon)
            if (d > radiusM) return@mapNotNull null
            val count = if (row.realtimeLinked) live[row.id] else null // 값이 멈춘 곳은 앱 DB에서 실시간 연계가 꺼져 있어요
            val age = count?.let { Duration.between(it.updatedAt, now).seconds / 60.0 }?.roundToInt()
            Lot(row, d.roundToInt(), count, age, count?.let { c -> predict?.invoke(row, c) })
        }
        .sortedWith(compareBy<Lot>({ it.rankTier }, { it.distanceM }, { it.row.id }))
    val shown = all.take(limit)
    val nearest = all.minWithOrNull(compareBy<Lot>({ it.distanceM }, { it.row.id }))
    if (!keepNearestFull || limit < 2 || nearest == null || nearest.recommendable || nearest in shown) return shown
    return shown.dropLast(1) + nearest
}

/** 안내 버튼이 처음 가리킬 주차장의 순서입니다. 만차(만차 위험)가 아닌 첫 곳이고, 모두 만차면 null이에요. */
fun recommendedIndex(lots: List<Lot>): Int? = lots.indexOfFirst { it.recommendable }.takeIf { it >= 0 }
