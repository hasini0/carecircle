"""The Alexa+ side: an agent that talks to the CareCircle MCP server as a real MCP client.

Brain, picked automatically (override with CARECIRCLE_BRAIN=bedrock|anthropic|local):
  1. Amazon Bedrock (Converse API + tool use)  - if AWS credentials are available
  2. Anthropic API                              - if ANTHROPIC_API_KEY is set
  3. Local planner                              - no keys needed; deterministic intent routing

Whichever brain is used, every action goes through MCP tools/call over Streamable HTTP.
"""
from __future__ import annotations

import functools
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from mcp import Client

MCP_URL = os.environ.get("CARECIRCLE_MCP_URL", "http://localhost:8000/mcp")
def _household_person() -> str:
    """Default person: CARECIRCLE_PERSON, else the name in the household config, else Margaret."""
    path = Path(os.environ.get("CARECIRCLE_HOUSEHOLD", Path(__file__).parent.parent / "config" / "household.json"))
    try:
        return json.loads(path.read_text())["person"]["name"].strip().title()
    except Exception:
        return "Margaret"


PERSON = os.environ.get("CARECIRCLE_PERSON") or _household_person()
BEDROCK_MODEL = os.environ.get("CARECIRCLE_BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
ANTHROPIC_MODEL = os.environ.get("CARECIRCLE_ANTHROPIC_MODEL", "claude-sonnet-5-5")

SYSTEM = (
    f"You are Alexa+, speaking with {PERSON}, an older adult who lives alone. You have the CareCircle tools. "
    f"Always pass person=\"{PERSON}\". When they mention medicine they took, call log_medication once per "
    "medication. When they describe how they feel, slept, pain or anything worrying, call daily_checkin with "
    "mood 1-5 and their own words as notes. For 'what's next' use whats_next. Reply in at most two short, warm, "
    "spoken sentences - no markdown, no lists. Each tool result includes a `speech` field you can reuse. "
    "If they ask to hear messages from family, call get_family_messages. For refills use get_refill_status or "
    "record_refill; before a doctor's appointment use prepare_doctor_visit; to show things on screen use "
    "show_care_dashboard."
)


@dataclass
class Turn:
    speech: str = ""
    cards: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)  # shown in the simulator's "under the hood" panel
    brain: str = ""


@functools.cache
def pick_brain() -> str:
    """Decided once per process: this runs on hot paths (/api/info is polled every 2 s)."""
    forced = os.environ.get("CARECIRCLE_BRAIN")
    if forced:
        return forced
    # Only ask boto3 when AWS credentials are actually configured. Otherwise its credential chain
    # falls through to the EC2 metadata endpoint, which on hosts like Render blocks for seconds
    # and starves the event loop (health checks fail -> restarts -> 502s).
    aws_configured = (os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE")
                      or os.path.exists(os.path.expanduser("~/.aws/credentials")))
    if aws_configured:
        try:
            import boto3
            if boto3.Session().get_credentials() is not None:
                return "bedrock"
        except Exception:
            pass
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "local"


async def _call(client: Client, turn: Turn, name: str, args: dict) -> dict:
    t0 = time.perf_counter()
    res = await client.call_tool(name, args)
    data: dict[str, Any] = res.structured_content or {}
    if not data and res.content:
        try:
            data = json.loads(res.content[0].text)
        except Exception:
            data = {"speech": res.content[0].text}
    if res.is_error:
        data = {"error": True, "speech": (res.content[0].text if res.content else "Something went wrong.")}
    turn.calls.append({"tool": name, "arguments": args, "ms": round((time.perf_counter() - t0) * 1000),
                       "result": {k: v for k, v in data.items() if k != "card"}})
    if data.get("card"):
        turn.cards.append(data["card"])
    ui = _ui_resources.get(name)
    if ui is None and name not in _ui_resources:
        tool = next((t for t in (await client.list_tools()).tools if t.name == name), None)
        ui = ((tool.meta or {}).get("ui") or {}).get("resourceUri") if tool else None
        _ui_resources[name] = ui
    if ui:  # MCP Apps: this tool has a UI; read it over MCP (resources/read) and render it with the result
        html = (await client.read_resource(ui)).contents[0].text
        turn.calls.append({"tool": f"resources/read {ui}", "arguments": {}, "ms": 0,
                           "result": {"mimeType": "text/html;profile=mcp-app", "bytes": len(html)}})
        turn.cards.append({"type": "mcp_app", "title": "MCP App", "resource_uri": ui, "html": html,
                           "tool_result": {k: v for k, v in data.items() if k not in ("card", "speech")}})
    return data


