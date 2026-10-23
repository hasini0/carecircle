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


# Demo "time travel": the care clock can be shifted so a recorded demo can jump to 8 PM dose time.
_clock_offset = timedelta(0)


def set_clock(hhmm: str | None) -> None:
    """Shift the care clock to a time of day today, as if the day had gone normally until then.

    Doses scheduled before that time are marked taken; doses at or after it are reopened (and can be
    reminded again), so jumping to 20:00 means "it's 8 PM and the evening dose is due" whatever the
    real time is where the server runs.
    """
    global _clock_offset
    if not hhmm:
        _clock_offset = timedelta(0)
        return
    real = datetime.now()
    target = datetime.combine(real.date(), datetime.strptime(hhmm, "%H:%M").time())  # ValueError if bad
    _clock_offset = target - real
    day, slot = real.date().isoformat(), target.strftime("%Y-%m-%dT%H:%M")
    conn = connect()
    conn.execute("DELETE FROM dose_log WHERE scheduled_for>=? AND scheduled_for<?", (slot, f"{day}T99"))
    conn.execute("DELETE FROM reminders_sent WHERE scheduled_for>=?", (slot,))
    conn.execute("DELETE FROM alerts WHERE at>=?", (target.isoformat(),))
    conn.execute("DELETE FROM checkins WHERE at>=?", (target.isoformat(),))
    for m in conn.execute("SELECT person, name, times FROM medications WHERE active=1").fetchall():
        for t in json.loads(m["times"]):
            s_for = f"{day}T{t}"
            if s_for < slot and not conn.execute("SELECT 1 FROM dose_log WHERE person=? AND medication=? AND "
                                                 "scheduled_for=?", (m["person"], m["name"], s_for)).fetchone():
                conn.execute("INSERT INTO dose_log(person, medication, scheduled_for, status, logged_at) "
                             "VALUES (?,?,?,?,?)", (m["person"], m["name"], s_for, "taken", f"{s_for}:00"))
                conn.execute("INSERT OR IGNORE INTO reminders_sent(person, scheduled_for) VALUES (?,?)",
                             (m["person"], s_for))
    conn.commit()
    conn.close()


