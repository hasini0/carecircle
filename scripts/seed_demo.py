"""Seed a household and a realistic week of history from config/household.json.

Run:  .venv/bin/python scripts/seed_demo.py
Resets data/carecircle.db and data/outbox.jsonl. The server also calls seed() on first start
and from the "Reset demo" button. Point CARECIRCLE_HOUSEHOLD at another JSON file to demo a
different family without touching code.
"""
import json
import os
import random
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "server"))
import notify  # noqa: E402
import store  # noqa: E402

HOUSEHOLD = Path(os.environ.get("CARECIRCLE_HOUSEHOLD", ROOT / "config" / "household.json"))


def load_household() -> dict:
    return json.loads(HOUSEHOLD.read_text())


def seed() -> None:
    h = load_household()
    name = h["person"]["name"].strip().title()
    history = h.get("demo_history", {})

    conn = store.connect()
    conn.executescript("""DELETE FROM people; DELETE FROM family; DELETE FROM medications; DELETE FROM dose_log;
        DELETE FROM checkins; DELETE FROM appointments; DELETE FROM alerts; DELETE FROM messages;
        DELETE FROM reminders_sent;""")
    notify.OUTBOX.unlink(missing_ok=True)

    random.seed(7)
    store.setup_person(conn, name, h["person"].get("age"), h["person"].get("notes", ""), h.get("family", []))
    for m in h.get("medications", []):
        store.add_medication(conn, name, m["name"], m["dose"], m["times"], m.get("purpose", ""), m.get("pills_left"))

    now = store._now().replace(second=0)
    meds = conn.execute("SELECT name, times FROM medications WHERE person=?", (name,)).fetchall()
    notes = history.get("checkin_notes") or ["Fine, quiet day"]
    for day_ago in range(history.get("days", 6), 0, -1):
        d = (now - timedelta(days=day_ago)).date()
        for m in meds:
            for t in json.loads(m["times"]):
                if random.random() < history.get("adherence", 0.88):  # a couple of realistic misses
                    conn.execute("INSERT INTO dose_log(person, medication, scheduled_for, status, logged_at) "
                                 "VALUES (?,?,?,?,?)", (name, m["name"], f"{d}T{t}", "taken", f"{d}T{t}:00"))
        conn.execute("INSERT INTO checkins(person, at, mood, sleep_hours, pain, notes) VALUES (?,?,?,?,?,?)",
                     (name, f"{d}T09:15:00", random.choice([3, 4, 4, 5]), random.choice([6.5, 7, 7.5, 8]),
                      random.choice([0, 1, 2]), random.choice(notes)))

    # Today: everything due more than 2h ago was taken, so the live demo starts clean.
    for m in meds:
        for t in json.loads(m["times"]):
            slot = now.replace(hour=int(t[:2]), minute=int(t[3:]))
            if slot + timedelta(hours=2) < now:
                conn.execute("INSERT INTO dose_log(person, medication, scheduled_for, status, logged_at) "
                             "VALUES (?,?,?,?,?)", (name, m["name"], slot.strftime("%Y-%m-%dT%H:%M"),
                                                    "taken", slot.isoformat()))

    if h.get("family"):
        conn.execute("INSERT INTO alerts(person, at, urgency, reason, recipients, trigger) VALUES (?,?,?,?,?,?)",
                     (name, (now - timedelta(days=4)).isoformat(), "low",
                      f"{name} has slept under 5 hours two nights running", json.dumps([h["family"][0]["name"]]),
                      "poor_sleep_trend"))
    for a in h.get("appointments", []):
        hh, mm = (int(x) for x in a.get("time", "10:00").split(":"))
        at = (now + timedelta(days=a.get("in_days", 1))).replace(hour=hh, minute=mm)
        conn.execute("INSERT INTO appointments(person, at, title, location) VALUES (?,?,?,?)",
                     (name, at.isoformat(), a["title"], a.get("location", "")))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed()
    print(f"Seeded {load_household()['person']['name']} from {HOUSEHOLD} ->", store.DB_PATH)
