import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// 后端地址不进仓库：由本机的 local.properties 注入（已在 .gitignore 里）。
// 没配置就用占位地址；用户在 App 里也可以自己填服务器。
val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}
// 优先级：环境变量 DECK_BASE_URL > local.properties 的 deck.baseUrl > 占位地址
val deckBaseUrl: String = (
    System.getenv("DECK_BASE_URL")?.takeIf { it.isNotBlank() }
        ?: localProperties.getProperty("deck.baseUrl")
        ?: "https://example.com"
    ).trim().trimEnd('/')

android {
    namespace = "com.agentdeck.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.agentdeck.app"
        minSdk = 24
        targetSdk = 35
        versionCode = 4
        versionName = "1.3"

        // 自己部署：改 local.properties 的 deck.baseUrl，或在登录页填服务器地址
        buildConfigField("String", "BASE_URL", "\"$deckBaseUrl\"")
    }

    // 品牌覆盖：app/src/local/ 放私有名字和头像图标（该目录已被 gitignore 忽略），
    // 开源默认 oss；私有包用 local。两者 applicationId 相同，不能同时安装。
    flavorDimensions += "brand"
    productFlavors {
        create("oss") {
            dimension = "brand"
        }
        create("local") {
            dimension = "brand"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    kotlinOptions {
        jvmTarget = "11"
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    packaging {
        resources.excludes += setOf(
            "META-INF/AL2.0",
            "META-INF/LGPL2.1",
            "META-INF/*.kotlin_module"
        )
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)
    implementation(libs.okhttp)
    implementation(libs.coil.compose)
    implementation(libs.coil.svg)
    implementation(libs.jlatexmath)

    testImplementation("junit:junit:4.13.2")
    debugImplementation(libs.androidx.ui.tooling)
}
