plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "org.midimech.app"
    // 36 (not 35) so android:pageSizeCompat is recognized - see AndroidManifest.xml.
    // Chaquopy 17.0's bundled OpenSSL/SQLite _python-suffixed native libraries aren't yet
    // 16KB-page-aligned (a confirmed open upstream issue: github.com/chaquo/chaquopy/issues/1171,
    // #1324 - the _chaquopy stub libraries were fixed, the underlying _python ones weren't as
    // of Chaquopy 17.0, the current latest release), which crashes on real 16KB-page devices.
    // We can't fix or safely hand-patch a prebuilt OpenSSL build ourselves, so this opts into
    // Android 16's OS-level 4KB-page compatibility shim instead.
    compileSdk = 36

    defaultConfig {
        applicationId = "org.midimech.app"
        // android.media.midi needs 23+; Chaquopy 17.0 needs 24+.
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"

        ndk {
            // Chaquopy's Python 3.13 prebuilds only cover these ABIs (no 32-bit ARM).
            abiFilters += listOf("arm64-v8a", "x86_64")
        }

        externalNativeBuild {
            cmake {
                cppFlags += "-std=c++17"
            }
        }
    }

    externalNativeBuild {
        cmake {
            path("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    buildTypes {
        release {
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
}

chaquopy {
    defaultConfig {
        version = "3.13"
        buildPython("${project.rootDir}/../.venv/bin/python3.13")
        pip {
            install("pyyaml")
            install("webcolors")
        }
    }
    sourceSets {
        getByName("main") {
            srcDirs("pysrc")
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
}
