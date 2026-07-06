plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "uk.company.utility"
    compileSdk = 35

    defaultConfig {
        applicationId = "uk.template.shift"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"

        vectorDrawables {
            useSupportLibrary = true
        }
    }

    flavorDimensions += "brand"

// >>> GENERATED FLAVORS START — managed by apps/generator/generate.py
    signingConfigs {
        create("c15046885") {
            storeFile = file("keystores/15046885.jks")
            storePassword = "474c4992049490b5e1cc76a7430659f4"
            keyAlias = "upload"
            keyPassword = "474c4992049490b5e1cc76a7430659f4"
        }
    }

    productFlavors {
        create("c15046885") {
            dimension = "brand"
            applicationId = "uk.kenukautospares.app"
            versionCode = 1
            versionName = "1.0"
            buildConfigField("String", "COMPANY_NAME", "\"Kenuk Autospares\"")
            buildConfigField("String", "COMPANY_NUMBER", "\"15046885\"")
            buildConfigField("String", "SUPPORT_EMAIL", "\"dev@kenuk-autospares.online\"")
            buildConfigField("String", "COMPANY_DOMAIN", "\"kenuk-autospares.online\"")
            buildConfigField("String", "PRIVACY_POLICY_URL", "\"\"")
            buildConfigField("String", "CONTACT_ADDRESS", "\"7 Upper Ox Hill, Purton, Swindon, SN5 4GG, England\"")
            buildConfigField("String", "ROLE_NOUN", "\"shift\"")
            buildConfigField("String", "ROLE_VERB_START", "\"Start Shift\"")
            buildConfigField("String", "ROLE_VERB_END", "\"End Shift\"")
            buildConfigField("String", "EXPORT_TITLE", "\"Shift Log\"")
            buildConfigField("String", "CALC_TITLE", "\"Hours & Rate Calculator\"")
            buildConfigField("String", "CALC_LABEL_A", "\"Hourly Rate (£)\"")
            buildConfigField("String", "CALC_LABEL_B", "\"Hours\"")
            buildConfigField("String", "CALC_FORMULA", "\"MULTIPLY\"")
            buildConfigField("String", "CALC_RESULT_LABEL", "\"Total Amount\"")
            buildConfigField("String", "INFO_TITLE", "\"Business Reference\"")
            buildConfigField("String", "INFO_ITEMS_JSON", "\"[{\\\"k\\\":\\\"Corporation Tax\\\",\\\"v\\\":\\\"19%\\\\u201325% (2024)\\\"},{\\\"k\\\":\\\"VAT Standard Rate\\\",\\\"v\\\":\\\"20%\\\"},{\\\"k\\\":\\\"VAT Registration\\\",\\\"v\\\":\\\"\\\\u00a390,000 turnover threshold\\\"},{\\\"k\\\":\\\"National Living Wage\\\",\\\"v\\\":\\\"\\\\u00a311.44/hr (25+, Apr 2024)\\\"},{\\\"k\\\":\\\"Annual Investment Allow.\\\",\\\"v\\\":\\\"\\\\u00a31,000,000 / year\\\"},{\\\"k\\\":\\\"Small Business Rates Relief\\\",\\\"v\\\":\\\"100% for RV \\\\u2264 \\\\u00a312,000\\\"},{\\\"k\\\":\\\"Companies House Filing\\\",\\\"v\\\":\\\"Annual confirmation statement\\\"},{\\\"k\\\":\\\"PAYE Registration\\\",\\\"v\\\":\\\"Required before first payroll\\\"}]\"")
            buildConfigField("String", "ACTION_LABEL", "\"Get in Touch\"")
            signingConfig = signingConfigs.getByName("c15046885")
        }
    }
    // <<< GENERATED FLAVORS END

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            ndk {
                debugSymbolLevel = "SYMBOL_TABLE"
            }
        }
        debug {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.androidx.datastore)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
