from __future__ import annotations

import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from autoapply.learning import normalize_label, predict_answer, predict_mapping
from autoapply.profile import flatten_profile

REVIEW_PATTERNS = (
    "gender", "sex", "race", "ethnicity", "disability", "veteran", "religion",
    "sexual orientation", "date of birth", "dob", "national insurance", "social security",
    "criminal", "conviction", "background check", "salary", "compensation",
    "work authorization", "right to work", "sponsorship", "visa", "relocation",
    "signature", "certify", "attest", "terms", "privacy", "consent",
)
CAPTCHA_MARKERS = ("recaptcha", "hcaptcha", "captcha")


@dataclass
class FieldResult:
    label: str
    element_type: str
    profile_key: str = ""
    action: str = ""
    confidence: float = 0.0
    note: str = ""


@dataclass
class ApplicationRunResult:
    url: str
    status: str
    submitted: bool = False
    steps_completed: int = 0
    filled: List[FieldResult] = field(default_factory=list)
    unresolved: List[FieldResult] = field(default_factory=list)
    review_required: List[FieldResult] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    screenshot_path: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _is_review_label(label: str) -> bool:
    label_n = normalize_label(label)
    return any(marker in label_n for marker in REVIEW_PATTERNS)


def _make_driver():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except ImportError as exc:
        raise RuntimeError("selenium is not installed") from exc
    options = Options()
    if os.getenv("AUTOAPPLY_HEADLESS", "true").lower() in {"1", "true", "yes"}:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1200")
    binary = os.getenv("CHROMIUM_BINARY", "/usr/bin/chromium")
    if os.path.exists(binary):
        options.binary_location = binary
    return webdriver.Chrome(options=options)


def _element_label(driver, element) -> str:
    pieces: List[str] = []
    element_id = element.get_attribute("id") or ""
    if element_id:
        try:
            pieces.extend(x.text for x in driver.find_elements("css selector", f'label[for="{element_id}"]') if x.text)
        except Exception:
            pass
    try:
        parent = element.find_element("xpath", "ancestor::label[1]")
        if parent.text:
            pieces.append(parent.text)
    except Exception:
        pass
    for attr in ("aria-label", "placeholder", "name", "id", "autocomplete"):
        value = element.get_attribute(attr)
        if value:
            pieces.append(value)
    return normalize_label(" ".join(pieces))


def _discover_fields(driver):
    fields = []
    for element in driver.find_elements("css selector", "input, textarea, select"):
        try:
            if not element.is_displayed() or not element.is_enabled():
                continue
        except Exception:
            continue
        element_type = (element.get_attribute("type") or element.tag_name).lower()
        if element_type in {"hidden", "submit", "button", "reset", "image"}:
            continue
        fields.append((element, _element_label(driver, element), element_type))
    return fields


def _set_value(element, element_type: str, value: Any) -> bool:
    value = "" if value is None else str(value)
    if not value:
        return False
    try:
        if element_type == "file":
            path = os.path.expanduser(value)
            if not os.path.exists(path):
                return False
            element.send_keys(path)
            return True
        if element.tag_name.lower() == "select":
            from selenium.webdriver.support.ui import Select
            select = Select(element)
            wanted = normalize_label(value)
            for option in select.options:
                if normalize_label(option.text) == wanted or normalize_label(option.get_attribute("value")) == wanted:
                    select.select_by_visible_text(option.text)
                    return True
            for option in select.options:
                if wanted and wanted in normalize_label(option.text):
                    select.select_by_visible_text(option.text)
                    return True
            return False
        if element_type in {"checkbox", "radio"}:
            truthy = normalize_label(value) in {"yes", "true", "1", "y", "accept", "accepted"}
            if truthy != element.is_selected():
                element.click()
            return True
        element.clear()
        element.send_keys(value)
        return True
    except Exception:
        return False


def _page_has_captcha(driver) -> bool:
    source = (driver.page_source or "").lower()
    return any(marker in source for marker in CAPTCHA_MARKERS)


