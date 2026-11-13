package com.statio.app.data

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class SeoulLiveSourceTest {

    @Test fun parsesLinkedLotsOnly() {
        val json = """
            {"GetParkingInfo": {"list_total_count": 122, "RESULT": {"CODE": "INFO-000"}, "row": [
              {"PKLT_CD": "171721", "PRK_STTS_YN": "1", "TPKCT": 1260.0, "NOW_PRK_VHCL_CNT": 152.0, "NOW_PRK_VHCL_UPDT_TM": "2026-10-22 03:17:23"},
              {"PKLT_CD": "999999", "PRK_STTS_YN": "0", "TPKCT": 50.0, "NOW_PRK_VHCL_CNT": 0.0, "NOW_PRK_VHCL_UPDT_TM": ""}
            ]}}
        """.trimIndent()
        val page = SeoulLiveSource.parseParkingInfo(json)
        assertEquals(122, page.total)
        assertEquals(setOf("171721"), page.counts.keys)
        val count = page.counts.getValue("171721")
        assertEquals(1108, count.free)
        assertEquals(LocalDateTime.of(2026, 10, 22, 3, 17, 23), count.updatedAt)
    }

    @Test fun noDataIsEmpty() {
        val page = SeoulLiveSource.parseParkingInfo("""{"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}""")
        assertTrue(page.counts.isEmpty())
    }

    @Test(expected = IllegalStateException::class)
    fun invalidKeyFails() {
        SeoulLiveSource.parseParkingInfo("""{"RESULT": {"CODE": "INFO-100", "MESSAGE": "인증키가 유효하지 않습니다."}}""")
    }
}
