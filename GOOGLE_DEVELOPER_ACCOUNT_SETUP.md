# Google Play Developer Account — Complete Registration Reference

Exhaustive breakdown of everything required to create a Google Play Console
developer account, for **Personal** accounts first and then **Organization**
accounts, with each field mapped to where our pipeline sources it.

> **Hard rule, unchanged.** This document is a reference for a *human operator*.
> We do not automate Google Play Console or Google Search Console sign-up, sign-in,
> OTP entry or document upload. See `play_console_readiness.py:210`. The pipeline
> prepares data; a person fills the forms.

Verified against Google primary documentation on **15 October 2026**. Google
revises these requirements frequently — re-verify before relying on any date or
threshold. Sources are listed at the end.

---

## 0. Decide the account type FIRST — it is irreversible

> **Once verifications are complete, the account type cannot be changed.**

This is the single most consequential decision in the whole process. Choosing
wrong means abandoning the account and starting over with a new Google account,
a new $25 fee, and a new verification cycle.

| | Personal | Organization |
|---|---|---|
| D-U-N-S number | Not required | **Required** (+ up to ~30 days to obtain) |
| Website | Optional | **Required** (accounts created from Feb 2024) |
| Public display | Legal name, country, developer email | Legal name, legal address, developer email, developer phone |
| Identity docs | Gov photo ID **+ proof of address** | Gov photo ID of authorised rep **+ org registration doc** |
| Extra verification | **Device verification** (real Android device) | **Website verification** |
| Closed-testing gate | **12 testers × 14 days** before production | Not required |
| Setup time | Days | **Weeks** (D-U-N-S is the long pole) |

**Our pipeline is built for Organization accounts.** Companies House → D-U-N-S →
domain → `dev@domain` is precisely the Organization data set. For a Personal
account most of the pipeline is irrelevant — but see §1.7, the closed-testing
requirement is a far heavier operational burden than anything in the Org path.

---

## 1. PERSONAL ACCOUNT

### 1.1 Prerequisites

| Item | Detail |
|---|---|
| Age | 18 or older |
| Google account | Standard consumer Google account |
| 2-Step Verification | Enable **before** starting — retrofitting mid-verification causes lockouts |
| Registration fee | **US$25, one-time** (not recurring) |
| Accepted cards | Mastercard, Visa, American Express, Discover (US only), Visa Electron (outside US only) |
| Rejected cards | **Prepaid cards are not accepted.** Accepted types vary by location |
| Refund | Not stated generally; explicitly **not refunded if identity information is judged invalid** |
| Android device | A real, certified Android device is required for device verification |

### 1.2 Required fields

| Field | Source | Shown publicly? | OTP verified? |
|---|---|---|---|
| **Developer name** | Chosen. May differ from legal name, changeable any time | Yes | No |
| **Legal name** | Pulled from linked Google Payments profile | Yes | Via documents |
| **Legal address** | Pulled from linked Google Payments profile | Country only (full address if monetising) | Via documents |
| **Contact email** | Entered | No | **Yes** |
| **Contact phone** | Entered | No | **Yes** |
| **Developer email** | Entered | **Yes** | **Yes** |
| **Developer website** | Entered | Yes | Optional for personal |

All verified contact points **must remain operational for the life of the
account**. Google periodically checks that the account is reachable by email or
phone. Unreachable → possible block from Play Console.

Regional note: **Korean personal accounts must supply a phone number** or they
are blocked from publishing.

### 1.3 Identity documents — UK

**Government photo ID (one of):**
- Driving licence
- Passport
- Permanent residence card

**Proof of address (one of):**
- Government-issued photo ID showing the address
- Utility bill (electricity, water, gas, internet, cable TV)
- Insurance statement (home, health, etc.)
- Credit card or bank statement

**Proof of address rules:**
- Full name must match the payments profile **exactly**
- Address must match the payments profile **exactly**
- Must be **dated within the last 90 days**

### 1.4 Document format rules (apply to both account types)

- Valid and **not expired**
- **Colour** image — black and white is rejected
- Clear and well lit
- **Not a photocopy**
- **Screenshots are not accepted**

**EEA / UK / Guernsey specific:** no data on the ID may be blocked or redacted.
Google attributes this to the EU Anti-Money Laundering Directive, which binds
Google Payments Ireland Ltd. Redacting your document number "for safety" is a
guaranteed rejection here.

