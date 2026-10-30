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


def test_dashboard_shows_the_ai_assistant(app):
    app.switch_page("views/executive_dashboard.py").run()
    assert "AI Operations Assistant" in [s.value for s in app.subheader]


def test_demo_banner_is_always_shown(app):
    app.switch_page("views/executive_dashboard.py").run()
    assert any("DEMO MODE" in m.value for m in app.markdown)


def test_dashboard_flags_todays_escalations(app):
    app.switch_page("views/executive_dashboard.py").run()
    assert any("critical issue(s) to escalate today" in e.value for e in app.error)


# --- Executive Dashboard behaviour ------------------------------------------------------------

def fresh_dashboard():
    return AppTest.from_file(str(config.PROJECT_ROOT / "app.py"), default_timeout=60).run()


def shown_references(at):
    """References printed under each issue in the 'most urgent' rows."""
    return [m.value.split("<span class='kpi-period'>")[1].split("<")[0]
            for m in at.markdown if "<span class='kpi-period'>" in m.value]


def test_dashboard_shows_the_five_most_urgent_issues_in_order(demo_bundle):
    from src import metrics
    at = fresh_dashboard()
    items = metrics.attention_items(metrics.enrich_all(demo_bundle.frames, demo_bundle.as_of))
    urgent = items[items["severity"].isin(["Critical", "High"])]
    assert shown_references(at) == list(urgent["reference"].head(config.DASHBOARD_TOP_ISSUES))
    all_issues = [df for df in at.dataframe if "action_status" in df.value.columns][0].value
    assert len(all_issues) == len(items)                          # "view all" lists every open issue
    assert "Action status of these 17 issues" in " ".join(m.value for m in at.markdown)


@pytest.mark.parametrize("button, page_title, check", [
    ("Overdue shipments →", "Shipping Management", lambda at: at.toggle[0].value is True),
    ("Below minimum →", "Inventory Management",
     lambda at: at.multiselect[3].value == ["Out of Stock", "Critical", "Low"]),
    ("Unpaid payments →", "Payments", lambda at: at.toggle[0].value is True),
    ("Tickets past SLA →", "Customer Support", lambda at: at.toggle[0].value is True),
])
def test_drill_down_buttons_open_the_filtered_view(button, page_title, check):
    at = fresh_dashboard()
    next(b for b in at.button if b.label == button).click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.value for t in at.title] == [page_title]
    assert check(at)


def test_open_button_focuses_the_issue_on_its_page():
    at = fresh_dashboard()
    first = shown_references(at)[0]
    next(b for b in at.button if b.label == "View →").click().run()
    assert any(f"Opened from the dashboard: {first}" in m.value for m in at.markdown)


def test_dashboard_filters_still_change_the_numbers():
    at = fresh_dashboard()

    def orders_card():
        card = [m.value for m in at.markdown if "ORDERS PROCESSED" in m.value.upper() and "kpi-card" in m.value][0]
        return card.split('kpi-value">')[1].split("<")[0]

    seven = orders_card()
    at.radio[0].set_value(28).run()
    assert orders_card() != seven and not at.exception
    assert any("Last 28 days" in m.value for m in at.markdown)
    at.multiselect[0].set_value(["Spain"]).run()
    assert not at.exception and orders_card() != seven
