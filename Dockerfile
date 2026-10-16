# The app factory: Flask dashboard, pipeline worker and Android build host in
# one image, because the build stage runs in-process with the rest of the
# pipeline and needs the same company directories on disk.
#
# Built in two stages so the Android SDK download (~1.5GB of installer and
# intermediate archives) does not sit in the shipped layers.

# ── stage 1: the Android SDK ─────────────────────────────────────────────────
FROM eclipse-temurin:21-jdk-jammy AS android

ARG ANDROID_CMDLINE_TOOLS=11076708_latest
ARG ANDROID_PLATFORM=android-35
ARG ANDROID_BUILD_TOOLS=35.0.0
ENV ANDROID_SDK_ROOT=/opt/android-sdk

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl unzip ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p "${ANDROID_SDK_ROOT}/cmdline-tools" \
 && curl -fsSL -o /tmp/tools.zip \
      "https://dl.google.com/android/repository/commandlinetools-linux-${ANDROID_CMDLINE_TOOLS}.zip" \
 && unzip -q /tmp/tools.zip -d "${ANDROID_SDK_ROOT}/cmdline-tools" \
 && mv "${ANDROID_SDK_ROOT}/cmdline-tools/cmdline-tools" "${ANDROID_SDK_ROOT}/cmdline-tools/latest" \
 && rm /tmp/tools.zip

# yes | ... accepts the SDK licences. They are Google's terms for the build
# tools; review them before shipping this image anywhere.
RUN yes | "${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager" --licenses > /dev/null \
 && "${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager" --install \
      "platform-tools" \
      "platforms;${ANDROID_PLATFORM}" \
      "build-tools;${ANDROID_BUILD_TOOLS}" > /dev/null \
 && rm -rf "${ANDROID_SDK_ROOT}/.temp" "${ANDROID_SDK_ROOT}/emulator" \
           "${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/../../../.downloadIntermediates"

# ── stage 2: the application ─────────────────────────────────────────────────
FROM eclipse-temurin:21-jdk-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ANDROID_SDK_ROOT=/opt/android-sdk \
    ANDROID_HOME=/opt/android-sdk \
    JAVA_HOME=/opt/java/openjdk \
    GRADLE_USER_HOME=/var/cache/gradle \
    PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    PIPELINE_OUTPUT_DIR=/data/pipeline_output

# Ubuntu ships Chromium only as a snap, so there is no `chromium` deb to
# install here; Playwright downloads and manages its own build below.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv \
      postgresql-client \
      ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=android /opt/android-sdk /opt/android-sdk

WORKDIR /app

COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt \
 && pip3 install --no-cache-dir gunicorn

# The DUNS and director ID stages drive a browser. --with-deps pulls the
# shared libraries Chromium needs, which differ by base image and are easy
# to get wrong by hand.
RUN python3 -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY . .

# Warm the Gradle wrapper and dependency cache into the image so the first
# build on a fresh container is not also the first download.
#
# The repository ships only gradlew.bat, so there is no executable wrapper
# script to run on Linux. Drive the wrapper JAR directly, which is what
# apps/generator/toolchain.gradle_command does at build time. Non-fatal: a
# checkout without the template should still produce a usable image.
RUN set -eu; \
    tpl=apps/template-shift-journal; \
    if [ -f "$tpl/gradle/wrapper/gradle-wrapper.jar" ]; then \
      (cd "$tpl" && java -classpath gradle/wrapper/gradle-wrapper.jar \
         org.gradle.wrapper.GradleWrapperMain --no-daemon help > /dev/null 2>&1) \
      || echo "  gradle warm-up skipped (non-fatal)"; \
    fi

RUN useradd --create-home --uid 10001 factory \
 && mkdir -p /data/pipeline_output /var/cache/gradle \
 && chown -R factory:factory /app /data /var/cache/gradle /opt/android-sdk

USER factory

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-5000}/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]
CMD ["serve"]
