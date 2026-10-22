"""CareCircle - a self-hosted MCP server that gives Alexa+ a memory for elder care.

Transport: Streamable HTTP at http://localhost:8000/mcp (MCP spec 2025-11-25 and later).
Extras on the same port:
  GET /dashboard  - live family dashboard (what the family sees on their phone)
  GET /api/state  - JSON behind the dashboard
  GET /health

Every tool returns a dict with:
  speech - one or two short sentences Alexa should say out loud
  card   - a visual card for screen devices (Echo Show / Fire TV)
plus the raw data, so an agent can reason over it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).parent))
import store  # noqa: E402

from mcp.server.apps import Apps  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402

UI_DIR = Path(__file__).parent / "ui"

apps = Apps()

# MCP App (interactive UI). Registered before MCPServer() because extensions are applied at construction.

@apps.tool(resource_uri="ui://carecircle/dashboard.html", title="Show care dashboard")
def show_care_dashboard(person: str) -> dict[str, Any]:
    """Open the interactive CareCircle dashboard (doses, mood trend, alerts) on a screen device."""
    data = _state(person)
    return {**data, "speech": f"Here's {_first_name(person)}'s care dashboard."}


apps.add_html_resource("ui://carecircle/dashboard.html", (UI_DIR / "dashboard.html").read_text())


mcp = MCPServer(
    name="carecircle",
    title="CareCircle",
    version="1.0.0",
    instructions=(
        "CareCircle helps an older adult live independently while keeping their family in the loop. "
        "Use it whenever the user talks about their medicine, how they feel, sleep, pain, appointments, "
        "or asks what's next today. After any check-in, CareCircle decides on its own whether family "
        "should be alerted - tell the user plainly when that happens. Keep spoken replies short and warm."
    ),
    extensions=[apps],
)


def _db():
    return store.connect()


def _first_name(person: str) -> str:
    return person.strip().split()[0].title()


# ------------------------------------------------------------------ setup

@mcp.tool(title="Set up a care profile")
def setup_person(name: str, age: int | None = None, notes: str = "",
                 family: list[dict] | None = None) -> dict[str, Any]:
    """Create or replace a care profile. `family` is a list of {name, relation, contact}."""
    with _db() as conn:
        r = store.setup_person(conn, name, age, notes, family or [])
    return {**r, "speech": f"{r['person']}'s care circle is set up with {r['family_count']} family members."}


@mcp.tool(title="Add a medication")
def add_medication(person: str, name: str, dose: str, times: list[str], purpose: str = "") -> dict[str, Any]:
    """Add a medication. `times` are 24h HH:MM strings, e.g. ["08:00", "20:00"]."""
    with _db() as conn:
        r = store.add_medication(conn, person, name, dose, times, purpose)
    when = " and ".join(_fmt_time(t) for t in r["times"])
    return {**r, "speech": f"Got it. {r['medication']} {r['dose']} at {when}."}


@mcp.tool(title="Add an appointment")
def add_appointment(person: str, when: str, title: str, location: str = "") -> dict[str, Any]:
    """Add an appointment. `when` is ISO 8601, e.g. 2026-10-24T10:30."""
    with _db() as conn:
        r = store.add_appointment(conn, person, when, title, location)
    return {**r, "speech": f"Added {title} on {_fmt_dt(r['at'])}."}


# ------------------------------------------------------------------ daily use

@mcp.tool(title="Log a dose")
def log_medication(person: str, medication: str, taken: bool = True) -> dict[str, Any]:
    """Record that a medication was taken (or skipped). Matches the nearest scheduled dose today."""
    with _db() as conn:
        r = store.log_dose(conn, person, medication, taken)
        remaining = [d for d in store.todays_schedule(conn, person) if d["status"] == "pending"]
        alerts = _auto_escalate(conn, person)
    nxt = f" Next up: {remaining[0]['medication']} at {_fmt_time(remaining[0]['time'])}." if remaining else \
        " That's everything for today."
    verb = "Logged" if taken else "Noted you skipped"
    return {
        "logged": r, "alerts_sent": alerts,
        "speech": f"{verb} your {r['medication']}.{nxt}" + _alert_speech(alerts),
        "card": _schedule_card(person, remaining_only=False),
    }


@mcp.tool(title="Daily check-in")
def daily_checkin(person: str, mood: int, notes: str = "", sleep_hours: float | None = None,
                  pain: int | None = None) -> dict[str, Any]:
    """Record how the person feels. mood 1 (awful) - 5 (great), pain 0-10, notes = their own words.

    After saving, CareCircle runs its risk rules (red-flag phrases like a fall or chest pain,
    missed doses, low-mood or poor-sleep trends) and alerts family automatically if needed.
    """
    with _db() as conn:
        r = store.record_checkin(conn, person, mood, notes, sleep_hours, pain)
        alerts = _auto_escalate(conn, person, notes)
    name = _first_name(person)
    if alerts:
        speech = f"Thank you for telling me, {name}." + _alert_speech(alerts)
    elif r["mood"] >= 4:
        speech = f"Lovely to hear, {name}. I've noted it."
    else:
        speech = f"Thanks {name}, I've noted that. I'll keep an eye on how you're doing."
    return {"checkin": r, "alerts_sent": alerts, "speech": speech,
            "card": {"type": "checkin", "title": "Check-in saved", "mood": r["mood"],
                     "sleep_hours": r["sleep_hours"], "pain": r["pain"], "notes": r["notes"],
                     "alerts": [a["reason"] for a in alerts]}}


@mcp.tool(title="What's next today")
def whats_next(person: str) -> dict[str, Any]:
    """The agenda: next doses, anything missed, upcoming appointments, and whether a check-in is due."""
    with _db() as conn:
        person = store.ensure_person(conn, person)
        sched = store.todays_schedule(conn, person)
        appts = store.upcoming_appointments(conn, person, days=3)
        checked_in = any(c["at"].startswith(datetime.now().date().isoformat())
                         for c in store.recent_checkins(conn, person, days=1))
    pending = [d for d in sched if d["status"] == "pending"]
    missed = [d for d in sched if d["status"] == "missed"]
    parts = []
    if missed:
        parts.append(f"You missed {missed[0]['medication']} at {_fmt_time(missed[0]['time'])}.")
    if pending:
        parts.append(f"Next is {pending[0]['medication']} {pending[0]['dose']} at {_fmt_time(pending[0]['time'])}.")
    if appts:
        parts.append(f"You have {appts[0]['title']} {_fmt_dt(appts[0]['at'])}.")
    if not checked_in:
        parts.append("And I haven't heard how you're feeling today.")
    return {"schedule": sched, "appointments": appts, "checked_in_today": checked_in,
            "speech": " ".join(parts) or "You're all caught up for today.",
            "card": {"type": "agenda", "title": f"{_first_name(person)}'s day", "doses": sched,
                     "appointments": appts, "checked_in_today": checked_in}}


@mcp.tool(title="Medication schedule")
def get_medication_schedule(person: str) -> dict[str, Any]:
    """Today's full medication schedule with taken / pending / missed status."""
    with _db() as conn:
        sched = store.todays_schedule(conn, person)
    taken = sum(1 for d in sched if d["status"] == "taken")
    return {"schedule": sched, "speech": f"{taken} of {len(sched)} doses taken so far today.",
            "card": _schedule_card(person)}