_ui_resources: dict[str, str | None] = {}


# ------------------------------------------------------------------ LLM brains

async def _llm_turn(client: Client, turn: Turn, history: list[dict], text: str, brain: str) -> None:
    tools = (await client.list_tools()).tools
    history.append({"role": "user", "content": [{"text": text}] if brain == "bedrock" else text})

    for _ in range(6):  # tool-use loop
        if brain == "bedrock":
            import boto3
            resp = boto3.client("bedrock-runtime").converse(
                modelId=BEDROCK_MODEL, system=[{"text": SYSTEM}], messages=history,
                toolConfig={"tools": [{"toolSpec": {"name": t.name, "description": t.description or "",
                                                    "inputSchema": {"json": t.input_schema}}} for t in tools]},
                inferenceConfig={"maxTokens": 600})
            msg = resp["output"]["message"]
            history.append(msg)
            uses = [b["toolUse"] for b in msg["content"] if "toolUse" in b]
            if not uses:
                turn.speech = " ".join(b["text"] for b in msg["content"] if "text" in b).strip()
                return
            results = []
            for u in uses:
                data = await _call(client, turn, u["name"], u["input"])
                results.append({"toolResult": {"toolUseId": u["toolUseId"], "content": [{"json": data}]}})
            history.append({"role": "user", "content": results})
        else:
            import anthropic
            resp = anthropic.Anthropic().messages.create(
                model=ANTHROPIC_MODEL, max_tokens=600, system=SYSTEM, messages=history,
                tools=[{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                       for t in tools])
            history.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            uses = [b for b in resp.content if b.type == "tool_use"]
            if not uses:
                turn.speech = " ".join(b.text for b in resp.content if b.type == "text").strip()
                return
            results = []
            for u in uses:
                data = await _call(client, turn, u.name, u.input)
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(data)})
            history.append({"role": "user", "content": results})


# ------------------------------------------------------------------ local planner (no keys needed)
#
# An ordered registry of intents. Each intent is a regex (or predicate) plus an async handler that
# makes MCP tool calls and returns what Alexa+ should say. The first match wins, so put specific
# intents before broad ones. Adding one (see docs/EXTENDING.md):
#
#     @intent("hydration", r"\b(drank|had)\b.*\b(water|glass)\b")
#     async def hydration(ctx: Ctx) -> str:
#         return (await ctx.call("log_water", {**ctx.p, "glasses": 1}))["speech"]

MOOD_WORDS = [
    (1, r"\b(awful|terrible|horrible|miserable|scared)\b"),
    (2, r"\b(dizzy|bad|sad|lonely|low|unwell|sick|fell|chest pain|not (so |too )?(good|great|well))\b"),
    (3, r"\b(tired|okay|ok|alright|so-so|sore|meh)\b"),
    (5, r"\b(great|wonderful|fantastic|excellent|amazing)\b"),
    (4, r"\b(good|fine|well|happy|lovely)\b"),
]


@dataclass
class Ctx:
    client: Client
    turn: Turn
    text: str   # what the person said
    t: str      # lower-cased, stripped
    profile: dict  # carecircle://people/{name}/profile, read over MCP

    @property
    def p(self) -> dict:
        return {"person": PERSON}

    async def call(self, tool: str, args: dict) -> dict:
        return await _call(self.client, self.turn, tool, args)

    def family_member(self) -> str | None:
        """Family member named (or referred to by relation) in what was said, e.g. 'Priya' or 'my son'."""
        for f in self.profile.get("family", []):
            if f["name"].lower() in self.t or (f.get("relation") and f["relation"].lower() in self.t):
                return f["name"]
        return None


@dataclass
class Intent:
    name: str
    matches: Callable[["Ctx"], bool]
    handler: Callable[[Ctx], Awaitable[str]]


INTENTS: list[Intent] = []


