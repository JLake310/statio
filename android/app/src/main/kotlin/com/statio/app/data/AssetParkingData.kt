package com.statio.app.data

import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteDatabaseCorruptException
import com.statio.app.core.CellRange
import com.statio.app.core.LotRow
import com.statio.app.core.ParkingData
import com.statio.app.core.PatternCell
import com.statio.app.core.RiskKind
import com.statio.app.core.RiskPoint
import com.statio.app.core.Sql
import java.io.File

/**
 * APK에 들어 있는 statio.db를 앱 저장소로 복사해 읽습니다.
 * 앱을 새로 설치하거나 업데이트하면 다시 복사해서 새 데이터로 바꿉니다.
 * 복사 직후 전원이 꺼져 복사본이 비거나 깨졌으면(시동을 바로 끈 경우 등) 열 때 알아채고 다시 복사합니다.
 */
class AssetParkingData(context: Context) : ParkingData {

    private val appContext = context.applicationContext
    private var db: SQLiteDatabase = open(appContext)

    override fun riskCandidates(range: CellRange): List<RiskPoint> =
        query(Sql.RISK_CANDIDATES, Sql.rangeArgs(range)) { c ->
            val kind = RiskKind.ofIndex(c.getInt(0))
            RiskPoint(kind, c.getLong(1) / Sql.E6, c.getLong(2) / Sql.E6, c.getStringOrNull(3), c.getIntOrNull(4)?.toDouble() ?: kind.radiusM)
        }

    override fun lotCandidates(range: CellRange): List<LotRow> =
        query(Sql.LOT_CANDIDATES, Sql.rangeArgs(range)) { c ->
            LotRow(
                id = c.getString(0), name = c.getString(1), address = c.getStringOrNull(2), category = c.getStringOrNull(3),
                type = c.getStringOrNull(4), total = c.getIntOrNull(5), lat = c.getLong(6) / Sql.E6, lon = c.getLong(7) / Sql.E6,
                basicFee = c.getIntOrNull(8), basicMin = c.getIntOrNull(9), addFee = c.getIntOrNull(10), addMin = c.getIntOrNull(11),
                dayMax = c.getIntOrNull(12), weekdayHours = c.getStringOrNull(13), weekendHours = c.getStringOrNull(14),
                holidayHours = c.getStringOrNull(15), realtimeLinked = c.getInt(16) == 1,
            )
        }

    override fun pattern(lotId: String, dayType: Int, slot: Int): PatternCell? =
        query(Sql.PATTERN, listOf(lotId, dayType, slot)) { c -> PatternCell(c.getInt(0), c.getInt(1)) }.firstOrNull()

    private fun <T> query(sql: String, args: List<Any>, map: (Cursor) -> T): List<T> {
        fun run() = db.rawQuery(sql, args.map { it.toString() }.toTypedArray()).use { c ->
            buildList { while (c.moveToNext()) add(map(c)) }
        }
        return try {
            run()
        } catch (e: SQLiteDatabaseCorruptException) {
            // 읽다가 깨진 곳을 만나면 다시 복사해서 한 번 더 읽어요.
            runCatching { db.close() }
            db = openChecked(prepare(appContext, force = true))
            run()
        }
    }

    private fun Cursor.getStringOrNull(i: Int): String? = if (isNull(i)) null else getString(i)
    private fun Cursor.getIntOrNull(i: Int): Int? = if (isNull(i)) null else getInt(i)

    companion object {
        private const val ASSET = "statio.db"
        private const val PREFS = "asset_db"
        private const val KEY_INSTALLED_AT = "copied_for_update_time"
        private val TABLES = listOf("risk_points", "parking_lots", "patterns")

        /** 복사본을 열고 표가 있는지 확인합니다. 열리지 않거나 표가 없으면 한 번 더 복사해서 엽니다. */
        private fun open(context: Context): SQLiteDatabase =
            runCatching { openChecked(prepare(context, force = false)) }
                .getOrElse { openChecked(prepare(context, force = true)) }

        private fun openChecked(file: File): SQLiteDatabase {
            val db = SQLiteDatabase.openDatabase(file.path, null, SQLiteDatabase.OPEN_READONLY)
            try {
                for (table in TABLES) db.rawQuery("SELECT 1 FROM $table LIMIT 1", null).close()
            } catch (e: Exception) {
                db.close()
                throw e
            }
            return db
        }

        /** 설치·업데이트 시각이 바뀌었거나 복사본 크기가 다를 때 복사합니다. [force]면 그대로 다시 복사해요. */
        private fun prepare(context: Context, force: Boolean): File {
            val target = context.getDatabasePath(ASSET)
            val updatedAt = context.packageManager.getPackageInfo(context.packageName, 0).lastUpdateTime
            val size = context.assets.openFd(ASSET).use { it.length }  // 압축하지 않은 asset이라 크기를 바로 알 수 있어요.
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            if (force || target.length() != size || prefs.getLong(KEY_INSTALLED_AT, -1) != updatedAt) {
                target.parentFile?.mkdirs()
                val tmp = File(target.path + ".tmp")
                context.assets.open(ASSET).use { input ->
                    tmp.outputStream().use { out -> input.copyTo(out); out.fd.sync() }  // 디스크에 다 쓴 뒤에 이름을 바꿔요.
                }
                tmp.renameTo(target)
                prefs.edit().putLong(KEY_INSTALLED_AT, updatedAt).commit()
            }
            return target
        }
    }
}
