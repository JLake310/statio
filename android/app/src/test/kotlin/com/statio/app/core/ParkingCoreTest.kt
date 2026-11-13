package com.statio.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalDateTime

/** server/test_parking_core.py와 같은 사례입니다. 예측은 앱이 쓰는 패턴표 방식에 맞췄습니다. */
class ParkingCoreTest {

    private val now = LocalDateTime.of(2026, 10, 22, 14, 0) // 목요일 14:00

    @Test fun feeBasicAndAdditionalUnits() {
        // 5분 430원, 추가 5분 430원 → 1시간 5,160원, 10분 860원
        assertEquals(5160, feeFor(60, 430, 5, 430, 5))
        assertEquals(860, feeFor(10, 430, 5, 430, 5))
    }

    @Test fun feeWithinBasicTime() = assertEquals(1000, feeFor(20, 1000, 30, 500, 10))

    @Test fun dayMaxCapsFee() = assertEquals(20000, feeFor(600, 430, 5, 430, 5, dayMax = 20000))

    @Test fun missingFeeInfo() {
        assertNull(feeFor(60, null, 5, 430, 5))
        assertNull(feeFor(60, 430, 0, 430, 5))
    }

    private fun lot(basicFee: Int?, basicMin: Int?, addFee: Int?, addMin: Int?, dayMax: Int? = null) =
        LotRow("1", "주차장", null, null, null, 100, 37.5, 127.0, basicFee, basicMin, addFee, addMin, dayMax, null, null, null, true)

    @Test fun minutesUntilFeeExceedsThreshold() {
        // 5분 430원 → 115분까지 9,890원, 116분에 10,320원
        assertEquals(116, minutesUntilFeeExceeds(10_000, lot(430, 5, 430, 5)))
        assertNull(minutesUntilFeeExceeds(10_000, lot(430, 5, 430, 5, dayMax = 8_000)))
        assertNull(minutesUntilFeeExceeds(10_000, lot(null, null, null, null)))
    }

    @Test fun nextFeeStepSkipsStepsBelowHourlyFee() {
        assertEquals(10_000 to 116, nextFeeStep(lot(430, 5, 430, 5)))        // 1시간 5,160원
        assertEquals(20_000 to 101, nextFeeStep(lot(1000, 5, 1000, 5)))      // 1시간 12,000원 → 2만 원은 101분
        assertNull(nextFeeStep(lot(0, 60, 0, 60)))                           // 무료
    }

    @Test fun patternWhenEnoughDays() =
        assertEquals(Prediction(12, PredictionBasis.PATTERN, 3), predictFree(PatternCell(12, 3), emptyList(), now, 15.0, 50, 100))

    @Test fun patternIgnoredWhenTooFewDays() =
        assertNotEquals(PredictionBasis.PATTERN, predictFree(PatternCell(12, 2), emptyList(), now, 15.0, 50, 100).basis)

    @Test fun trendWhenPatternMissing() {
        // 최근 20분 동안 빈자리 40 → 30 (분당 -0.5) → 10분 뒤 25
        val recent = listOf(LiveSample(now.minusMinutes(20), 40), LiveSample(now.minusMinutes(10), 35), LiveSample(now, 30))
        assertEquals(Prediction(25, PredictionBasis.TREND, 0), predictFree(null, recent, now, 10.0, 30, 100))
    }

    @Test fun predictionClampedToCapacity() {
        val recent = listOf(LiveSample(now.minusMinutes(20), 90), LiveSample(now.minusMinutes(10), 50), LiveSample(now, 10))
        assertEquals(0, predictFree(null, recent, now, 60.0, 10, 100).free)
    }

    @Test fun currentWhenNoHistory() =
        assertEquals(Prediction(7, PredictionBasis.CURRENT, 0), predictFree(null, emptyList(), now, 15.0, 7, 20))

    @Test fun patternSlotRoundsToNearestQuarter() {
        assertEquals(0 to 56, patternSlot(now.withMinute(7)))     // 14:07 → 14:00 칸
        assertEquals(0 to 57, patternSlot(now.withMinute(8)))     // 14:08 → 14:15 칸
        assertEquals(1 to 56, patternSlot(now.plusDays(2)))       // 토요일
        assertEquals(0 to 0, patternSlot(now.withHour(23).withMinute(55))) // 자정 칸으로 넘어감
    }

    @Test fun riskLevels() {
        fun risk(active: Boolean) = Risk(RiskKind.SCHOOL_ZONE, null, 10, 0.0, 0.0, active)
        assertEquals(RiskLevel.NONE, riskLevel(emptyList()))
        assertEquals(RiskLevel.CAUTION, riskLevel(listOf(risk(false))))
        assertEquals(RiskLevel.HIGH, riskLevel(listOf(risk(false), risk(true))))
    }

