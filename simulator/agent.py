"""The Alexa+ side: an agent that talks to the CareCircle MCP server as a real MCP client.

Brain, picked automatically (override with CARECIRCLE_BRAIN=bedrock|anthropic|local):
  1. Amazon Bedrock (Converse API + tool use)  - if AWS credentials are available
  2. Anthropic API                              - if ANTHROPIC_API_KEY is set
  3. Local planner                              - no keys needed; deterministic intent routing

Whichever brain is used, every action goes through MCP tools/call over Streamable HTTP.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from mcp import Client

MCP_URL = os.environ.get("CARECIRCLE_MCP_URL", "http://localhost:8000/mcp")
PERSON = os.environ.get("CARECIRCLE_PERSON", "Margaret")
BEDROCK_MODEL = os.environ.get("CARECIRCLE_BEDROCK_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
ANTHROPIC_MODEL = os.environ.get("CARECIRCLE_ANTHROPIC_MODEL", "claude-sonnet-5-5")

SYSTEM = (
    f"You are Alexa+, speaking with {PERSON}, an older adult who lives alone. You have the CareCircle tools. "
    f"Always pass person=\"{PERSON}\". When they mention medicine they took, call log_medication once per "
    "medication. When they describe how they feel, slept, pain or anything worrying, call daily_checkin with "
    "mood 1-5 and their own words as notes. For 'what's next' use whats_next. Reply in at most two short, warm, "
    "spoken sentences - no markdown, no lists. Each tool result includes a `speech` field you can reuse."
)


@dataclass
class Turn:
    speech: str = ""
    cards: list[dict] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)  # shown in the simulator's "under the hood" panel
    brain: str = ""


def pick_brain() -> str:
    forced = os.environ.get("CARECIRCLE_BRAIN")
    if forced:
        return forced
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
    if name == "show_care_dashboard":
        turn.cards.append({"type": "dashboard", "title": "Care dashboard", "person": PERSON})
    return data


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

MOOD_WORDS = [
    (1, r"\b(awful|terrible|horrible|miserable|scared)\b"),
    (2, r"\b(dizzy|bad|sad|lonely|low|unwell|sick|fell|chest pain|not (so |too )?(good|great|well))\b"),
    (3, r"\b(tired|okay|ok|alright|so-so|sore|meh)\b"),
    (5, r"\b(great|wonderful|fantastic|excellent|amazing)\b"),
    (4, r"\b(good|fine|well|happy|lovely)\b"),
]
FAMILY = {"priya": "Priya", "daughter": "Priya", "dev": "Dev", "son": "Dev"}


def _mood(text: str) -> int:
    for score, pat in MOOD_WORDS:
        if re.search(pat, text):
            return score
    return 3


async def _local_turn(client: Client, turn: Turn, text: str) -> None:
    t = text.lower().strip()
    p = {"person": PERSON}
    said: list[str] = []

    if re.search(r"\b(dashboard|show me)\b", t):
        said.append((await _call(client, turn, "show_care_dashboard", p))["speech"])

    elif re.search(r"\b(how('s| is) (mom|mum|margaret)|this week|summary|how has .* been)\b", t):
        said.append((await _call(client, turn, "get_wellbeing_summary", {**p, "days": 7}))["speech"])

    elif re.search(r"\b(let|tell|text|call|message)\b.*\b(know|priya|dev|daughter|son|family)\b", t):
        who = next((v for k, v in FAMILY.items() if k in t), "family")
        reason = f"{PERSON} asked to reach {who}: \"{text.strip()}\""
        said.append((await _call(client, turn, "alert_family", {**p, "reason": reason, "urgency": "medium",
                                                                **({"recipient": who} if who != "family" else {})}))["speech"])

    elif re.search(r"\b(took|taken|had|take|skipped|skip|didn'?t take|forgot)\b.*\b(pill|pills|med|meds|medicine|"
                   r"medication|tablet|metformin|lisinopril|vitamin)", t):
        sched = (await _call(client, turn, "get_medication_schedule", p))["schedule"]
        turn.cards.clear()
        taken = not re.search(r"\b(skip|skipped|didn'?t|forgot)\b", t)
        named = sorted({d["medication"] for d in sched if d["medication"].lower().split()[0] in t})
        if not named:  # "I took my morning pills" -> everything still open that's due around now
            hour = time.localtime().tm_hour
            named = sorted({d["medication"] for d in sched if d["status"] in ("pending", "missed")
                            and abs(int(d["time"][:2]) - hour) <= 3}) or \
                sorted({d["medication"] for d in sched if d["status"] in ("pending", "missed")})[:1]
        r = {}
        for med in named:
            r = await _call(client, turn, "log_medication", {**p, "medication": med, "taken": taken})
        if len(named) > 1:
            turn.cards[:] = turn.cards[-1:]
            said.append(f"Logged {', '.join(named[:-1])} and {named[-1]}." + r["speech"].split(".", 1)[1])
        elif r:
            said.append(r["speech"])
        else:
            said.append("You're all caught up on medicines for now.")

    elif re.search(r"\b(feel|feeling|felt|slept|sleep|pain|hurt|dizzy|fell|tired|morning|night)\b", t):
        args: dict[str, Any] = {**p, "mood": _mood(t), "notes": text.strip()}
        if m := re.search(r"(\d+(\.\d+)?)\s*(hours|hrs|hour)", t):
            args["sleep_hours"] = float(m.group(1))
        elif re.search(r"\b(didn'?t sleep|barely slept|couldn'?t sleep|bad night)\b", t):
            args["sleep_hours"] = 3.0
        if m := re.search(r"pain\D{0,12}(\d+)", t):
            args["pain"] = int(m.group(1))
        said.append((await _call(client, turn, "daily_checkin", args))["speech"])

    elif re.search(r"\b(next|today|my day|what do i have|schedule|plan)\b", t):
        said.append((await _call(client, turn, "whats_next", p))["speech"])

    else:
        said.append("I can track your medicines, check in on how you're feeling, tell you what's next today, "
                    "or let Priya and Dev know something.")
    turn.speech = " ".join(said)


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