> Google states plainly: **submitting unsupported documents is the primary
> reason for developer verification failures.** Modified or fake documents lead
> to immediate removal of the account and all its apps.

### 1.5 Payments profile and payment method

1. Link a Google Payments profile — new or existing. Holds legal name + address.
2. Submitted documents must match that profile exactly. Mismatch → you can update
   the profile during the verification task.
3. To monetise, add a payment method (**account owner only**): name on the bank
   account, sort code, account number.
4. Verification is either:
   - **Deposit challenge** — small deposit sent to the bank account, confirm the
     exact amount in Play Console, or
   - **Official bank document upload** — if a deposit challenge cannot be issued.
5. Payment method verification takes **up to 5 days**.

### 1.6 Device verification (new personal accounts)

Verify access to a **real Android device** using the Play Console mobile app.
Required **before you can make any app available**. An emulator does not satisfy
this. In a VPS-hosted model this is unavoidably client-side — budget for it.

### 1.7 Closed testing gate — the big one

Applies to **personal accounts created after 13 November 2023**:

- **At least 12 testers** opted in to a closed test when you apply for production
- Each must have been **continuously opted in for the preceding 14 days**
- The closed test must run **at least 14 days**
- Testers who opt in, test briefly and opt out **do not count**
- Opt out then back in → the 14 days must restart and be **consecutive**

**Applying for production access** — Dashboard → "Apply for production". Three sections:
1. *About your closed test* — ease of recruiting testers, tester engagement, feedback collected and how
2. *About your app/game* — target audience, app value, estimated first-year install range
3. *About your production readiness* — changes made from testing feedback, how readiness was determined

Save with "Next" per section, then "Apply". **"Discard" or navigating away loses
unsaved work.** Review is typically **within 7 days**, sometimes longer.
Insufficient testers or weak engagement → continue testing and reapply.

> **Operational reality:** 12 real testers × 14 consecutive days × every app is a
> serious recurring cost at factory scale. This alone is the strongest argument
> for the Organization path.

### 1.8 Personal account sequence

```
1.  Enable 2SV on the Google account
2.  Create/link Google Payments profile (legal name + address)
3.  Pay US$25 registration fee
4.  Submit government photo ID
5.  Submit proof of address (≤ 90 days old, exact match)
6.  Verify contact email       (6-digit OTP)
7.  Verify contact phone       (6-digit OTP, text or voice)
8.  Verify developer email     (6-digit OTP)
9.  Device verification        (Play Console app on real Android device)
10. Create app, complete app setup
11. Run closed test — 12+ testers, 14+ consecutive days
12. Apply for production access  (~7 day review)
```

---

## 2. ORGANIZATION ACCOUNT

### 2.1 Prerequisites

| Item | Detail |
|---|---|
| Legal entity | Any formal business entity |
| **D-U-N-S number** | **Mandatory.** Free from Dun & Bradstreet. **Up to ~30 days** |
| Google account | Standard Google account (becomes account owner) |
| Registration fee | **US$25, one-time** — same card rules as §1.1 |
| Website | **Required** for organization accounts created from February 2024 |

**On the D-U-N-S number:**
- Check whether the organisation **already has one** before applying — many do
- Applying is **free**. Google explicitly warns about third parties charging high
  administrative fees for it
- Large organisations may hold **several** — use the one carrying the exact entity
  details you want bound to the developer account
- You have a **limited number of attempts** to enter the 9-digit number correctly
- Without one you **cannot** create an organization account. Only organisations in
  regions Dun & Bradstreet does not support may request an alternative method.
  Google states it will **not grant exemptions or extensions**
- Government bodies may verify without one — contact support only if genuinely unable
- Google's help centre says organisations do **not** submit Dun & Bradstreet-issued
  documentation, only the number itself. Google's policy blog does say official
  organisation documents may be requested, so prepare for both

### 2.2 Required fields

| Field | Source | Shown publicly? | OTP verified? |
|---|---|---|---|
| **Developer name** | Chosen. May differ from legal name | Yes | No |
| **D-U-N-S number** | From linked Google Payments profile | No | Via D&B match |
| **Organization name** | From linked Google Payments profile | Yes | Via documents |
| **Organization address** | From linked Google Payments profile | Yes | Via documents |
| **Organization phone** | Entered — used for verification | No | Yes |
| **Organization website** | Entered | — | **Website verification** |
| **Contact name** | Entered | No | No |
| **Contact email** | Entered | No | **Yes** |
| **Contact phone** | Entered | No | **Yes** |
| **Developer email** | Entered | **Yes** | **Yes** |
| **Developer phone** | Entered | **Yes** | **Yes** |

