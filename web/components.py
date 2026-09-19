import html
import json
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping

from core.normalization import clean_company_display_name, extract_program_type
from core.scoring import calculate_skill_match_score


def _text(value: Any, default: str = "") -> str:
    value = default if value is None else value
    return str(value)


def _js_literal(value: Any) -> str:
    """Return an HTML-safe JavaScript string literal for inline handlers."""
    return html.escape(json.dumps(_text(value)), quote=True)


@dataclass(frozen=True)
class JobCardViewModel:
    """Immutable presentation object for one discovered/logged job.

    Keeping all card-derived values in one object makes the rendering rules
    deterministic and keeps dictionary-shape quirks out of the HTML template.
    """

    raw: Mapping[str, Any]
    is_reported_closed: bool = False
    is_applied: bool = False
    is_hidden: bool = False

    @property
    def job_id(self) -> str:
        return _text(self.raw.get("id"))

    @property
    def company(self) -> str:
        return clean_company_display_name(_text(self.raw.get("company"), "Unknown"))

    @property
    def title(self) -> str:
        return _text(self.raw.get("title"), "Role")

    @property
    def location(self) -> str:
        return _text(self.raw.get("location"), "Unknown")

    @property
    def link(self) -> str:
        return _text(self.raw.get("link"), "#")

    @property
    def date_found(self) -> str:
        return _text(self.raw.get("date_found"), "Recently")

    @property
    def metadata(self) -> Mapping[str, Any]:
        value = self.raw.get("metadata")
        return value if isinstance(value, dict) else {}

    @property
    def score(self) -> int:
        score = self.raw.get("match_score")
        if score is None:
            score = calculate_skill_match_score(
                self.title, self.company, self.location, metadata=self.metadata
            )
        try:
            return int(round(float(score)))
        except (TypeError, ValueError):
            return 0

    @property
    def tier(self) -> str:
        return _text(self.raw.get("match_tier"))

    @property
    def tier_label(self) -> str:
        return {
            "strong": "STRONG FIT",
            "good": "GOOD FIT",
            "borderline": "BORDERLINE",
        }.get(self.tier, "FIT")

    @property
    def program(self) -> str:
        return _text(self.raw.get("program_type")) or extract_program_type(self.title)

    @property
    def category(self) -> str:
        category = _text(self.raw.get("category"), "software")
        return "ml" if category == "ai_ml" else category

    @property
    def deadline(self) -> str:
        return _text(
            self.raw.get("deadline")
            or self.raw.get("closeDate")
            or self.raw.get("closing_date")
            or "Rolling / ASAP"
        )

    @property
    def status_tag(self) -> str:
        if self.is_applied:
            return "applied"
        if self.is_reported_closed:
            return "closed"
        return "not_applied"

    @property
    def source_text(self) -> str:
        sources = self.raw.get("sources")
        if not isinstance(sources, list) or not sources:
            sources = [_text(self.raw.get("source"), "Unknown")]
        return " + ".join(_text(source) for source in sources if _text(source)) or "Unknown"

    @property
    def source_url(self) -> str:
        return _text(self.raw.get("source_url")) or self.link

    @property
    def display_url(self) -> str:
        value = (
            self.source_url.replace("https://", "")
            .replace("http://", "")
            .replace("www.", "")
            .rstrip("/")
        )
        return value if len(value) <= 36 else value[:33] + "..."

    @property
    def reasons(self) -> list[str]:
        value = self.raw.get("match_reasons")
        if not isinstance(value, list):
            return []
        return [_text(item) for item in value[:3] if _text(item)]

    @property
    def program_badge(self) -> str:
        if self.program == "placement":
            return '<span class="badge badge-cyan">Placement · Yr 2</span>'
        if self.program == "internship":
            return '<span class="badge badge-papaya">Internship · Yr 2</span>'
        return '<span class="badge badge-yellow">Graduate · Yr 3+</span>'

    def render(self) -> str:
        if self.is_applied:
            status_dot = '<span class="status-dot dot-cyan"></span>'
            status_label = "Applied"
            sheet_action = (
                '<span class="btn btn-ghost card-secondary-action" '
                'style="cursor:default;color:var(--cyan);">In Sheet</span>'
            )
        elif self.is_reported_closed:
            status_dot = '<span class="status-dot dot-red"></span>'
            status_label = "Closed"
            sheet_action = (
                f'<button onclick="reopenJob({_js_literal(self.job_id)})" '
                'class="btn btn-ghost card-secondary-action">Re-open</button>'
            )
        else:
            status_dot = '<span class="status-dot dot-green"></span>'
            status_label = "Open"
            sheet_action = (
                f'<button onclick="logJob({_js_literal(self.company)}, {_js_literal(self.title)})" '
                'class="btn btn-tinted card-secondary-action">+ Log</button>'
            )

        report_action = ""
        if not self.is_reported_closed:
            report_action = (
                f'<button onclick="reportClosedJob({_js_literal(self.job_id)}, {_js_literal(self.link)})" '
                'class="btn btn-ghost btn-danger-text card-tertiary-action">Closed?</button>'
            )

        agent_action = ""
        if not self.is_reported_closed and self.link != "#":
            agent_action = (
                f'<a href="/autoapply?job_id={urllib.parse.quote(self.job_id)}" '
                'class="btn btn-ghost card-secondary-action">Apply Agent</a>'
            )

        reason_html = ""
        if self.reasons:
            reason_html = (
                '<div class="card-reasons">'
                + " · ".join(html.escape(reason) for reason in self.reasons)
                + "</div>"
            )

        source_html = (
            f'<div class="card-source">'
            f'<a href="{html.escape(self.source_url, quote=True)}" target="_blank" '
            f'rel="noopener" class="link-muted">{html.escape(self.display_url)}</a>'
            f' · <span>{html.escape(self.source_text)}</span></div>'
        )

        return f'''<article class="card job-card"
data-search="{html.escape((self.company + " " + self.title + " " + self.location + " " + self.status_tag + " " + self.category + " " + self.program + " " + self.source_text).lower(), quote=True)}"
data-status="{html.escape(self.status_tag, quote=True)}"
data-cat="{html.escape(self.category, quote=True)}"
data-program="{html.escape(self.program, quote=True)}"
data-date="{html.escape(self.date_found, quote=True)}"
data-deadline="{html.escape(self.deadline.lower(), quote=True)}"
data-match="{self.score}"
data-company="{html.escape(self.company.lower(), quote=True)}"
data-title="{html.escape(self.title.lower(), quote=True)}">
    <div class="card-top">
        <div class="card-status">{status_dot}<span>{status_label}</span>{self.program_badge}</div>
        <div class="card-match">{self.score}% {html.escape(self.tier_label)}</div>
    </div>
    <div class="card-company">{html.escape(self.company)}</div>
    <div class="card-role">{html.escape(self.title)}</div>
    <div class="card-meta">{html.escape(self.location)} · {html.escape(self.deadline)}</div>
    <div class="card-detail">
        {reason_html}
        {source_html}
    </div>
    <div class="card-actions">
        <a href="{html.escape(self.link, quote=True)}" target="_blank" rel="noopener noreferrer" class="btn btn-filled card-primary-action">Apply ↗</a>
        {agent_action}
        {sheet_action}
        {report_action}
    </div>
</article>'''


