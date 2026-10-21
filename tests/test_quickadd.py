from datetime import date, time

import pytest

from tahgul.quickadd import Target, parse

TODAY = date(2026, 10, 21)  # a Wednesday

TARGETS = [
    Target("project", "p-audit", "Network audit"),
    Target("project", "p-dash", "Yield dashboard"),
    Target("client", "c-acme", "Acme Farms", "ACME", ["p-audit"]),
    Target("client", "c-prairie", "Prairie Co-op", None, ["p-dash", "p-other"]),
]


def p(text):
    return parse(text, TODAY, TARGETS)


def test_spec_example():
    r = p("Fix VPN for Acme tomorrow !high #network")
    # "Acme" is the client's code; it has one open project, so that's the project.
    assert r.title == "Fix VPN"
    assert r.due_date == date(2026, 10, 22)
    assert r.priority == "high"
    assert r.labels == ["network"]
    assert (r.client_id, r.project_id, r.matched) == ("c-acme", "p-audit", "Acme Farms")


def test_hash_names_a_project_or_client():
    r = p("Call Laura Thursday 2pm #acme")
    assert r.title == "Call Laura"
    assert (r.due_date, r.due_time) == (date(2026, 10, 22), time(14, 0))
    assert r.project_id == "p-audit" and r.labels == []


def test_client_with_several_projects_needs_confirmation():
    r = p("Send report for Prairie Co-op")
    assert r.client_id == "c-prairie" and r.project_id is None
    assert r.candidates == ["p-dash", "p-other"]


def test_multi_word_project_after_for():
    r = p("Check sensors for yield dashboard fri")
    assert r.project_id == "p-dash" and r.title == "Check sensors"
    assert r.due_date == date(2026, 10, 23)


def test_for_without_a_match_stays_in_title():
    r = p("Write agenda for board meeting")
    assert r.title == "Write agenda for board meeting" and r.project_id is None


@pytest.mark.parametrize(
    "text,due",
    [
        ("x today", date(2026, 10, 21)),
        ("x wed", date(2026, 10, 21)),  # same weekday = today
        ("x next wednesday", date(2026, 10, 28)),
        ("x mon", date(2026, 10, 26)),
        ("x in 3 days", date(2026, 10, 24)),
        ("x in 2 weeks", date(2026, 11, 4)),
        ("x next week", date(2026, 10, 26)),
        ("x oct 30", date(2026, 10, 30)),
        ("x 30th oct", date(2026, 10, 30)),
        ("x 11/2", date(2026, 11, 2)),
        ("x jan 5", date(2027, 1, 5)),  # already past this year → next year
        ("x 2026-12-01", date(2026, 12, 1)),
    ],
)
def test_dates(text, due):
    r = p(text)
    assert r.due_date == due and r.title == "x"


@pytest.mark.parametrize(
    "text,t",
    [
        ("x 2pm", time(14)),
        ("x 2:30pm", time(14, 30)),
        ("x 14:05", time(14, 5)),
        ("x at 3 pm", time(15)),
        ("x noon", time(12)),
        ("x tonight", time(20)),
    ],
)
def test_times_default_to_today(text, t):
    r = p(text)
    assert r.due_time == t and r.due_date == TODAY and r.title == "x"


def test_bare_numbers_stay_in_title():
    r = p("Order 3 sensors")
    assert r.title == "Order 3 sensors" and r.due_time is None


@pytest.mark.parametrize("bang,prio", [("!urgent", "urgent"), ("!low", "low"), ("!med", "medium")])
def test_priority(bang, prio):
    assert p(f"x {bang}").priority == prio


def test_only_tokens_keeps_original_text_as_title():
    assert p("#ops tomorrow").title == "#ops tomorrow"
