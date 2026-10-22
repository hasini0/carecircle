"""CareCircle persistence + care logic.

Everything lives in one SQLite file so state survives across Alexa+ sessions,
server restarts and devices. No network calls here - the MCP layer wraps it.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path

DB_PATH = Path(os.environ.get("CARECIRCLE_DB", Path(__file__).parent.parent / "data" / "carecircle.db"))

# Words in a check-in that should escalate straight to family, no waiting for trends.
RED_FLAGS = {
    "fell": "reported a fall",
    "fall": "reported a fall",
    "chest pain": "reported chest pain",
    "dizzy": "reported dizziness",
    "can't breathe": "reported trouble breathing",
    "short of breath": "reported shortness of breath",
    "confused": "reported confusion",
}


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS people (
            name TEXT PRIMARY KEY, age INTEGER, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS family (
            id INTEGER PRIMARY KEY, person TEXT, name TEXT, relation TEXT, contact TEXT
        );
        CREATE TABLE IF NOT EXISTS medications (
            id INTEGER PRIMARY KEY, person TEXT, name TEXT, dose TEXT, times TEXT,
            purpose TEXT, active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS dose_log (
            id INTEGER PRIMARY KEY, person TEXT, medication TEXT, scheduled_for TEXT,
            status TEXT, logged_at TEXT
        );
        CREATE TABLE IF NOT EXISTS checkins (
            id INTEGER PRIMARY KEY, person TEXT, at TEXT, mood INTEGER, sleep_hours REAL,
            pain INTEGER, notes TEXT
        );
        CREATE TABLE IF NOT EXISTS appointments (
            id INTEGER PRIMARY KEY, person TEXT, at TEXT, title TEXT, location TEXT
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY, person TEXT, at TEXT, urgency TEXT, reason TEXT,
            recipients TEXT, trigger TEXT
        );
        """
    )
    return conn


# ---------- people & family ----------

def ensure_person(conn, person: str) -> str:
    person = person.strip().title()
    if not conn.execute("SELECT 1 FROM people WHERE name=?", (person,)).fetchone():
        raise ValueError(f"I don't have a care profile for {person} yet. Use setup_person first.")
    return person


def setup_person(conn, name: str, age: int | None, notes: str, family: list[dict]) -> dict:
    name = name.strip().title()
    conn.execute("INSERT OR REPLACE INTO people(name, age, notes) VALUES (?,?,?)", (name, age, notes))
    conn.execute("DELETE FROM family WHERE person=?", (name,))
    for f in family:
        conn.execute(
            "INSERT INTO family(person, name, relation, contact) VALUES (?,?,?,?)",
            (name, f.get("name"), f.get("relation", "family"), f.get("contact", "")),
        )
    conn.commit()
    return {"person": name, "family_count": len(family)}


