# Extending CareCircle

CareCircle is built from small, registered pieces. Most new features are one function in one file;
the engine finds it automatically. Run the tests after any change:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

| I want to… | Where | How |
|---|---|---|
| Watch for a new kind of risk | `server/rules.py` | Add an `@rule` function |
| Send alerts somewhere new (Telegram, WhatsApp, SMS…) | `server/notify.py` | Add a `@channel` function, or set `CARECIRCLE_WEBHOOK_URL` |
| Understand a new spoken request | `simulator/agent.py` | Add an `@intent` function |
| Give the assistant a new ability | `server/carecircle_server.py` | Add an `@mcp.tool` function |
| Demo a different family | `config/household.json` | Edit the JSON (no code) |

## 1. A new risk rule

Rules run after every dose log and check-in. Return a list of `Finding`s (empty means all good).
`trigger` is a stable id: the same trigger is only sent to the family once every 6 hours.

```python
# server/rules.py
@rule("hydration", "No water logged by 3 PM")
def hydration(ctx: RuleContext) -> list[Finding]:
    if store._now().hour < 15:
        return []
    glasses = ...  # query ctx.conn for today's water log
    if glasses == 0:
        return [Finding("low", f"{ctx.person} hasn't had any water today", "hydration")]
    return []
```

A rule that raises an exception is skipped and logged, so it can't silence the other rules.
Switch rules off without code changes: `CARECIRCLE_DISABLED_RULES=poor_sleep,refills`.
Every registered rule is listed over MCP at the `carecircle://rules` resource.

## 2. A new alert channel

```python
# server/notify.py
@channel("telegram", active=lambda: bool(os.environ.get("TELEGRAM_BOT_TOKEN")), background=True)
def telegram(alert: Alert) -> None:
    for member in alert.recipients:
        ...  # POST alert.message to the Telegram Bot API for member["contact"]
```

`background=True` runs the channel in its own thread. **Use it for anything that touches the network**:
a slow provider must never block the voice reply or the server's event loop.

No code at all: set `CARECIRCLE_WEBHOOK_URL` and every alert is POSTed as JSON
(`person`, `urgency`, `reason`, `recipients`, `message`), ready for n8n, Zapier, Make or your own bridge.

## 3. A new spoken request

Intents are tried in registration order and the first match wins, so put specific intents above broad ones.
`ctx.call()` makes a real MCP `tools/call` and shows it in the demo's MCP panel.

```python
# simulator/agent.py
@intent("hydration", r"\b(drank|had)\b.*\b(water|glass)\b")
async def _hydration(ctx: Ctx) -> str:
    return (await ctx.call("log_water", {**ctx.p, "glasses": 1}))["speech"]
```

`ctx.profile` holds the person's profile (family, medicines), read over MCP, and `ctx.family_member()`
finds who was named ("Priya", "my son"). Add a line to `test_intent_routing` in `tests/test_core.py`.

## 4. A new tool (assistant ability)

```python
# server/carecircle_server.py
@mcp.tool(title="Log water")
def log_water(person: str, glasses: int = 1) -> dict[str, Any]:
    """Record glasses of water."""
    ...
    return {"speech": f"Nice, that's {glasses} glass of water logged.", "card": {...}}
```

Return `speech` (what the voice assistant says) and, optionally, a `card` (what a screen shows).
Any MCP client (the simulator, Claude, an IDE) sees the new tool immediately.

## 5. A different household

Edit `config/household.json`, or point `CARECIRCLE_HOUSEHOLD` at another file, then **Reset demo**
(or run `scripts/seed_demo.py`). The person's name, family, medicines, pill counts, appointments
and the shape of the demo history all come from there.
