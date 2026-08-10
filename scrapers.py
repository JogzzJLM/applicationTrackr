"""
Scrapers facade for ApplicationTrackr.
Re-exports scrapers_engine functions maintaining 100% backward compatibility.
"""
from scrapers_engine.verifier import verify_live_page_applyable
from scrapers_engine.ats_scrapers import (
    validate_job_legitimacy, is_relevant_role, add_discovered_job,
    extract_and_register_ats_company, scrape_greenhouse_jobs,
    scrape_lever_jobs, scrape_ashby_jobs, scrape_smartrecruiters_jobs,
    _JOB_LOCK
)
from scrapers_engine.trackr_scraper import (
    parse_trackr_date, is_trackr_item_active_and_recent, scrape_trackr_website
)
from scrapers_engine.audit import (
    load_seen_jobs, save_seen_jobs, load_discovered_jobs,
    save_discovered_jobs, purge_expired_jobs,
    recheck_existing_open_jobs_for_closure, run_all_scrapers
)
from core.scoring import calculate_skill_match_score