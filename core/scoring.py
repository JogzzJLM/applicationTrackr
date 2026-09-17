"""Compatibility layer for the relevance/fit scoring engine."""
from core.relevance import evaluate_job


def calculate_skill_match_score(title, company, location, skills_list=None, metadata=None):
    """Return the calibrated fit score for a job.

    Eligibility is evaluated before scoring.  An ineligible role returns 0 rather
    than an artificial 70+ baseline.
    """
    decision = evaluate_job(
        title=title,
        company=company,
        location=location,
        metadata=metadata or {},
        skills_list=skills_list,
    )
    return decision.score
