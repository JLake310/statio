package com.statio.app.core

import java.time.LocalDateTime

/** 앱 내장 DB에서 후보를 꺼내는 쪽입니다. 앱은 Android SQLite로, 단위 테스트는 JDBC로 같은 DB 파일을 읽습니다. */
interface ParkingData {
    fun riskCandidates(range: CellRange): List<RiskPoint>
    fun lotCandidates(range: CellRange): List<LotRow>
    fun pattern(lotId: String, dayType: Int, slot: Int): PatternCell?
}

/** 실시간 빈자리를 주는 쪽입니다. 앱은 서울시 API를 부르고, 테스트는 빈 값을 씁니다. */
interface LiveSource {
    /** 주차장 코드별 최신 값입니다. 네트워크를 쓸 수 있어서 메인 스레드에서 부르면 안 됩니다. */
    fun latest(): Map<String, LiveCount>
    fun recent(lotId: String): List<LiveSample>
}

object NoLiveSource : LiveSource {
    override fun latest(): Map<String, LiveCount> = emptyMap()
    override fun recent(lotId: String): List<LiveSample> = emptyList()
}

/**
 * P단 판단과 주차장 안내를 한곳에서 처리합니다.
 *
 * [predictor]가 있으면 도착 시 빈자리를 빈자리 예측 모델로도 계산합니다. [aiOnScreen]이 꺼져 있으면 화면에는 예전 방식
 * (패턴표 → 추세 → 지금 값)을 쓰고, 모델 값은 [aiLog]로만 남깁니다(숨김 모드).
 */
class ParkingAdvisor(
    private val data: ParkingData,
    private val live: LiveSource,
    private val predictor: SpacePredictor? = null,
    private val aiOnScreen: Boolean = false,
    private val aiLog: ((String) -> Unit)? = null,
) {

    /** P단 순간의 판단: 위험 목록, 위험 단계, 대안 주차장을 한 번에 돌려줍니다. */
    fun check(
        lat: Double,
        lon: Double,
        at: LocalDateTime,
        marginM: Double = 5.0,
        radiusM: Double = 700.0,
        limit: Int = 5,
        now: LocalDateTime = at,
    ): ParkingCheck {
        val candidates = data.riskCandidates(cellRange(lat, lon, RiskKind.MAX_RADIUS_M + marginM))
        val risks = evaluateRisks(candidates, lat, lon, marginM, at)
        return ParkingCheck(
            lat = lat,
            lon = lon,
            checkedAt = at,
            level = riskLevel(risks),
            risks = risks,
            fineKrw = risks.filter { it.activeNow }.maxOfOrNull { it.fineKrw } ?: 0,
            alternatives = nearbyLots(lat, lon, now, radiusM = radiusM, limit = limit),
        )
    }

    /** 이 위치가 들어 있는 주차장입니다. 주차장 좌표는 대표 지점 하나라서 [radiusM] 안의 가장 가까운 곳을 고릅니다. */
    fun lotAt(lat: Double, lon: Double, radiusM: Double = 80.0): LotRow? =
        data.lotCandidates(cellRange(lat, lon, radiusM))
            .map { it to distanceM(lat, lon, it.lat, it.lon) }
            .filter { it.second <= radiusM }
            .minByOrNull { it.second }?.first

    /** 목적지 근처 주차장입니다. [etaMin]을 주면 도착 시점 예상 빈자리를 함께 계산합니다. */
    fun nearbyLots(
        lat: Double,
        lon: Double,
        now: LocalDateTime,
        etaMin: Double? = null,
        radiusM: Double = 700.0,
        limit: Int = 10,
        keepNearestFull: Boolean = false,
    ): List<Lot> {
        val candidates = data.lotCandidates(cellRange(lat, lon, radiusM))
        val predict = etaMin?.let { eta ->
            { row: LotRow, count: LiveCount ->
                val (dayType, slot) = patternSlot(now.plusSeconds((eta * 60).toLong()))
                val recent = live.recent(row.id)
                val base = predictFree(data.pattern(row.id, dayType, slot), recent, now, eta, count.free, count.total)
                val ai = predictor?.predict(row.id, now, eta, count, recent) { d, s -> data.pattern(row.id, d, s) }
                if (ai != null) {
                    aiLog?.invoke(
                        String.format(
                            java.util.Locale.US, "ai lot=%s eta=%.1f now=%d base=%d(%s) ai=%d p=%.3f risk=%.3f warn=%b",
                            row.id, eta, count.free, base.free, base.basis, ai.free, ai.probFree ?: Double.NaN, ai.fullRisk ?: Double.NaN, ai.fullWarning,
                        ),
                    )
                }
                if (aiOnScreen && ai != null) Prediction(ai.free, PredictionBasis.AI, 0, ai.probFree, ai.fullWarning) else base
            }
        }
        return rankLots(candidates, live.latest(), lat, lon, radiusM, now, limit, predict, keepNearestFull)
    }
}
