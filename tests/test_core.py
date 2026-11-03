"""Core behaviour: risk rules, alerts, the demo clock, alert channels and voice intent routing."""
import notify
import rules
import store


def triggers(findings):
    return {f["trigger"] for f in findings}


# ---------------------------------------------------------------- risk rules

def test_red_flag_phrase_is_high_urgency(conn):
    findings = store.assess_risk(conn, "Margaret", "I'm feeling a bit dizzy today")
    flag = next(f for f in findings if f["trigger"] == "red_flag_phrase")
    assert flag["urgency"] == "high"
    assert "dizziness" in flag["reason"]


def test_calm_check_in_raises_no_red_flag(conn):
    assert "red_flag_phrase" not in triggers(store.assess_risk(conn, "Margaret", "Lovely day, made soup"))


def test_two_missed_doses_escalate(conn):
    store.set_clock("23:00")  # evening: morning doses count as taken...
    for med in ("Metformin", "Lisinopril"):  # ...so skip two explicitly
        conn.execute("DELETE FROM dose_log WHERE medication=? AND scheduled_for LIKE ?",
                     (med, f"{store._now().date()}T08:00%"))
    conn.commit()
    assert "missed_doses" in triggers(store.assess_risk(conn, "Margaret"))


def test_low_supply_triggers_refill_alert(conn):
    conn.execute("UPDATE medications SET pills_left=4 WHERE name='Metformin'")  # 2 a day -> 2 days left
    conn.commit()
    assert "refill_metformin" in triggers(store.assess_risk(conn, "Margaret"))


def test_custom_rule_can_be_registered_and_disabled(conn, monkeypatch):
    @rules.rule("test_always", "fires every time")
    def always(ctx):
        return [rules.Finding("low", f"{ctx.person} test", "test_always")]

    try:
        assert "test_always" in triggers(store.assess_risk(conn, "Margaret"))
        monkeypatch.setenv("CARECIRCLE_DISABLED_RULES", "test_always")
        assert "test_always" not in triggers(store.assess_risk(conn, "Margaret"))
    finally:
        rules.REGISTRY.pop("test_always")


def test_a_crashing_rule_does_not_silence_the_others(conn):
    @rules.rule("test_broken")
    def broken(ctx):
        raise RuntimeError("boom")

    try:
        assert "red_flag_phrase" in triggers(store.assess_risk(conn, "Margaret", "I fell"))
    finally:
        rules.REGISTRY.pop("test_broken")


# ---------------------------------------------------------------- alerts

def test_repeat_automatic_alert_is_deduplicated(conn):
    first = store.send_alert(conn, "Margaret", "high", "dizzy", trigger="red_flag_phrase")
    second = store.send_alert(conn, "Margaret", "high", "dizzy again", trigger="red_flag_phrase")
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True


def test_alert_to_one_named_family_member(conn):
    r = store.send_alert(conn, "Margaret", "medium", "call me", only_to="Priya")
    assert r["recipients"] == ["Priya"]


def test_custom_alert_channel_receives_alerts(conn):
    got = []
    notify.channel("test_capture")(lambda alert: got.append(alert))
    try:
        store.send_alert(conn, "Margaret", "medium", "hello", only_to="Dev")
        assert [a.reason for a in got] == ["hello"]
        assert got[0].recipients[0]["name"] == "Dev"
    finally:
        notify.CHANNELS.pop("test_capture")


def test_webhook_channel_is_off_without_url(monkeypatch):
    monkeypatch.delenv("CARECIRCLE_WEBHOOK_URL", raising=False)
    assert notify.CHANNELS["webhook"].active() is False


# ---------------------------------------------------------------- demo clock

def test_clock_jump_plays_the_day_out_normally(conn):
    store.set_clock("20:00")
    status = {(d["medication"], d["time"]): d["status"] for d in store.todays_schedule(conn, "Margaret")}
    assert status[("Metformin", "08:00")] == "taken"
    assert status[("Vitamin D", "13:00")] == "taken"
    assert status[("Metformin", "20:00")] == "pending"
    assert [d["medication"] for d in store.due_reminders(conn, "Margaret")] == ["Metformin"]
    assert store.due_reminders(conn, "Margaret") == []  # each dose is reminded once


def test_logging_a_dose_counts_down_the_supply(conn):
    store.set_clock("20:00")
    before = next(r for r in store.refill_status(conn, "Margaret") if r["medication"] == "Metformin")["pills_left"]
    store.log_dose(conn, "Margaret", "Metformin")
    after = next(r for r in store.refill_status(conn, "Margaret") if r["medication"] == "Metformin")["pills_left"]
    assert after == before - 1


# ---------------------------------------------------------------- voice intents

def route(text, profile=None):
    import agent
    ctx = agent.Ctx(client=None, turn=agent.Turn(), text=text, t=text.lower().strip(),
                    profile=profile or {"family": [{"name": "Priya", "relation": "daughter"},
                                                   {"name": "Dev", "relation": "son"}]})
    return next((i.name for i in agent.INTENTS if i.matches(ctx)), None)


def test_intent_routing():
    assert route("What's next today?") == "whats_next"
    assert route("tell me what's next") == "whats_next"  # "tell" alone must not message the family
    assert route("I just took my Metformin") == "log_medication"
    assert route("im feeling a lil dizzy and only slept for 4 hrs") == "checkin"
    assert route("Let Priya know I'd love a call") == "message_family"
    assert route("call my son") == "message_family"
    assert route("Get me ready for Dr. Chen") == "doctor_visit"
    assert route("Read my messages") == "read_messages"
    assert route("Show me the dashboard") == "dashboard"
    assert route("sing me a song") is None
