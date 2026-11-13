package com.statio.app.ui

import android.content.Context
import android.view.LayoutInflater
import android.view.View
import android.widget.LinearLayout
import android.widget.TextView
import com.statio.app.R
import com.statio.app.core.Lot
import com.statio.app.core.PredictionBasis
import com.statio.app.vehicle.CarSignals
import java.time.DayOfWeek
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import kotlin.math.max
import kotlin.math.roundToInt

/** 목적지 근처 주차장 비교입니다. [selected]는 안내 버튼이 가리키는 주차장입니다. */
data class Arrival(
    val destination: CarSignals.Destination,
    val lots: List<Lot>,
    val computedAt: LocalDateTime,
    val selected: Int = 0,
) {
    val selectedLot: Lot? get() = lots.getOrNull(selected)
}

/** 도착 전 주차장 비교 화면(시안 01)을 채웁니다. */
class ArrivalRenderer(private val context: Context, private val root: View, private val onSelect: (Int) -> Unit) {

    private val inflater = LayoutInflater.from(context)
    private fun color(id: Int) = context.getColor(id)
    private fun <T : View> find(id: Int): T = root.findViewById(id)

    fun render(arrival: Arrival) {
        val dest = arrival.destination
        val remainMin = (dest.remainSec / 60.0).roundToInt()
        find<TextView>(R.id.arrival_title).text = "${dest.name} 근처 주차장"
        val ai = arrival.lots.any { it.predicted?.basis == PredictionBasis.AI }
        val allFull = arrival.lots.isNotEmpty() && arrival.lots.none { it.recommendable }
        find<TextView>(R.id.arrival_sub).text = listOfNotNull(
            remainMin.takeIf { dest.remainSec > 0 }?.let { "도착 ${it}분 전" },
            when {
                allFull -> "근처 주차장이 모두 만차이거나 만차 위험이에요"
                ai -> "AI가 도착 때 빈자리를 예측했어요"
                else -> "빈자리 있는 곳부터 가까운 순서예요"
            },
        ).joinToString(" · ")
        find<TextView>(R.id.arrival_notice).setText(if (ai) R.string.arrival_notice_ai else R.string.arrival_notice)

        // 가로 전체 창에만 있는 목적지 카드
        root.findViewById<TextView>(R.id.arrival_dest_name)?.text = dest.name
        root.findViewById<TextView>(R.id.arrival_dest_info)?.text = listOfNotNull(
            dest.address?.takeIf { it.isNotBlank() },
            if (dest.remainSec > 0) "남은 ${Format.distance(dest.remainM)} · ${arrival.computedAt.plusSeconds(dest.remainSec.toLong()).format(TIME)} 도착 예정" else null,
        ).joinToString("\n")

        val list = find<LinearLayout>(R.id.arrival_list)
        list.removeAllViews()
        arrival.lots.forEachIndexed { i, lot -> addCard(list, lot, i, picked = i == arrival.selected, at = arrival.computedAt) }

        val route = find<TextView>(R.id.btn_arrival_route)
        val lot = arrival.selectedLot
        route.visibility = if (lot != null) View.VISIBLE else View.GONE
        route.text = lot?.let { "${Format.shortName(it.row.name)}으로 안내" }.orEmpty()
    }

    private fun addCard(parent: LinearLayout, lot: Lot, index: Int, picked: Boolean, at: LocalDateTime) {
        val card = inflater.inflate(R.layout.item_arrival_lot, parent, false)
        card.setBackgroundResource(if (picked) R.drawable.bg_card_pick else R.drawable.bg_card)
        if (parent.childCount > 0) (card.layoutParams as LinearLayout.LayoutParams).topMargin = (20 * context.resources.displayMetrics.density).roundToInt()
        card.setOnClickListener { onSelect(index) }
        card.findViewById<TextView>(R.id.lot_name).text = Format.shortName(lot.row.name)
        card.findViewById<TextView>(R.id.lot_sub).text = listOfNotNull(Format.distance(lot.distanceM), lot.row.category).joinToString(" · ")
        CheckRenderer.bindFree(card, lot)
        val predict = card.findViewById<TextView>(R.id.lot_predict)
        val (predictLabel, predictColor) = predictText(lot, at)
        predict.text = predictLabel
        predict.setTextColor(color(predictColor))
        card.findViewById<TextView>(R.id.lot_fee).apply { text = Format.hourFee(lot); setTextColor(color(R.color.text_2)) }
        parent.addView(card)
    }

    /**
     * '도착 시 예상 넉넉 (지금 기준)'처럼 예상 빈자리와 근거를 짧게 씁니다. 실시간이 없으면 운영 시간을 보여 줍니다.
     * AI 예측이면 'AI 예측 · 도착 때 약 742면 · 빈자리 확률 99%'처럼 AI를 앞에 둡니다.
     * 만차 위험이면 빈자리 수 대신 'AI 예측 · 도착 때 만차 위험'을 빨간색으로 보여 줍니다.
     */
    private fun predictText(lot: Lot, at: LocalDateTime): Pair<String, Int> {
        val p = lot.predicted ?: return (hoursText(lot, at) ?: "실시간 정보 없음") to R.color.text_3
        val total = lot.live?.total ?: 0
        if (p.basis == PredictionBasis.AI) {
            if (p.fullWarning) {
                val now = if (lot.live?.free == 0) "지금 만차 · " else ""
                return "AI 예측 · ${now}도착 때 만차 위험" to R.color.negative
            }
            val count = if (p.free == 0) "만차" else "약 ${Format.count(p.free)}면"
            val prob = p.probFree?.let { " · 빈자리 확률 ${(it * 100).roundToInt().coerceIn(1, 99)}%" }.orEmpty()  // 0%·100%는 단정처럼 보여 1~99%로 보여요
            val color = when {
                p.free == 0 -> R.color.negative
                (p.probFree ?: 1.0) < 0.8 || p.free <= 5 -> R.color.caution
                else -> R.color.active_text
            }
            return "AI 예측 · 도착 때 $count$prob" to color
        }
        val basis = when (p.basis) {
            PredictionBasis.PATTERN -> "지난 ${p.days}일 같은 시간대"
            PredictionBasis.TREND -> "최근 30분 추세"
            PredictionBasis.CURRENT -> "지금 기준"
            PredictionBasis.AI -> "AI 예측"
        }
        return when {
            p.free == 0 -> "도착 시 예상 만차 ($basis)" to R.color.negative
            p.free >= max(30, (total * 0.3).roundToInt()) -> "도착 시 예상 넉넉 ($basis)" to R.color.active_text
            p.free <= 5 -> "도착 시 예상 ${p.free}면 ($basis)" to R.color.caution
            else -> "도착 시 예상 약 ${Format.count(p.free)}면 ($basis)" to R.color.text_2
        }
    }

    /** '0800-2200' → '운영 08:00-22:00', '0000-2400' → '24시간 운영' */
    private fun hoursText(lot: Lot, at: LocalDateTime): String? {
        val weekend = at.dayOfWeek == DayOfWeek.SATURDAY || at.dayOfWeek == DayOfWeek.SUNDAY
        val raw = (if (weekend) lot.row.weekendHours else lot.row.weekdayHours)?.trim() ?: return null
        val m = Regex("^(\\d{2})(\\d{2})-(\\d{2})(\\d{2})$").find(raw) ?: return null
        val (h1, m1, h2, m2) = m.destructured
        return if (h1 == "00" && m1 == "00" && h2 == "24") "24시간 운영" else "운영 $h1:$m1-$h2:$m2"
    }

    companion object {
        private val TIME = DateTimeFormatter.ofPattern("HH:mm")
    }
}
