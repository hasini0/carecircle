"""CareCircle risk rules: the judgment that decides when family should hear about something.

Each rule is a plain function registered with @rule. It receives a RuleContext (the database
connection, the person, and the words they just said) and returns zero or more Findings.
The engine runs every enabled rule after each dose log and check-in, and store.send_alert
de-duplicates by `trigger`, so a rule can fire freely without spamming the family.

Adding a rule (see docs/EXTENDING.md):

    @rule("hydration", description="No water logged by 3 PM")
    def hydration(ctx: RuleContext) -> list[Finding]:
        ...
        return [Finding("low", f"{ctx.person} hasn't logged any water today", "hydration")]

Rules can be switched off without code changes:  CARECIRCLE_DISABLED_RULES=poor_sleep,refills
"""
from __future__ import annotations

import os
from dataclasses import dataclass, asdict
from typing import Callable, Literal

import store

Urgency = Literal["low", "medium", "high"]


@dataclass
class Finding:
    urgency: Urgency
    reason: str
    trigger: str  # stable id used for de-duplication, e.g. "missed_doses" or "refill_metformin"

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class RuleContext:
    conn: object
    person: str
    latest_notes: str = ""


@dataclass
class Rule:
    name: str
    description: str
    fn: Callable[[RuleContext], list[Finding]]


REGISTRY: dict[str, Rule] = {}


def rule(name: str, description: str = ""):
    """Register a risk rule under a unique name."""
    def decorator(fn: Callable[[RuleContext], list[Finding]]):
        if name in REGISTRY:
            raise ValueError(f"Risk rule {name!r} is already registered")
        REGISTRY[name] = Rule(name, description or (fn.__doc__ or "").strip(), fn)
        return fn
    return decorator


def enabled_rules() -> list[Rule]:
    disabled = {n.strip() for n in os.environ.get("CARECIRCLE_DISABLED_RULES", "").split(",") if n.strip()}
    return [r for name, r in REGISTRY.items() if name not in disabled]


def evaluate(conn, person: str, latest_notes: str = "") -> list[dict]:
    """Run every enabled rule. A rule that crashes is skipped so one bad rule can't silence the rest."""
    ctx = RuleContext(conn, person, latest_notes)
    findings: list[dict] = []
    for r in enabled_rules():
        try:
            findings += [f.as_dict() for f in (r.fn(ctx) or [])]
        except Exception as e:  # pragma: no cover - defensive
            print(f"  !! risk rule {r.name!r} failed: {e}", flush=True)
    return findings


# ---------------------------------------------------------------- built-in rules

RED_FLAGS = {
    "fell": "reported a fall",
    "fall": "reported a fall",
    "chest pain": "reported chest pain",
    "dizzy": "reported dizziness",
    "can't breathe": "reported trouble breathing",
    "short of breath": "reported shortness of breath",
    "confused": "reported confusion",
    "can't get up": "can't get up",
    "need help": "asked for help",
    "help me": "asked for help",
}


@rule("red_flag_phrase", "A worrying phrase (fall, dizziness, chest pain, help me…) in what the person just said")
def red_flag_phrase(ctx: RuleContext) -> list[Finding]:
    text = ctx.latest_notes.lower()
    for word, reason in RED_FLAGS.items():
        if word in text:
            return [Finding("high", f"{ctx.person} {reason}: \"{ctx.latest_notes}\"", "red_flag_phrase")]
    return []


@rule("missed_doses", "Two or more missed or skipped doses today")
def missed_doses(ctx: RuleContext) -> list[Finding]:
    missed = [d for d in store.todays_schedule(ctx.conn, ctx.person) if d["status"] in ("missed", "skipped")]
    if len(missed) < 2:
        return []
    names = ", ".join(f"{d['medication']} ({d['time']})" for d in missed)
    return [Finding("medium", f"{ctx.person} has missed {len(missed)} doses today: {names}", "missed_doses")]


@rule("low_mood", "Mood of 2/5 or lower in the last two check-ins")
def low_mood(ctx: RuleContext) -> list[Finding]:
    moods = [c["mood"] for c in store.recent_checkins(ctx.conn, ctx.person, days=3)]
    if len(moods) >= 2 and all(m <= 2 for m in moods[-2:]):
        return [Finding("medium", f"{ctx.person}'s mood has been low ({moods[-2]}/5, {moods[-1]}/5) "
                        "in the last two check-ins", "low_mood_trend")]
    return []


@rule("refills", "A tracked medicine with 5 days of supply or less")
def refills(ctx: RuleContext) -> list[Finding]:
    return [Finding("low", f"{ctx.person}'s {r['medication']} runs out in {r['days_left']} "
                    f"day{'s' * (r['days_left'] != 1)} ({r['pills_left']} left) - time to order a refill",
                    f"refill_{r['medication'].lower()}")
            for r in store.refill_status(ctx.conn, ctx.person) if r["low"]]


@rule("poor_sleep", "Under 5 hours of sleep two nights running")
def poor_sleep(ctx: RuleContext) -> list[Finding]:
    sleeps = [c["sleep_hours"] for c in store.recent_checkins(ctx.conn, ctx.person, days=3)
              if c["sleep_hours"] is not None]
    if len(sleeps) >= 2 and all(s < 5 for s in sleeps[-2:]):
        return [Finding("low", f"{ctx.person} has slept under 5 hours two nights running", "poor_sleep_trend")]
    return []
