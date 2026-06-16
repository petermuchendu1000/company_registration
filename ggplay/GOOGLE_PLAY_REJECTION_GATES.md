# Google Play Rejection Gates

This document records the local gates we enforce before any human submits an
app to Google Play. The pipeline cannot guarantee approval, but it must fail
closed on known rejection risks.

## Rejection Risks We Must Treat As Blockers

### 1. Spam, Repetitive Content, And Minimum Functionality

Risk: A large factory of near-identical white-label apps can be interpreted as
repetitive or low-quality, even if each app technically builds.

Local gate:

```powershell
python -m apps.generator.policy_gate --count 2 --offset 0
```

The gate blocks scaling past two apps unless explicitly overridden. Do not use
the override for Play submission readiness.

### 2. Broken Functionality

Risk: Apps that do not install, do not load, crash, freeze, or are unresponsive
can be rejected.

Local gates:

```powershell
python -m apps.generator.generate --count 2 --offset 0 --artifact both --sdk "C:\Users\LENOVO\AppData\Local\Android\Sdk"
python -m apps.generator.verify --sdk "C:\Users\LENOVO\AppData\Local\Android\Sdk" pipeline_output\apps\13510663\swift-plus-personnel-release.apk pipeline_output\apps\02591663\51-st-margarets-road-managemen-release.apk
```

Before submission, also install on emulator/device and confirm every screen
loads and responds.

### 3. Metadata And Store Listing Quality

Risk: Misleading, thin, badly formatted, or non-descriptive listing metadata can
be rejected.

Local gate checks:

- App title is present and 30 characters or less.
- Short description is present and 80 characters or less.
- Full description explains actual functionality and privacy.
- Icon, screenshots, and feature graphic exist.
- Listing does not use placeholders for developer contact fields.

### 4. User Data, Privacy Policy, And Data Safety

Risk: Missing, inaccessible, inaccurate, or inconsistent privacy/data-safety
claims can be rejected.

Local gate checks:

- `privacy_policy_url` must be a real public HTTPS URL.
- Privacy policy must match the app name/developer.
- App code declares zero permissions.
- Data safety says no collection/no sharing only because the app has no network,
  no ads, no analytics, no login, and no sensitive permissions.

### 5. Target API And Publishing Format

Risk: Google blocks submissions that miss target API or publishing artifact
requirements.

Local gate checks:

- `targetSdk` is 35 or higher.
- Signed AAB exists for every app.
- APK verification succeeds for local install testing.

## Current Two-App Status

Technical artifact gate:

```text
PASS: 2/2 APK+AAB artifacts generated.
PASS: 2/2 APKs verified as signed, targetSdk 35.
```

Policy gate:

```text
FAIL: privacy_policy_url is still a placeholder.
FAIL: developer_email is still Gmail, not dev@registered-domain.
FAIL: developer_phone is still missing.
```

These are correct blockers. Do not submit until they are resolved.

## Official References

- Google Play Developer Program Policy: Spam, Functionality, and User Experience.
- Google Play Metadata policy.
- Google Play User Data policy and privacy policy requirements.
- Google Play Data safety form requirements.
- Google Play target API level requirements.
- Google Play app bundle publishing requirements.
