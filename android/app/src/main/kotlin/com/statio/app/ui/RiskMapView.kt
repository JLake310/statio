package com.statio.app.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.util.AttributeSet
import android.view.View
import com.statio.app.R
import com.statio.app.core.Lot
import com.statio.app.core.ParkingCheck
import com.statio.app.core.Risk
import com.statio.app.core.RiskKind
import kotlin.math.cos
import kotlin.math.max
import kotlin.math.min

/**
 * 내 차, 가장 중요한 위험 지점과 그 범위, 추천 주차장을 한눈에 보여 주는 약도입니다.
 * 지도 타일 없이 실제 거리 비율로만 그립니다. 격자 한 칸은 [GRID_M]입니다.
 */
class RiskMapView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : View(context, attrs) {

    private var check: ParkingCheck? = null
    private var lot: Lot? = null

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density
    private fun color(id: Int) = context.getColor(id)

    // 표시(내 차·주차장·위험 지점) 크기 배율입니다. 세로 분할 창처럼 약도가 낮으면 표시도 작게 그려 서로 겹치지 않게 해요.
    private var scale = 1f
    private fun m(v: Float) = dp(v) * scale

    // 가로 전체 창에서만 범례 칩을 약도 아래쪽에 겹쳐 보여요(세로 분할 창은 숨김). 칩 자리를 비워 두고 그려요.
    private val legend = resources.getBoolean(R.bool.wide)

