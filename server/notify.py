"""Family alert delivery through pluggable channels.

Every alert goes to each active channel:
  outbox   always on: console + data/outbox.jsonl, so the demo shows exactly what family received
  webhook  when CARECIRCLE_WEBHOOK_URL is set: POSTs JSON, so alerts can reach Telegram, Discord,
           Slack, WhatsApp bridges, n8n/Zapier flows, a hospital system… without touching the core
  sns      when CARECIRCLE_SNS_TOPIC_ARN is set (and boto3 is installed): Amazon SNS SMS/email fan-out

Network channels run in a background thread: a slow or failing provider must never block the
voice reply (or the server's event loop).

Adding a channel (see docs/EXTENDING.md):

    @channel("telegram", active=lambda: bool(os.environ.get("TELEGRAM_BOT_TOKEN")), background=True)
    def telegram(alert: Alert) -> None:
        ...
"""
from __future__ import annotations

import json
import os
import threading
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

OUTBOX = Path(os.environ.get("CARECIRCLE_OUTBOX", Path(__file__).parent.parent / "data" / "outbox.jsonl"))


@dataclass
class Alert:
    person: str
    urgency: str
    reason: str
    recipients: list[dict]  # [{name, relation, contact}]

    @property
    def message(self) -> str:
        return f"[CareCircle · {self.urgency.upper()}] {self.reason}"


@dataclass
class Channel:
    name: str
    send: Callable[[Alert], None]
    active: Callable[[], bool]
    background: bool


CHANNELS: dict[str, Channel] = {}


def channel(name: str, active: Callable[[], bool] = lambda: True, background: bool = False):
    """Register a delivery channel. `active` is checked on every alert; `background` runs it off-thread."""
    def decorator(fn: Callable[[Alert], None]):
        CHANNELS[name] = Channel(name, fn, active, background)
        return fn
    return decorator


def deliver(family: list[dict], person: str, urgency: str, reason: str) -> dict:
    alert = Alert(person, urgency, reason, family)
    used = []
    for ch in CHANNELS.values():
        if not ch.active():
            continue
        used.append(ch.name)
        if ch.background:
            threading.Thread(target=_safe_send, args=(ch, alert), daemon=True).start()
        else:
            _safe_send(ch, alert)
    return {"channel": "+".join(used), "channels": used, "sent": len(family)}


def _safe_send(ch: Channel, alert: Alert) -> None:
    try:
        ch.send(alert)
    except Exception as e:  # never let a delivery failure break the voice flow
        print(f"  !! alert channel {ch.name!r} failed: {e}", flush=True)


# ---------------------------------------------------------------- built-in channels

@channel("outbox")
def outbox(alert: Alert) -> None:
    OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    with OUTBOX.open("a") as f:
        for member in alert.recipients:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "to": member["name"],
                                "contact": member.get("contact"), "message": alert.message}) + "\n")
    print(f"  -> alert to {[m['name'] for m in alert.recipients]}: {alert.message}", flush=True)


@channel("webhook", active=lambda: bool(os.environ.get("CARECIRCLE_WEBHOOK_URL")), background=True)
def webhook(alert: Alert) -> None:
    body = json.dumps({**asdict(alert), "message": alert.message, "text": alert.message}).encode()
    req = urllib.request.Request(os.environ["CARECIRCLE_WEBHOOK_URL"], data=body, method="POST",
                                 headers={"content-type": "application/json", "user-agent": "CareCircle"})
    urllib.request.urlopen(req, timeout=5).close()


@channel("sns", active=lambda: bool(os.environ.get("CARECIRCLE_SNS_TOPIC_ARN")), background=True)
def sns(alert: Alert) -> None:
    import boto3
    boto3.client("sns").publish(TopicArn=os.environ["CARECIRCLE_SNS_TOPIC_ARN"],
                                Subject=f"CareCircle alert for {alert.person}", Message=alert.message)
