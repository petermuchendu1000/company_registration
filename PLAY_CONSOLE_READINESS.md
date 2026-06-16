# Google Play Console Readiness Pipeline

This pipeline prepares a human-fillable Google Play Console organization signup
dossier. It must not automate Google Play Console or Google Search Console.

## Data Sources

- Companies House: legal company name, company number, status, address, SIC,
  directors, and incorporation certificate.
- D&B: D-U-N-S number plus D&B legal name/address when the lookup response
  provides it.
- `emails.xlsx`: Gmail owner/login for the Play Console account.
- Optional phone file: physical organization/contact numbers.
- Namecheap: domain registration, existing account contact profile, DNS TXT
  records, and `dev@domain` email forwarding.

## Optional Phone File

Use any one of these local files:

- `physical_numbers.json`
- `phone_numbers.json`
- `physical_numbers.xlsx`
- `phone_numbers.xlsx`
- `phones.xlsx`

JSON may be:

```json
{
  "13510663": "+441234567890",
  "owner@gmail.com": "+441234567891"
}
```

Spreadsheet headers may include:

- `Company Number`
- `Phone Number`
- `Gmail` or `Email`

## Domain And Email Rules

- Developer email is always `dev@{registered_domain}`.
- Namecheap forwarding is configured as `dev@domain -> Gmail owner`.
- Forwarding can receive verification/user emails.
- Forwarding cannot send as `dev@domain`; use a mailbox provider if sending is
  needed later.
- Existing forwarding aliases are preserved where Namecheap returns them.

## Google TXT Rule

The pipeline does not get tokens from Google. A human must copy the TXT token
from the Google screen. Then run the pipeline with:

```powershell
python run_pipeline.py --refill --google-txt-token "google-site-verification=..."
```

Without a token, the dossier records `token_pending`.

## Dossier-Only Mode

To regenerate the workbook readiness sheet and Markdown/JSON dossier without
calling live APIs:

```powershell
python run_pipeline.py --play-dossier-only
```

Outputs:

- `pipeline_output/companies_pipeline.xlsx`
- `pipeline_output/play_console_dossiers/play_console_dossier_*.md`
- `pipeline_output/play_console_dossiers/play_console_readiness_*.json`

## Manual Fields Still Required

- Director/authorized representative ID document.
- Google account recovery/2FA confirmation.
- Physical phone OTP confirmation.
- Google Payments profile and payment card.
- Google TXT token copied by a human.
- Final Play Console form submission by a human.
