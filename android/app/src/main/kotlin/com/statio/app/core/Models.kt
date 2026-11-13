package com.statio.app.core

import java.time.LocalDateTime
import kotlin.math.max

/**
 * 주정차 위험 종류입니다. 순서가 경고 우선순위이자 앱 DB의 kind 번호입니다(server/parking_core.py의 SEVERITY).
 * 과태료는 승용차 기준이에요(2026-10 지자체·서울시 안내로 확인). 같은 장소 2시간 이상 가산은 넣지 않아 화면에는 '추정'으로 보여요.
 */
enum class RiskKind(val label: String, val radiusM: Double, val rule: String, val fineKrw: Int) {
    CCTV("주정차 단속 CCTV", 50.0, "주정차 단속 CCTV 반경 50m (추정 단속 범위)", 40_000),
    BUS_LANE("버스전용차로 CCTV", 30.0, "버스전용차로 단속 CCTV 반경 30m", 50_000),
    /** 가장 최근 분기 서울시 단속 기록으로 만든 자리라 단속 카메라 바로 다음이에요. 이름에 그 분기와 반경 안 단속 수가 들어 있어요(예: 25년 10~12월 132번 단속). */
    HOTSPOT("단속 잦은 자리", 30.0, "지난 분기 실제 단속이 잦았던 자리 반경 30m (서울시 단속 기록, 20m 안 10번 이상)", 40_000),
    HYDRANT("소화전", 5.0, "소화전 5m 이내 주정차 금지 (주민신고 대상)", 80_000),
    CROSSWALK("횡단보도", 10.0, "횡단보도 10m 이내 주정차 금지 (주민신고 대상)", 40_000),
    BUS_STOP("버스정류소", 10.0, "버스정류소 10m 이내 주정차 금지 (주민신고 대상)", 40_000),
    /** 반경은 교차로마다 달라요(앱 DB radius_m, 교차로 반폭 + 5m). 여기 값은 크기를 모를 때의 기본값입니다. */
    INTERSECTION("교차로 모퉁이(추정)", 20.0, "교차로 모퉁이 5m 이내 주정차 금지 (주민신고 대상, 교차로 크기는 횡단보도 위치로 추정)", 40_000),
    SCHOOL_ZONE("어린이보호구역(추정)", 100.0, "어린이보호구역 추정 (대상 시설 반경 100m, 시설 앞 도로 기준, 주민신고는 평일 08~20시)", 120_000);

    /** server/parking_core.py와 정답 사례에서 쓰는 이름입니다. */
    val code: String get() = name.lowercase()

    companion object {
        const val MAX_RADIUS_M = 100.0
        fun ofIndex(index: Int): RiskKind = entries[index]
    }
}

/** [radiusM]은 지점별 판단 반경입니다. 앱 DB에 값이 없는 종류는 종류 기본값을 써요. */
data class RiskPoint(val kind: RiskKind, val lat: Double, val lon: Double, val name: String?, val radiusM: Double = kind.radiusM)

data class Risk(
    val kind: RiskKind,
    val name: String?,
    val distanceM: Int,
    val lat: Double,
    val lon: Double,
    /** 지금 단속·신고 대상인지입니다. 어린이보호구역만 시간대에 따라 달라집니다. */
    val activeNow: Boolean,
    /** 이 지점의 판단 반경입니다. 약도에 범위를 그릴 때 써요. */
    val radiusM: Double = kind.radiusM,
) {
    val fineKrw: Int get() = kind.fineKrw
}

/** HIGH: 지금 단속·신고 대상, CAUTION: 시간대에 따라 대상, NONE: 알려진 위험 없음. */
enum class RiskLevel { HIGH, CAUTION, NONE }

data class LotRow(
    val id: String,
    val name: String,
    val address: String?,
    val category: String?,
    val type: String?,
    val total: Int?,
    val lat: Double,
    val lon: Double,
    val basicFee: Int?,
    val basicMin: Int?,
    val addFee: Int?,
    val addMin: Int?,
    val dayMax: Int?,
    val weekdayHours: String?,
    val weekendHours: String?,
    val holidayHours: String?,
    /** 서울시 실시간 연계 대상으로 표시된 주차장입니다. 실제 값은 실시간 API에 있을 때만 옵니다. */
    val realtimeLinked: Boolean,
)

/** 실시간 API 한 번의 값입니다. */
data class LiveCount(val total: Int, val parked: Int, val updatedAt: LocalDateTime) {
    val free: Int get() = max(0, total - parked)
}

/** 시간대 패턴표 한 칸입니다. */
data class PatternCell(val free: Int, val days: Int)

enum class PredictionBasis { PATTERN, TREND, CURRENT, AI }

/**
 * [probFree]는 AI 예측일 때만 있는 '도착 시 빈자리가 1면 이상 있을 확률'이고,
 * [fullWarning]은 AI가 도착 때 만차 위험이 있다고 본 경우입니다(지금 만차이거나 만차 위험이 기준값을 넘을 때).
 */
data class Prediction(val free: Int, val basis: PredictionBasis, val days: Int, val probFree: Double? = null, val fullWarning: Boolean = false)

data class Lot(
    val row: LotRow,
    val distanceM: Int,
    val live: LiveCount?,
    /** 실시간 값이 몇 분 전 기준인지입니다. */
    val ageMin: Int?,
    val predicted: Prediction?,
) {
    val per10MinKrw: Int? get() = feeFor(10, row)
    val perHourKrw: Int? get() = feeFor(60, row)
    val hasFreeSpace: Boolean get() = (live?.free ?: 0) > 0

    /** 도착했을 때 빈자리가 남아 있을지입니다. AI 예측이 있으면 만차 위험 경고가 없는지, 없으면 지금 빈자리가 있는지로 봅니다. */
    val expectFree: Boolean get() = predicted?.takeIf { it.basis == PredictionBasis.AI }?.let { !it.fullWarning } ?: hasFreeSpace

    /**
     * 추천 순서 칸입니다. 0 도착 때 자리가 있을 곳, 1 실시간 정보가 없는 곳, 2 만차이거나 AI가 만차 위험으로 본 곳.
     * 상태를 모르는 곳이 만차로 알려진 곳보다 낫다고 봐요(돌아가는 위험이 더 커서).
     */
    val rankTier: Int get() = when {
        live == null -> 1
        expectFree -> 0
        else -> 2
    }

    /** 안내 버튼이 가리켜도 되는 주차장인지입니다. 만차이거나 만차 위험인 곳은 가리키지 않아요. */
    val recommendable: Boolean get() = rankTier < 2
}

data class ParkingCheck(
    val lat: Double,
    val lon: Double,
    val checkedAt: LocalDateTime,
    val level: RiskLevel,
    val risks: List<Risk>,
    /** 지금 적용되는 위험 중 가장 큰 과태료 추정치입니다. 없으면 0입니다. */
    val fineKrw: Int,
    val alternatives: List<Lot>,
) {
    /** 안내 버튼이 가리킬 대안 주차장입니다. 만차인 곳은 건너뛰고, 모두 만차면 null이에요. */
    val bestAlternative: Lot? get() = alternatives.getOrNull(recommendedIndex(alternatives) ?: -1)
}