def intent(name: str, pattern: str | None = None, when: Callable[[Ctx], bool] | None = None):
    """Register an intent matched by a regex `pattern` (on the lower-cased text) and/or a `when(ctx)` predicate."""
    def matches(ctx: Ctx) -> bool:
        return bool((pattern and re.search(pattern, ctx.t)) or (when and when(ctx)))

    def decorator(fn: Callable[[Ctx], Awaitable[str]]):
        INTENTS.append(Intent(name, matches, fn))
        return fn
    return decorator


def _hour() -> int:
    """Hour of the care clock (which the demo can shift), falling back to local time."""
    try:
        import store
        return store._now().hour
    except Exception:
        return time.localtime().tm_hour


def _mood(text: str) -> int:
    for score, pat in MOOD_WORDS:
        if re.search(pat, text):
            return score
    return 3


@intent("dashboard", r"\b(dashboard|show me)\b")
async def _dashboard(ctx: Ctx) -> str:
    return (await ctx.call("show_care_dashboard", ctx.p))["speech"]


@intent("read_messages", r"\b(read|play|any|hear|check|new)\b.*\bmessages?\b|\b(read|play) it\b",
        when=lambda c: bool(re.fullmatch(r"(yes|yeah|sure|ok|okay)[.! ]*(please)?[.!]*", c.t)))
async def _read_messages(ctx: Ctx) -> str:
    return (await ctx.call("get_family_messages", ctx.p))["speech"]


@intent("doctor_visit", when=lambda c: bool(re.search(r"\b(doctor|dr\.? \w+|check-?up)\b", c.t)
                                             and re.search(r"\b(prep|ready|tell|summary|brief|say|mention)", c.t)))
async def _doctor_visit(ctx: Ctx) -> str:
    return (await ctx.call("prepare_doctor_visit", ctx.p))["speech"]


@intent("record_refill", r"\b(picked up|got|collected)\b.*\b(refill|prescription)\b")
async def _record_refill(ctx: Ctx) -> str:
    sched = (await ctx.call("get_refill_status", ctx.p))["refills"]
    ctx.turn.cards.clear()
    if not sched:
        return "I'm not tracking pill counts for any of your medicines yet."
    med = next((r["medication"] for r in sched if r["medication"].lower().split()[0] in ctx.t), sched[0]["medication"])
    n = int(m.group(1)) if (m := re.search(r"(\d+)\s*(pills|tablets)", ctx.t)) else 60
    return (await ctx.call("record_refill", {**ctx.p, "medication": med, "pills": n}))["speech"]


@intent("refill_status", r"\b(refill|running low|run out|supply|pills left|how many pills)\b")
async def _refill_status(ctx: Ctx) -> str:
    return (await ctx.call("get_refill_status", ctx.p))["speech"]


@intent("wellbeing_summary", r"\b(how('s| is) (mom|mum|dad|grandma|grandpa)|this week|summary|how has .* been)\b",
        when=lambda c: bool(re.search(rf"\bhow('s| is) {re.escape(PERSON.lower())}\b", c.t)))
async def _wellbeing_summary(ctx: Ctx) -> str:
    return (await ctx.call("get_wellbeing_summary", {**ctx.p, "days": 7}))["speech"]


@intent("message_family", when=lambda c: bool(re.search(r"\b(let|tell|text|call|message)\b", c.t)) and bool(
    c.family_member() or re.search(r"\b(family|let \w+ know)\b", c.t)))
async def _message_family(ctx: Ctx) -> str:
    who = ctx.family_member()
    reason = f"{PERSON} asked to reach {who or 'family'}: \"{ctx.text.strip()}\""
    args = {**ctx.p, "reason": reason, "urgency": "medium", **({"recipient": who} if who else {})}
    return (await ctx.call("alert_family", args))["speech"]


@intent("log_medication", r"\b(took|taken|had|take|skipped|skip|didn'?t take|forgot)\b.*"
                          r"\b(pill|pills|med|meds|medicine|medication|tablet|tablets)\b",
        when=lambda c: bool(re.search(r"\b(took|taken|had|skipped|skip|didn'?t take|forgot)\b", c.t)))
