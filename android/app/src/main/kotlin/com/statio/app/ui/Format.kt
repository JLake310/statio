package com.statio.app.ui

import com.statio.app.core.Lot
import com.statio.app.core.LotRow
import com.statio.app.core.feeFor
import java.text.NumberFormat
import java.util.Locale
import kotlin.math.ceil

/** 화면에 쓰는 숫자·거리·시간 표기입니다. */
object Format {
    private val number = NumberFormat.getInstance(Locale.KOREA)

    fun won(krw: Int): String = "${number.format(krw)}원"

    fun count(n: Int): String = number.format(n)

    fun distance(m: Int): String = if (m >= 1000) "%.1fkm".format(m / 1000.0) else "${m}m"

    /** 걷는 속도 분당 67m(시속 4km) 기준입니다. */
    fun walkMinutes(m: Int): Int = maxOf(1, ceil(m / 67.0).toInt())

    /** 125 → '2시간 5분', 60 → '1시간', 45 → '45분' */
    fun duration(minutes: Int): String {
        val h = minutes / 60
        val m = minutes % 60
        return listOfNotNull(h.takeIf { it > 0 }?.let { "${it}시간" }, m.takeIf { it > 0 || h == 0 }?.let { "${it}분" }).joinToString(" ")
    }

    /** 10,000 → '1만 원', 25,000 → '2만 5천 원' */
    fun manWon(krw: Int): String {
        val man = krw / 10_000
        val cheon = krw % 10_000 / 1_000
        return listOfNotNull(man.takeIf { it > 0 }?.let { "${it}만" }, cheon.takeIf { it > 0 }?.let { "${it}천" }).joinToString(" ") + " 원"
    }

    /** '세종로 공영주차장(시)'처럼 뒤에 붙은 관리 주체 표시를 뺍니다. */
    fun shortName(name: String): String = name.replace(Regex("\\((시|구)\\)$"), "").trim()

    /** '5분 430원 · 1시간 5,160원' */
    fun fee(row: LotRow): String {
        if (row.basicFee == null || row.basicMin == null) return "요금 정보 없음"
        if (row.basicFee == 0) return "무료"
        val hour = feeFor(60, row)?.let { " · 1시간 ${won(it)}" }.orEmpty()
        return "${row.basicMin}분 ${won(row.basicFee)}$hour"
    }

    fun hourFee(lot: Lot): String = lot.perHourKrw?.let { "1시간 약 ${won(it)}" } ?: "요금 정보 없음"
}
