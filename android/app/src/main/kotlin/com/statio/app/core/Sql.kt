package com.statio.app.core

/**
 * 앱 내장 DB(assets/statio.db) 조회문입니다. Android SQLite와 테스트용 JDBC가 같은 문장을 씁니다.
 * DB는 datapipe/export_app_assets.py가 만듭니다. 좌표는 위경도 × 1,000,000 정수입니다.
 */
object Sql {
    const val E6 = 1_000_000.0

    const val RISK_CANDIDATES =
        "SELECT kind, lat_e6, lon_e6, name, radius_m FROM risk_points WHERE cell_lat BETWEEN ? AND ? AND cell_lon BETWEEN ? AND ?"

    const val LOT_CANDIDATES =
        "SELECT id, name, addr, category, type, total, lat_e6, lon_e6, basic_fee, basic_min, add_fee, add_min, day_max, " +
            "weekday_hours, weekend_hours, holiday_hours, realtime FROM parking_lots " +
            "WHERE cell_lat BETWEEN ? AND ? AND cell_lon BETWEEN ? AND ?"

    const val PATTERN = "SELECT free, days FROM patterns WHERE lot_id = ? AND day_type = ? AND slot = ?"

    fun rangeArgs(range: CellRange): List<Int> = listOf(range.latFrom, range.latTo, range.lonFrom, range.lonTo)
}