@dataclass(frozen=True)
class ApplicationCardViewModel:
    """Presentation object for a tracked Google-Sheets application."""

    raw: Mapping[str, Any]

    @property
    def company(self) -> str:
        return clean_company_display_name(_text(self.raw.get("company"), "Unknown"))

    @property
    def role(self) -> str:
        return _text(self.raw.get("role"), "Software/Quant Role")

    @property
    def latest_stage(self) -> str:
        return _text(self.raw.get("latest_stage"), "Applied")

    @property
    def status(self) -> str:
        return _text(self.raw.get("status"), self.latest_stage)

    @property
    def badge_class(self) -> str:
        value = self.latest_stage.lower()
        if "offer" in value:
            return "badge-green"
        if "reject" in value or "fail" in value:
            return "badge-red"
        if "interview" in value:
            return "badge-papaya"
        if "assessment" in value or "oa" in value or "test" in value:
            return "badge-cyan"
        return "badge-yellow"

    def render(self) -> str:
        return f'''<article class="application-card">
    <div class="application-card-head">
        <div>
            <div class="application-company">{html.escape(self.company)}</div>
            <div class="application-role">{html.escape(self.role)}</div>
        </div>
        <span class="badge {self.badge_class}">{html.escape(self.latest_stage)}</span>
    </div>
    <div class="application-card-foot">
        <span class="application-status">{html.escape(self.status)}</span>
        <div class="application-actions">
            <button onclick="quickUpdateStage({_js_literal(self.company)}, 'Interview', {_js_literal(self.role)})" class="btn btn-tinted">+ Interview</button>
            <button onclick="quickUpdateStage({_js_literal(self.company)}, 'Rejected', {_js_literal(self.role)})" class="btn btn-ghost btn-danger-text">Reject</button>
        </div>
    </div>
</article>'''


def render_job_card(
    j,
    is_reported_closed=False,
    is_applied=False,
    is_hidden=False,
    company_resp_map=None,
):
    """Backward-compatible facade used by the dashboard."""
    return JobCardViewModel(
        raw=j,
        is_reported_closed=is_reported_closed,
        is_applied=is_applied,
        is_hidden=is_hidden,
    ).render()


def render_application_card(application):
    return ApplicationCardViewModel(raw=application).render()
