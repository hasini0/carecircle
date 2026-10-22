# Devpost submission: copy-paste kit

Fill in the `TODO`s before submitting. **Deadline: Fri Oct 23, 2026, 12:00 pm Pacific (= Sat Oct 24, 12:30 am IST).**

---

## Project name
CareCircle: Alexa+ that remembers, so families don't have to worry

## Tagline (one line)
A self-hosted MCP server that gives Alexa+ a memory for elder care: medicines, check-ins and trends, with the judgment to alert family on its own.

## Tracks
- **Primary track:** Alexa+ (self-hosted MCP server, MCP spec 2025-11-25 over Streamable HTTP, demoed in a simulated Alexa+ experience)
- **Mini challenges:** AWS Builder (Amazon Bedrock Converse API with tool use as the agent's brain; Amazon SNS for family alerts) · Open Source (MIT-licensed public repo)

## Description (for the main text box)

**The problem.** About 1 in 4 adults over 65 lives alone. Missed doses are one of the most common reasons older adults
end up in hospital, and families end up either calling every day to ask "did you take your pills?" or finding out too late.

**What CareCircle does.** Margaret, 78, just talks to Alexa+ the way she normally would:
- "What's next today?" She hears her next dose, the morning dose she missed, and tomorrow's appointment with Dr. Chen.
- "I just took my Metformin and Lisinopril." Each one is logged against the right scheduled dose.
- "I'm a bit dizzy and only slept 4 hours." The check-in is saved, and CareCircle's risk engine recognises a
  red flag and **messages her daughter and son on its own**, then tells Margaret gently that it has done so.
- Priya opens the **family dashboard** (or asks "How's Mom been this week?") and sees 93% adherence, the mood
  trend and every alert, without having to make the daily "did you take your pills?" call.

**How it's built.**
- A **self-hosted MCP server** (Python, official MCP SDK) implementing spec **2025-11-25** over **Streamable HTTP**.
  It has 10 tools, a resource template, a guided `morning_routine` prompt, and an **MCP App**
  (`ui://carecircle/dashboard.html`) so screen devices can render the interactive dashboard.
- Every tool returns both `speech` (what Alexa says) and a `card` (what an Echo Show shows): voice-first, with screens as a bonus.
- **Agentic judgment, not just CRUD:** after each interaction the server runs risk rules (red-flag phrases, 2+ missed
  doses, low-mood or poor-sleep trends) and decides by itself whether to escalate, how urgently, and to whom, with
  6-hour de-duplication so families aren't spammed.
- **State across sessions:** SQLite memory means Alexa+ remembers last week, not just the last sentence.
- **The Alexa+ side** is an agent that connects to CareCircle as a real MCP client. Its brain is **Amazon Bedrock**
  (Converse API with tool use), with fallbacks to the Anthropic API or a local planner so it always runs. A simulated
  Echo Show (voice in, voice out, cards) shows every MCP `tools/call` live in an "under the hood" panel.

**What's next.** OAuth 2.1 per household, Amazon SNS SMS for every family member, caregiver-to-caregiver handoff
notes, and certification as a real Alexa+ integration.

## Links
- Code repository: `TODO https://github.com/<you>/carecircle` (public, MIT license; check it shows in the About sidebar)
- Demo video (YouTube, public, < 3 min): `TODO`

## How it was significantly built during the Submission Period
CareCircle was built from scratch during the hackathon window (first commit Oct 22, 2026).

---

## Product feedback (required)

**Which developer tools, APIs and SDKs did you use, and for what?**
- **Model Context Protocol, Python SDK (`mcp` 2.3.0):** the CareCircle server (`MCPServer`, Streamable HTTP
  transport, `Apps` extension for the MCP App, resource templates, prompts) and the client side in the simulator (`mcp.Client`).
- **MCP spec 2025-11-25:** protocol version negotiated by the simulator's client (verified in our tests).
- **MCP Apps extension (`io.modelcontextprotocol/ui`):** the `show_care_dashboard` tool bound to a `ui://` HTML resource.
- **Amazon Bedrock, Converse API with tool use:** the Alexa+ agent's reasoning; MCP tool schemas are mapped directly into `toolConfig`.
- **Amazon SNS (optional):** fan-out of family alerts by SMS or email.
- **Web Speech API:** voice in and out for the simulated device.

**What worked well?**
- MCP Python SDK: `MCPServer` + `@tool` turned type-annotated functions into fully-typed tool schemas with no boilerplate.
  Streamable HTTP worked first time, and the SDK negotiated 2025-11-25 automatically.
- Having MCP Apps built into the SDK (`Apps().tool(resource_uri=...)`) made "voice + visual" one line of code.
- Bedrock Converse's `toolConfig` takes a JSON schema, so MCP `inputSchema` maps straight across.

**What needs work?**
- *MCP SDK v2 migration:* `FastMCP` was renamed to `MCPServer`. The error message is good, but most tutorials and
  the getting-started docs still show `from mcp.server.fastmcp import FastMCP`, which fails on a fresh install.
- *Apps extension:* `@apps.tool` decorators silently don't register if you create `MCPServer(extensions=[apps])`
  **before** decorating. Extensions are applied at construction, so the tool just never shows up in `tools/list`. A
  warning (or lazy registration) would save real time.
- *Alexa+ track:* there is no public way to connect a self-hosted MCP server to a real Alexa+ device for testing,
  so every team has to build a simulator. A sandbox or a "test on your own Echo" path would make demos far more credible.
- `TODO` add anything you hit, e.g. Bedrock model-access approval time.

**How was your onboarding (zero to hello world)?**
About 20 minutes from `pip install mcp` to a working `tools/call` over Streamable HTTP. Most of that was the v1 → v2
rename above. The MCP Inspector made it easy to check the server before writing any client.

**Would you build with these again?**
Yes. MCP let us write the care logic once and expose it to any agent (Alexa+, Claude, an IDE), and Bedrock let the
same tool schemas drive the agent without glue code. We'd build again as soon as Alexa+ accepts self-hosted MCP servers on real devices.

---

## Friction log (up to +10% bonus, don't skip)

| # | Task attempted | Steps taken | Expected | Actual | Severity | Workaround | Suggestion |
|---|---|---|---|---|---|---|---|
| 1 | Start an MCP server from the getting-started guide | `pip install mcp`; `from mcp.server.fastmcp import FastMCP` | Server starts | `ModuleNotFoundError`: FastMCP removed in mcp 2.x | Medium | Read migration guide; switched to `from mcp.server.mcpserver import MCPServer` | Update getting-started docs and snippets for v2, or keep a deprecated alias for one release |
| 2 | Add an MCP App tool | Created `MCPServer(extensions=[apps])`, then `@apps.tool(...)` below it | Tool appears in `tools/list` | Tool silently missing; 9 of 10 tools listed | High (silent) | Moved the `Apps()` registrations above the `MCPServer(...)` call | Warn when an extension gains tools after it's applied, or register lazily |
| 3 | Get structured tool output | Tools annotated `-> dict` | `structuredContent` populated | Only text content returned | Low | Annotated `-> dict[str, Any]` | Document which return annotations produce structured output |
| 4 | Test on a real Alexa+ device | Looked for a way to attach a self-hosted MCP server to an Echo | Developer test path | None publicly available; simulation path only | Medium | Built an Echo Show-style simulator + MCP client | Offer an Alexa+ developer sandbox for MCP servers |
| 5 | `TODO` (e.g. Bedrock model access) | | | | | | |

## Feature requests (optional)
- **Alexa+ developer sandbox for self-hosted MCP servers.** Needed to demo on real hardware. *Critical*
- **Proactive notifications from MCP servers to Alexa+** (so CareCircle can say "time for your 8 PM Metformin" unprompted). *Important*
- **APL-style card schema for MCP tool results**, so `card` fields render natively on Echo Show. *Nice-to-have*

## Open Source mini challenge
- Contribution URL / Project repository URL: `TODO https://github.com/<you>/carecircle`
- GitHub username: `TODO`
- **What we did:** released CareCircle as an MIT-licensed, self-hostable MCP server for caregiving: a reference
  example of an agentic, stateful MCP server with an MCP App on MCP SDK v2.
- **How it works:** `MCPServer` tools over Streamable HTTP, SQLite memory, a rule-based escalation engine, and a `ui://` dashboard.
- **Why it matters:** most MCP examples are stateless API wrappers. This one shows memory across sessions, autonomous
  escalation and MCP Apps together on MCP SDK v2, with a migration-friendly codebase other developers can fork.

---

# Demo video script (target 2:40)

Record the screen with QuickTime (File → New Screen Recording), browser at http://localhost:3000, sound on.
Use the voice mic button if it works in your browser; otherwise click the chips. Run `./run.sh` right before recording so the data is fresh.

| Time | Show | Say (voice-over) |
|---|---|---|
| 0:00–0:15 | Title slide or the simulator idle screen | "Margaret is 78 and lives alone. Her kids, Priya and Dev, worry every day: did she take her pills, is she OK? CareCircle gives Alexa+ a memory and the judgment to keep the family in the loop." |
| 0:15–0:35 | Click **What's next today?** | "CareCircle is a self-hosted MCP server, spec 2025-11-25 over Streamable HTTP. On the right you can see every real MCP tool call Alexa+ makes. She hears her next dose and tomorrow's appointment." |
| 0:35–0:55 | Click **I just took my Metformin and Lisinopril** | "Natural speech becomes two `log_medication` calls, matched to the right scheduled doses." |
| 0:55–1:30 | Click **I'm feeling a bit dizzy…slept 4 hours**. Pause on the red "Family alerted" card. Open the terminal showing the alert lines. | "This is the important part. Nobody asked it to alert anyone. CareCircle's risk engine saw a red flag and decided on its own to message Priya and Dev, high urgency. It also watches for missed doses, low-mood streaks and bad sleep, and it de-duplicates so families aren't spammed." |
| 1:30–1:55 | Click **Show me the dashboard** | "On a screen device, the same server delivers an MCP App, an interactive dashboard over the ui:// resource." |
| 1:55–2:15 | Switch to http://localhost:8000/dashboard (the family view) | "And this is what Priya sees on her phone, updated live: adherence, mood trend, and the alert that just fired." |
| 2:15–2:30 | Click **How's Mom been this week?** | "Because everything persists across sessions, Alexa+ can answer 'how's Mom been this week?', not just the last thing she said." |
| 2:30–2:45 | README / architecture diagram | "The agent runs on Amazon Bedrock with tool use, alerts can go out over Amazon SNS, and the whole thing is open source under MIT. CareCircle: Alexa+ that remembers, so families don't have to worry." |