def _find_progress_button(driver):
    for element in driver.find_elements("css selector", "button, input[type=button], a[role=button]"):
        try:
            text = normalize_label(element.text or element.get_attribute("value") or element.get_attribute("aria-label"))
            if text in {"next", "continue", "save and continue", "continue application", "next step"}:
                return element
        except Exception:
            pass
    return None


def _find_submit_button(driver):
    for element in driver.find_elements("css selector", "button, input[type=submit]"):
        try:
            text = normalize_label(element.text or element.get_attribute("value") or element.get_attribute("aria-label"))
            if any(x in text for x in ("submit application", "submit", "apply now", "send application")):
                return element
        except Exception:
            pass
    return None


def run_application(url: str, profile: Dict[str, Any], auto_submit: bool = False, screenshot_dir: Optional[str] = None) -> ApplicationRunResult:
    result = ApplicationRunResult(url=url, status="starting")
    flat_profile = flatten_profile(profile)
    max_steps = max(1, int(os.getenv("AUTOAPPLY_MAX_STEPS", "5")))
    page_timeout = max(10, int(os.getenv("AUTOAPPLY_PAGE_TIMEOUT", "30")))
    env_submit = os.getenv("AUTOAPPLY_AUTO_SUBMIT", "false").lower() in {"1", "true", "yes"}
    driver = _make_driver()
    driver.set_page_load_timeout(page_timeout)
    try:
        driver.get(url)
        time.sleep(1.5)
        for step in range(max_steps):
            result.steps_completed = step + 1
            if _page_has_captcha(driver):
                result.blockers.append("CAPTCHA detected; agent will not bypass it")
                result.status = "blocked"
                break
            page_unresolved: List[FieldResult] = []
            page_review: List[FieldResult] = []
            for element, label, element_type in _discover_fields(driver):
                if not label:
                    page_unresolved.append(FieldResult("unlabelled field", element_type, action="unresolved", note="no usable label"))
                    continue
                profile_key, confidence, mapping_note = predict_mapping(label)
                explicit_value = flat_profile.get(profile_key) if profile_key else None
                learned_answer, answer_conf, answer_note = predict_answer(label)
                value = explicit_value if str(explicit_value or "").strip() else learned_answer
                item = FieldResult(label, element_type, profile_key or "", confidence=max(confidence, answer_conf), note=mapping_note if explicit_value else answer_note)
                if _is_review_label(label):
                    if value and _set_value(element, element_type, value):
                        item.action = "filled-explicit-review"
                        result.filled.append(item)
                    else:
                        item.action = "review-required"
                    page_review.append(item)
                    continue
                if not value:
                    item.action = "unresolved"
                    page_unresolved.append(item)
                elif _set_value(element, element_type, value):
                    item.action = "filled"
                    result.filled.append(item)
                else:
                    item.action = "unresolved"
                    item.note = (item.note + "; could not set value").strip("; ")
                    page_unresolved.append(item)
            result.unresolved.extend(page_unresolved)
            result.review_required.extend(page_review)
            if page_unresolved or page_review:
                result.status = "needs_review"
                break
            progress = _find_progress_button(driver)
            if progress:
                progress.click()
                time.sleep(1.2)
                continue
            submit = _find_submit_button(driver)
            if submit:
                if auto_submit and env_submit and not result.unresolved and not result.review_required:
                    submit.click()
                    time.sleep(1.5)
                    result.submitted = True
                    result.status = "submitted"
                else:
                    result.status = "ready_for_review"
                break
            result.status = "filled_no_submit_found"
            break
        if screenshot_dir:
            Path(screenshot_dir).mkdir(parents=True, exist_ok=True)
            path = str(Path(screenshot_dir) / f"run-{int(time.time())}.png")
            try:
                driver.save_screenshot(path)
                result.screenshot_path = path
            except Exception:
                pass
        if result.status == "starting":
            result.status = "max_steps_reached"
        return result
    except Exception as exc:
        result.status = "error"
        result.blockers.append(f"{type(exc).__name__}: {exc}")
        return result
    finally:
        try:
            driver.quit()
        except Exception:
            pass
