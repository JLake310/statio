package com.statio.app.core

import org.json.JSONArray
import org.json.JSONObject
import java.time.Duration
import java.time.LocalDate
import java.time.LocalDateTime
import kotlin.math.abs
import kotlin.math.exp

/**
 * 차 안에서 돌리는 빈자리 예측 모델입니다. ml/export_model.py가 만든 assets/predictor.json(결정트리 앙상블 두 개)을 읽습니다.
 *
 * - 빈자리 변화: 지금부터 도착까지 점유율이 얼마나 바뀌는지 예측해 도착 시 빈자리(면)로 바꿉니다.
 * - 빈자리 확률: 도착했을 때 빈자리가 1면 이상 있을 확률입니다.
 * - 만차 위험: max(지금 만차면 1, 1 - 빈자리 확률)이 기준값(헛경보 2%에 맞춘 값)보다 크면 경고합니다.
 *   운전자에게는 자리가 없어 돌아가는 쪽이 더 큰 손해라서, 지금 만차인 곳은 늘 경고합니다.
 * - 입력은 ml/dataset.py의 학습 입력과 같은 규칙으로 앱이 가진 값(실시간 값, 최근 기록, 패턴표)에서 만듭니다.
 *   파이썬과 같은 값이 나오는지 SpacePredictorTest가 대조합니다.
 */
class SpacePredictor(json: String) {

    data class Result(val free: Int, val probFree: Double?, val delta: Double, val fullRisk: Double?, val fullWarning: Boolean)

    private class LotInfo(val idx: Int, val onstreet: Double)

    val madeAt: String
    val lotCount: Int get() = lots.size
    private val featureNames: List<String>
    private val hMin: Double
    private val hMax: Double
    private val toleranceSec: Long
    private val lots: Map<String, LotInfo>
    private val holidays: Set<LocalDate>
    private val regressor: TreeEnsemble
    private val classifier: TreeEnsemble?
    private val fullThreshold: Double?

    init {
        val root = JSONObject(json)
        madeAt = root.getString("made")
        featureNames = root.getJSONArray("features").let { a -> List(a.length()) { a.getString(it) } }
        root.getJSONArray("horizon_min").let { hMin = it.getDouble(0); hMax = it.getDouble(1) }
        toleranceSec = root.getLong("match_tolerance_sec")
        val lotsJson = root.getJSONObject("lots")
        lots = lotsJson.keys().asSequence().associateWith { id ->
            val o = lotsJson.getJSONObject(id)
            LotInfo(o.getInt("idx"), if (o.isNull("onstreet")) Double.NaN else if (o.getBoolean("onstreet")) 1.0 else 0.0)
        }
        holidays = root.getJSONArray("holidays").let { a -> (0 until a.length()).map { LocalDate.parse(a.getString(it)) }.toSet() }
        regressor = TreeEnsemble(root.getJSONObject("regressor"))
        classifier = if (root.isNull("classifier")) null else TreeEnsemble(root.getJSONObject("classifier"))
        fullThreshold = root.optJSONObject("full_risk")?.getDouble("threshold")
    }

    fun knows(lotId: String): Boolean = lotId in lots

    /** 도착 시 빈자리와 확률입니다. 모델이 모르는 주차장(값이 멈춘 곳·버스 전용 등)이면 null입니다. */
    fun predict(
        lotId: String,
        now: LocalDateTime,
        etaMin: Double,
        count: LiveCount,
        recent: List<LiveSample>,
        pattern: (dayType: Int, slot: Int) -> PatternCell?,
    ): Result? {
        val x = features(lotId, now, etaMin, count, recent, pattern) ?: return null
        val delta = regressor.raw(x)
        val occ = count.parked.coerceIn(0, count.total).toDouble() / count.total
        val free = Math.rint(count.total * (1 - (occ + delta).coerceIn(0.0, 1.0))).toInt()  // 파이썬(numpy)과 같은 반올림
        val prob = classifier?.let { 1.0 / (1.0 + exp(-it.raw(x))) }
        val risk = prob?.let { maxOf(if (count.free == 0) 1.0 else 0.0, 1 - it) }
        val warning = risk != null && fullThreshold != null && risk > fullThreshold
        return Result(free, prob, delta, risk, warning)
    }