async def _log_medication(ctx: Ctx) -> str:
    sched = (await ctx.call("get_medication_schedule", ctx.p))["schedule"]
    ctx.turn.cards.clear()
    taken = not re.search(r"\b(skip|skipped|didn'?t|forgot)\b", ctx.t)
    named = sorted({d["medication"] for d in sched if d["medication"].lower().split()[0] in ctx.t})
    if not named:
        if not re.search(r"\b(pill|pills|med|meds|medicine|medication|tablet|tablets)\b", ctx.t):
            return await _checkin(ctx)  # "I had a bad night" is a check-in, not a dose
        hour = _hour()  # "I took my morning pills" -> everything still open that's due around now
        named = sorted({d["medication"] for d in sched if d["status"] in ("pending", "missed")
                        and abs(int(d["time"][:2]) - hour) <= 3}) or \
            sorted({d["medication"] for d in sched if d["status"] in ("pending", "missed")})[:1]
    r = {}
    for med in named:
        r = await ctx.call("log_medication", {**ctx.p, "medication": med, "taken": taken})
    if len(named) > 1:
        ctx.turn.cards[:] = ctx.turn.cards[-1:]
        return f"Logged {', '.join(named[:-1])} and {named[-1]}." + r["speech"].split(".", 1)[1]
    return r["speech"] if r else "You're all caught up on medicines for now."


@intent("checkin", r"\b(feel|feeling|felt|slept|sleep|pain|hurt|dizzy|fell|tired|morning|night)\b")
async def _checkin(ctx: Ctx) -> str:
    args: dict[str, Any] = {**ctx.p, "mood": _mood(ctx.t), "notes": ctx.text.strip()}
    if m := re.search(r"(\d+(\.\d+)?)\s*(hours|hrs|hour|hr|h)\b", ctx.t):
        args["sleep_hours"] = float(m.group(1))
    elif re.search(r"\b(didn'?t sleep|barely slept|couldn'?t sleep|bad night)\b", ctx.t):
        args["sleep_hours"] = 3.0
    if m := re.search(r"pain\D{0,12}(\d+)", ctx.t):
        args["pain"] = min(10, int(m.group(1)))
    return (await ctx.call("daily_checkin", args))["speech"]


@intent("whats_next", r"\b(next|today|my day|what do i have|schedule|plan)\b")
async def _whats_next(ctx: Ctx) -> str:
    return (await ctx.call("whats_next", ctx.p))["speech"]


async def _local_turn(client: Client, turn: Turn, text: str) -> None:
    t = text.lower().strip()
    try:
        res = await client.read_resource(f"carecircle://people/{PERSON}/profile")
        profile = json.loads(res.contents[0].text)
    except Exception:
        profile = {}
    ctx = Ctx(client, turn, text, t, profile)
    for it in INTENTS:
        if it.matches(ctx):
            turn.speech = await it.handler(ctx)
            return
    family = " and ".join(f["name"] for f in profile.get("family", [])) or "your family"
    turn.speech = ("I can track your medicines and refills, check in on how you're feeling, read messages from "
                   f"your family, get you ready for your next appointment, or let {family} know something.")


# ------------------------------------------------------------------ entry point

_histories: dict[str, list[dict]] = {}


async def handle(text: str, session: str = "default") -> Turn:
    brain = pick_brain()
    turn = Turn(brain=brain)
    async with Client(MCP_URL, mode="legacy") as client:
        if brain in ("bedrock", "anthropic"):
            try:
                await _llm_turn(client, turn, _histories.setdefault(session, []), text, brain)
                _histories[session] = _histories[session][-20:]
            except Exception as e:  # e.g. no model access yet - fall back so the device never goes silent
                _histories.pop(session, None)
                turn = Turn(brain=f"local (fallback: {type(e).__name__})")
                await _local_turn(client, turn, text)
        else:
            await _local_turn(client, turn, text)
    return turn


async def nudge() -> Turn:
    """Proactive turn: ask CareCircle (over MCP) whether Alexa+ should speak first."""
    turn = Turn(brain="proactive")
    async with Client(MCP_URL, mode="legacy") as client:
        data = await _call(client, turn, "check_nudges", {"person": PERSON})
    turn.speech = data.get("speech", "")
    if not turn.speech:
        turn.calls = []  # nothing to say: keep the MCP log quiet
    return turn
