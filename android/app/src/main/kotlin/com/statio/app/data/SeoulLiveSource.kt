package com.statio.app.data

import android.util.Log
import com.statio.app.core.LiveCount
import com.statio.app.core.LiveSample
import com.statio.app.core.LiveSource
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter

/**
 * 서울 열린데이터광장 시영주차장 실시간 주차대수(GetParkingInfo)를 차에서 직접 부릅니다.
 *
 * - 서울 API는 HTTPS를 지원하지 않아 res/xml/network_security_config.xml에서 이 도메인만 평문 통신을 허용합니다.
 * - 키는 빌드할 때 BuildConfig로 들어옵니다. 키가 들어간 주소는 로그에 남기지 않습니다.
 * - 1분 안에 다시 부르면 저장해 둔 값을 돌려줍니다. 실패하면 마지막으로 받은 값을 그대로 씁니다.
 */
class SeoulLiveSource(private val apiKey: String, private val ttlMs: Long = 60_000) : LiveSource {

    private val lock = Any()
    private var cache: Map<String, LiveCount> = emptyMap()
    private var fetchedAtMs = 0L
    private val samples = HashMap<String, ArrayDeque<LiveSample>>()

    /** 마지막 호출이 실패했으면 그 이유입니다. 화면에 '실시간 정보를 받지 못했어요'를 보여 줄 때 씁니다. */
    @Volatile var lastError: String? = null
        private set

    val hasKey: Boolean get() = apiKey.isNotBlank()

    override fun latest(): Map<String, LiveCount> = synchronized(lock) {
        if (!hasKey) return emptyMap()
        if (System.currentTimeMillis() - fetchedAtMs < ttlMs) return cache
        try {
            cache = fetchAll()
            fetchedAtMs = System.currentTimeMillis()
            lastError = null
            remember(cache)
        } catch (e: Exception) {
            lastError = e.javaClass.simpleName
            Log.w(TAG, "실시간 빈자리 호출 실패: ${e.javaClass.simpleName}")
        }
        cache
    }

    override fun recent(lotId: String): List<LiveSample> = synchronized(lock) { samples[lotId]?.toList().orEmpty() }

    private fun fetchAll(): Map<String, LiveCount> {
        val result = HashMap<String, LiveCount>()
        var start = 1
        while (true) {
            val page = parseParkingInfo(get("$BASE/$apiKey/json/$SERVICE/$start/${start + PAGE - 1}/"))
            result.putAll(page.counts)
            if (start + PAGE - 1 >= page.total) return result
            start += PAGE
        }
    }

    private fun get(url: String): String {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.connectTimeout = 5_000
        conn.readTimeout = 10_000
        try {
            if (conn.responseCode != 200) throw IllegalStateException("HTTP ${conn.responseCode}")
            return conn.inputStream.bufferedReader().use { it.readText() }
        } finally {
            conn.disconnect()
        }
    }

    /** 예측(trend)에 쓰려고 최근 1시간 기록을 주차장별로 쌓습니다. 같은 갱신 시각은 한 번만 넣습니다. */
    private fun remember(counts: Map<String, LiveCount>) {
        for ((id, count) in counts) {
            val list = samples.getOrPut(id) { ArrayDeque() }
            if (list.lastOrNull()?.at != count.updatedAt) list.addLast(LiveSample(count.updatedAt, count.free))
            while (list.isNotEmpty() && list.first().at.isBefore(count.updatedAt.minusHours(1))) list.removeFirst()
        }
    }

    companion object {
        private const val TAG = "SeoulLive"
        private const val BASE = "http://openapi.seoul.go.kr:8088"
        private const val SERVICE = "GetParkingInfo"
        private const val PAGE = 1000
        private const val NO_DATA = "INFO-200"
        private val TIME = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")

        data class Page(val total: Int, val counts: Map<String, LiveCount>)

        /** 응답 한 페이지를 읽습니다. 실시간 연계(PRK_STTS_YN=1)이고 갱신 시각이 있는 주차장만 씁니다. */
        fun parseParkingInfo(json: String): Page {
            val root = JSONObject(json)
            val body = root.optJSONObject(SERVICE)
            if (body == null) {
                val result = root.optJSONObject("RESULT")
                if (result?.optString("CODE") == NO_DATA) return Page(0, emptyMap())
                throw IllegalStateException("$SERVICE 호출 실패: ${result?.optString("CODE")}")
            }
            val rows = body.optJSONArray("row")
            val counts = HashMap<String, LiveCount>()
            for (i in 0 until (rows?.length() ?: 0)) {
                val r = rows!!.getJSONObject(i)
                val updated = r.optString("NOW_PRK_VHCL_UPDT_TM")
                if (r.optString("PRK_STTS_YN") != "1" || updated.isBlank() || r.isNull("TPKCT") || r.isNull("NOW_PRK_VHCL_CNT")) continue
                counts[r.getString("PKLT_CD")] = LiveCount(
                    total = r.getDouble("TPKCT").toInt(),
                    parked = r.getDouble("NOW_PRK_VHCL_CNT").toInt(),
                    updatedAt = LocalDateTime.parse(updated, TIME),
                )
            }
            return Page(body.optInt("list_total_count", counts.size), counts)
        }
    }
}