def _now() -> datetime:
    return (datetime.now() + _clock_offset).replace(microsecond=0)


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
            purpose TEXT, active INTEGER DEFAULT 1, pills_left INTEGER
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY, person TEXT, at TEXT, sender TEXT, body TEXT, delivered_at TEXT
        );
        CREATE TABLE IF NOT EXISTS reminders_sent (
            person TEXT, scheduled_for TEXT, PRIMARY KEY (person, scheduled_for)
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

def add_medication(conn, person: str, name: str, dose: str, times: list[str], purpose: str = "",
                   pills_left: int | None = None) -> dict:
    person = ensure_person(conn, person)
    conn.execute(
        "INSERT INTO medications(person, name, dose, times, purpose, pills_left) VALUES (?,?,?,?,?,?)",
        (person, name.title(), dose, json.dumps(sorted(times)), purpose, pills_left),
    )
    conn.commit()
    return {"person": person, "medication": name.title(), "dose": dose, "times": sorted(times),
            "pills_left": pills_left}


def refill_status(conn, person: str) -> list[dict]:
    """Days of supply left per medication (only for meds where a pill count is tracked)."""
    person = ensure_person(conn, person)
    out = []
    for m in conn.execute("SELECT name, dose, times, pills_left FROM medications WHERE person=? AND active=1 "
                          "AND pills_left IS NOT NULL", (person,)):
        per_day = len(json.loads(m["times"])) or 1
        days = m["pills_left"] // per_day
        out.append({"medication": m["name"], "dose": m["dose"], "pills_left": m["pills_left"],
                    "days_left": days, "low": days <= 5})
    return sorted(out, key=lambda r: r["days_left"])


def refill_medication(conn, person: str, medication: str, pills: int) -> dict:
    person = ensure_person(conn, person)
    cur = conn.execute("UPDATE medications SET pills_left = COALESCE(pills_left, 0) + ? WHERE person=? "
                       "AND lower(name)=lower(?) AND active=1", (pills, person, medication.strip()))
    if not cur.rowcount:
        raise ValueError(f"{medication} isn't on {person}'s schedule.")
    conn.commit()
    return next(r for r in refill_status(conn, person) if r["medication"].lower() == medication.strip().lower())


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
    if taken and best["status"] != "taken":
        conn.execute("UPDATE medications SET pills_left = MAX(pills_left - 1, 0) WHERE person=? AND name=? "
                     "AND pills_left IS NOT NULL", (person, best["medication"]))
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

    for r in refill_status(conn, person):
        if r["low"]:
            findings.append({"urgency": "low", "reason": f"{person}'s {r['medication']} runs out in {r['days_left']} "
                             f"day{'s' * (r['days_left'] != 1)} ({r['pills_left']} left) - time to order a refill",
                             "trigger": f"refill_{r['medication'].lower()}"})

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
    return {"deduplicated": False, "urgency": urgency, "reason": reason, "recipients": recipients, "trigger": trigger,
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


# ---------- family -> person messages (the two-way loop) ----------

def send_family_message(conn, person: str, sender: str, body: str) -> dict:
    person = ensure_person(conn, person)
    conn.execute("INSERT INTO messages(person, at, sender, body) VALUES (?,?,?,?)",
                 (person, _now().isoformat(), sender.strip().title(), body.strip()))
    conn.commit()
    return {"person": person, "sender": sender.strip().title(), "body": body.strip()}


def take_family_messages(conn, person: str) -> list[dict]:
    """Undelivered messages for the person; marks them delivered (Alexa has now read them out)."""
    person = ensure_person(conn, person)
    rows = [dict(r) for r in conn.execute(
        "SELECT id, at, sender, body FROM messages WHERE person=? AND delivered_at IS NULL ORDER BY at", (person,))]
    if rows:
        conn.execute(f"UPDATE messages SET delivered_at=? WHERE id IN ({','.join('?' * len(rows))})",
                     (_now().isoformat(), *[r["id"] for r in rows]))
        conn.commit()
    return rows


def pending_message_count(conn, person: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE person=? AND delivered_at IS NULL",
                        (person,)).fetchone()[0]


# ---------- proactive reminders ----------

def due_reminders(conn, person: str) -> list[dict]:
    """Doses that are due now (from their time up to 2h after) and haven't been reminded yet.

    Each dose is reminded once; the device polls this so Alexa+ can speak first.
    """
    person = ensure_person(conn, person)
    now = _now()
    due = []
    for d in todays_schedule(conn, person):
        slot = datetime.fromisoformat(d["scheduled_for"])
        if d["status"] == "pending" and slot <= now <= slot + timedelta(hours=2):
            if not conn.execute("SELECT 1 FROM reminders_sent WHERE person=? AND scheduled_for=?",
                                (person, d["scheduled_for"])).fetchone():
                due.append(d)
    conn.executemany("INSERT OR IGNORE INTO reminders_sent(person, scheduled_for) VALUES (?,?)",
                     [(person, d["scheduled_for"]) for d in due])
    conn.commit()
    return due


# ---------- doctor visit prep ----------

def doctor_summary(conn, person: str, days: int = 30) -> dict:
    """A one-page brief to take to the next appointment."""
    person = ensure_person(conn, person)
    first = conn.execute("SELECT MIN(scheduled_for) FROM dose_log WHERE person=?", (person,)).fetchone()[0]
    if first:  # don't count days before CareCircle was tracking anything as "missed"
        days = max(1, min(days, (_now().date() - datetime.fromisoformat(first).date()).days + 1))
    adh = adherence(conn, person, days)
    checks = recent_checkins(conn, person, days)
    moods = [c["mood"] for c in checks]
    sleeps = [c["sleep_hours"] for c in checks if c["sleep_hours"] is not None]
    pains = [c["pain"] for c in checks if c["pain"] is not None]
    flagged = [c for c in checks if any(w in (c["notes"] or "").lower() for w in RED_FLAGS)]
    missed_by_med: dict[str, int] = {}
    today = _now().date()
    for i in range(days - 1, 0, -1):
        for s in _schedule_for_day(conn, person, today - timedelta(days=i)):
            if s["status"] != "taken":
                missed_by_med[s["medication"]] = missed_by_med.get(s["medication"], 0) + 1
    appts = upcoming_appointments(conn, person, days=14)
    return {
        "person": person, "period_days": days, "next_appointment": appts[0] if appts else None,
        "adherence_pct": adh["pct"], "missed_by_medication": missed_by_med,
        "average_mood": round(sum(moods) / len(moods), 1) if moods else None,
        "average_sleep": round(sum(sleeps) / len(sleeps), 1) if sleeps else None,
        "max_pain": max(pains) if pains else None,
        "symptoms_reported": [{"at": c["at"], "notes": c["notes"]} for c in flagged],
        "alerts": recent_alerts(conn, person, days),
        "medications": [dict(r) for r in conn.execute(
            "SELECT name, dose, times, purpose FROM medications WHERE person=? AND active=1", (person,))],
    }


# ---------- activity feed for the family app ----------

def activity_feed(conn, person: str, limit: int = 25) -> list[dict]:
    person = ensure_person(conn, person)
    since = (_now() - timedelta(days=3)).isoformat()
    items = []
    for r in conn.execute("SELECT medication, status, logged_at FROM dose_log WHERE person=? AND logged_at>=?",
                          (person, since)):
        items.append({"at": r["logged_at"], "kind": "dose", "status": r["status"],
                      "text": f"{'Took' if r['status'] == 'taken' else 'Skipped'} {r['medication']}"})
    for r in conn.execute("SELECT at, mood, notes FROM checkins WHERE person=? AND at>=?", (person, since)):
        items.append({"at": r["at"], "kind": "checkin", "mood": r["mood"],
                      "text": f"Check-in · mood {r['mood']}/5" + (f" · “{r['notes']}”" if r["notes"] else "")})
    for r in conn.execute("SELECT at, urgency, reason FROM alerts WHERE person=? AND at>=?", (person, since)):
        items.append({"at": r["at"], "kind": "alert", "urgency": r["urgency"], "text": r["reason"]})
    for r in conn.execute("SELECT at, sender, body, delivered_at FROM messages WHERE person=? AND at>=?",
                          (person, since)):
        items.append({"at": r["at"], "kind": "message", "delivered": r["delivered_at"] is not None,
                      "text": f"{r['sender']}: “{r['body']}”"})
    return sorted(items, key=lambda i: i["at"], reverse=True)[:limit]
