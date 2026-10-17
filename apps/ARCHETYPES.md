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
archetype-quote/java/uk/company/utility/arch/QuoteArchetype.kt
```

An archetype also chooses which of the shared screens it carries, through
`sharedScreens`. That matters more than it looks: when every app shipped
Calculator, Reference and Contact, three of five screens were identical
across the whole catalog no matter which archetype was selected. A quote
builder that already totals labour, materials and VAT does not get a generic
rate calculator bolted on beside it.

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
4. Add a branch to `plan()` in `ListingScreenshotTest` naming which tabs to
   photograph for the store listing, and seed records for them in `seed()`.
   The screenshots are captured from the running app, so an archetype with
   no branch there publishes another archetype's default set — the policy
   gate blocks that, but only after the build.
5. If the store copy needs to change, extend `short_description` and
   `full_description` in `apps/generator/assets.py`. Describing a mileage log
   as a one-tap timer is a metadata violation as well as a lie.

Nothing else needs editing. `policy_gate.evaluate_template()` picks the new
archetype up automatically and fails the build if the directory is missing,
defines no `activeArchetype()`, or has no interactive screen.

## The three archetypes today

| | `shift` | `mileage` | `quote` |
|---|---|---|---|
| Records | a span of time | a distance | work not yet done |
| Input | start/end tap, free-text note | opening and closing odometer, purpose | line items: description, quantity, unit price |
| Derived | elapsed duration, per-day counts | miles, HMRC approved mileage relief | net, VAT, total, saved pipeline value |
| Arithmetic | none | 45p for the first 10,000 miles of the UK tax year, 25p after, consumed in journey order | line extension and VAT at the standard rate |
| Own tabs | Home (timer), Log | Home (entry + tax-year summary), Journeys by month | Quote builder, Saved quotes |
| Shared tabs | Calc, Info, Contact | Info, Contact | Info, Contact |
| Trades | staffing, care, property, storage | couriers, hauliers, taxis, removals, postal | construction, building trades, cleaning, landscaping |

Both ship zero Android permissions, no network and no account. The
`verify*NoPermissions` Gradle task checks the **merged** manifest of every
variant, so neither archetype can acquire one through a dependency.

HMRC rates are in `archetypes.py` as `AMAP_FIRST_10K_PENCE` /
`AMAP_AFTER_10K_PENCE` / `AMAP_BAND_MILES`. They have been unchanged since
2011-12 but they are a government rate, not a constant of nature; when they
move, change them there and rebuild.

## What this does not fix

- **Two apps in the same archetype with the same trade content are still the
  same app.** The gate now blocks on this (`identical_product`) rather than
  only warning: if two companies share an archetype *and* the same calculator
  formula and reference list, they differ in nothing but colour and name,
  which is exactly what Play's Spam policy names. `archetype_spread` still
  reports the distribution on every run, and the `repetitive_app_factory`
  blocker past two apps is unchanged.
- **A shared screen an archetype opted out of is still in the binary.**
  `MainActivity` references every `SharedScreen` branch statically, so R8
  cannot prove `CalculatorScreen` unreachable in a mileage or quote build.
  The tab is gone and the user never sees it, but the class ships. Moving the
  shared screens into opt-in source directories would fix it properly.
- **Tablet layouts are the phone layout, stretched.** The 10-inch
  screenshots are honest — they show what a tablet user sees — and what they
  show is a single narrow column with a lot of empty space below it. Nothing
  is broken and nothing is misrepresented, but the listing would be stronger
  with a layout that uses the width.
