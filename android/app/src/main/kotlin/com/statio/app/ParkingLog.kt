package com.statio.app

import android.content.Context
import com.statio.app.core.LogEvent
import com.statio.app.core.LogKind
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDateTime

/**
 * 월간 리포트용 기록(주차, 단속 위험 경고, 주차장으로 옮김, 만차 피함)을 기기 안에 쌓습니다. 서버로 보내지 않아요.
 * 13개월이 지난 기록과 [MAX_EVENTS]를 넘는 오래된 기록은 지워요.
 */
object ParkingLog {
    private const val PREFS = "parking_log"
    private const val KEY = "events"
    private const val MAX_EVENTS = 2_000
    private const val KEEP_MONTHS = 13L

    fun load(context: Context): List<LogEvent> {
        val text = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null) ?: return emptyList()
        return runCatching {
            val array = JSONArray(text)
            (0 until array.length()).mapNotNull { i -> runCatching { fromJson(array.getJSONObject(i)) }.getOrNull() }
        }.getOrDefault(emptyList())
    }

    fun add(context: Context, event: LogEvent) {
        val cutoff = event.at.minusMonths(KEEP_MONTHS)
        val kept = (load(context) + event).filter { it.at.isAfter(cutoff) }.takeLast(MAX_EVENTS)
        val array = JSONArray().apply { kept.forEach { put(toJson(it)) } }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, array.toString()).apply()
    }

    /** 시연을 새로 시작할 때 기록을 비워요(adb --es cmd report_clear). */
    fun clear(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(KEY).apply()

    private fun toJson(e: LogEvent) = JSONObject().apply {
        put("kind", e.kind.name); put("at", e.at.toString()); put("place", e.place)
        if (e.fineKrw != 0) put("fine", e.fineKrw)
        if (e.feeKrw != 0) put("fee", e.feeKrw)
        if (e.inLot) put("in_lot", true)
    }

    private fun fromJson(o: JSONObject) = LogEvent(
        kind = LogKind.valueOf(o.getString("kind")),
        at = LocalDateTime.parse(o.getString("at")),
        place = o.getString("place"),
        fineKrw = o.optInt("fine", 0),
        feeKrw = o.optInt("fee", 0),
        inLot = o.optBoolean("in_lot", false),
    )
}
