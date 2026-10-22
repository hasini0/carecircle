"""Seed a realistic week of history for the demo persona, Margaret (78).

Run:  .venv/bin/python scripts/seed_demo.py
Resets data/carecircle.db and data/outbox.jsonl. The server also calls seed() on first start
and from the "Reset demo" button.
"""
import json
import random
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "server"))
import store  # noqa: E402

NOTES = ["Slept well, did some gardening", "A bit tired today", "Feeling good, Priya called",
         "Knee is sore after the walk", "Lovely day, made soup", "Fine, quiet day"]


def seed() -> None:
    conn = store.connect()
    conn.executescript("""DELETE FROM people; DELETE FROM family; DELETE FROM medications; DELETE FROM dose_log;
        DELETE FROM checkins; DELETE FROM appointments; DELETE FROM alerts; DELETE FROM messages;
        DELETE FROM reminders_sent;""")
    (ROOT / "data" / "outbox.jsonl").unlink(missing_ok=True)

    random.seed(7)
    store.setup_person(conn, "Margaret", 78, "Lives alone. Type 2 diabetes, high blood pressure. Loves gardening.", [
        {"name": "Priya", "relation": "daughter", "contact": "+1-555-0142"},
        {"name": "Dev", "relation": "son", "contact": "+1-555-0177"},
    ])
    # Metformin at 12 pills = 6 days; the next dose taken tips it to 5 days and triggers a refill alert.
    store.add_medication(conn, "Margaret", "Metformin", "500 mg", ["08:00", "20:00"], "blood sugar", pills_left=12)
    store.add_medication(conn, "Margaret", "Lisinopril", "10 mg", ["08:00"], "blood pressure", pills_left=24)
    store.add_medication(conn, "Margaret", "Vitamin D", "1000 IU", ["13:00"], "bones", pills_left=40)

    now = store._now().replace(second=0)
    meds = conn.execute("SELECT name, times FROM medications WHERE person='Margaret'").fetchall()
    for day_ago in range(6, 0, -1):
        d = (now - timedelta(days=day_ago)).date()
        for m in meds:
            for t in json.loads(m["times"]):
                if random.random() < 0.88:  # mostly good adherence, a couple of realistic misses
                    conn.execute("INSERT INTO dose_log(person, medication, scheduled_for, status, logged_at) "
                                 "VALUES (?,?,?,?,?)", ("Margaret", m["name"], f"{d}T{t}", "taken", f"{d}T{t}:00"))
        conn.execute("INSERT INTO checkins(person, at, mood, sleep_hours, pain, notes) VALUES (?,?,?,?,?,?)",
                     ("Margaret", f"{d}T09:15:00", random.choice([3, 4, 4, 5]), random.choice([6.5, 7, 7.5, 8]),
                      random.choice([0, 1, 2]), random.choice(NOTES)))

    # Today: everything due more than 2h ago was taken, so the live demo starts clean.
    for m in meds:
        for t in json.loads(m["times"]):
            slot = now.replace(hour=int(t[:2]), minute=int(t[3:]))
            if slot + timedelta(hours=2) < now:
                conn.execute("INSERT INTO dose_log(person, medication, scheduled_for, status, logged_at) "
                             "VALUES (?,?,?,?,?)", ("Margaret", m["name"], slot.strftime("%Y-%m-%dT%H:%M"),
                                                    "taken", slot.isoformat()))

    conn.execute("INSERT INTO alerts(person, at, urgency, reason, recipients, trigger) VALUES (?,?,?,?,?,?)",
                 ("Margaret", (now - timedelta(days=4)).isoformat(), "low",
                  "Margaret has slept under 5 hours two nights running", json.dumps(["Priya"]), "poor_sleep_trend"))
    conn.execute("INSERT INTO appointments(person, at, title, location) VALUES (?,?,?,?)",
                 ("Margaret", (now + timedelta(days=1)).replace(hour=10, minute=30).isoformat(),
                  "Dr. Chen, diabetes check-up", "Riverside Clinic"))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    seed()
    print("Seeded Margaret with 6 days of history ->", store.DB_PATH)
