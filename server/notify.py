"""Family alert delivery.

Default: write to the console + data/outbox.jsonl so the demo can show exactly what
family members received. If CARECIRCLE_SNS_TOPIC_ARN is set and boto3 is installed,
alerts are also published to Amazon SNS (SMS / email fan-out to the family).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

OUTBOX = Path(__file__).parent.parent / "data" / "outbox.jsonl"


def deliver(family: list[dict], person: str, urgency: str, reason: str) -> dict:
    msg = f"[CareCircle · {urgency.upper()}] {reason}"
    OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    with OUTBOX.open("a") as f:
        for member in family:
            f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "to": member["name"],
                                "contact": member.get("contact"), "message": msg}) + "\n")
    print(f"  -> alert to {[m['name'] for m in family]}: {msg}", flush=True)

    topic = os.environ.get("CARECIRCLE_SNS_TOPIC_ARN")
    if not topic:
        return {"channel": "outbox", "sent": len(family)}
    try:
        import boto3
        boto3.client("sns").publish(TopicArn=topic, Subject=f"CareCircle alert for {person}", Message=msg)
        return {"channel": "sns", "sent": len(family)}
    except Exception as e:  # never let a delivery failure break the voice flow
        return {"channel": "outbox", "sent": len(family), "sns_error": str(e)}
