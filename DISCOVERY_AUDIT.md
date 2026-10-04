# Discovery audit — 4 October 2026

The initial live app indexed 39 discovered roles. The supplied page displayed 47 cards, including logged applications, missing links and unsuitable roles; those totals are not a fair measure of discovery coverage.

## What was wrong

- In the current Trackr summer feed, 92 otherwise relevant adverts failed solely because a title-only score of 71 was below the configured cutoff of 72.
- Trackr's programme divisions, deadlines, eligibility and location notes were discarded. Generic technical programmes were lost, while Amsterdam exceptions were labelled UK.
- Gradcracker's employer hub pages were mistaken for application links, and title elements were sometimes used as company names.
- Failed HTTP checks were treated as open listings; closure checks could mistake unrelated page text for an expired job.
- Deduplication discarded meaningful query IDs and could merge different requisitions or recruitment years.
- Notifications used only the last scan's new-job count, forgot failed sends, and required the scheduler to hit an exact minute. A successful ntfy receipt proved publication, not phone delivery.

## Implemented improvements

- Eligibility gates determine admission; scores rank suitable roles instead of excluding sparse adverts. Filters retain the user's June 2028 graduation, technical internships/placements and UK geography. Explicit postgraduate requirements, overseas titles/locations, electro-optics and pure trading roles are rejected.
- Trackr retains programme metadata, rejects future openings and finished summers, and treats deadlines as inclusive dates.
- Added paginated Higherin technical internship/placement feeds and GRB internship/placement feeds. Validated and added employer feeds for Databricks, Figma, Stripe, Anduril and Epic Games. SmartRecruiters now paginates and reads qualification details.
- Specific live job evidence is required. Workday details, JobPosting data, and matching job headings/application controls supply actual descriptions and locations. Blocked/ambiguous pages remain outside the apply feed, with reasons visible in the coverage report. Checks use a cache/backoff and do not permanently ban a job because of a temporary failure.
- Corrected Gradcracker opportunity/employer extraction and cross-source identity matching, including employer-hosted Greenhouse `gh_jid` links.
- Removed unsafe substring employer aliases (PIMCO was becoming IMC), normalised UK country spellings for cross-source duplicates, and preserved user notes, stages and email history when a listing's name is corrected.
- Roles explicitly requiring British citizenship stay in review until citizenship is confirmed in Filter Settings. Postgraduate-only study requirements are rejected, including the G-Research research role found in the live spot-check.
- Each source fails independently; overlapping scans are prevented. Source diagnostics report unavailable employer feeds instead of always saying active.
- Job cards show actual deadlines (or “Deadline not published”), verification time and expandable job/eligibility context. The page defaults to unapplied roles; discovery totals count verified adverts separately from application history.
- ntfy has persistent delivery retries and duplicate protection. New verified, unapplied roles include company, title, location, deadline and links. Morning/evening summaries run at 08:00/18:00 Europe/London with upcoming deadlines and source failures. Weekly application progress remains available.
- Briefings wait for the first completed discovery scan. Greenhouse skips unrelated full-time adverts before parsing their descriptions, reducing unnecessary work on the R430.

## Validation

An earlier isolated public-source trial produced **49 unique verified roles across 31 employers**, before the final live eligibility and identity corrections. The Mac and R430 received different responses from some websites.

The deployed R430's first pass held 60 verified adverts across 35 employers and published an alert containing 57 unapplied roles. The final stricter feed contains **49 verified listings across 33 employers**: duplicate adverts were merged, eight roles with unconfirmed British-citizenship requirements were held for review, and one postgraduate-only research role was excluded. This is a point-in-time count on 4 October 2026, not a guaranteed future total.

Live source reads fetched 675 Trackr entries, 141 Higherin entries across 11 pages, and 27 GRB entries across two pages. Employer APIs and specific application pages add independent evidence. Successful later scans took roughly 50–72 seconds; the initial migration scan took 392 seconds. Cache warming contributes to that difference, so it cannot all be attributed to the parsing optimisation.

Validation passed **85 Python tests and nine Safari helper tests**. The app's health endpoint, discovery report and ntfy topic all responded successfully; ntfy had no queued failed sends. The detailed deadline-and-link alert was retrieved from the topic itself. Phone popup delivery has not been confirmed by the user.

The regression suite covers sparse-score exclusions, misleading locations, postgraduate/graduation requirements, expired/blocked pages, hub URLs, cross-source identity, source payloads, notification retry and scheduler catch-up. Existing Safari helper tests are retained. Deployment results should be checked through `/api/status`, `/api/health` and `/discovery-report`.

## Research and remaining limitations

- [Greenhouse public Job Board API](https://docs.greenhouse.io/job-board.html) supplies published posts, full descriptions and native IDs without submission credentials.
- [Lever public postings documentation](https://github.com/lever/postings-api/blob/master/README.md) supplies employer-published job data.
- [Higherin technical internships](https://higherin.com/search-jobs/internships/technology) and [placements](https://higherin.com/search-jobs/placements/technology) provide paginated student roles and detail pages.
- [GRB internships](https://www.grb.uk.com/internships/) provide another independent student source.
- [Bright Network software internships](https://www.brightnetwork.co.uk/internships/software-development/) have useful coverage, but direct requests from this Mac received HTTP 403. No bypass or falsely healthy integration was added. Targetjobs was researched; its search pages require a separate service adapter and it is not represented as an active source.

Live verification establishes that an advert exists and passes the known filters. It cannot establish unknown grades, every clearance requirement, timetable constraints or every employer-specific qualification. Explicit British-citizenship requirements are held for review while citizenship is unconfirmed. Requirements are shown for review, and listings with failed verification are excluded rather than claimed suitable. Some configured employer board names return 404 and are shown as partial coverage; that is not reported as complete employer coverage. No applications were submitted during this audit.

## Individual new-listing alerts

Each newly verified, suitable, unapplied listing now receives its own ntfy notification after the completed scan saves the final feed. The message names the employer and role, gives location and deadline, includes suitability evidence and the tracker link, and opens the specific application page when tapped. Normal notification priority avoids marking every new advert urgent.

The first startup after this change baselines the already saved verified listings, preventing a backlog of alerts. Persistent listing IDs, native ATS IDs and normalised URLs suppress repeat alerts across scans, restarts and alternate sources. Failed sends remain in the durable outbox; a restart does not silence a job saved after baseline but not yet notified. Morning and evening briefings remain enabled.

Eight added regressions cover one alert per listing, backlog suppression, source aliases, distinct requisitions, eligibility/applied exclusions, later verification, saved-before-notified restarts, failed-send retries and delivery while older failures are backing off. The release passes 93 Python tests and nine Safari helper tests. Alerts mean the advert passed the current checks and was saved; they do not claim absolute certainty about every employer-specific qualification.
