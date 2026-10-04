# Responsive dashboard and portable application details

## Changes

- A short header with a Google Sheet shortcut and a More actions menu replaces the crowded action row.
- Dashboard statistics stay on Home. Jobs goes directly to search, status, categories and results.
- Listings use collapsible context/actions, two main buttons and 12 results per page. Search, programme, domain and application status filters combine; sorting and filtering reset the page correctly.
- Compact view defaults on phones/tablets and can be changed, with the preference saved per browser.
- Long titles, badges, buttons, grids and form fields can wrap/shrink. Browser zoom is enabled. Keyboard focus indicators, input labels, dialog focus containment and reduced-motion support are included.
- Open & fill is shown only when a connected helper supports the destination. Other browsers/sites offer Saved details: copy individual answers, open the employer form and download owned CV/cover-letter files. No application is submitted by these controls.
- A grouped /profile editor uses the existing profile and upload endpoints. Existing answers and UK-format dates are preserved. Private documents are restricted to the upload directory; profile pages, bundles and downloads have no permissive cross-origin header and are not cached.
- Legacy application-agent and discovery-report tables also adapt to small screens.

## Verification

Browser checks using isolated sample data:

- Home, Jobs, Settings, Status and Closed: 320×740, 390×844, 768×1024, 1024×768, 1440×960. No horizontal overflow or application-script errors.
- Profile, legacy application agent and discovery report: 320×740. No horizontal overflow.
- Long company/title/location strings at 320px, expanded categories and listing context, pagination, sorting, no-results/reset, compact toggle, mobile add-job dialog, saved-answer copying and profile save/reload.
- Python tests cover private document downloads, directory/symlink restrictions, document metadata-only bundles, legacy graduation dates and unusual saved eligibility answers.
- Existing browser-helper tests still check destination identity, permission boundaries and leaving unknown fields blank.

## Android Chrome limitation

Chrome's mobile extension action installs on desktop, rather than running a Safari/desktop helper on Android: [Google Chrome documentation](https://support.google.com/chrome/answer/2664769?hl=en). A normal tracker page cannot write into another website's tab. Android Chrome therefore uses the explicit copy/download workflow. Full automatic filling there would require a separate supported browser or Android integration. This change does not claim that it provides automatic Android Chrome filling.

Physical Android hardware was not available for testing; viewport and workflow checks used the in-app browser, plus live deployment checks.
