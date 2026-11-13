package com.statio.app.core

import kotlin.math.ceil
import kotlin.math.cos
import kotlin.math.hypot

/** 위경도를 이 값만큼 곱해 정수로 자른 칸 번호로 검색 범위를 좁힙니다. server/parking_core.py의 CELL과 같습니다. */
const val CELL = 1000

/** 짧은 거리용 평면 근사 거리(m)입니다. server/parking_core.py의 distance_m과 같습니다. */
fun distanceM(lat1: Double, lon1: Double, lat2: Double, lon2: Double): Double {
    val x = Math.toRadians(lon2 - lon1) * cos(Math.toRadians((lat1 + lat2) / 2))
    val y = Math.toRadians(lat2 - lat1)
    return 6_371_000 * hypot(x, y)
}

data class CellRange(val latFrom: Int, val latTo: Int, val lonFrom: Int, val lonTo: Int)

/** 한 지점에서 [meters] 안에 들어올 수 있는 칸 범위입니다. */
fun cellRange(lat: Double, lon: Double, meters: Double): CellRange {
    val dLat = ceil(meters / 111_000 * CELL).toInt()
    val dLon = ceil(meters / (111_000 * cos(Math.toRadians(lat))) * CELL).toInt()
    val cellLat = (lat * CELL).toInt()
    val cellLon = (lon * CELL).toInt()
    return CellRange(cellLat - dLat, cellLat + dLat, cellLon - dLon, cellLon + dLon)
}
