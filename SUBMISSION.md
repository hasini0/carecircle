# Devpost submission: copy-paste kit

Fill in the `TODO`s before submitting. **Deadline: Fri Oct 23, 2026, 12:00 pm Pacific (= Sat Oct 24, 12:30 am IST).**

---

## Project name
CareCircle: Alexa+ that remembers, so families don't have to worry

## Tagline
A self-hosted MCP server that gives Alexa+ a memory, a sense of timing, and the judgment to keep an older adult's family in the loop.

## Tracks
- **Primary track:** Alexa+ (self-hosted MCP server, spec 2025-11-25 over Streamable HTTP, demoed through a simulated Alexa+ Echo Show)
- **Mini challenges:** AWS Builder (Amazon Bedrock Converse API with tool use as the agent brain; Amazon SNS for family alerts) · Open Source (MIT-licensed public repo)
  - ⚠️ Only claim AWS Builder if you actually run it on Bedrock for the video. Otherwise delete this line and the AWS parts of the feedback below.

## Description

**The problem.** About 1 in 4 adults over 65 lives alone. Missed doses are one of the most common reasons older adults
end up in hospital, and families end up either calling every day to ask "did you take your pills?" or finding out too late.

**What CareCircle does.** Margaret, 78, talks to Alexa+ the way she always has:
- At 8 PM, **Alexa+ speaks first**: "Margaret, it's time for your Metformin."
- "I just took it." It's logged, and because the bottle now has 5 days left, CareCircle **asks Priya to pick up a refill**.
- "I'm a bit dizzy and only slept 4 hours." CareCircle's risk engine recognises a red flag and **alerts her daughter
  and son on its own**. Priya's phone turns red within seconds.
- Priya replies from the CareCircle app, "Call you at 7? ❤️". Margaret's Echo announces it, Alexa+ reads it out, and Priya sees **"heard ✓"**.
- "Get me ready for Dr. Chen." A visit brief covering adherence, which doses get missed, mood, sleep and symptoms in her own words.
- "Show me the dashboard." An **MCP App** renders on screen.

**How it's built.**
- A **self-hosted MCP server** (Python, official MCP SDK v2) implementing spec **2025-11-25** over **Streamable HTTP**:
  15 tools, a resource template, a `morning_routine` prompt, and an **MCP App** (`ui://carecircle/dashboard.html`).
- **Agentic judgment, not CRUD:** after every interaction the server decides on its own whether to escalate, how
  urgently and to whom: red-flag phrases, missed doses, low-mood and poor-sleep streaks, low medicine supply. It de-duplicates so families aren't spammed.
- **Proactive:** the device polls `check_nudges` over MCP, so Alexa+ starts the conversation when a dose comes due or a family message arrives.
- **Memory across sessions:** SQLite, so "how's Mom been this week?" has a real answer.
- **Voice-first, screen-aware:** every tool returns `speech` for Alexa and a `card` for Echo Show.
- **The Alexa+ side** is an agent that connects as a real MCP client. Its brain is **Amazon Bedrock** (Converse +
  tool use), with Anthropic API and local fallbacks. The live demo page shows the Echo Show, the family phone and
  every MCP request side by side.
- **Deployed:** one service (Render blueprint + Dockerfile), with DNS-rebinding protection left on in production.

**What's next.** OAuth 2.1 per household, SNS SMS to every family member, multi-person households, and a real Alexa+ integration once self-hosted MCP is open to developers.

## Links
- Live demo: https://carecircle-9d2k.onrender.com/ (MCP endpoint: `/mcp`)
- Code repository: https://github.com/hasini0/carecircle (public, MIT)
- Demo video (YouTube, public, under 3 min): `TODO`

## Testing instructions for judges
Open the live demo, or run `./run.sh` and open http://localhost:8000. Click the prompts under the Echo Show, or press
space and talk (Chrome). Use **Demo clock → 8:00 PM** to trigger the proactive reminder. Send a message from the phone
on the right to see the two-way loop. **Reset demo** restores the starting state. Any MCP client can connect to `/mcp`.
(The free Render service may take about 30 s to wake on the first request.)

## How it was built during the Submission Period
Built from scratch during the hackathon window (first commit Oct 22, 2026).

---

## Product feedback

**Which developer tools, APIs and SDKs did you use, and for what?**
- **MCP Python SDK (`mcp` 2.3.0):** the server (`MCPServer`, Streamable HTTP, `Apps` extension, resource templates, prompts, transport security) and the client in the Alexa+ agent (`mcp.Client`).
- **MCP spec 2025-11-25:** negotiated by our client (verified in tests).
- **MCP Apps extension:** `show_care_dashboard` bound to a `ui://` HTML resource; our simulator is a minimal host.
- **Amazon Bedrock, Converse API with tool use:** the agent's reasoning. MCP `inputSchema` maps straight into `toolConfig`.
- **Amazon SNS (optional):** family alert fan-out.
- **Web Speech API:** voice in and out on the simulated device.

**What worked well?**
- `MCPServer` + `@tool` turned typed Python functions into complete tool schemas with no boilerplate.
- Streamable HTTP worked the first time, and the SDK negotiated 2025-11-25 automatically.
- Having MCP Apps built into the SDK made "voice + screen" a single decorator.
- DNS-rebinding protection is on by default for localhost, a good safe default.

**What needs work?**
- *SDK v2 migration:* `FastMCP` was renamed `MCPServer`; most tutorials still use `mcp.server.fastmcp`, which fails on a fresh install.
- *Apps extension:* `@apps.tool` silently doesn't register if `MCPServer(extensions=[apps])` is created first. The tool just vanishes from `tools/list`.
- *Deployment:* DNS-rebinding protection auto-enables only when binding to localhost. Bind to `0.0.0.0` to deploy and it is silently **off**. Turning it back on means listing every allowed host yourself, and a missed host gets a bare **421** with no hint.
- *Alexa+:* there's no public way to connect a self-hosted MCP server to a real Echo, so every team builds its own simulator.
- *Proactive output:* MCP has no standard way for a server to make an assistant speak first, so we had to poll a tool.

