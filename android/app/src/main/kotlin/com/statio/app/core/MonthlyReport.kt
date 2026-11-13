package com.statio.app.core

import java.time.LocalDateTime
import java.time.YearMonth

/**
 * 월간 리포트(시안 05)에 쓰는 기록 한 건입니다. 기기 안에만 쌓아요.
 * - PARK: 운전석 문이 열려 주차 위치를 기록한 순간입니다. [place]는 주차장 이름이나 길 이름이고, 주차장이면 [inLot]이 true예요.
 * - WARN: P단 판단에서 지금 단속·신고 대상이라 경고한 순간입니다. [fineKrw]는 과태료 추정이에요.
 * - MOVE: 경고를 받고 Statio가 안내한 주차장에 실제로 세운 순간입니다. [fineKrw]는 피한 과태료, [feeKrw]는 그 주차장 1시간 요금이에요.
 * - AVOID_FULL: 도착 전 비교에서 목적지에 더 가까운 만차(만차 위험) 주차장 대신 다른 주차장으로 안내한 순간입니다.
 */
enum class LogKind { PARK, WARN, MOVE, AVOID_FULL }

data class LogEvent(
    val kind: LogKind,
    val at: LocalDateTime,
    val place: String,
    val fineKrw: Int = 0,
    val feeKrw: Int = 0,
    val inLot: Boolean = false,
) {
    /** 주차장으로 옮겨 아낀 돈입니다. 과태료에서 주차 요금(1시간 기준)을 빼요. */
    val savedKrw: Int get() = if (kind == LogKind.MOVE) maxOf(0, fineKrw - feeKrw) else 0
}

/** 주별 단속 위험 경고와 주차장으로 옮긴 횟수입니다. [week]는 1주(1~7일)부터 셉니다. */
data class WeekCount(val week: Int, val warnings: Int, val moves: Int)

data class MonthlyReport(
    val month: YearMonth,
    val parks: Int,
    val warnings: Int,
    val moves: Int,
    val avoidedFull: Int,
    val savedKrw: Int,
    /** 가장 자주 세운 주차장과 횟수입니다. 주차장에 세운 적이 없으면 null이에요. */
    val topLot: Pair<String, Int>?,
    val weeks: List<WeekCount>,
) {
    val isEmpty: Boolean get() = parks == 0 && warnings == 0 && avoidedFull == 0
}

/** 한 달의 1~7일을 1주, 8~14일을 2주…로 묶어요. 29일 이후는 5주예요. */
fun weekOfMonth(day: Int): Int = (day - 1) / 7 + 1

fun monthlyReport(events: List<LogEvent>, month: YearMonth): MonthlyReport {
    val inMonth = events.filter { YearMonth.from(it.at) == month }
    fun count(kind: LogKind) = inMonth.count { it.kind == kind }
    // 가장 자주 세운 주차장입니다. 횟수가 같으면 최근에 세운 곳이에요.
    val topLot = inMonth.filter { it.kind == LogKind.PARK && it.inLot }
        .groupBy { it.place }
        .maxWithOrNull(compareBy<Map.Entry<String, List<LogEvent>>> { it.value.size }.thenBy { e -> e.value.maxOf { it.at } })
        ?.let { it.key to it.value.size }
    val weeks = (1..weekOfMonth(month.lengthOfMonth())).map { w ->
        val inWeek = inMonth.filter { weekOfMonth(it.at.dayOfMonth) == w }
        WeekCount(w, inWeek.count { it.kind == LogKind.WARN }, inWeek.count { it.kind == LogKind.MOVE })
    }
    return MonthlyReport(
        month = month,
        parks = count(LogKind.PARK),
        warnings = count(LogKind.WARN),
        moves = count(LogKind.MOVE),
        avoidedFull = count(LogKind.AVOID_FULL),
        savedKrw = inMonth.sumOf { it.savedKrw },
        topLot = topLot,
        weeks = weeks,
    )
}
