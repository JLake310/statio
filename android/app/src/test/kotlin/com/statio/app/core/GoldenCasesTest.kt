package com.statio.app.core

import org.json.JSONArray
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import java.io.File
import java.sql.Connection
import java.sql.DriverManager
import java.time.LocalDateTime
import kotlin.math.abs

/**
 * 앱 내장 DB와 Kotlin 로직이 server/parking_core.py와 같은 답을 내는지 대조합니다.
 * 정답 사례(golden_cases.json)는 datapipe/export_app_assets.py가 DB와 함께 만듭니다.
 * 앱 DB는 좌표를 정수로 줄여 저장하므로 거리는 1m까지 차이를 허용합니다.
 */
class GoldenCasesTest {

    @Test fun matchesPythonReference() {
        val cases = JSONArray(javaClass.classLoader!!.getResource("golden_cases.json")!!.readText())
        assertTrue("정답 사례가 비어 있어요", cases.length() > 0)
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val label = "${case.getString("label")} (${case.getDouble("lat")}, ${case.getDouble("lon")}, ${case.getString("when")})"
            val result = advisor.check(
                case.getDouble("lat"), case.getDouble("lon"), LocalDateTime.parse(case.getString("when")),
                marginM = case.getDouble("margin_m"), radiusM = case.getDouble("radius_m"),
            )

            assertEquals("$label 위험 단계", case.getString("risk_level"), result.level.name.lowercase())
            val risks = case.getJSONArray("risks")
            assertEquals("$label 위험 종류", (0 until risks.length()).map { risks.getJSONObject(it).getString("kind") }, result.risks.map { it.kind.code })
            for (j in 0 until risks.length()) {
                val expected = risks.getJSONObject(j)
                val actual = result.risks[j]
                assertClose("$label ${actual.kind} 거리", expected.getInt("distance_m"), actual.distanceM)
                assertEquals("$label ${actual.kind} 지금 대상", expected.getBoolean("active_now"), actual.activeNow)
                assertEquals("$label ${actual.kind} 과태료", expected.getInt("fine_krw"), actual.fineKrw)
            }

            val lots = case.getJSONArray("lots")
            val expectedById = (0 until lots.length()).associate { lots.getJSONObject(it).let { o -> o.getString("id") to o } }
            assertEquals("$label 주차장", expectedById.keys, result.alternatives.map { it.row.id }.toSet())
            for (lot in result.alternatives) {
                val expected = expectedById.getValue(lot.row.id)
                assertClose("$label ${lot.row.name} 거리", expected.getInt("distance_m"), lot.distanceM)
                assertEquals("$label ${lot.row.name} 10분 요금", expected.optIntOrNull("per_10min_krw"), lot.per10MinKrw)
                assertEquals("$label ${lot.row.name} 1시간 요금", expected.optIntOrNull("per_hour_krw"), lot.perHourKrw)
            }
        }
    }

    private fun assertClose(message: String, expected: Int, actual: Int) =
        assertTrue("$message: 기대 $expected, 실제 $actual", abs(expected - actual) <= 1)

    private fun org.json.JSONObject.optIntOrNull(key: String): Int? = if (isNull(key)) null else getInt(key)

    companion object {
        private lateinit var conn: Connection
        private lateinit var advisor: ParkingAdvisor

        @BeforeClass @JvmStatic fun open() {
            val db = File("src/main/assets/statio.db")
            assertTrue("앱 DB가 없어요. python3 datapipe/export_app_assets.py를 먼저 실행하세요", db.exists())
            conn = DriverManager.getConnection("jdbc:sqlite:${db.absolutePath}")
            advisor = ParkingAdvisor(JdbcParkingData(conn), NoLiveSource)
        }

        @AfterClass @JvmStatic fun close() = conn.close()
    }
}

/** 테스트에서 앱 DB 파일을 JDBC로 읽습니다. 조회문은 앱과 같은 [Sql]을 씁니다. */
class JdbcParkingData(private val conn: Connection) : ParkingData {

    override fun riskCandidates(range: CellRange): List<RiskPoint> =
        query(Sql.RISK_CANDIDATES, Sql.rangeArgs(range)) { r ->
            val kind = RiskKind.ofIndex(r.getInt(1))
            val radius = r.getInt(5).takeUnless { r.wasNull() }?.toDouble() ?: kind.radiusM
            RiskPoint(kind, r.getLong(2) / Sql.E6, r.getLong(3) / Sql.E6, r.getString(4), radius)
        }

    override fun lotCandidates(range: CellRange): List<LotRow> =
        query(Sql.LOT_CANDIDATES, Sql.rangeArgs(range)) { r ->
            fun int(i: Int): Int? = r.getInt(i).takeUnless { r.wasNull() }
            LotRow(
                id = r.getString(1), name = r.getString(2), address = r.getString(3), category = r.getString(4), type = r.getString(5),
                total = int(6), lat = r.getLong(7) / Sql.E6, lon = r.getLong(8) / Sql.E6, basicFee = int(9), basicMin = int(10),
                addFee = int(11), addMin = int(12), dayMax = int(13), weekdayHours = r.getString(14), weekendHours = r.getString(15),
                holidayHours = r.getString(16), realtimeLinked = r.getInt(17) == 1,
            )
        }

    override fun pattern(lotId: String, dayType: Int, slot: Int): PatternCell? =
        query(Sql.PATTERN, listOf(lotId, dayType, slot)) { r -> PatternCell(r.getInt(1), r.getInt(2)) }.firstOrNull()

    private fun <T> query(sql: String, args: List<Any>, map: (java.sql.ResultSet) -> T): List<T> =
        conn.prepareStatement(sql).use { st ->
            args.forEachIndexed { i, a -> st.setObject(i + 1, a) }
            st.executeQuery().use { r -> buildList { while (r.next()) add(map(r)) } }
        }
}
