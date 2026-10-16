"""
Locating the build toolchain across operating systems.

Every executable the factory shells out to — the Gradle wrapper, keytool,
aapt2, apksigner — is named or launched differently on Windows and Linux.
Resolving them in one place keeps the platform checks out of the build code
and gives a single file to fix when a VPS has its JDK somewhere unusual.

Everything here is resolved from PATH or the standard environment variables
(JAVA_HOME, ANDROID_HOME / ANDROID_SDK_ROOT). No machine-specific paths.
"""
from __future__ import annotations

import os
import shutil
import sys

IS_WINDOWS = sys.platform.startswith("win")


def _exe(name: str) -> str:
    """Windows needs the .exe suffix; POSIX does not."""
    return f"{name}.exe" if IS_WINDOWS else name


def sdk_dir() -> str:
    """The Android SDK root. ANDROID_HOME wins, then ANDROID_SDK_ROOT."""
    return (
        os.environ.get("ANDROID_HOME")
        or os.environ.get("ANDROID_SDK_ROOT")
        or os.path.expanduser("~/Android/Sdk")
    )


def java_home() -> str:
    """
    JAVA_HOME if set, otherwise derived from the java on PATH.

    Returns "" when no JDK can be located, so callers can decide whether that
    is fatal. Never invents a path that does not exist.
    """
    env = os.environ.get("JAVA_HOME", "").strip()
    if env and os.path.isdir(env):
        return env
    java = shutil.which("java")
    if java:
        # <home>/bin/java -> <home>
        return os.path.dirname(os.path.dirname(os.path.realpath(java)))
    return ""


def java_executable() -> str:
    java = shutil.which("java")
    if java:
        return java
    home = java_home()
    if home:
        candidate = os.path.join(home, "bin", _exe("java"))
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        "java not found — install a JDK (17 or newer) or set JAVA_HOME"
    )


def find_keytool() -> str:
    """keytool from PATH, else JAVA_HOME/bin."""
    found = shutil.which("keytool")
    if found:
        return found
    home = java_home()
    if home:
        candidate = os.path.join(home, "bin", _exe("keytool"))
        if os.path.exists(candidate):
            return candidate
    raise FileNotFoundError(
        "keytool not found — install a JDK or set JAVA_HOME to one"
    )


def latest_build_tools() -> str:
    """Newest installed build-tools directory under the SDK."""
    build_tools = os.path.join(sdk_dir(), "build-tools")
    if not os.path.isdir(build_tools):
        raise FileNotFoundError(
            f"missing {build_tools} — install Android SDK build-tools, or set "
            f"ANDROID_HOME to an SDK that has them"
        )
    versions = sorted(os.listdir(build_tools))
    if not versions:
        raise FileNotFoundError(f"no build-tools installed under {build_tools}")
    return os.path.join(build_tools, versions[-1])


def sdk_tool(name: str, build_tools: str | None = None) -> str:
    """
    Path to a build-tools executable.

    apksigner and friends ship as .bat wrappers on Windows and extensionless
    shell scripts elsewhere; aapt2 is a plain binary with a .exe suffix on
    Windows. Both shapes are tried so a renamed SDK layout still resolves.
    """
    base = build_tools or latest_build_tools()
    if IS_WINDOWS:
        candidates = [f"{name}.bat", f"{name}.exe", name]
    else:
        candidates = [name, f"{name}.sh"]
    for candidate in candidates:
        path = os.path.join(base, candidate)
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        f"{name} not found in {base} (tried: {', '.join(candidates)})"
    )


def gradle_command(template_root: str) -> list[str]:
    """
    The argv prefix that runs the Gradle wrapper for this project.

    Prefers a wrapper script when one is present and usable. This repository
    ships only gradlew.bat, so on Linux the wrapper JAR is driven directly —
    which is exactly what the gradlew script does once its shell boilerplate
    has worked out the same classpath and main class.
    """
    if IS_WINDOWS:
        bat = os.path.join(template_root, "gradlew.bat")
        if os.path.exists(bat):
            return [bat]
    else:
        script = os.path.join(template_root, "gradlew")
        if os.path.exists(script) and os.access(script, os.X_OK):
            return [script]

    jar = os.path.join(template_root, "gradle", "wrapper", "gradle-wrapper.jar")
    if not os.path.exists(jar):
        raise FileNotFoundError(
            f"no Gradle wrapper for this platform: neither an executable "
            f"gradlew script nor {jar}"
        )
    return [
        java_executable(),
        "-classpath", jar,
        "org.gradle.wrapper.GradleWrapperMain",
    ]


def describe() -> str:
    """One-line-per-tool summary, for startup logs and troubleshooting."""
    def safe(fn):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            return f"NOT FOUND ({exc})"

    lines = [
        f"  platform     : {sys.platform}",
        f"  java         : {safe(java_executable)}",
        f"  JAVA_HOME    : {java_home() or 'NOT SET'}",
        f"  keytool      : {safe(find_keytool)}",
        f"  Android SDK  : {sdk_dir()}",
        f"  build-tools  : {safe(latest_build_tools)}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print("Build toolchain:")
    print(describe())
