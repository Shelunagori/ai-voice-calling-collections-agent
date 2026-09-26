"""Country-aware demo contact window (POLICY_COUNTRY)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.config import Settings
from app.domain.policy import Decision, PolicyConfig, PolicyEngine, Rule
from app.domain.scenarios import get_scenario
from app.runtime import policy_from_settings

ACC = get_scenario("A").account
AT_10_JST = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)
AT_23_JST = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)


def rules(decisions):
    return {d.rule: d.decision for d in decisions}


def test_jp_applies_tokyo_window_inside():
    pe = PolicyEngine(PolicyConfig(country="JP"))
    d = rules(pe.evaluate_contact(ACC, AT_10_JST))
    assert d[Rule.CONTACT_WINDOW] == Decision.ALLOW


def test_jp_blocks_outside_window():
    pe = PolicyEngine(PolicyConfig(country="JP"))
    out = pe.evaluate_contact(ACC, AT_23_JST)
    w = next(x for x in out if x.rule == Rule.CONTACT_WINDOW)
    assert w.decision == Decision.BLOCK and "Asia/Tokyo" in w.reason and "simulated demo" in w.reason


@pytest.mark.parametrize("country", ["", "US", "IN", "none"])
def test_non_jp_window_not_applicable_but_other_rules_run(country):
    pe = PolicyEngine(PolicyConfig(country=country))
    out = pe.evaluate_contact(replace(ACC, contact_attempts=3, stop_contact=True), AT_23_JST)
    d = rules(out)
    assert d[Rule.CONTACT_WINDOW] == Decision.NOT_APPLICABLE
    assert d[Rule.MAX_CONTACT_ATTEMPTS] == Decision.BLOCK
    assert d[Rule.STOP_CONTACT_ACTIVE] == Decision.BLOCK
    assert not all(x.allowed for x in out)  # still blocked, by the other rules


def test_non_jp_allows_when_other_rules_pass():
    out = PolicyEngine(PolicyConfig(country="")).evaluate_contact(ACC, AT_23_JST)
    assert all(x.allowed for x in out)


def test_country_code_is_case_insensitive():
    assert (
        rules(PolicyEngine(PolicyConfig(country="jp")).evaluate_contact(ACC, AT_23_JST))[Rule.CONTACT_WINDOW]
        == Decision.BLOCK
    )


def test_browser_sessions_remain_not_applicable():
    for c in ("JP", ""):
        d = PolicyEngine(PolicyConfig(country=c)).reviewer_initiated_contact(uuid.uuid4(), AT_23_JST)
        assert d.rule == Rule.CONTACT_WINDOW and d.decision == Decision.NOT_APPLICABLE


def test_settings_default_is_jp_and_empty_env_disables(monkeypatch):
    monkeypatch.delenv("POLICY_COUNTRY", raising=False)
    assert Settings(_env_file=None).policy_country == "JP"
    monkeypatch.setenv("POLICY_COUNTRY", "")
    s = Settings(_env_file=None)
    assert s.policy_country == ""
    assert policy_from_settings(s).config.country == ""
    monkeypatch.setenv("POLICY_COUNTRY", "JP")
    assert policy_from_settings(Settings(_env_file=None)).config.country == "JP"
