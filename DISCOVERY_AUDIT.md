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
- Each source fails independently; overlapping scans are prevented. Source diagnostics report unavailable employer feeds instead of always saying active.
- Job cards show actual deadlines (or “Deadline not published”), verification time and expandable job/eligibility context. The page defaults to unapplied roles; discovery totals count verified adverts separately from application history.
- ntfy has persistent delivery retries and duplicate protection. New verified, unapplied roles include company, title, location, deadline and links. Morning/evening summaries run at 08:00/18:00 Europe/London with upcoming deadlines and source failures. Weekly application progress remains available.

## Validation

A fresh isolated public-source scan produced **49 unique verified roles across 31 employers**, after collapsing one employer-hosted/direct-feed duplicate. Higherin contributed 17 primary-source cards and GRB one; other cards came from Trackr and employer feeds. This is a trial result, not a claim about the deployed server's count: websites can respond differently to the Mac and R430.

The regression suite covers sparse-score exclusions, misleading locations, postgraduate/graduation requirements, expired/blocked pages, hub URLs, cross-source identity, source payloads, notification retry and scheduler catch-up. Existing Safari helper tests are retained. Deployment results should be checked through `/api/status`, `/api/health` and `/discovery-report`.

## Research and remaining limitations

- [Greenhouse public Job Board API](https://docs.greenhouse.io/job-board.html) supplies published posts, full descriptions and native IDs without submission credentials.
- [Lever public postings documentation](https://github.com/lever/postings-api/blob/master/README.md) supplies employer-published job data.
- [Higherin technical internships](https://higherin.com/search-jobs/internships/technology) and [placements](https://higherin.com/search-jobs/placements/technology) provide paginated student roles and detail pages.
- [GRB internships](https://www.grb.uk.com/internships/) provide another independent student source.
- [Bright Network software internships](https://www.brightnetwork.co.uk/internships/software-development/) have useful coverage, but direct requests from this Mac received HTTP 403. No bypass or falsely healthy integration was added. Targetjobs was researched; its search pages require a separate service adapter and it is not represented as an active source.

Live verification establishes that an advert exists and passes the known filters. It cannot establish unknown grades, citizenship/clearance, timetable constraints or every employer-specific qualification. Requirements are shown for review, and listings with failed verification are excluded rather than claimed suitable. No applications were submitted during this audit.
