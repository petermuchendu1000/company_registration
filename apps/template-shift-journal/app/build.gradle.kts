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
        create("c02591663") {
            storeFile = file("keystores/02591663.jks")
            storePassword = "81b69ec2d57ac457c45efe79cf380dc1"
            keyAlias = "upload"
            keyPassword = "81b69ec2d57ac457c45efe79cf380dc1"
        }
    }

    productFlavors {
        create("c02591663") {
            dimension = "brand"
            applicationId = "uk.co51stmargaretsroad.app"
            versionCode = 6
            versionName = "1.3"
            buildConfigField("String", "COMPANY_NAME", "\"51 St Margarets Road\"")
            buildConfigField("String", "COMPANY_NUMBER", "\"02591663\"")
            buildConfigField("String", "SUPPORT_EMAIL", "\"dev@51stmargarets.site\"")
            buildConfigField("String", "COMPANY_DOMAIN", "\"51stmargarets.site\"")
            buildConfigField("String", "PRIVACY_POLICY_URL", "\"\"")
            buildConfigField("String", "CONTACT_ADDRESS", "\"Flat 1 St. Margarets Road, Twickenham, TW1 2LL, England\"")
            buildConfigField("String", "ROLE_NOUN", "\"visit\"")
            buildConfigField("String", "ROLE_VERB_START", "\"Start Visit\"")
            buildConfigField("String", "ROLE_VERB_END", "\"End Visit\"")
            buildConfigField("String", "EXPORT_TITLE", "\"Site Visit Log\"")
            buildConfigField("String", "CALC_TITLE", "\"Service Charge Calculator\"")
            buildConfigField("String", "CALC_LABEL_A", "\"Annual Budget (£)\"")
            buildConfigField("String", "CALC_LABEL_B", "\"Number of Units\"")
            buildConfigField("String", "CALC_FORMULA", "\"DIVIDE\"")
            buildConfigField("String", "CALC_RESULT_LABEL", "\"Charge Per Unit\"")
            buildConfigField("String", "INFO_TITLE", "\"Residents Management Reference\"")
            buildConfigField("String", "INFO_ITEMS_JSON", "\"[{\\\"k\\\":\\\"LTA 1985 Section 19\\\",\\\"v\\\":\\\"Charges must be reasonable\\\"},{\\\"k\\\":\\\"Section 20 Threshold\\\",\\\"v\\\":\\\"Consult if works > \\\\u00a3250/unit\\\"},{\\\"k\\\":\\\"Ground Rent Cap\\\",\\\"v\\\":\\\"Peppercorn (Leasehold Reform 2022)\\\"},{\\\"k\\\":\\\"Right to Manage\\\",\\\"v\\\":\\\"50% leaseholder participation\\\"},{\\\"k\\\":\\\"Sinking Fund\\\",\\\"v\\\":\\\"Optional but recommended\\\"},{\\\"k\\\":\\\"Management Company AGM\\\",\\\"v\\\":\\\"Required under articles\\\"},{\\\"k\\\":\\\"Accounts Filing\\\",\\\"v\\\":\\\"9 months after year-end (ltd)\\\"},{\\\"k\\\":\\\"Directors' Duties\\\",\\\"v\\\":\\\"Companies Act 2006 s.172\\\"}]\"")
            buildConfigField("String", "ACTION_LABEL", "\"Contact Management\"")
            signingConfig = signingConfigs.getByName("c02591663")
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
