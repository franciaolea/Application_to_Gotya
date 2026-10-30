"""Smoke tests: every page of the real app renders without an error.

Streamlit's AppTest runs the app in memory (no browser), clicks through each
page and reports any exception, so "the app runs" is checked automatically.
"""
import pytest
from streamlit.testing.v1 import AppTest

from src import config

PAGES = ["executive_dashboard", "daily_briefing", "action_tracker", "inventory", "shipping",
         "payments", "support", "data_health"]


@pytest.fixture(scope="module")
def app():
    at = AppTest.from_file(str(config.PROJECT_ROOT / "app.py"), default_timeout=60)
    return at.run()


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(app, page):
    app.switch_page(f"views/{page}.py").run()
    assert not app.exception, [e.value for e in app.exception]
    assert app.title, "every page has a title"


def test_demo_banner_is_always_shown(app):
    app.switch_page("views/executive_dashboard.py").run()
    assert any("DEMO MODE" in m.value for m in app.markdown)


def test_dashboard_flags_todays_escalations(app):
    app.switch_page("views/executive_dashboard.py").run()
    assert any("critical issue(s) to escalate today" in e.value for e in app.error)
