package com.statio.app.ui

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import com.statio.app.R
import com.statio.app.core.Lot
import com.statio.app.core.ParkingCheck
import com.statio.app.core.Risk
import com.statio.app.core.RiskKind
import com.statio.app.core.RiskLevel
import kotlin.math.roundToInt

/** P단 판단 화면(시안 02)을 채웁니다. 세로·가로 레이아웃이 같은 id를 씁니다. */
class CheckRenderer(private val context: Context, private val root: View) {

    private val inflater = LayoutInflater.from(context)
    private fun color(id: Int) = context.getColor(id)
    private fun <T : View> find(id: Int): T = root.findViewById(id)

    init {
        // 세로 분할 창에서 약도에 남는 높이가 적으면 빈 칸 대신 숨겨요.
        // 높이는 레이아웃이 끝날 때마다 다시 재요. 판단 화면이 처음 나타날 때는 render() 직후에 아직 높이가 0이에요.
        find<View>(R.id.map_card).addOnLayoutChangeListener { card, _, top, _, bottom, _, _, _, _ ->
            card.visibility = if (bottom - top < dp(MIN_MAP_DP)) View.INVISIBLE else View.VISIBLE
        }
    }

    /** 화면에 보여 준 위험 단계를 돌려줍니다. 내비가 주정차 금지 구간을 알려 줬으면 '주의'로 올립니다. */
    fun render(check: ParkingCheck, place: String, naviNoParkingZone: Boolean): RiskLevel {
        val level = if (check.level == RiskLevel.NONE && naviNoParkingZone) RiskLevel.CAUTION else check.level
        val best = check.bestAlternative  // 만차인 곳은 건너뛴 첫 대안

        val box = find<View>(R.id.verdict_box)
        val icon = find<TextView>(R.id.verdict_icon)
        val title = find<TextView>(R.id.verdict_title)
        when (level) {
            RiskLevel.HIGH -> {
                box.setBackgroundResource(R.drawable.bg_warn); icon.setBackgroundResource(R.drawable.bg_icon_bad); icon.text = "!"
                title.text = "여기는 단속 위험이 있어요"; title.setTextColor(color(R.color.negative))
            }
            RiskLevel.CAUTION -> {
                box.setBackgroundResource(R.drawable.bg_caution); icon.setBackgroundResource(R.drawable.bg_icon_caution); icon.text = "!"
                title.text = "시간대에 따라 신고 대상이에요"; title.setTextColor(color(R.color.caution))
            }
            RiskLevel.NONE -> {
                box.setBackgroundResource(R.drawable.bg_safe); icon.setBackgroundResource(R.drawable.bg_icon_ok); icon.text = "✓"
                title.text = "알려진 단속 위험이 없어요"; title.setTextColor(color(R.color.active_text))
            }
        }
        find<TextView>(R.id.verdict_place).text = "$place · 차를 세운 위치 기준"

        val riskList = find<LinearLayout>(R.id.risk_list)
        riskList.removeAllViews()
        for (risk in check.risks.take(3)) addRiskRow(riskList, risk.kind.label, risk.name, riskValue(risk), riskColor(risk))
        if (naviNoParkingZone) addRiskRow(riskList, "주정차 금지 구간", "내비 지도 기준", "해당", R.color.caution)
        if (riskList.childCount == 0) {
            addRiskRow(riskList, "반경 안 단속·신고 지점", "CCTV·단속 잦은 자리·소화전·횡단보도·정류소·교차로·어린이보호구역", "없음", R.color.active_text)
        }

        renderFine(check)
        val feeLot = check.alternatives.firstOrNull { it.per10MinKrw != null }
        find<TextView>(R.id.lot_fee_value).text = feeLot?.per10MinKrw?.let { Format.won(it) } ?: "–"
        find<TextView>(R.id.lot_fee_note).text = if (feeLot != null) "10분 기준" else "요금 정보 없음"

        val lotList = find<LinearLayout>(R.id.lot_list)
        lotList.removeAllViews()
        check.alternatives.take(context.resources.getInteger(R.integer.check_lot_count))
            .forEachIndexed { i, lot -> addLotCard(lotList, lot, picked = i == 0) }
        if (check.alternatives.isEmpty()) addRiskRow(lotList, "근처 700m 안", "좌표가 있는 주차장이 없어요", "", R.color.text_3)

        find<RiskMapView>(R.id.risk_map).show(check, best)
        val main = check.risks.firstOrNull()
        find<TextView>(R.id.legend_risk).apply {
            visibility = if (main != null) View.VISIBLE else View.GONE
            text = main?.let { "● ${it.kind.label} 범위" }.orEmpty()
        }
        find<TextView>(R.id.legend_lot).apply {
            visibility = if (best != null) View.VISIBLE else View.GONE
            text = best?.let { "P ${Format.shortName(it.row.name)} · 걸어서 ${Format.walkMinutes(it.distanceM)}분" }.orEmpty()
        }
        // 이미 주차장 안에 세웠으면 그 주차장으로 안내할 필요가 없어요.
        val atLot = check.alternatives.any { it.distanceM <= AT_LOT_M }
        find<View>(R.id.btn_route).visibility = if (best != null && !atLot) View.VISIBLE else View.GONE
        find<TextView>(R.id.btn_dismiss).text = if (level == RiskLevel.NONE) "확인" else "잠깐만 세울게요"
        return level
    }

