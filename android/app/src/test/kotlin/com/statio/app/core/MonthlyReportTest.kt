package com.statio.app.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime
import java.time.YearMonth

class MonthlyReportTest {

    private val november = YearMonth.of(2026, 11)
    private fun at(day: Int, hour: Int = 12) = LocalDateTime.of(2026, 11, day, hour, 0)

    @Test fun emptyMonth() {
        val r = monthlyReport(emptyList(), november)
        assertTrue(r.isEmpty)
        assertEquals(0, r.savedKrw)
        assertNull(r.topLot)
        assertEquals(5, r.weeks.size) // 30일까지라 5주
    }

    @Test fun savingIsFineMinusHourlyFee() {
        // 단속 CCTV 4만 원 경고 → 세종로 공영주차장(1시간 5,160원)으로 옮김
        val events = listOf(
            LogEvent(LogKind.WARN, at(3), "광화문 세종대로", fineKrw = 40_000),
            LogEvent(LogKind.MOVE, at(3), "세종로 공영주차장", fineKrw = 40_000, feeKrw = 5_160),
            LogEvent(LogKind.PARK, at(3), "세종로 공영주차장", inLot = true),
        )
        val r = monthlyReport(events, november)
        assertEquals(34_840, r.savedKrw)
        assertEquals(1, r.warnings)
        assertEquals(1, r.moves)
        assertEquals(1, r.parks)
    }

    @Test fun savingNeverNegative() =
        assertEquals(0, LogEvent(LogKind.MOVE, at(1), "비싼 주차장", fineKrw = 40_000, feeKrw = 60_000).savedKrw)

    @Test fun otherMonthsAreIgnored() {
        val events = listOf(
            LogEvent(LogKind.WARN, LocalDateTime.of(2026, 10, 31, 23, 0), "길가", fineKrw = 40_000),
            LogEvent(LogKind.WARN, at(1, 0), "길가", fineKrw = 40_000),
        )
        assertEquals(1, monthlyReport(events, november).warnings)
    }

    @Test fun weeksSplitBySevenDays() {
        val events = listOf(7, 8, 8, 29).map { LogEvent(LogKind.WARN, at(it), "길가", fineKrw = 40_000) } +
            LogEvent(LogKind.MOVE, at(8), "주차장", fineKrw = 40_000, feeKrw = 1_000)
        val weeks = monthlyReport(events, november).weeks
        assertEquals(listOf(1, 2, 0, 0, 1), weeks.map { it.warnings })
        assertEquals(listOf(0, 1, 0, 0, 0), weeks.map { it.moves })
    }

    @Test fun topLotCountsOnlyLotsAndPrefersRecentOnTie() {
        val events = listOf(
            LogEvent(LogKind.PARK, at(2), "세종로 공영주차장", inLot = true),
            LogEvent(LogKind.PARK, at(3), "종묘주차장", inLot = true),
            LogEvent(LogKind.PARK, at(4), "세종대로", inLot = false),
            LogEvent(LogKind.PARK, at(5), "세종대로", inLot = false),
        )
        val r = monthlyReport(events, november)
        assertEquals("종묘주차장" to 1, r.topLot)
        assertEquals(4, r.parks)
    }
}
