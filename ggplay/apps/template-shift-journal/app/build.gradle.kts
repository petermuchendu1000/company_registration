plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "uk.template.shift"
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

// >>> GENERATED FLAVORS START - managed by apps/generator/generate.py
    val generatedSigning = java.util.Properties()
    val generatedSigningFile = file("generated-signing.properties")
    if (generatedSigningFile.exists()) {
        generatedSigningFile.inputStream().use { generatedSigning.load(it) }
    }

    signingConfigs {
        create("c13510663") {
            storeFile = file("keystores/13510663.jks")
            storePassword = generatedSigning.getProperty("c13510663.storePassword") ?: ""
            keyAlias = "upload"
            keyPassword = generatedSigning.getProperty("c13510663.keyPassword") ?: ""
        }
        create("c02591663") {
            storeFile = file("keystores/02591663.jks")
            storePassword = generatedSigning.getProperty("c02591663.storePassword") ?: ""
            keyAlias = "upload"
            keyPassword = generatedSigning.getProperty("c02591663.keyPassword") ?: ""
        }
    }

    productFlavors {
        create("c13510663") {
            dimension = "brand"
            applicationId = "uk.c13510663.shift"
            buildConfigField("String", "COMPANY_NAME", "\"Swift Plus Personnel\"")
            buildConfigField("String", "COMPANY_NUMBER", "\"13510663\"")
            buildConfigField("String", "SUPPORT_EMAIL", "\"drscholarysophia408@gmail.com\"")
            buildConfigField("String", "ROLE_NOUN", "\"shift\"")
            buildConfigField("String", "ROLE_VERB_START", "\"Start Shift\"")
            buildConfigField("String", "ROLE_VERB_END", "\"End Shift\"")
            buildConfigField("String", "EXPORT_TITLE", "\"Shift Log\"")
            buildConfigField("String", "MODULE_KEYS", "\"work_log|daily_plan|handover|notes|insights\"")
            buildConfigField("String", "MODULE_TITLES", "\"Work Log|Daily Plan|Handover|Notes|Insights\"")
            buildConfigField("String", "MODULE_NAV_LABELS", "\"Log|Plan|Handover|Notes|Insights\"")
            buildConfigField("String", "MODULE_SUMMARIES", "\"Capture local session entries with a clear start and finish flow.|Outline the next few local tasks for the day.|Prepare a concise end-of-session handover note.|Write local notes for handover, visit context, or follow-up reminders.|Summarize local counts and completion status.\"")
            buildConfigField("String", "MODULE_DETAILS", "\"Record each work session as a simple local entry with status, count, and notes.|Keep a short work plan visible so repeated actions stay organized.|Capture what is complete, what remains open, and what needs attention next.|Notes are stored only on the device and can be cleared with app storage.|A small dashboard highlights today's count, open items, and completion ratio.\"")
            buildConfigField("String", "MODULE_PRIMARY_ACTIONS", "\"Start Entry|Add Plan|Prepare|Save Note|Refresh\"")
            buildConfigField("String", "MODULE_SECONDARY_ACTIONS", "\"Close Entry|Review Plan|Complete|Clear Draft|Compare\"")
            buildConfigField("String", "MODULE_METRIC_LABELS", "\"Entries|Planned|Open|Drafts|Score\"")
            buildConfigField("String", "MODULE_SAMPLE_VALUES", "\"3|5|2|1|82%\"")
            signingConfig = signingConfigs.getByName("c13510663")
        }
        create("c02591663") {
            dimension = "brand"
            applicationId = "uk.c02591663.shift"
            buildConfigField("String", "COMPANY_NAME", "\"51 St Margarets Road Mgmt\"")
            buildConfigField("String", "COMPANY_NUMBER", "\"02591663\"")
            buildConfigField("String", "SUPPORT_EMAIL", "\"abdulelahhabib060@gmail.com\"")
            buildConfigField("String", "ROLE_NOUN", "\"visit\"")
            buildConfigField("String", "ROLE_VERB_START", "\"Start Visit\"")
            buildConfigField("String", "ROLE_VERB_END", "\"End Visit\"")
            buildConfigField("String", "EXPORT_TITLE", "\"Site Visit Log\"")
            buildConfigField("String", "MODULE_KEYS", "\"checklist|incident|reference|history|handover\"")
            buildConfigField("String", "MODULE_TITLES", "\"Checklist|Incident Notes|Reference|History|Handover\"")
            buildConfigField("String", "MODULE_NAV_LABELS", "\"Tasks|Incident|Reference|History|Handover\"")
            buildConfigField("String", "MODULE_SUMMARIES", "\"Track routine checks without needing an account or network access.|Capture a private local note when something needs follow-up.|Keep a short local reference panel for repeated work reminders.|Review recent activity in a calm chronological view.|Prepare a concise end-of-session handover note.\"")
            buildConfigField("String", "MODULE_DETAILS", "\"Use a compact checklist for pre-work, handover, site, or delivery checks.|Incident notes are plain local text for internal memory, not reporting automation.|Reference cards help users remember routine steps without leaving the app.|Recent entries make it easier to confirm what happened during the session.|Capture what is complete, what remains open, and what needs attention next.\"")
            buildConfigField("String", "MODULE_PRIMARY_ACTIONS", "\"Mark Done|Add Note|Open Card|Review|Prepare\"")
            buildConfigField("String", "MODULE_SECONDARY_ACTIONS", "\"Reset List|Mark Stable|Review|Pin Item|Complete\"")
            buildConfigField("String", "MODULE_METRIC_LABELS", "\"Done|Open|Cards|Recent|Open\"")
            buildConfigField("String", "MODULE_SAMPLE_VALUES", "\"4/6|0|6|8|2\"")
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