    @Test fun schoolZoneOnlyActiveOnWeekdayDaytime() {
        val point = listOf(RiskPoint(RiskKind.SCHOOL_ZONE, 37.5, 127.0, "초등학교"))
        assertEquals(true, evaluateRisks(point, 37.5, 127.0, 5.0, now).single().activeNow)
        assertEquals(false, evaluateRisks(point, 37.5, 127.0, 5.0, now.withHour(21)).single().activeNow)
        assertEquals(false, evaluateRisks(point, 37.5, 127.0, 5.0, now.plusDays(2)).single().activeNow)
    }

    @Test fun schoolZoneCoversOnlyRoadsNearTheFacility() {
        // 어린이보호구역은 시설 반경 100m(여유 5m 포함 105m)까지만 봐요. 예전 300m는 서울 어디서나 걸렸어요.
        val point = listOf(RiskPoint(RiskKind.SCHOOL_ZONE, 37.5, 127.0, "초등학교"))
        assertEquals(1, evaluateRisks(point, 37.5 + 100 / 111_195.0, 127.0, 5.0, now).size)
        assertEquals(0, evaluateRisks(point, 37.5 + 150 / 111_195.0, 127.0, 5.0, now).size)
    }

    @Test fun intersectionUsesItsOwnRadius() {
        // 큰 교차로(반경 30m)는 33m에서도 걸리고(여유 5m 포함), 작은 교차로(반경 10m)는 16m에서 걸리지 않아요.
        val big = RiskPoint(RiskKind.INTERSECTION, 37.5, 127.0, "큰 교차로", radiusM = 30.0)
        val small = RiskPoint(RiskKind.INTERSECTION, 37.5 + 80 / 111_195.0, 127.0, "작은 교차로", radiusM = 10.0)
        val found = evaluateRisks(listOf(big, small), 37.5 + 33 / 111_195.0, 127.0, 5.0, now).single()
        assertEquals("큰 교차로", found.name)
        assertEquals(30.0, found.radiusM, 0.0)
        assertEquals(40_000, found.fineKrw)
        assertEquals(emptyList<Risk>(), evaluateRisks(listOf(big, small), 37.5 + 36 / 111_195.0, 127.0, 5.0, now))
        assertEquals(emptyList<Risk>(), evaluateRisks(listOf(big, small), 37.5 + 64 / 111_195.0, 127.0, 5.0, now))
    }

    @Test fun intersectionComesAfterBusStopBeforeSchoolZone() {
        val points = listOf(RiskKind.SCHOOL_ZONE, RiskKind.INTERSECTION, RiskKind.BUS_STOP).map { RiskPoint(it, 37.5, 127.0, it.label) }
        assertEquals(listOf(RiskKind.BUS_STOP, RiskKind.INTERSECTION, RiskKind.SCHOOL_ZONE), evaluateRisks(points, 37.5, 127.0, 5.0, now).map { it.kind })
        assertEquals("intersection", RiskKind.INTERSECTION.code)
        assertEquals(6, RiskKind.INTERSECTION.ordinal)  // 앱 DB 종류 번호 = server/parking_core.py SEVERITY
    }

    @Test fun hotspotComesRightAfterCamerasBeforeLegalZones() {
        val points = listOf(RiskKind.CROSSWALK, RiskKind.HOTSPOT, RiskKind.INTERSECTION, RiskKind.CCTV).map { RiskPoint(it, 37.5, 127.0, it.label) }
        assertEquals(listOf(RiskKind.CCTV, RiskKind.HOTSPOT, RiskKind.CROSSWALK, RiskKind.INTERSECTION), evaluateRisks(points, 37.5, 127.0, 5.0, now).map { it.kind })
        assertEquals("hotspot", RiskKind.HOTSPOT.code)
        assertEquals(2, RiskKind.HOTSPOT.ordinal)  // 앱 DB 종류 번호 = server/parking_core.py SEVERITY
    }

    @Test fun shortDistance() = assertEquals(111.2, distanceM(37.5, 127.0, 37.501, 127.0), 0.5) // 위도 0.001도 ≈ 111m

    // ── 추천 순서와 안내 대상 ─────────────────────────────

    /** 목적지(37.5, 127.0)에서 북쪽으로 [meters]만큼 떨어진 주차장입니다. */
    private fun rowAt(id: String, meters: Double) =
        LotRow(id, "주차장$id", null, "공영", null, 100, 37.5 + meters / 111_195.0, 127.0, 1000, 60, 500, 30, null, null, null, null, true)

    private fun live(free: Int) = LiveCount(100, 100 - free, now.minusMinutes(1))

