# Responsive dashboard and portable application details

## Changes

- A short header with a Google Sheet shortcut and a More actions menu replaces the crowded action row.
- Dashboard statistics are visible on every desktop dashboard tab. On phone-sized screens they stay on Home to keep job browsing short.
- Listings use collapsible context/actions, two main buttons and 12 results per page. Search, programme, domain and application status filters combine; sorting and filtering reset the page correctly.
- Job density adapts automatically through CSS as the viewport changes. There is no compact-view control or stored density preference.
- Long titles, badges, buttons, grids and form fields can wrap/shrink. Browser zoom is enabled. Keyboard focus indicators, input labels, dialog focus containment and reduced-motion support are included.
- Open & fill is shown only when a connected helper supports the destination. Other browsers/sites use Open listing; there is no copy-and-paste fallback. No application is submitted by these controls.
- A grouped /profile editor uses the existing profile and upload endpoints. Existing answers and UK-format dates are preserved. Private documents are restricted to the upload directory; profile pages, bundles and downloads have no permissive cross-origin header and are not cached.
- Legacy application-agent and discovery-report tables also adapt to small screens.

## Verification

Browser checks using isolated sample data:

- Home, Jobs, Settings, Status and Closed: 320×740, 390×844, 768×1024, 1024×768, 1440×960. No horizontal overflow or application-script errors.
- Profile, legacy application agent and discovery report: 320×740. No horizontal overflow.
- Long company/title/location strings at 320px, expanded categories and listing context, pagination, sorting, no-results/reset, automatic phone/tablet density, mobile add-job dialog, saved-answer copying and profile save/reload.
- Python tests cover private document downloads, directory/symlink restrictions, document metadata-only bundles, legacy graduation dates and unusual saved eligibility answers.
- Existing browser-helper tests still check destination identity, permission boundaries and leaving unknown fields blank.

## Android Chrome limitation

Chrome's mobile extension action installs on desktop, rather than running a Safari/desktop helper on Android: [Google Chrome documentation](https://support.google.com/chrome/answer/2664769?hl=en). A normal tracker page cannot write into another website's tab. Android Chrome therefore uses the explicit copy/download workflow. Full automatic filling there would require a separate supported browser or Android integration. This change does not claim that it provides automatic Android Chrome filling.

Physical Android hardware was not available for testing; viewport and workflow checks used the in-app browser, plus live deployment checks.

## November 2026 mobile refinement

- Phone Home uses a one-line title and compact 2×2 statistics; the quick log button stays visible beside Sheets and More.
- Applications come before deadlines and the diagram on phones. The application panel grows with the viewport, and scrolling at its edge can continue down the page.
- Pending email reviews collapse to a labelled disclosure. Background refresh waits while a review is open, preserving the selection being reviewed.
- Deadlines form a horizontally scrolling strip on phones. Job cards retain 44px actions, readable titles and full-width search; select padding leaves more room for option text.
- Edit forms open as bottom sheets with dynamic viewport limits and safe-area padding. Diagram disclosure and active navigation expose their current state to assistive technology.
- Checked 320px and 390px phone layouts, the logging form, expanded categories, Home/Jobs/Closed/Settings navigation, and 768px tablet layout against a read-only local snapshot of live listings. No application was submitted or modified during layout checks. Physical phone keyboards were not available.
- Validation: 161 Python tests and 10 browser-helper tests pass.
