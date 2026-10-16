# Archetypes — how one template produces different products

## Why this exists

Google Play's Spam policy lists, verbatim, as a violation of Repetitive
Content:

> Creating multiple apps with highly similar functionality, content, and user
> experience.

and

> We don't allow apps that merely provide the same experience as other apps
> already on Google Play. Apps should provide value to users through the
> creation of unique content or services.

A palette and a word list do not answer that. An archetype is meant to: a
different data model, different screens, different arithmetic, different
export. Two companies in the same trade still get near-identical apps, and the
policy gate says so out loud — see **What this does not fix** below.

## The seam

`apps/template-shift-journal/app/src/`

```
main/java/uk/company/utility/
    MainActivity.kt          navigation host — names no archetype
    core/Archetype.kt        the interface: Archetype, FeatureTab
    core/Store.kt            SharedPreferences JSON rows, no network
    core/Format.kt           dates, durations, money, miles
    core/Ui.kt               shared chrome: hero, stat cards, empty states
    screens/                 Calculator, Reference, Contact — every app has these
archetype-shift/java/uk/company/utility/arch/ShiftArchetype.kt
archetype-mileage/java/uk/company/utility/arch/MileageArchetype.kt
```

Each archetype directory defines exactly one

```kotlin
fun activeArchetype(): Archetype
```

`MainActivity` calls `uk.company.utility.arch.activeArchetype()`. It resolves
at compile time because the generator puts exactly one archetype directory on
each flavor's source set:

```kotlin
sourceSets {
    getByName("c02591663") {
        java.srcDir("src/archetype-mileage/java")
    }
}
```

**Why a source directory and not a second flavor dimension.** A second
dimension makes Gradle configure every company × every archetype. At 100
companies and 4 archetypes that is 400 variants to configure for 100 builds.
Adding a directory to the flavor's own source set keeps one dimension and one
variant per company, and the unused archetypes are not compiled, not
minified, and not in the artifact.

Verified on a real build: the shift APK contains
`uk/company/utility/arch/ShiftArchetype` and not `MileageArchetype`; the
mileage APK the reverse. The separation survives R8 in the release bundle.

## Adding an archetype

1. Add an `Archetype(...)` to `apps/generator/archetypes.py` with its `id`,
   `src_dir`, `modules` and `screenshot_screens`, and map its SIC codes in
   `SIC_ARCHETYPE`.
2. If its screens need build-time constants, return them from
   `build_config_fields`. They are emitted only for flavors on that archetype,
   so an unused constant cannot drift away from the code that reads it.
3. Create `app/src/<src_dir>/java/uk/company/utility/arch/` with an object
   implementing `Archetype` and a top-level `activeArchetype()`.
4. If the store copy needs to change, extend `short_description` and
   `full_description` in `apps/generator/assets.py`. Describing a mileage log
   as a one-tap timer is a metadata violation as well as a lie.

Nothing else needs editing. `policy_gate.evaluate_template()` picks the new
archetype up automatically and fails the build if the directory is missing,
defines no `activeArchetype()`, or has no interactive screen.

## The two archetypes today

| | `shift` | `mileage` |
|---|---|---|
| Records | a span of time | a distance |
| Input | start/end tap, free-text note | opening and closing odometer, purpose |
| Derived | elapsed duration, per-day counts | miles, HMRC approved mileage relief |
| Arithmetic | none | 45p for the first 10,000 miles of the UK tax year, 25p after, consumed in journey order |
| Tabs | Home (timer), Log | Home (entry + tax-year summary), Journeys (grouped by month) |
| Trades | staffing, care, property, storage | couriers, hauliers, taxis, removals, postal |

Both ship zero Android permissions, no network and no account. The
`verify*NoPermissions` Gradle task checks the **merged** manifest of every
variant, so neither archetype can acquire one through a dependency.

HMRC rates are in `archetypes.py` as `AMAP_FIRST_10K_PENCE` /
`AMAP_AFTER_10K_PENCE` / `AMAP_BAND_MILES`. They have been unchanged since
2011-12 but they are a government rate, not a constant of nature; when they
move, change them there and rebuild.

## What this does not fix

- **Two apps in the same archetype are still near-identical.** That is the
  honest position and the gate reports it as `archetype_spread` on every run.
  The `repetitive_app_factory` blocker past two apps is unchanged.
- **Screenshots are still synthetic.** `assets.py` draws a mock of the shift
  home screen for every app. The listing now *names* the right screens via
  `selected_modules` and `screenshot_screens`, but the PNGs do not match a
  mileage app yet. Submitting those would be a metadata problem. They need to
  come from a real device or emulator.
- **Signing passwords are still written into `build.gradle.kts`**, which is
  tracked. The gate blocks on `signing_secrets_in_vcs` until that moves to a
  git-ignored properties file or the environment.
