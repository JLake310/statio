package com.statio.app.ui

import android.view.View
import android.widget.TextView
import com.statio.app.R
import com.statio.app.core.MonthlyReport

/** 월간 리포트 화면(시안 05)을 채웁니다. 숫자는 기기 안에 쌓인 기록(ParkingLog)으로만 계산해요. */
class ReportRenderer(private val root: View) {

    private fun <T : View> find(id: Int): T = root.findViewById(id)

    fun render(r: MonthlyReport) {
        find<TextView>(R.id.report_sub).text = "${r.month.year}년 ${r.month.monthValue}월 · 주차 ${r.parks}번"
        find<TextView>(R.id.report_saved).text = saved(r.savedKrw)
        find<TextView>(R.id.report_warnings).text = "${r.warnings}번"
        find<TextView>(R.id.report_warnings_note).text = "그중 ${r.moves}번 주차장으로 옮김"
        find<TextView>(R.id.report_avoided).text = "${r.avoidedFull}번"
        find<TextView>(R.id.report_top_lot).text = r.topLot?.first ?: "–"
        find<TextView>(R.id.report_top_lot_note).text = r.topLot?.let { "${it.second}번" } ?: "주차장에 세운 기록 없음"
        find<WeeklyBarsView>(R.id.report_chart).setWeeks(r.weeks)
        find<TextView>(R.id.report_empty).visibility = if (r.isEmpty) View.VISIBLE else View.GONE
    }

    companion object {
        private fun saved(krw: Int): String = if (krw >= 1_000) Format.manWon(krw) else Format.won(krw)

        /** 대기 화면 카드에 넣을 한 줄 요약입니다. */
        fun summary(r: MonthlyReport): String =
            if (r.isEmpty) "아직 이번 달 기록이 없어요"
            else "경고 ${r.warnings}번 · 주차장으로 옮김 ${r.moves}번 · 절감 ${saved(r.savedKrw)}"
    }
}