@mcp.tool(title="Wellbeing summary")
def get_wellbeing_summary(person: str, days: int = 7) -> dict[str, Any]:
    """Trends for the family: adherence %, mood & sleep over time, recent alerts."""
    with _db() as conn:
        person = store.ensure_person(conn, person)
        adh = store.adherence(conn, person, days)
        checks = store.recent_checkins(conn, person, days)
        alerts = store.recent_alerts(conn, person, days)
    moods = [c["mood"] for c in checks]
    avg_mood = round(sum(moods) / len(moods), 1) if moods else None
    speech = f"Over the last {days} days {_first_name(person)} took {adh['pct']}% of doses"
    speech += f", average mood {avg_mood} out of 5" if avg_mood else ""
    speech += f", and there {'was' if len(alerts) == 1 else 'were'} {len(alerts)} alert{'s' * (len(alerts) != 1)}."
    return {"adherence": adh, "checkins": checks, "alerts": alerts, "average_mood": avg_mood,
            "speech": speech,
            "card": {"type": "summary", "title": f"{_first_name(person)} · last {days} days",
                     "adherence_pct": adh["pct"], "per_day": adh["per_day"], "average_mood": avg_mood,
                     "moods": [{"at": c["at"], "mood": c["mood"]} for c in checks],
                     "alerts": alerts[:3]}}


