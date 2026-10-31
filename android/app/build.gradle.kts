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

/** 环境变量，两个 flavor 都认（CI / 临时试别的服务器时用）。 */
val envBaseUrl: String? = System.getenv("DECK_BASE_URL")?.takeIf { it.isNotBlank() }

/**
 * 私有包（local）的地址：环境变量 > local.properties > 占位地址。
 *
 * 开源包（oss）**故意不读 local.properties**：这个文件在本机存着真实部署地址，
 * 一旦被烤进要发出去的 APK，等于把服务器域名公开了。开源包要么用环境变量显式指定，
 * 要么就是占位地址，让用户自己在登录页填。
 */
val privateBaseUrl: String = (envBaseUrl ?: localProperties.getProperty("deck.baseUrl") ?: "https://example.com")
    .trim().trimEnd('/')
val openBaseUrl: String = (envBaseUrl ?: "https://example.com").trim().trimEnd('/')

android {
    namespace = "com.agentdeck.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.agentdeck.app"
        minSdk = 24
        targetSdk = 35
        versionCode = 13
        versionName = "1.12"
    }

    // 品牌覆盖：app/src/local/ 放私有名字和头像图标（该目录已被 gitignore 忽略），
    // 开源默认 oss；私有包用 local。两者 applicationId 相同，不能同时安装。
    flavorDimensions += "brand"
    productFlavors {
        create("oss") {
            dimension = "brand"
            // 开源包：绝不读 local.properties
            buildConfigField("String", "BASE_URL", "\"$openBaseUrl\"")
        }
        create("local") {
            dimension = "brand"
            // 私有包：本机配置的地址（改 local.properties 或设 DECK_BASE_URL）
            buildConfigField("String", "BASE_URL", "\"$privateBaseUrl\"")
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
    // 单元测试里用真实 org.json（android.jar 里的是 stub）
    testImplementation("org.json:json:20240303")
    debugImplementation(libs.androidx.ui.tooling)
}