**Four separate OTP verifications** for organizations (contact email, contact
phone, developer email, developer phone) versus three for personal.

**Field quality rules Google states explicitly:**
- **Organization phone** — should be a number users would plausibly find for the
  organisation on the web or in a public registry such as Dun & Bradstreet. A
  random mobile number is a red flag
- **Contact email** — must **not be generic or a personal address**; should be
  associated with the organisation. Google recommends it differ from the account
  creation address, especially with multiple managers
- **Developer email** — should be an **official organisation address**, not
  generic or personal. This one is shown publicly on Google Play

### 2.3 Identity documents — UK

**Organisation registration document (one of):**
- Certificate of incorporation
- VAT registration certificate
- Certificate of registration of charity
- *Government departments and agencies only:* official letter showing full name, address and date

**Authorised representative's government photo ID (one of):**
- Passport
- Identification card
- Driving licence
- Permanent residence card

An **authorised representative** is someone with legal authority to represent the
organisation — CEO, director, or legal representative. **The ID may be issued in
any country** — the representative does not need to be a UK national.

All format rules from §1.4 apply identically.

### 2.4 The exact-match requirement — most common failure

The Google Payments profile legal name and address must match the **Dun &
Bradstreet profile** exactly, and submitted documents must match the payments
profile exactly. Three records, one string:

```
Companies House  →  D&B (D-U-N-S)  →  Google Payments profile
      must all carry the identical legal name and address
```

On mismatch Google shows a banner and sends email with a deadline. **If not
corrected by that deadline, the developer account may be restricted — which
removes all of its apps from Google Play.**

Our pipeline already captures `D&B Legal Name` and `D&B Address` separately from
the Companies House `Company Name` and `Address` precisely so this can be
diffed before a human ever opens the form. Treat any difference — including
"Ltd" vs "Limited", punctuation, or abbreviated street types — as a blocker.

### 2.5 Organization account sequence

```
1.  Obtain / confirm D-U-N-S number            ← up to ~30 days, START HERE
2.  Verify D&B legal name + address are correct and current
3.  Create/link Google Payments profile — must match D&B exactly
4.  Enter the 9-digit D-U-N-S number           (limited attempts!)
5.  Pay US$25 registration fee
6.  Submit organisation registration document  (cert. of incorporation / VAT cert.)
7.  Submit authorised representative's photo ID
8.  Complete identity verification
9.  Website verification                        (org website required)
10. Verify contact email      (6-digit OTP)
11. Verify contact phone      (6-digit OTP, international format, text or voice)
12. Verify developer email    (6-digit OTP)
13. Verify developer phone    (6-digit OTP)
14. Add + verify payment method if monetising   (deposit challenge, up to 5 days)
15. Create and publish apps — no closed-testing gate
```

Note the ordering constraint: **identity verification must complete before
website/device verification**, which must complete before the developer phone can
be verified. These cannot be parallelised.

---

## 3. Field → pipeline source mapping

How each Google Organization field is populated today:

| Google field | Pipeline source | DB column | Status |
|---|---|---|---|
| Organization name | Companies House | `company_name` | **Auto** |
| Organization address | Companies House | `address` | **Auto** |
| D-U-N-S number | D&B via `duns_automation.py` | `duns_number` | **Auto** |
| D&B legal name (match check) | D&B lookup | `dnb_legal_name` | **Auto** |
| D&B address (match check) | D&B lookup | `dnb_address` | **Auto** |
| Organization website | Namecheap registration | `domain` | **Auto** |
| Developer email | `dev@{domain}` | `developer_email` | **Auto** |
| Email forwarding → Gmail | Namecheap | `dev_email_forwarding` | **Auto** |
| Contact name | Companies House director | `directors` | **Auto** |
| Account owner Gmail | `emails.xlsx` | `assigned_email`, `account_name` | **Auto** |
| Registration document | Companies House certificate | `certificate_path` | **Auto** |
| Organization phone | Optional phone file | `organization_phone` | **Semi** |
| Google TXT token | Human copies from Google screen | `google_txt_status` | **Manual** |
| Representative photo ID | Client upload | — | **Manual** |
| Payment card | Human | — | **Manual** |
| All OTP codes (×4) | Human | — | **Manual** |
| Form submission | Human | — | **Manual** |