@mcp.tool(title="Alert family")
def alert_family(person: str, reason: str, urgency: Literal["low", "medium", "high"] = "medium",
                 recipient: str | None = None) -> dict[str, Any]:
    """Message the person's family right now. `recipient` (a name like "Priya" or a relation like "daughter")
    limits it to one person; leave empty to reach the whole circle."""
    with _db() as conn:
        r = store.send_alert(conn, person, urgency, reason, trigger="manual", only_to=recipient)
    who = " and ".join(r.get("recipients", [])) or "your family"
    return {**r, "speech": f"I've let {who} know.",
            "card": {"type": "alert", "title": "Family notified", "urgency": urgency, "reason": reason,
                     "recipients": r.get("recipients", [])}}


@mcp.resource("carecircle://people/{name}/profile", title="Care profile")
def profile_resource(name: str) -> dict[str, Any]:
    """Profile, family circle and active medications for one person."""
    with _db() as conn:
        person = store.ensure_person(conn, name)
        p = dict(conn.execute("SELECT * FROM people WHERE name=?", (person,)).fetchone())
        meds = [dict(r) for r in conn.execute(
            "SELECT name, dose, times, purpose FROM medications WHERE person=? AND active=1", (person,))]
        return {**p, "family": store.family_of(conn, person), "medications": meds}


@mcp.prompt(title="Morning routine")
def morning_routine(person: str) -> str:
    """A guided morning conversation: greet, check how they slept, go through morning meds."""
    return (f"Greet {person} warmly. Call whats_next for {person}. Ask how they slept and how they feel "
            f"(mood 1-5), then call daily_checkin. Walk through any morning doses one at a time and call "
            f"log_medication for each one they confirm. If CareCircle alerts family, tell {person} gently.")


# ------------------------------------------------------------------ helpers

def _auto_escalate(conn, person: str, notes: str = "") -> list[dict]:
    """The agentic part: decide on our own whether family needs to hear about this."""
    sent = []
    for f in store.assess_risk(conn, person, notes):
        r = store.send_alert(conn, person, f["urgency"], f["reason"], trigger=f["trigger"])
        if not r.get("deduplicated"):
            sent.append(r)
    return sent


def _alert_speech(alerts: list[dict]) -> str:
    if not alerts:
        return ""
    who = sorted({n for a in alerts for n in a["recipients"]})
    names = " and ".join(who) if len(who) <= 2 else ", ".join(who[:-1]) + " and " + who[-1]
    if any(a["urgency"] == "high" for a in alerts):
        return f" I've let {names} know right away so someone can check on you."
    return f" I've sent a note to {names} so they're in the loop."


def _schedule_card(person: str, remaining_only: bool = False) -> dict[str, Any]:
    with _db() as conn:
        sched = store.todays_schedule(conn, person)
    return {"type": "schedule", "title": "Today's medicines",
            "doses": [d for d in sched if not remaining_only or d["status"] == "pending"]}


def _state(person: str) -> dict[str, Any]:
    with _db() as conn:
        person = store.ensure_person(conn, person)
        return {
            "person": person,
            "schedule": store.todays_schedule(conn, person),
            "adherence": store.adherence(conn, person, 7),
            "checkins": store.recent_checkins(conn, person, 7),
            "alerts": store.recent_alerts(conn, person, 7),
            "appointments": store.upcoming_appointments(conn, person, 14),
            "family": store.family_of(conn, person),
        }


def _fmt_time(hhmm: str) -> str:
    return datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p").replace(":00 ", " ")


def _fmt_dt(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    today = datetime.now().date()
    day = "today" if dt.date() == today else "tomorrow" if (dt.date() - today).days == 1 else dt.strftime("on %A")
    return f"{day} at {_fmt_time(dt.strftime('%H:%M'))}"


# ------------------------------------------------------------------ HTTP extras

@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request):
    return JSONResponse({"ok": True, "server": "carecircle"})


@mcp.custom_route("/api/state", methods=["GET"])
async def api_state(request: Request):
    try:
        return JSONResponse(_state(request.query_params.get("person", "Margaret")))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard(_: Request):
    return HTMLResponse((UI_DIR / "dashboard.html").read_text())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"CareCircle MCP server  ->  http://localhost:{port}/mcp   (dashboard: /dashboard)", flush=True)
    mcp.run("streamable-http", host=os.environ.get("HOST", "127.0.0.1"), port=port)