    @Test fun frozenLotIgnoresLiveCount() {
        // 값이 멈춘 주차장은 앱 DB에서 실시간 연계가 꺼져 있어요. 늘 만차로 멈춘 값이 와도 '실시간 정보 없음'으로 다뤄요.
        val frozen = rowAt("frozen", 50.0).copy(realtimeLinked = false)
        val lot = rankLots(listOf(frozen), mapOf("frozen" to live(0)), 37.5, 127.0, 700.0, now, 10).single()
        assertEquals(null, lot.live)
        assertEquals(1, lot.rankTier)
    }

    @Test fun rankFreeThenUnknownThenFull() {
        val rows = listOf(rowAt("full", 50.0), rowAt("unknown", 100.0), rowAt("free", 300.0))
        val lots = rankLots(rows, mapOf("full" to live(0), "free" to live(5)), 37.5, 127.0, 700.0, now, 10)
        assertEquals(listOf("free", "unknown", "full"), lots.map { it.row.id })
        assertEquals(listOf(0, 1, 2), lots.map { it.rankTier })
    }

    @Test fun aiFullWarningGoesAfterUnknown() {
        val rows = listOf(rowAt("warned", 50.0), rowAt("unknown", 100.0), rowAt("safe", 300.0))
        val predict = { row: LotRow, count: LiveCount ->
            Prediction(count.free, PredictionBasis.AI, 0, probFree = 0.9, fullWarning = row.id == "warned")
        }
        val lots = rankLots(rows, mapOf("warned" to live(10), "safe" to live(10)), 37.5, 127.0, 700.0, now, 10, predict)
        assertEquals(listOf("safe", "unknown", "warned"), lots.map { it.row.id })
        assertEquals(false, lots.last().recommendable)
    }

    @Test fun routeSkipsFullLots() {
        val rows = listOf(rowAt("full", 50.0), rowAt("unknown", 100.0))
        val lots = rankLots(rows, mapOf("full" to live(0)), 37.5, 127.0, 700.0, now, 10)
        assertEquals(0, recommendedIndex(lots))  // 정보 없는 곳이 만차인 곳보다 먼저예요
        assertEquals("unknown", lots[recommendedIndex(lots)!!].row.id)
    }

    @Test fun nearestFullLotKeptAtBottom() {
        val rows = listOf(rowAt("full", 30.0), rowAt("u60", 60.0), rowAt("u80", 80.0), rowAt("u100", 100.0))
        val live = mapOf("full" to live(0))
        assertEquals(listOf("u60", "u80", "u100"), rankLots(rows, live, 37.5, 127.0, 700.0, now, 3).map { it.row.id })
        val kept = rankLots(rows, live, 37.5, 127.0, 700.0, now, 3, keepNearestFull = true)
        assertEquals(listOf("u60", "u80", "full"), kept.map { it.row.id })  // 개수는 그대로, 마지막 칸만 바꿔요
        assertEquals(0, recommendedIndex(kept))                             // 안내 버튼은 여전히 만차가 아닌 첫 곳
    }

    @Test fun nearestAiWarnedLotKeptAtBottom() {
        val rows = listOf(rowAt("warned", 30.0), rowAt("a", 60.0), rowAt("b", 80.0), rowAt("c", 100.0))
        val predict = { row: LotRow, count: LiveCount ->
            Prediction(count.free, PredictionBasis.AI, 0, probFree = 0.9, fullWarning = row.id == "warned")
        }
        val live = rows.associate { it.id to live(10) }
        val kept = rankLots(rows, live, 37.5, 127.0, 700.0, now, 3, predict, keepNearestFull = true)
        assertEquals(listOf("a", "b", "warned"), kept.map { it.row.id })
    }

    @Test fun nearestLotNotFullOrAlreadyShownStaysAsIs() {
        val rows = listOf(rowAt("u30", 30.0), rowAt("full", 60.0), rowAt("u80", 80.0), rowAt("u100", 100.0))
        val live = mapOf("full" to live(0))
        // 가장 가까운 곳이 정보 없음이면 바꾸지 않아요
        assertEquals(listOf("u30", "u80", "u100"), rankLots(rows, live, 37.5, 127.0, 700.0, now, 3, keepNearestFull = true).map { it.row.id })
        // 가장 가까운 만차 주차장이 이미 목록에 있으면 그대로예요
        val few = listOf(rowAt("full", 30.0), rowAt("free", 200.0))
        assertEquals(listOf("free", "full"), rankLots(few, mapOf("full" to live(0), "free" to live(3)), 37.5, 127.0, 700.0, now, 3, keepNearestFull = true).map { it.row.id })
    }

    @Test fun noRouteWhenAllFull() {
        val rows = listOf(rowAt("a", 50.0), rowAt("b", 100.0))
        val lots = rankLots(rows, mapOf("a" to live(0), "b" to live(0)), 37.5, 127.0, 700.0, now, 10)
        assertNull(recommendedIndex(lots))
        val check = ParkingCheck(37.5, 127.0, now, RiskLevel.NONE, emptyList(), 0, lots)
        assertNull(check.bestAlternative)
    }
}
