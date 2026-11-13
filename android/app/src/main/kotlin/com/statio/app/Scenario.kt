package com.statio.app

import android.content.Context

/**
 * 시연 시나리오입니다. 에뮬레이터 내비 위치는 수정구 시흥동에 고정이라,
 * 시나리오를 고르면 P단 판단에 내비 위치 대신 이 위치를 씁니다.
 */
data class Scenario(val id: String, val label: String, val place: String, val lat: Double, val lon: Double) {

    companion object {
        /** 위치는 datapipe·server의 조회로 고른 실제 지점입니다(2026-10-22). */
        val presets = listOf(
            Scenario("gwanghwamun", "광화문 세종대로", "세종대로", 37.57256, 126.97829), // 단속 CCTV 11m · 단속 잦은 자리(224번)
            Scenario("wonhyoro", "용산 원효로", "원효로", 37.53618, 126.96337),        // 횡단보도·소화전·어린이보호구역
            Scenario("jongmyo", "종묘 앞", "종로", 37.5713, 126.9946),                  // 알려진 위험 없음
        )

        private const val PREFS = "scenario"
        private const val KEY = "selected"

        fun byId(id: String?): Scenario? = presets.firstOrNull { it.id == id }

        /** 선택한 시나리오입니다. null이면 실제 내비 위치를 씁니다. */
        fun selected(context: Context): Scenario? =
            byId(context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null))

        fun select(context: Context, scenario: Scenario?) =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, scenario?.id).apply()
    }
}
