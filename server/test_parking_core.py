"""parking_core 단위 테스트입니다.

    python3 -m unittest discover -s server
"""

import datetime
import pathlib
import sqlite3
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import parking_core as core  # noqa: E402

NOW = datetime.datetime(2026, 10, 22, 14, 0)  # 목요일 14:00


def at(days_ago, hour, minute):
    d = NOW - datetime.timedelta(days=days_ago)
    return d.replace(hour=hour, minute=minute)


class FeeTest(unittest.TestCase):
    def test_basic_and_additional_units(self):
        # 5분 430원, 추가 5분 430원 → 1시간 5,160원, 10분 860원
        self.assertEqual(core.fee_for(60, 430, 5, 430, 5), 5160)
        self.assertEqual(core.fee_for(10, 430, 5, 430, 5), 860)

    def test_within_basic_time(self):
        self.assertEqual(core.fee_for(20, 1000, 30, 500, 10), 1000)

    def test_day_max_caps_fee(self):
        self.assertEqual(core.fee_for(600, 430, 5, 430, 5, day_max=20000), 20000)

    def test_missing_fee_info(self):
        self.assertIsNone(core.fee_for(60, None, 5, 430, 5))
        self.assertIsNone(core.fee_for(60, 430, 0, 430, 5))


class PredictTest(unittest.TestCase):
    def test_pattern_uses_same_time_on_past_weekdays(self):
        # 지난 목·수·화(평일) 14:15 무렵 빈자리 10, 12, 14 → 중앙값 12
        history = [(at(1, 14, 15), 100, 88), (at(2, 14, 10), 100, 90), (at(7, 14, 20), 100, 86)]
        result = core.predict_free(history, NOW, 15, current_free=50, total=100)
        self.assertEqual(result, {"free": 12, "basis": "pattern", "days": 3})

    def test_pattern_ignores_weekends_for_weekday_target(self):
        saturday = (NOW - datetime.timedelta(days=5)).replace(hour=14, minute=15)  # 토요일
        history = [(at(1, 14, 15), 100, 88), (at(2, 14, 10), 100, 90), (saturday, 100, 0)]
        result = core.predict_free(history, NOW, 15, current_free=50, total=100)
        self.assertNotEqual(result["basis"], "pattern")

    def test_trend_when_history_is_short(self):
        # 최근 20분 동안 빈자리 40 → 30 (분당 -0.5) → 10분 뒤 25
        history = [(NOW - datetime.timedelta(minutes=20), 100, 60),
                   (NOW - datetime.timedelta(minutes=10), 100, 65),
                   (NOW, 100, 70)]
        result = core.predict_free(history, NOW, 10, current_free=30, total=100)
        self.assertEqual(result, {"free": 25, "basis": "trend", "days": 0})

    def test_prediction_is_clamped_to_capacity(self):
        history = [(NOW - datetime.timedelta(minutes=20), 100, 10),
                   (NOW - datetime.timedelta(minutes=10), 100, 50),
                   (NOW, 100, 90)]
        result = core.predict_free(history, NOW, 60, current_free=10, total=100)
        self.assertEqual(result["free"], 0)

    def test_current_when_no_history(self):
        self.assertEqual(core.predict_free([], NOW, 15, current_free=7, total=20),
                         {"free": 7, "basis": "current", "days": 0})


class RiskLevelTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(core.risk_level([]), "none")
        self.assertEqual(core.risk_level([{"active_now": False}]), "caution")
        self.assertEqual(core.risk_level([{"active_now": False}, {"active_now": True}]), "high")


class RiskTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("CREATE TABLE risk_points (kind TEXT, name TEXT, lat REAL, lon REAL, radius_m REAL, rule TEXT,"
                          " cell_lat INTEGER, cell_lon INTEGER)")

    def add(self, kind, name, lat, lon, radius):
        self.conn.execute("INSERT INTO risk_points VALUES (?, ?, ?, ?, ?, '', ?, ?)",
                          (kind, name, lat, lon, radius, int(lat * core.CELL), int(lon * core.CELL)))

    def north(self, meters):
        return 37.5 + meters / 111_195.0

    def test_intersection_uses_its_own_radius(self):
        # 큰 교차로(반경 30m)는 33m에서도 걸리고(여유 5m 포함), 작은 교차로(반경 10m)는 걸리지 않아요.
        self.add("intersection", "큰 교차로", 37.5, 127.0, 30)
        self.add("intersection", "작은 교차로", self.north(80), 127.0, 10)
        found = core.find_risks(self.conn, self.north(33), 127.0, 5, NOW)
        self.assertEqual([(r["kind"], r["name"], r["fine_krw_estimate"]) for r in found], [("intersection", "큰 교차로", 40000)])
        self.assertEqual(core.find_risks(self.conn, self.north(36), 127.0, 5, NOW), [])
        self.assertEqual(core.find_risks(self.conn, self.north(64), 127.0, 5, NOW), [])

    def test_intersection_comes_after_bus_stop_before_school_zone(self):
        for kind, radius in (("school_zone", 100), ("intersection", 20), ("bus_stop", 10)):
            self.add(kind, kind, 37.5, 127.0, radius)
        found = core.find_risks(self.conn, 37.5, 127.0, 5, NOW)
        self.assertEqual([r["kind"] for r in found], ["bus_stop", "intersection", "school_zone"])
        self.assertTrue(all(r["active_now"] for r in found))  # 목요일 14:00이라 어린이보호구역도 대상

    def test_hotspot_comes_right_after_cameras_before_legal_zones(self):
        for kind, radius in (("crosswalk", 10), ("hotspot", 30), ("intersection", 20), ("cctv", 50)):
            self.add(kind, kind, 37.5, 127.0, radius)
        found = core.find_risks(self.conn, 37.5, 127.0, 5, NOW)
        self.assertEqual([r["kind"] for r in found], ["cctv", "hotspot", "crosswalk", "intersection"])
        self.assertEqual(found[1]["fine_krw_estimate"], 40000)


class FrozenLotTest(unittest.TestCase):
    def test_lot_that_never_changes_is_frozen(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE snapshots (pklt_cd TEXT, collected_at TEXT, total INTEGER, parked INTEGER)")
        start = datetime.datetime(2026, 10, 22)
        for i in range(6 * 288):  # 6일, 5분 간격
            t = (start + datetime.timedelta(minutes=5 * i)).strftime(core.TIME_FORMAT)
            conn.execute("INSERT INTO snapshots VALUES ('stuck', ?, 56, 56)", (t,))
            conn.execute("INSERT INTO snapshots VALUES ('busy', ?, 100, ?)", (t, i % 50))
            conn.execute("INSERT INTO snapshots VALUES ('slow', ?, 6, ?)", (t, 3 + (i // 300) % 2))  # 6일에 5번 바뀜
        self.assertEqual(core.frozen_lots(conn), {"stuck", "slow"})


class LotOrderTest(unittest.TestCase):
    def test_free_then_unknown_then_full(self):
        lots = [
            {"id": "full", "distance_m": 50, "realtime": {"free": 0}},
            {"id": "unknown", "distance_m": 100, "realtime": None},
            {"id": "free", "distance_m": 300, "realtime": {"free": 5}},
        ]
        self.assertEqual([core.lot_tier(l) for l in lots], [2, 1, 0])
        lots.sort(key=lambda l: (core.lot_tier(l), l["distance_m"], l["id"]))
        self.assertEqual([l["id"] for l in lots], ["free", "unknown", "full"])


class DistanceTest(unittest.TestCase):
    def test_short_distance(self):
        # 위도 0.001도 ≈ 111m
        self.assertAlmostEqual(core.distance_m(37.5, 127.0, 37.501, 127.0), 111.2, delta=0.5)


if __name__ == "__main__":
    unittest.main()
