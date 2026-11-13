package com.statio.app.core

import org.json.JSONArray
import org.json.JSONObject
import org.junit.AfterClass
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.BeforeClass
import org.junit.Test
import java.io.File
import java.sql.Connection
import java.sql.DriverManager
import java.time.LocalDateTime
import kotlin.math.abs

/**
 * 앱의 빈자리 예측(SpacePredictor)이 파이썬(scikit-learn)과 같은 값을 내는지 대조합니다.
 * 정답 사례(predictor_golden.json)와 모델(assets/predictor.json)은 ml/export_model.py가 함께 만듭니다.
 * 사례마다 앱이 가진 값(실시간 값, 최근 기록, 앱 DB 패턴표)으로 입력을 만들고, 입력·예측값·확률을 모두 비교합니다.
 */
class SpacePredictorTest {

    @Test fun matchesPythonReference() {
        val cases = JSONArray(javaClass.classLoader!!.getResource("predictor_golden.json")!!.readText())
        assertTrue("정답 사례가 비어 있어요", cases.length() > 0)
        var predicted = 0
        var warned = 0
        for (i in 0 until cases.length()) {
            val case = cases.getJSONObject(i)
            val lot = case.getString("lot")
            val label = "$lot ${case.getString("now")} 도착 ${case.getDouble("eta_min")}분"
            val count = LiveCount(case.getInt("total"), case.getInt("parked"), LocalDateTime.parse(case.getString("updated_at")))
            val recent = case.getJSONArray("recent").let { a ->
                List(a.length()) { a.getJSONObject(it).let { o -> LiveSample(LocalDateTime.parse(o.getString("at")), o.getInt("free")) } }
            }
            val now = LocalDateTime.parse(case.getString("now"))
            val eta = case.getDouble("eta_min")
            val pattern = { d: Int, s: Int -> data.pattern(lot, d, s) }

            val features = predictor.features(lot, now, eta, count, recent, pattern)
            val result = predictor.predict(lot, now, eta, count, recent, pattern)
            if (case.isNull("features")) {
                assertNull("$label 모델이 모르는 주차장이에요", result)
                continue
            }
            val expected = case.getJSONArray("features")
            assertEquals("$label 입력 개수", expected.length(), features!!.size)
            for (j in 0 until expected.length()) {
                val want = if (expected.isNull(j)) Double.NaN else expected.getDouble(j)
                assertTrue("$label 입력 $j: 기대 $want, 실제 ${features[j]}", same(want, features[j]))
            }
            assertTrue("$label 변화: 기대 ${case.getDouble("delta")}, 실제 ${result!!.delta}", abs(case.getDouble("delta") - result.delta) < 1e-12)
            assertEquals("$label 빈자리", case.getInt("free"), result.free)
            assertTrue("$label 확률", abs(case.getDouble("prob_free") - result.probFree!!) < 1e-12)
            assertTrue("$label 만차 위험", abs(case.getDouble("full_risk") - result.fullRisk!!) < 1e-12)
            assertEquals("$label 만차 위험 경고", case.getBoolean("full_warning"), result.fullWarning)
            if (result.fullWarning) warned++
            predicted++
        }
        assertTrue("예측한 사례가 너무 적어요($predicted)", predicted >= cases.length() / 2)
        assertTrue("만차 위험 경고 사례가 너무 적어요($warned)", warned >= 10)
    }

    @Test fun unknownLotIsSkipped() {
        val count = LiveCount(100, 40, LocalDateTime.of(2026, 10, 23, 12, 0))
        assertNull(predictor.predict("없는주차장", count.updatedAt, 20.0, count, emptyList()) { _, _ -> null })
    }

    private fun same(a: Double, b: Double) = (a.isNaN() && b.isNaN()) || abs(a - b) < 1e-12

    companion object {
        private lateinit var conn: Connection
        private lateinit var data: JdbcParkingData
        private lateinit var predictor: SpacePredictor

        @BeforeClass @JvmStatic fun open() {
            val model = File("src/main/assets/predictor.json")
            assertTrue("모델이 없어요. .venv/bin/python ml/export_model.py를 먼저 실행하세요", model.exists())
            predictor = SpacePredictor(model.readText())
            conn = DriverManager.getConnection("jdbc:sqlite:${File("src/main/assets/statio.db").absolutePath}")
            data = JdbcParkingData(conn)
            assertEquals("모델 입력 수", 16, JSONObject(model.readText()).getJSONArray("features").length())
        }

        @AfterClass @JvmStatic fun close() = conn.close()
    }
}
