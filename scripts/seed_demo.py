"""Seed a realistic week of history for the demo persona, Margaret (78).

Run:  .venv/bin/python scripts/seed_demo.py
Resets data/carecircle.db and data/outbox.jsonl.
"""
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "server"))
import store  # noqa: E402

for f in (store.DB_PATH, ROOT / "data" / "outbox.jsonl"):
    f.unlink(missing_ok=True)

random.seed(7)
conn = store.connect()
store.setup_person(conn, "Margaret", 78, "Lives alone. Type 2 diabetes, high blood pressure. Loves gardening.", [
    {"name": "Priya", "relation": "daughter", "contact": "+1-555-0142"},
    {"name": "Dev", "relation": "son", "contact": "+1-555-0177"},
])
store.add_medication(conn, "Margaret", "Metformin", "500 mg", ["08:00", "20:00"], "blood sugar")
store.add_medication(conn, "Margaret", "Lisinopril", "10 mg", ["08:00"], "blood pressure")
store.add_medication(conn, "Margaret", "Vitamin D", "1000 IU", ["13:00"], "bones")

now = datetime.now().replace(second=0, microsecond=0)
notes = ["Slept well, did some gardening", "A bit tired today", "Feeling good, Priya called",
         "Knee is sore after the walk", "Lovely day, made soup", "Fine, quiet day"]
for day_ago in range(6, 0, -1):
    d = (now - timedelta(days=day_ago)).date()
    for m in conn.execute("SELECT name, times FROM medications WHERE person='Margaret'").fetchall():
        for t in json.loads(m["times"]):
            # mostly good adherence, a couple of realistic misses
            if random.random() < 0.88:
                conn.execute("INSERT INTO dose_log(person, medication, scheduled_for, status, logged_at) VALUES "
                             "(?,?,?,?,?)", ("Margaret", m["name"], f"{d}T{t}", "taken", f"{d}T{t}:00"))
    conn.execute("INSERT INTO checkins(person, at, mood, sleep_hours, pain, notes) VALUES (?,?,?,?,?,?)",
                 ("Margaret", f"{d}T09:15:00", random.choice([3, 4, 4, 5]), random.choice([6.5, 7, 7.5, 8]),
                  random.choice([0, 1, 2]), random.choice(notes)))

conn.execute("INSERT INTO alerts(person, at, urgency, reason, recipients, trigger) VALUES (?,?,?,?,?,?)",
             ("Margaret", (now - timedelta(days=4)).isoformat(), "low",
              "Margaret has slept under 5 hours two nights running", json.dumps(["Priya"]), "poor_sleep_trend"))
conn.execute("INSERT INTO appointments(person, at, title, location) VALUES (?,?,?,?)",
             ("Margaret", (now + timedelta(days=1)).replace(hour=10, minute=30).isoformat(),
              "Dr. Chen, diabetes check-up", "Riverside Clinic"))
conn.commit()
print("Seeded Margaret with 6 days of history ->", store.DB_PATH)

# Today: everything due more than 2h ago was taken, so the live demo starts clean.
today = now.date()
for m in conn.execute("SELECT name, times FROM medications WHERE person='Margaret'").fetchall():
    for t in json.loads(m["times"]):
        slot = datetime.fromisoformat(f"{today}T{t}")
        if slot + timedelta(hours=2) < now:
            conn.execute("INSERT INTO dose_log(person, medication, scheduled_for, status, logged_at) VALUES (?,?,?,?,?)",
                         ("Margaret", m["name"], f"{today}T{t}", "taken", slot.isoformat()))
conn.commit()
