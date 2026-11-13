package com.statio.app.ui

import android.content.Context
import android.view.Gravity
import android.view.View
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.statio.app.ParkingRecord
import com.statio.app.PhoneCard
import com.statio.app.R
import com.statio.app.core.nextFeeStep
import kotlin.math.roundToInt

/** 하차 순간의 위치 기록 화면(시안 03)을 채웁니다. 층을 누르면 [onFloor]로 알립니다. 모름이면 null입니다. */
class ParkedRenderer(private val context: Context, private val root: View, private val onFloor: (String?) -> Unit) {

    private fun <T : View> find(id: Int): T = root.findViewById(id)
    private fun dp(v: Int): Int = (v * context.resources.displayMetrics.density).roundToInt()

    fun render(record: ParkingRecord) {
        val lot = record.lot
        val name = lot?.let { Format.shortName(it.name) } ?: record.place
        find<TextView>(R.id.parked_title).text = "${name}에 세웠어요"
        find<TextView>(R.id.parked_sub).text = when {
            lot == null -> "길가 주차예요. 휴대폰으로 위치를 받아 두세요"
            record.floor == null -> "몇 층이에요? 한 번만 눌러 주세요"
            else -> "${record.floor}에 세웠어요. 다른 층이면 다시 눌러 주세요"
        }

        val grid = find<LinearLayout>(R.id.floor_grid)
        grid.visibility = if (lot != null) View.VISIBLE else View.GONE
        if (lot != null) renderFloors(grid, record.floor)

        // 축소·확대 없이 화면 픽셀과 1:1로 그려야 모듈 경계가 또렷해서 휴대폰이 잘 읽어요.
        val qr = find<ImageView>(R.id.qr_image)
        val size = (qr.layoutParams.width.takeIf { it > 0 } ?: dp(170)) - qr.paddingLeft - qr.paddingRight
        qr.scaleType = ImageView.ScaleType.CENTER
        qr.setImageBitmap(QrCode.bitmap(PhoneCard.url(record), size))
        find<TextView>(R.id.qr_desc).text = "카메라로 찍으면 카카오맵에\n주차 위치가 층과 함께 표시돼요"

        val step = lot?.let { nextFeeStep(it) }
        val feeCard = find<View>(R.id.fee_card)
        feeCard.visibility = if (step != null) View.VISIBLE else View.GONE
        if (step != null) {
            val (krw, minutes) = step
            val dayMax = lot.dayMax?.takeIf { it > 0 }?.let { " 하루 최대 ${Format.won(it)}이에요." }.orEmpty()
            find<TextView>(R.id.fee_step).text = "${Format.duration(minutes - 1)}을 넘으면 ${Format.manWon(krw)}을 넘어요.$dayMax"
        }
        find<View>(R.id.fee_summary_row).visibility = if (lot != null) View.VISIBLE else View.GONE
        find<TextView>(R.id.fee_summary).text = lot?.let { Format.fee(it) }.orEmpty()
    }

    private fun renderFloors(grid: LinearLayout, selected: String?) {
        grid.removeAllViews()
        FLOORS.chunked(3).forEachIndexed { r, row ->
            val line = LinearLayout(context).apply { orientation = LinearLayout.HORIZONTAL }
            row.forEachIndexed { c, label ->
                val floor = label.takeIf { it != UNKNOWN }
                val picked = floor != null && floor == selected
                val button = TextView(context, null, 0, R.style.Text).apply {
                    text = label
                    gravity = Gravity.CENTER
                    textSize = if (floor == null) 30f else 48f
                    setTypeface(typeface, android.graphics.Typeface.BOLD)
                    setTextColor(context.getColor(if (picked) R.color.basic_00 else R.color.text_1))
                    setBackgroundResource(if (picked) R.drawable.bg_button_filled else R.drawable.bg_button_basic)
                    setOnClickListener { onFloor(floor) }
                }
                line.addView(button, LinearLayout.LayoutParams(0, dp(FLOOR_HEIGHT_DP), 1f).apply { if (c > 0) marginStart = dp(16) })
            }
            grid.addView(line, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { if (r > 0) topMargin = dp(16) })
        }
    }

    companion object {
        private const val UNKNOWN = "모름"
        private val FLOORS = listOf("B1", "B2", "B3", "B4", "1F", UNKNOWN)
        private const val FLOOR_HEIGHT_DP = 120
    }
}
