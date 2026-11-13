plugins {
    id("com.android.application")
}

/**
 * 서울 열린데이터광장 키입니다. 환경변수 SEOUL_OPEN_API_KEY, 없으면 저장소 루트의 .env에서 읽습니다.
 * 키가 없어도 빌드는 되고, 앱은 실시간 빈자리 없이 동작해요. 키는 저장소에 넣지 않아요.
 */
fun envValue(name: String): String {
    System.getenv(name)?.takeIf { it.isNotBlank() }?.let { return it }
    val env = rootProject.file("../.env")
    if (!env.exists()) return ""
    return env.readLines()
        .map { it.trim() }
        .firstOrNull { it.startsWith("$name=") }
        ?.substringAfter("=")?.trim()?.trim('"', '\'')
        .orEmpty()
}

android {
    namespace = "com.statio.app"
    compileSdk = 37
    // AAOS 표준 CarPropertyManager (기어·속도 비교용)
    useLibrary("android.car")

    defaultConfig {
        applicationId = "com.statio.app"
        minSdk = 31
        targetSdk = 34
        versionCode = 13
        versionName = "0.4.0"
        buildConfigField("String", "SEOUL_API_KEY", "\"${envValue("SEOUL_OPEN_API_KEY")}\"")
        // 빈자리 예측 모델 값을 화면에 쓸지입니다. 1차 버전부터 켜요(10/25 결정, 4일치 예비 모델).
        // 끄면 화면은 예전 방식(패턴표 → 추세 → 지금 값)을 쓰고, 모델은 숨김 모드로 돌며 로그(StatioAI)에만 남깁니다(ml/README.md).
        buildConfigField("boolean", "AI_PREDICTION_ON_SCREEN", "true")
    }

    // 제출용 release 서명입니다. 키 파일과 비밀번호는 .env에서 읽고 저장소에는 넣지 않아요.
    // 값이 없으면 release는 서명 없이(app-release-unsigned.apk) 만들어져요.
    signingConfigs {
        create("release") {
            val store = envValue("RELEASE_STORE_FILE")
            if (store.isNotEmpty()) {
                storeFile = rootProject.file("..").resolve(store)  // 저장소 루트 기준, 절대 경로면 그대로
                storePassword = envValue("RELEASE_STORE_PASSWORD")
                keyAlias = envValue("RELEASE_KEY_ALIAS")
                keyPassword = envValue("RELEASE_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            // Pleos SDK가 R8 축소 규칙을 따로 주지 않아서 축소는 끄고 그대로 담아요.
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("release").takeIf { it.storeFile != null }
        }
    }

    buildFeatures {
        buildConfig = true
    }

    // 앱 내장 DB는 압축하지 않아야 설치 후 복사가 빨라요.
    androidResources {
        noCompress += "db"
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("ai.pleos.playground:Vehicle:2.3.2")
    implementation("ai.pleos.playground:NaviHelper:3.0.5")
    // 휴대폰 위치 카드 QR
    implementation("com.google.zxing:core:3.5.3")

    testImplementation("junit:junit:4.13.2")
    // 앱 내장 DB를 JVM 테스트에서 읽고, 정답 사례 JSON을 읽습니다.
    testImplementation("org.xerial:sqlite-jdbc:3.46.1.3")
    testImplementation("org.json:json:20240303")
}