    private fun renderFine(check: ParkingCheck) {
        val label = find<TextView>(R.id.fine_label)
        val value = find<TextView>(R.id.fine_value)
        val note = find<TextView>(R.id.fine_note)
        val inactiveFine = check.risks.filter { !it.activeNow }.maxOfOrNull { it.fineKrw } ?: 0
        when {
            check.fineKrw > 0 -> {
                label.text = "여기 세우면"; value.text = Format.won(check.fineKrw)
                value.setTextColor(color(R.color.negative)); note.text = "과태료 추정 (승용차)"
            }
            inactiveFine > 0 -> {
                label.text = "신고 시간대라면"; value.text = Format.won(inactiveFine)
                value.setTextColor(color(R.color.caution)); note.text = "평일 08~20시 과태료 추정"
            }
            else -> {
                label.text = "여기 세우면"; value.text = "0원"
                value.setTextColor(color(R.color.active)); note.text = "알려진 단속 위험 없음"
            }
        }
    }

    private fun addRiskRow(parent: LinearLayout, label: String, name: String?, value: String, colorRes: Int) {
        val row = inflater.inflate(R.layout.item_risk, parent, false)
        row.findViewById<View>(R.id.risk_dot).background.mutate().setTint(color(colorRes))
        row.findViewById<TextView>(R.id.risk_label).text = label
        row.findViewById<TextView>(R.id.risk_name).text = name.orEmpty()
        row.findViewById<TextView>(R.id.risk_value).apply { text = value; setTextColor(color(colorRes)) }
        parent.addView(row)
    }

    private fun addLotCard(parent: LinearLayout, lot: Lot, picked: Boolean) {
        val card = inflater.inflate(R.layout.item_lot, parent, false)
        card.setBackgroundResource(if (picked) R.drawable.bg_card_pick else R.drawable.bg_card)
        if (parent.childCount > 0) (card.layoutParams as LinearLayout.LayoutParams).topMargin = dp(20)
        card.findViewById<TextView>(R.id.lot_name).text = Format.shortName(lot.row.name)
        card.findViewById<TextView>(R.id.lot_sub).text =
            listOfNotNull(Format.distance(lot.distanceM), lot.row.category, "걸어서 ${Format.walkMinutes(lot.distanceM)}분").joinToString(" · ")
        bindFree(card, lot)
        card.findViewById<TextView>(R.id.lot_fee).text = Format.fee(lot.row)
        parent.addView(card)
    }

    private fun riskValue(risk: Risk): String =
        if (risk.kind == RiskKind.SCHOOL_ZONE && !risk.activeNow) "시간 외" else Format.distance(risk.distanceM)

    private fun riskColor(risk: Risk): Int = if (risk.activeNow) R.color.negative else R.color.caution

    private fun dp(v: Int): Int = (v * context.resources.displayMetrics.density).roundToInt()

    companion object {
        private const val AT_LOT_M = 50
        private const val MIN_MAP_DP = 140

        /** 주차장 카드의 빈자리 숫자·기준 시각·막대를 채웁니다. 도착 전 비교 화면도 같이 씁니다. */
        fun bindFree(card: View, lot: Lot) {
            val context = card.context
            val free = card.findViewById<TextView>(R.id.lot_free)
            val age = card.findViewById<TextView>(R.id.lot_age)
            val bar = card.findViewById<ProgressBar>(R.id.lot_bar)
            val live = lot.live
            if (live != null) {
                free.text = if (live.free > 0) "${Format.count(live.free)}면" else "만차"
                free.setTextColor(context.getColor(if (live.free > 0) R.color.active else R.color.negative))
                age.text = "${lot.ageMin ?: 0}분 전 기준"
                bar.visibility = View.VISIBLE
                bar.progress = if (live.total > 0) (live.free * 100.0 / live.total).roundToInt() else 0
            } else {
                free.text = "정보 없음"
                free.textSize = 32f
                free.setTextColor(context.getColor(R.color.text_4))
                age.text = "실시간 미제공"
                bar.visibility = View.GONE
            }
        }
    }
}
