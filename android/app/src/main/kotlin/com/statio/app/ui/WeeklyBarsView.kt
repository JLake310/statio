package com.statio.app.ui

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View
import com.statio.app.R
import com.statio.app.core.WeekCount

/** 월간 리포트의 주별 막대 그래프입니다. 주마다 단속 위험 경고(빨강)와 주차장으로 옮긴 횟수(초록)를 나란히 그려요. */
class WeeklyBarsView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : View(context, attrs) {

    private var weeks: List<WeekCount> = emptyList()
    private val density = resources.displayMetrics.density
    private val scaled = resources.displayMetrics.scaledDensity
    private val warnPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.negative) }
    private val movePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.active) }
    private val basePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = context.getColor(R.color.basic_200) }
    private val labelPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.text_3); textSize = 22 * scaled; textAlign = Paint.Align.CENTER
    }
    private val valuePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = context.getColor(R.color.text_2); textSize = 20 * scaled; textAlign = Paint.Align.CENTER; isFakeBoldText = true
    }
    private val rect = RectF()

    fun setWeeks(next: List<WeekCount>) {
        weeks = next
        invalidate()
    }

    override fun onDraw(canvas: Canvas) {
        if (weeks.isEmpty()) return
        val labelH = 40 * density
        val valueH = 30 * density
        val bottom = height - labelH
        val top = valueH
        val maxCount = weeks.maxOf { maxOf(it.warnings, it.moves) }.coerceAtLeast(1)
        val slot = width.toFloat() / weeks.size
        val barW = minOf(slot * 0.22f, 84 * density)
        val gap = 8 * density
        val radius = 10 * density
        canvas.drawRect(0f, bottom, width.toFloat(), bottom + 2 * density, basePaint)
        weeks.forEachIndexed { i, w ->
            val center = slot * i + slot / 2
            bar(canvas, center - gap / 2 - barW, barW, w.warnings, maxCount, top, bottom, radius, warnPaint)
            bar(canvas, center + gap / 2, barW, w.moves, maxCount, top, bottom, radius, movePaint)
            canvas.drawText("${w.week}주", center, bottom + labelH * 0.75f, labelPaint)
        }
    }

    private fun bar(canvas: Canvas, left: Float, w: Float, count: Int, max: Int, top: Float, bottom: Float, r: Float, paint: Paint) {
        val h = if (count == 0) 0f else maxOf((bottom - top) * count / max, r * 2)
        if (h > 0f) {
            rect.set(left, bottom - h, left + w, bottom + r) // 아래 모서리는 기준선 밑으로 숨겨 위만 둥글게 보여요
            canvas.save()
            canvas.clipRect(left, top - r, left + w, bottom)
            canvas.drawRoundRect(rect, r, r, paint)
            canvas.restore()
        }
        if (count > 0) canvas.drawText(count.toString(), left + w / 2, bottom - h - 8 * density, valuePaint)
    }
}