`play_signup_missing` is the existing gap tracker — it should assert every
**Auto** row is non-empty and every match check in §2.4 passes before a dossier
is handed to a client.

---

## 4. Rejection and failure modes

| Cause | Consequence |
|---|---|
| Unsupported document type | **Google's stated #1 cause of verification failure** |
| Black-and-white, photocopied, or screenshot ID | Rejected |
| Expired ID | Rejected |
| Redacted/blocked data on ID (EEA/UK/Guernsey) | Rejected — AML rules |
| Proof of address older than 90 days | Rejected |
| Name/address mismatch across CH / D&B / Payments | Banner + deadline → **account restricted, all apps removed** |
| Generic or personal contact email on an Org account | Flagged |
| D-U-N-S entered wrong too many times | Attempts exhausted — support escalation |
| Identity judged invalid | **$25 fee not refunded** |
| Modified or fake documents | **Immediate removal of account and all apps** |
| Unreachable contact details | May be blocked from Play Console |
| Required info missing when it becomes mandatory | May be blocked from Play Console |

**Timings to plan around:**

| Step | Duration |
|---|---|
| D-U-N-S number | **Up to ~30 days** |
| Payment method verification | Up to 5 days |
| Production access review (personal) | ~7 days, sometimes longer |
| Closed testing (personal) | **14 days minimum**, 12 testers |
| Verification window | Can start up to 60 days before an individual deadline |

**Critical path for an Organization account is the D-U-N-S number.** Everything
else is days; that is weeks. Our D&B automation exists to attack exactly this
bottleneck.

---

## 5. Separate programme: Android Developer Verification

Do not confuse this with Play Console account verification. It is a **different
programme** with its own registration, covering apps installed on certified
Android devices regardless of which store they come from.

- **Enforcement began 30 September 2026** in **Indonesia, Singapore and Thailand**,
  initially covering installs from Google Play, HONOR App Market, OPPO App Market,
  Galaxy Store, Palm Store, V-Appstore and GetApps
- **Global expansion planned for 2027**, all apps on certified devices
- Verified developers must **register the apps** they distribute
- Verification confirms **who the developer is** — it does not review app content
- Unverified by the deadline → apps **unavailable for new installation** on
  certified devices in applicable countries

**Account types:**
- **Full distribution** — $25 fee + government ID; organisations also need a
  D-U-N-S number (free, stated as up to 28 days on this page)
- **Limited distribution** — free, for students/hobbyists/small closed groups;
  unlimited apps on **up to 20 devices**, no government ID

Sideloading remains supported; power users can install unverified apps via an
advanced flow after a one-time setup, and ADB still works for development.

**Action for us:** Google has said most Play developers were verified
automatically and >99% of apps registered. Each account's verification status
should be checked in Play Console, and any app not auto-registered registered
manually. Add this as a post-creation checklist item — it is not covered by the
account-creation flow above, and the 2027 global rollout will make it apply to
every app we ship.

---

## 6. Open items to confirm per-client

- [ ] Account type decision recorded **before** any verification starts (irreversible)
- [ ] D-U-N-S legal name/address diffed against Companies House — byte-exact
- [ ] Organisation phone is plausibly discoverable publicly
- [ ] Developer email is `dev@{domain}`, not a Gmail address
- [ ] Representative ID is colour, unexpired, unredacted, not a photocopy
- [ ] Certificate of incorporation legible and matching
- [ ] 2SV enabled on the Google account before starting
- [ ] Android Developer Verification status checked post-creation

---

## Sources

All retrieved 15 October 2026.

- [Required information to create a Play Console developer account](https://support.google.com/googleplay/android-developer/answer/13628312?hl=en)
- [Verify your developer identity information](https://support.google.com/googleplay/android-developer/answer/10841920?hl=en)
- [Contact information requirements for developer accounts](https://support.google.com/googleplay/android-developer/answer/10840893?hl=en)
- [Documents required for verification (UK)](https://support.google.com/googleplay/android-developer/answer/15633622?hl=en-GB&gl=GB)
- [Developer account registration fee](https://support.google.com/googleplay/android-developer/answer/6112435?hl=en)
- [Closed testing requirements for production access](https://support.google.com/googleplay/android-developer/answer/14151465?hl=en)
- [Understanding Android developer verification](https://support.google.com/android-developer-console/answer/16561738?hl=en)
- [New policy update to boost trust and transparency on Google Play](https://android-developers.googleblog.com/2023/07/boosting-trust-and-transparency-in-google-play.html)
