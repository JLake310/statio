package com.statio.app

import android.content.Context
import com.statio.app.core.LotRow
import org.json.JSONObject
import java.time.LocalDateTime

/**
 * 운전자가 내린 순간의 주차 위치 기록입니다. 지금은 마지막 한 건만 저장합니다.
 * 주차장 안이면 [lot]에 그 주차장(요금 포함)이 들어가고, 층은 운전자가 고르면 채워집니다.
 */
data class ParkingRecord(
    val lat: Double,
    val lon: Double,
    /** 화면에 보여 줄 이름입니다. 주차장 안이면 주차장 이름, 아니면 길 이름입니다. */
    val place: String,
    val parkedAt: LocalDateTime,
    val lot: LotRow? = null,
    val floor: String? = null,
) {
    fun toJson(): String = JSONObject().apply {
        put("lat", lat); put("lon", lon); put("place", place); put("parked_at", parkedAt.toString())
        floor?.let { put("floor", it) }
        lot?.let { l ->
            put("lot", JSONObject().apply {
                put("id", l.id); put("name", l.name); put("address", l.address); put("category", l.category)
                put("lat", l.lat); put("lon", l.lon)
                put("basic_fee", l.basicFee); put("basic_min", l.basicMin); put("add_fee", l.addFee)
                put("add_min", l.addMin); put("day_max", l.dayMax)
            })
        }
    }.toString()

    companion object {
        private const val PREFS = "parking_record"
        private const val KEY = "last"

        fun load(context: Context): ParkingRecord? =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null)?.let { runCatching { fromJson(it) }.getOrNull() }

        fun save(context: Context, record: ParkingRecord) =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, record.toJson()).apply()

        private fun fromJson(text: String): ParkingRecord {
            val o = JSONObject(text)
            fun JSONObject.intOrNull(k: String): Int? = if (isNull(k)) null else getInt(k)
            fun JSONObject.strOrNull(k: String): String? = if (isNull(k)) null else getString(k)
            val lot = o.optJSONObject("lot")?.let { l ->
                LotRow(
                    id = l.getString("id"), name = l.getString("name"), address = l.strOrNull("address"),
                    category = l.strOrNull("category"), type = null, total = null, lat = l.getDouble("lat"), lon = l.getDouble("lon"),
                    basicFee = l.intOrNull("basic_fee"), basicMin = l.intOrNull("basic_min"), addFee = l.intOrNull("add_fee"),
                    addMin = l.intOrNull("add_min"), dayMax = l.intOrNull("day_max"),
                    weekdayHours = null, weekendHours = null, holidayHours = null, realtimeLinked = false,
                )
            }
            return ParkingRecord(
                lat = o.getDouble("lat"), lon = o.getDouble("lon"), place = o.getString("place"),
                parkedAt = LocalDateTime.parse(o.getString("parked_at")), lot = lot, floor = o.strOrNull("floor"),
            )
        }
    }
}