**Onboarding (zero to hello world):** about 20 minutes from `pip install mcp` to a working `tools/call` over Streamable HTTP. Most of that was the v1 → v2 rename.

**Would you build with these again?** Yes. MCP let us write the care logic once and expose it to any agent, and Bedrock let the same schemas drive the agent with no glue code.

---

## Friction log (up to a 10% bonus)

| # | Task attempted | Steps taken | Expected | Actual | Severity | Workaround | Suggestion |
|---|---|---|---|---|---|---|---|
| 1 | Start a server from the getting-started guide | `pip install mcp`; `from mcp.server.fastmcp import FastMCP` | Server starts | `ModuleNotFoundError`: FastMCP removed in mcp 2.x | Medium | Switched to `mcp.server.mcpserver.MCPServer` | Update getting-started docs for v2, or keep a deprecated alias for one release |
| 2 | Add an MCP App tool | Created `MCPServer(extensions=[apps])`, then `@apps.tool(...)` | Tool in `tools/list` | Silently missing (9 of 10 tools listed) | High (silent) | Moved `Apps()` registrations above the `MCPServer(...)` call | Warn on late registration, or register lazily |
| 3 | Get structured tool output | Tools annotated `-> dict` | `structuredContent` populated | Only text content | Low | `-> dict[str, Any]` | Document which return annotations produce structured output |
| 4 | Deploy publicly with DNS-rebinding protection kept on | Bound to `0.0.0.0`; read SDK source to see when protection applies; tested with a public `Host` header | Protection on, public host accepted | Protection silently disabled for non-localhost binds; once enabled, any host not listed gets a bare `421` | High | `TransportSecuritySettings(allowed_hosts=[localhost…, public host])` from an env var; verified 200 (public host) vs 421 (spoofed host) | Warn when serving 0.0.0.0 unprotected; put the rejected host in the 421 body; support an `MCP_ALLOWED_HOSTS` env var |
| 5 | Make Alexa+ speak first (a dose reminder) | Looked for a server-to-assistant push in MCP | Proactive notification | None in spec; notifications only reach a connected client | Medium | Device polls a `check_nudges` tool | A proactive / "attention request" capability for voice hosts |
| 6 | Test on a real Alexa+ device | Looked for a developer path to attach a self-hosted MCP server | Test on own Echo | Not available | Medium | Built an Echo Show simulator + MCP client | An Alexa+ developer sandbox for MCP servers |

## Feature requests
- **Alexa+ developer sandbox for self-hosted MCP servers.** *Critical*
- **Proactive speech from MCP servers** (reminders without polling). *Important*
- **APL-style card schema for tool results** so `card` renders natively on Echo Show. *Nice-to-have*

## Open Source mini challenge
- Contribution / project repository URL: https://github.com/hasini0/carecircle
- GitHub username: hasini0
- **What we did:** an MIT-licensed, self-hostable MCP server for caregiving, and a reference example of a stateful,
  agentic MCP server with an MCP App, proactive nudges and production-safe deployment on MCP SDK v2.
- **How it works:** `MCPServer` tools over Streamable HTTP, SQLite memory, a rule-based escalation engine, a `ui://` dashboard, and a one-click Render blueprint.
- **Why it matters:** most MCP examples are stateless API wrappers. This shows memory, autonomous escalation, MCP Apps
  and a working public deployment (including the 421 host-check fix) on SDK v2, ready for other developers to fork.

---

# Demo video script (target 2:45)

Open the live demo page full-screen. Click **Reset demo** first, and set **Demo clock → 8:00 PM**.
Record with QuickTime (File → New Screen Recording) with system audio, so Alexa's voice is captured.

| Time | Do | Say |
|---|---|---|
| 0:00–0:15 | Page hero | "Margaret is 78 and lives alone. Her kids worry every day: did she take her pills, is she OK? CareCircle gives Alexa+ a memory, timing, and judgment." |
| 0:15–0:35 | Reminder pops up on the Echo by itself | "It's 8 PM. Nobody said anything, and Alexa+ speaks first. The device asks the CareCircle MCP server, and on the right you can see every real MCP call." |
| 0:35–0:55 | Click **I took it** | "One tap logs it. And the bottle is down to 5 days, so CareCircle asks Priya to pick up a refill on its own. Look at her phone." |
| 0:55–1:25 | Click **dizzy… 4 hours** prompt | "This is the important part. Nobody asked it to alert anyone. It heard a red flag and messaged Priya and Dev at high urgency, and Priya's phone just turned red." |
| 1:25–1:50 | On the phone, click **Call you tonight at 7?**; Echo pops up; click **Read it** | "Priya replies from the app. Margaret's Echo tells her, reads it out, and Priya gets a 'heard' receipt. No more daily check-up calls." |
| 1:50–2:10 | Click **Get me ready for Dr. Chen** | "Before tomorrow's appointment: adherence, missed doses, and her symptoms in her own words." |
| 2:10–2:25 | Click **Show me the dashboard** | "On screen devices, the same server delivers an MCP App. The host reads the ui:// resource over MCP and renders it." |
| 2:25–2:45 | Scroll to How it works | "It's a real, self-hosted MCP server, spec 2025-11-25 over Streamable HTTP, deployed publicly, with Amazon Bedrock as the agent's brain and open source under MIT. CareCircle: Alexa+ that remembers, so families don't have to worry." |