def family_of(conn, person: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT name, relation, contact FROM family WHERE person=?", (person,))]


# ---------- medications ----------

def add_medication(conn, person: str, name: str, dose: str, times: list[str], purpose: str = "") -> dict:
    person = ensure_person(conn, person)
    conn.execute(
        "INSERT INTO medications(person, name, dose, times, purpose) VALUES (?,?,?,?,?)",
        (person, name.title(), dose, json.dumps(sorted(times)), purpose),
    )
    conn.commit()
    return {"person": person, "medication": name.title(), "dose": dose, "times": sorted(times)}


def _schedule_for_day(conn, person: str, day: date) -> list[dict]:
    """Every dose due on `day`, joined with whatever was logged for it."""
    out = []
    for m in conn.execute("SELECT * FROM medications WHERE person=? AND active=1", (person,)):
        for t in json.loads(m["times"]):
            slot = f"{day.isoformat()}T{t}"
            log = conn.execute(
                "SELECT status, logged_at FROM dose_log WHERE person=? AND medication=? AND scheduled_for=?",
                (person, m["name"], slot),
            ).fetchone()
            out.append({
                "medication": m["name"], "dose": m["dose"], "purpose": m["purpose"], "time": t,
                "scheduled_for": slot,
                "status": log["status"] if log else "pending",
                "logged_at": log["logged_at"] if log else None,
            })
    return sorted(out, key=lambda d: d["time"])


def todays_schedule(conn, person: str) -> list[dict]:
    person = ensure_person(conn, person)
    now = _now()
    sched = _schedule_for_day(conn, person, now.date())
    for d in sched:
        if d["status"] == "pending" and datetime.fromisoformat(d["scheduled_for"]) + timedelta(hours=2) < now:
            d["status"] = "missed"
    return sched


def log_dose(conn, person: str, medication: str, taken: bool = True) -> dict:
    """Attach the dose to the closest scheduled slot today."""
    person = ensure_person(conn, person)
    now = _now()
    candidates = [d for d in _schedule_for_day(conn, person, now.date())
                  if d["medication"].lower() == medication.strip().lower()]
    if not candidates:
        raise ValueError(f"{medication} isn't on {person}'s schedule.")
    best = min(candidates, key=lambda d: abs(datetime.fromisoformat(d["scheduled_for"]) - now))
    status = "taken" if taken else "skipped"
    conn.execute(
        "DELETE FROM dose_log WHERE person=? AND medication=? AND scheduled_for=?",
        (person, best["medication"], best["scheduled_for"]),
    )
    conn.execute(
        "INSERT INTO dose_log(person, medication, scheduled_for, status, logged_at) VALUES (?,?,?,?,?)",
        (person, best["medication"], best["scheduled_for"], status, now.isoformat()),
    )
    conn.commit()
    return {**best, "status": status, "logged_at": now.isoformat()}


def adherence(conn, person: str, days: int = 7) -> dict:
    person = ensure_person(conn, person)
    today = _now().date()
    due = taken = 0
    per_day = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        sched = todays_schedule(conn, person) if i == 0 else _schedule_for_day(conn, person, d)
        # today's pending doses aren't "due" yet
        counted = [s for s in sched if not (i == 0 and s["status"] == "pending")]
        t = sum(1 for s in counted if s["status"] == "taken")
        due += len(counted)
        taken += t
        per_day.append({"date": d.isoformat(), "due": len(counted), "taken": t})
    return {"pct": round(100 * taken / due) if due else 100, "due": due, "taken": taken, "per_day": per_day}


# ---------- check-ins, risk engine, alerts ----------

def record_checkin(conn, person: str, mood: int, notes: str = "", sleep_hours: float | None = None,
                   pain: int | None = None) -> dict:
    person = ensure_person(conn, person)
    mood = max(1, min(5, int(mood)))
    conn.execute(
        "INSERT INTO checkins(person, at, mood, sleep_hours, pain, notes) VALUES (?,?,?,?,?,?)",
        (person, _now().isoformat(), mood, sleep_hours, pain, notes),
    )
    conn.commit()
    return {"person": person, "mood": mood, "sleep_hours": sleep_hours, "pain": pain, "notes": notes}


def recent_checkins(conn, person: str, days: int = 7) -> list[dict]:
    since = (_now() - timedelta(days=days)).isoformat()
    return [dict(r) for r in conn.execute(
        "SELECT at, mood, sleep_hours, pain, notes FROM checkins WHERE person=? AND at>=? ORDER BY at",
        (person, since),
    )]


def assess_risk(conn, person: str, latest_notes: str = "") -> list[dict]:
    """Rules that decide on their own whether family should hear about something.

    Returns a list of {urgency, reason, trigger}. Empty list = all good.
    """
    person = ensure_person(conn, person)
    findings = []

    text = latest_notes.lower()
    for word, reason in RED_FLAGS.items():
        if word in text:
            findings.append({"urgency": "high", "reason": f"{person} {reason}: \"{latest_notes}\"",
                             "trigger": "red_flag_phrase"})
            break

    missed = [d for d in todays_schedule(conn, person) if d["status"] in ("missed", "skipped")]
    if len(missed) >= 2:
        names = ", ".join(f"{d['medication']} ({d['time']})" for d in missed)
        findings.append({"urgency": "medium", "reason": f"{person} has missed {len(missed)} doses today: {names}",
                         "trigger": "missed_doses"})

    moods = [c["mood"] for c in recent_checkins(conn, person, days=3)]
    if len(moods) >= 2 and all(m <= 2 for m in moods[-2:]):
        findings.append({"urgency": "medium", "reason": f"{person}'s mood has been low ({moods[-2]}/5, {moods[-1]}/5) "
                         "in the last two check-ins", "trigger": "low_mood_trend"})

    sleeps = [c["sleep_hours"] for c in recent_checkins(conn, person, days=3) if c["sleep_hours"] is not None]
    if len(sleeps) >= 2 and all(s < 5 for s in sleeps[-2:]):
        findings.append({"urgency": "low", "reason": f"{person} has slept under 5 hours two nights running",
                         "trigger": "poor_sleep_trend"})
    return findings


def send_alert(conn, person: str, urgency: str, reason: str, trigger: str = "manual",
               only_to: str | None = None) -> dict:
    """Record a family alert. Delivery is pluggable (see notify.py); the log is the source of truth."""
    person = ensure_person(conn, person)
    # don't spam: same trigger within 6h is deduplicated
    since = (_now() - timedelta(hours=6)).isoformat()
    dup = conn.execute("SELECT id FROM alerts WHERE person=? AND trigger=? AND at>=? AND trigger!='manual'",
                       (person, trigger, since)).fetchone()
    if dup:
        return {"deduplicated": True, "reason": reason}
    fam = family_of(conn, person)
    if only_to:
        fam = [f for f in fam if only_to.lower() in (f["name"].lower(), f["relation"].lower())] or fam
    recipients = [f["name"] for f in fam] if urgency != "low" else [f["name"] for f in fam[:1]]
    conn.execute(
        "INSERT INTO alerts(person, at, urgency, reason, recipients, trigger) VALUES (?,?,?,?,?,?)",
        (person, _now().isoformat(), urgency, reason, json.dumps(recipients), trigger),
    )
    conn.commit()
    from notify import deliver  # local import keeps store.py dependency-free
    delivery = deliver(fam if urgency != "low" else fam[:1], person, urgency, reason)
    return {"deduplicated": False, "urgency": urgency, "reason": reason, "recipients": recipients,
            "delivery": delivery}


def recent_alerts(conn, person: str, days: int = 7) -> list[dict]:
    since = (_now() - timedelta(days=days)).isoformat()
    rows = conn.execute("SELECT at, urgency, reason, recipients, trigger FROM alerts WHERE person=? AND at>=? "
                        "ORDER BY at DESC", (person, since))
    return [{**dict(r), "recipients": json.loads(r["recipients"])} for r in rows]


# ---------- appointments ----------

def add_appointment(conn, person: str, when: str, title: str, location: str = "") -> dict:
    person = ensure_person(conn, person)
    at = datetime.fromisoformat(when).isoformat()
    conn.execute("INSERT INTO appointments(person, at, title, location) VALUES (?,?,?,?)",
                 (person, at, title, location))
    conn.commit()
    return {"person": person, "at": at, "title": title, "location": location}


def upcoming_appointments(conn, person: str, days: int = 14) -> list[dict]:
    now = _now()
    return [dict(r) for r in conn.execute(
        "SELECT at, title, location FROM appointments WHERE person=? AND at>=? AND at<=? ORDER BY at",
        (person, now.isoformat(), (now + timedelta(days=days)).isoformat()),
    )]
