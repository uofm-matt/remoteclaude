plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.matt.rclauncher"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.matt.rclauncher"
        minSdk = 29
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
        // Token injected from the RC_TOKEN Actions secret at build time; empty for
        // local builds (the app then asks for it). Never committed — repo is public.
        buildConfigField("String", "RC_TOKEN", "\"${System.getenv("RC_TOKEN") ?: ""}\"")
        // Mac host:port — one source of truth for both activities. Override with RC_HOST;
        // include the scheme (e.g. http://host:8787) — it's used as a URL base verbatim.
        buildConfigField("String", "RC_HOST", "\"${System.getenv("RC_HOST") ?: "http://192.168.1.100:8787"}\"")
    }

    // A stable signing key so CI builds share a signature: sideload can then update
    // in place (adb install -r) instead of uninstall+reinstall, which keeps the
    // home-screen icon and app data. Populated from the KEYSTORE_* Actions secrets;
    // empty for a local build (which just uses the default debug key).
    signingConfigs {
        create("stable") {
            System.getenv("KEYSTORE_FILE")?.let { ks ->
                storeFile = file(ks)
                storePassword = System.getenv("KEYSTORE_PASS")
                keyAlias = System.getenv("KEY_ALIAS")
                keyPassword = System.getenv("KEYSTORE_PASS")
            }
        }
    }

    buildTypes {
        debug {
            if (System.getenv("KEYSTORE_FILE") != null)
                signingConfig = signingConfigs.getByName("stable")
        }
        release {
            isMinifyEnabled = false
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    testImplementation("junit:junit:4.13.2")  // JVM unit tests for the pure upload-resume logic
}