    private val grid = Paint().apply { color = color(R.color.basic_200); strokeWidth = dp(2f) }
    private val zoneFill = Paint(Paint.ANTI_ALIAS_FLAG)
    private val zoneLine = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = dp(3f); pathEffect = DashPathEffect(floatArrayOf(dp(10f), dp(8f)), 0f)
    }
    private val marker = Paint(Paint.ANTI_ALIAS_FLAG)
    private val walk = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = dp(7f); strokeCap = Paint.Cap.ROUND
        color = color(R.color.active); pathEffect = DashPathEffect(floatArrayOf(dp(1f), dp(16f)), 0f)
    }
    private val label = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = color(R.color.basic_00); textSize = dp(34f); typeface = Typeface.DEFAULT_BOLD; textAlign = Paint.Align.CENTER
    }
    private val scaleText = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = color(R.color.text_3); textSize = dp(20f) }

    fun show(check: ParkingCheck, lot: Lot?) {
        this.check = check
        this.lot = lot
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        val c = check ?: return
        if (height < dp(MIN_HEIGHT_DP)) return // 세로 분할 창에서 남는 높이가 적으면 그리지 않아요.
        val main = c.risks.firstOrNull()
        scale = min(1f, height / dp(FULL_SIZE_DP))
        fun eastOf(lon: Double) = (lon - c.lon) * cos(Math.toRadians(c.lat)) * M_PER_DEG  // 내 차 기준 미터
        fun northOf(lat: Double) = (lat - c.lat) * M_PER_DEG

        // 내 차 주변, 가장 중요한 위험 범위, 추천 주차장이 모두 들어오는 상자를 잡아 약도 칸에 꽉 차게 맞춥니다.
        // 축척은 가로·세로 중 빠듯한 쪽에 맞춥니다. 내 차가 가운데가 아닐 수 있어요.
        var west = -MIN_HALF_M; var east = MIN_HALF_M; var south = -MIN_HALF_M; var north = MIN_HALF_M
        fun include(e: Double, n: Double, r: Double = 0.0) {
            west = min(west, e - r); east = max(east, e + r); south = min(south, n - r); north = max(north, n + r)
        }
        main?.let { include(eastOf(it.lon), northOf(it.lat), it.radiusM) }
        lot?.let { include(eastOf(it.row.lon), northOf(it.row.lat)) }
        val area = RectF(m(PAD), m(PAD), width - m(PAD), height - if (legend) dp(PAD_LEGEND) else m(PAD))
        val pxPerM = min(area.width() / (east - west), area.height() / (north - south)).toFloat()
        val cx = area.centerX() - ((west + east) / 2).toFloat() * pxPerM  // 내 차 위치
        val cy = area.centerY() + ((south + north) / 2).toFloat() * pxPerM
        fun x(lon: Double) = cx + eastOf(lon).toFloat() * pxPerM
        fun y(lat: Double) = cy - northOf(lat).toFloat() * pxPerM

        val step = GRID_M * pxPerM
        if (step > dp(24f)) {
            var gx = cx % step
            while (gx < width) { canvas.drawLine(gx, 0f, gx, height.toFloat(), grid); gx += step }
            var gy = cy % step
            while (gy < height) { canvas.drawLine(0f, gy, width.toFloat(), gy, grid); gy += step }
            // 주차장 표시가 오른쪽 위로 오면 축척 글자는 생략해요(표시가 더 중요해요).
            val scaleBox = RectF(width - dp(176f), 0f, width.toFloat(), dp(48f))
            val covered = lot?.let {
                val lx = x(it.row.lon)
                val ly = y(it.row.lat)
                scaleBox.intersects(lx - m(34f), ly - m(34f), lx + m(34f), ly + m(34f))
            } ?: false
            if (!covered) canvas.drawText("격자 한 칸 ${GRID_M.toInt()}m", width - dp(170f), dp(36f), scaleText)
        }

        if (main != null) {
            val tint = if (main.activeNow) R.color.negative else R.color.caution
            zoneFill.color = color(tint) and 0x00FFFFFF or 0x1F000000
            zoneLine.color = color(tint)
            // 소화전 5m처럼 범위가 작으면 실제 축척으로는 표시에 가려서, 최소 크기로 그려 범위가 보이게 해요.
            val r = max(main.radiusM.toFloat() * pxPerM, m(MIN_ZONE_DP))
            canvas.drawCircle(x(main.lon), y(main.lat), r, zoneFill)
            canvas.drawCircle(x(main.lon), y(main.lat), r, zoneLine)
        }

        lot?.let {
            val lx = x(it.row.lon)
            val ly = y(it.row.lat)
            walk.strokeWidth = m(7f)
            canvas.drawLine(cx, cy, lx, ly, walk)
            marker.color = color(R.color.active)
            canvas.drawCircle(lx, ly, m(34f), marker)
            label.textSize = m(34f)
            canvas.drawText("P", lx, ly + m(12f), label)
        }

        marker.color = color(R.color.basic_900)
        canvas.drawRoundRect(RectF(cx - m(22f), cy - m(14f), cx + m(22f), cy + m(14f)), m(6f), m(6f), marker)

        // 소화전·횡단보도처럼 차 바로 옆의 위험이 내 차 표시에 가리지 않도록 맨 위에 그립니다.
        for (risk in c.risks.reversed()) drawRiskMarker(canvas, risk, x(risk.lon), y(risk.lat), big = risk == main)
    }

    private fun drawRiskMarker(canvas: Canvas, risk: Risk, px: Float, py: Float, big: Boolean) {
        marker.color = color(if (risk.activeNow && risk.kind != RiskKind.SCHOOL_ZONE) R.color.negative else R.color.caution)
        val half = m(if (big) 14f else 10f)
        canvas.drawRoundRect(RectF(px - half, py - half, px + half, py + half), m(6f), m(6f), marker)
    }

    companion object {
        private const val M_PER_DEG = 111_320.0
        private const val GRID_M = 50f
        private const val MIN_HEIGHT_DP = 140f
        private const val MIN_HALF_M = 60.0      // 내 차 둘레로 최소한 보여 줄 거리
        private const val MIN_ZONE_DP = 40f      // 위험 범위 원의 최소 반지름 (내 차 표시보다 크게)
        private const val PAD = 44f              // 주차장 표시(반지름 34dp)가 잘리지 않을 여백
        private const val PAD_LEGEND = 100f      // 범례 칩(약 56dp) + 주차장 표시 반지름
        private const val FULL_SIZE_DP = 360f    // 약도가 이보다 낮으면 표시를 비율대로 줄여요
    }
}
