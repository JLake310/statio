package com.statio.app

import android.app.Application
import android.util.Log
import com.statio.app.core.ParkingAdvisor
import com.statio.app.core.SpacePredictor
import com.statio.app.data.AssetParkingData
import com.statio.app.data.SeoulLiveSource
import java.time.LocalDateTime
import java.time.ZoneId
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

/** 앱 전체에서 함께 쓰는 판단기와 실시간 데이터입니다. DB 복사와 네트워크는 [worker]에서만 합니다. */
class StatioApp : Application() {

    val live by lazy { SeoulLiveSource(BuildConfig.SEOUL_API_KEY) }

    /**
     * 빈자리 예측 모델(assets/predictor.json, ml/export_model.py가 만들어요)입니다. 없거나 읽지 못하면 예전 방식만 씁니다.
     * 화면에 쓸지는 BuildConfig.AI_PREDICTION_ON_SCREEN이 정하고, 꺼져 있으면 예측값을 로그(StatioAI)에만 남겨요.
     */
    private val predictor by lazy {
        runCatching { SpacePredictor(assets.open("predictor.json").bufferedReader().use { it.readText() }) }
            .onSuccess { Log.i(AI_TAG, "model loaded made=${it.madeAt} lots=${it.lotCount} onScreen=${BuildConfig.AI_PREDICTION_ON_SCREEN}") }
            .onFailure { Log.w(AI_TAG, "model not loaded: ${it.javaClass.simpleName}") }
            .getOrNull()
    }

    /** 처음 쓸 때 앱 DB(약 9MB)를 복사하고 예측 모델을 읽으므로 메인 스레드에서 건드리지 않습니다. */
    val advisor by lazy {
        ParkingAdvisor(AssetParkingData(this), live, predictor, BuildConfig.AI_PREDICTION_ON_SCREEN) { Log.i(AI_TAG, it) }
    }

    val worker: ExecutorService = Executors.newSingleThreadExecutor()

    companion object {
        private val SEOUL = ZoneId.of("Asia/Seoul")
        private const val AI_TAG = "StatioAI"

        /** 단속 시간대 규칙은 한국 시각 기준입니다. 에뮬레이터 시간대와 상관없이 서울 시각을 씁니다. */
        fun nowSeoul(): LocalDateTime = LocalDateTime.now(SEOUL).withNano(0)
    }
}