    /** 모델 입력입니다. 순서는 predictor.json의 features를 따르고, 없는 값은 NaN입니다. */
    fun features(
        lotId: String,
        now: LocalDateTime,
        etaMin: Double,
        count: LiveCount,
        recent: List<LiveSample>,
        pattern: (dayType: Int, slot: Int) -> PatternCell?,
    ): DoubleArray? {
        val lot = lots[lotId] ?: return null
        val total = count.total
        if (total <= 0) return null
        val h = etaMin.coerceIn(hMin, hMax)
        val target = now.plusSeconds((h * 60).toLong())
        val occ = count.parked.coerceIn(0, total).toDouble() / total

        // 실시간 기록에서 [minutes]분 전에 가장 가까운 값(앞뒤 2분 30초 안)의 점유율입니다. 지금 총 주차면으로 계산합니다.
        fun past(minutes: Long): Double {
            val ref = count.updatedAt.minusMinutes(minutes)
            var best: LiveSample? = null
            var bestSec = Long.MAX_VALUE
            for (s in recent) {
                val d = abs(Duration.between(ref, s.at).seconds)
                if (d <= toleranceSec && d < bestSec) { best = s; bestSec = d }
            }
            return best?.let { (1.0 - it.free.toDouble() / total).coerceIn(0.0, 1.0) } ?: Double.NaN
        }

        val (dayTypeTarget, slotTarget) = patternSlot(target)
        val (dayTypeNow, slotNow) = patternSlot(now)
        val cellTarget = pattern(dayTypeTarget, slotTarget)
        val cellNow = pattern(dayTypeNow, slotNow)
        val patTarget = cellTarget?.let { 1 - it.free.toDouble() / total } ?: Double.NaN
        val patNow = cellNow?.let { 1 - it.free.toDouble() / total } ?: Double.NaN
        val dow = target.dayOfWeek.value - 1
        val values = mapOf(
            "lot_idx" to lot.idx.toDouble(),
            "total" to total.toDouble(),
            "onstreet" to lot.onstreet,
            "dow" to dow.toDouble(),
            "weekend" to if (dow >= 5) 1.0 else 0.0,
            "holiday" to if (target.toLocalDate() in holidays) 1.0 else 0.0,
            "tod" to (now.hour * 60 + now.minute).toDouble(),
            "tod_target" to (target.hour * 60 + target.minute).toDouble(),
            "h" to h,
            "occ" to occ,
            "d15" to occ - past(15),
            "d30" to occ - past(30),
            "pat_now" to patNow,
            "pat_target" to patTarget,
            "pat_delta" to patTarget - patNow,
            "pat_days" to (cellTarget?.days ?: 0).toDouble(),
        )
        return DoubleArray(featureNames.size) { values.getValue(featureNames[it]) }
    }
}

/** scikit-learn HistGradientBoosting과 같은 규칙으로 나무들을 따라 내려가 잎 값을 더합니다. */
private class TreeEnsemble(json: JSONObject) {
    private val baseline = json.getDouble("baseline")
    private val known: Map<Int, Set<Int>> = json.getJSONObject("known_categories").let { o ->
        o.keys().asSequence().associate { it.toInt() to o.getJSONArray(it).toIntList().toSet() }
    }
    private val trees: List<Tree> = json.getJSONArray("trees").let { a -> List(a.length()) { Tree(a.getJSONObject(it)) } }

    fun raw(x: DoubleArray): Double {
        var sum = baseline
        for (tree in trees) sum += tree.leaf(x, known)
        return sum
    }
}

private class Tree(json: JSONObject) {
    private val feature = json.getJSONArray("f").toIntList().toIntArray()   // 잎이면 -1
    private val x = json.getJSONArray("x").let { a -> DoubleArray(a.length()) { a.getDouble(it) } }  // 기준값, 잎이면 잎 값
    private val left = json.getJSONArray("l").toIntList().toIntArray()
    private val right = json.getJSONArray("r").toIntList().toIntArray()
    private val missingLeft = json.getJSONArray("m").toIntList().map { it == 1 }.toBooleanArray()
    private val catLeft: Map<Int, Set<Int>> = json.getJSONArray("cat_nodes").toIntList()
        .zip(json.getJSONArray("cat_left").let { a -> List(a.length()) { a.getJSONArray(it).toIntList().toSet() } }).toMap()

    fun leaf(input: DoubleArray, known: Map<Int, Set<Int>>): Double {
        var i = 0
        while (true) {
            val f = feature[i]
            if (f < 0) return x[i]
            val v = input[f]
            val missing = if (missingLeft[i]) left[i] else right[i]
            val cats = catLeft[i]
            i = when {
                v.isNaN() -> missing
                cats != null -> when {
                    v < 0 -> missing
                    v.toInt() in cats -> left[i]
                    v.toInt() in known[f].orEmpty() -> right[i]
                    else -> missing  // 학습에 없던 범주는 값이 없는 것으로 봐요
                }
                v <= x[i] -> left[i]
                else -> right[i]
            }
        }
    }
}

private fun JSONArray.toIntList(): List<Int> = List(length()) { getInt(it) }
