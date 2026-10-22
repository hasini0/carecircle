# CareCircle — Alexa+ that remembers, so families don't have to worry

CareCircle is a **self-hosted MCP server** (spec **2025-11-25**, **Streamable HTTP**) that gives Alexa+ a long-term
memory for elder care. An older adult just talks to Alexa the way they normally would: "I took my pills," "I'm a bit
dizzy today," "what's next?" CareCircle remembers it across sessions, spots patterns, and **decides on its own** when
the family needs to know.

> Built for the Build, Ship, Shape: Amazon Developer Hackathon. **Alexa+ track** (self-hosted MCP server + simulated
> Alexa+ device), plus the **AWS Builder** and **Open Source** mini challenges.

![architecture](docs/architecture.svg)

## Why

About 1 in 4 adults over 65 lives alone, and missed medication is one of the most common reasons older adults
end up in hospital. Families either call every day to ask "did you take your pills?" or don't know anything is
wrong until something has gone badly wrong. A voice assistant is already in the kitchen. It just needs memory and judgment.

## What it does

| Margaret says (to Alexa+) | CareCircle MCP tool | What happens |
|---|---|---|
| "What's next today?" | `whats_next` | Next doses, anything missed, tomorrow's appointment, nudges a check-in |
| "I just took my Metformin and Lisinopril" | `log_medication` ×2 | Matches each to the nearest scheduled dose, card on screen |
| "I'm a bit dizzy and only slept 4 hours" | `daily_checkin` | Saves mood/sleep, **risk engine flags "dizzy" → alerts Priya & Dev automatically** |
| "Let Priya know I'd love a call" | `alert_family` | Messages just Priya |
| "How's Mom been this week?" (family) | `get_wellbeing_summary` | Adherence %, mood trend, alerts, as a spoken summary + card |
| "Show me the dashboard" | `show_care_dashboard` | **MCP App** (`ui://carecircle/dashboard.html`) rendered on screen |

**The agentic part:** after every check-in or dose log, CareCircle runs its own risk rules and acts without being
asked:

- red-flag phrases (fall, chest pain, dizziness, confusion, short of breath) → **high** urgency, everyone notified
- 2+ missed or skipped doses today → **medium**
- low mood two check-ins in a row → **medium**
- under 5 h sleep two nights running → **low**, one family member only
- the same trigger is deduplicated for 6 h so families aren't spammed

**State across sessions:** everything is stored in SQLite, so Alexa+ "remembers" yesterday, last week, and what was
already reported, across sessions, devices and server restarts.

## MCP surface

- **Tools (10):** `setup_person`, `add_medication`, `add_appointment`, `log_medication`, `daily_checkin`,
  `whats_next`, `get_medication_schedule`, `get_wellbeing_summary`, `alert_family`, `show_care_dashboard`
- **MCP Apps extension** (`io.modelcontextprotocol/ui`): `show_care_dashboard` is bound to the
  `ui://carecircle/dashboard.html` resource (`text/html;profile=mcp-app`), so hosts that support MCP Apps render
  the interactive dashboard in place
- **Resource template:** `carecircle://people/{name}/profile`
- **Prompt:** `morning_routine` (a guided greet → check-in → walk through morning meds flow)
- Every tool returns `speech` (what Alexa should say) + `card` (what a screen device should show) + raw data

## Run it (2 minutes)

Requires Python 3.10+.

```bash
./run.sh
```

That creates a virtualenv, installs dependencies, seeds a week of demo history for "Margaret, 78", and starts:

| URL | What |
|---|---|
| http://localhost:8000/mcp | **CareCircle MCP server** (Streamable HTTP). Point any MCP client here |
| http://localhost:3000 | **Alexa+ simulator**: Echo Show-style screen, mic (Chrome), voice replies, live MCP log |
| http://localhost:8000/dashboard | **Family dashboard**: what Priya sees on her phone, live-updating |

Manual start, if you prefer:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/seed_demo.py
.venv/bin/python server/carecircle_server.py      # terminal 1
.venv/bin/python simulator/app.py                 # terminal 2
```

### Use it from any MCP client

```bash
npx @modelcontextprotocol/inspector   # then connect to http://localhost:8000/mcp (Streamable HTTP)
```

Or from Python:

```python
from mcp import Client
async with Client("http://localhost:8000/mcp") as c:
    print(await c.call_tool("whats_next", {"person": "Margaret"}))
```

## The simulator's brain (Amazon Bedrock)

The simulator is an agent that connects to CareCircle **as a real MCP client** and picks its model automatically:

1. **Amazon Bedrock** (Converse API with tool use): used when AWS credentials are available.
   Model: `CARECIRCLE_BEDROCK_MODEL` (default `us.anthropic.claude-haiku-4-5-20251001-v1:0`).
2. **Anthropic API**: used if `ANTHROPIC_API_KEY` is set.
3. **Local planner**: deterministic intent routing, so the demo runs with **no keys at all**.

Whatever the brain, every action is an MCP `tools/call` over Streamable HTTP, and the right-hand panel of the
simulator shows each one live. Force a brain with `CARECIRCLE_BRAIN=bedrock|anthropic|local`.

### Optional: real SMS / email to family with Amazon SNS

```bash
export CARECIRCLE_SNS_TOPIC_ARN=arn:aws:sns:us-east-1:123456789012:carecircle-family
```

Without it, alerts go to `data/outbox.jsonl` and the server console (that's what the demo shows).

## Project layout

```
server/carecircle_server.py   MCP server: tools, MCP App, resource, prompt, HTTP routes
server/store.py               SQLite persistence + risk engine (the "judgment")
server/notify.py              family alert delivery (outbox / Amazon SNS)
server/ui/dashboard.html      family dashboard, also served as the MCP App resource
simulator/agent.py            Alexa+ agent: MCP client + Bedrock / Anthropic / local brain
simulator/app.py, device.html Echo Show-style simulator
scripts/seed_demo.py          demo persona + a week of history
```

## Limits (honest)

- CareCircle is a reminder and family-communication tool, **not a medical device**. It doesn't diagnose, and a
  red-flag alert tells family; it doesn't call emergency services.
- The Alexa+ device is simulated (the official hackathon path); the MCP server itself is real and works with any
  MCP 2025-11-25 client.
- Single-household demo: no authentication yet. Next step is OAuth 2.1 per household (supported by the MCP SDK).

## License

MIT, see [LICENSE](LICENSE).
