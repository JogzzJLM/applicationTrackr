from web.components import ApplicationCardViewModel, JobCardViewModel, render_job_card
from web.views import render_unified_dashboard_html


def test_dashboard_module_imports_after_responsive_template_changes():
    assert callable(render_unified_dashboard_html)


def test_job_view_model_normalizes_and_renders_one_job_object():
    model = JobCardViewModel(
        raw={
            "id": "job-1",
            "company": "Example Ltd",
            "title": "Software Engineering Intern",
            "location": "London, UK",
            "link": "https://example.com/jobs/1",
            "source": "Greenhouse",
            "program_type": "internship",
            "category": "software",
            "match_score": 88,
            "match_tier": "strong",
            "deadline": "2026-10-01",
            "match_reasons": ["software title", "UK location"],
        }
    )

    assert model.company == "Example Ltd"
    assert model.program == "internship"
    assert model.score == 88
    assert model.status_tag == "not_applied"

    markup = model.render()
    assert 'class="card job-card"' in markup
    assert "Software Engineering Intern" in markup
    assert "88% STRONG FIT" in markup
    assert "Apply Agent" in markup


def test_job_render_facade_preserves_applied_state():
    markup = render_job_card(
        {
            "id": "job-2",
            "company": "Example",
            "title": "Developer Internship",
            "location": "UK",
            "link": "#",
            "match_score": 80,
        },
        is_applied=True,
    )
    assert 'data-status="applied"' in markup
    assert "In Sheet" in markup


def test_application_view_model_renders_stacked_card_actions():
    model = ApplicationCardViewModel(
        raw={
            "company": "Barclays",
            "role": "Technology Summer Internship Programme 2027",
            "latest_stage": "Online Assessment",
            "status": "Active",
        }
    )
    markup = model.render()
    assert 'class="application-card"' in markup
    assert "Barclays" in markup
    assert "Online Assessment" in markup
    assert "+ Interview" in markup
    assert "Reject" in markup
