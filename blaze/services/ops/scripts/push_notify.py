#!/usr/bin/env python3
"""
push_notify.py — ntfy.sh push notifications for Blaze V4

Routes notifications to different ntfy topics based on business_unit:
  - CC (Content Co-op) → blaze-bailey-v4 (Bailey's phone)
  - ACS (Astro Cleanings) → blaze-astro-v4 (Caio's phone)
  - BOTH / unknown      → blaze-bailey-v4 (default to Bailey)
"""
import sys
from urllib.request import Request, urlopen

# Topic routing map
TOPIC_MAP = {
    "CC": "blaze-bailey-v4",
    "ACS": "blaze-astro-v4",
    "BOTH": "blaze-bailey-v4",
}
DEFAULT_TOPIC = "blaze-bailey-v4"


def _get_topic(business_unit):
    """Resolve ntfy topic from business_unit string."""
    if not business_unit:
        return DEFAULT_TOPIC
    return TOPIC_MAP.get(business_unit.upper(), DEFAULT_TOPIC)


def push(message, title=None, priority="default", tags=None, business_unit=None):
    """Send a push notification via ntfy.sh.

    Args:
        message: Notification body text.
        title: Optional notification title.
        priority: ntfy priority (urgent/high/default/low/min).
        tags: ntfy emoji tags (e.g. "bell", "rotating_light").
        business_unit: "CC", "ACS", or "BOTH". Routes to correct topic.
    """
    topic = _get_topic(business_unit)
    url = "https://ntfy.sh/%s" % topic

    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if title:
        headers["Title"] = title
    if priority:
        headers["Priority"] = priority
    if tags:
        headers["Tags"] = tags

    req = Request(url, data=message.encode("utf-8"), headers=headers)
    try:
        resp = urlopen(req, timeout=10)
        return resp.status == 200
    except Exception as e:
        print("Push failed (%s -> %s): %s" % (business_unit or "default", topic, e))
        return False


def push_event(message, score, title=None, business_unit=None):
    """Push an event with priority mapped from score.

    Args:
        message: Notification body text.
        score: Event score 0-100 (maps to ntfy priority).
        title: Optional notification title.
        business_unit: "CC", "ACS", or "BOTH". Routes to correct topic.
    """
    if score >= 85:
        priority = "urgent"   # sound + vibrate + persistent
        tags = "rotating_light"
    elif score >= 70:
        priority = "high"     # sound + banner
        tags = "bell"
    else:
        priority = "default"
        tags = "memo"

    return push(message, title=title or "Blaze", priority=priority,
                tags=tags, business_unit=business_unit)


if __name__ == "__main__":
    if "--test" in sys.argv:
        idx = sys.argv.index("--test")
        msg = " ".join(sys.argv[idx + 1:]) if idx + 1 < len(sys.argv) else "Blaze online. Push working."

        # Allow --bu flag for testing specific business unit
        bu = None
        if "--bu" in sys.argv:
            bu_idx = sys.argv.index("--bu")
            if bu_idx + 1 < len(sys.argv):
                bu = sys.argv[bu_idx + 1]

        topic = _get_topic(bu)
        ok = push(msg, title="Blaze Test", priority="default",
                  tags="white_check_mark", business_unit=bu)
        print("Push %s (topic: %s, bu: %s)" % (
            "sent" if ok else "FAILED", topic, bu or "default"))
    else:
        print("Usage: python3 push_notify.py --test [message] [--bu CC|ACS|BOTH]")
