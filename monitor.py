#!/usr/bin/env python3
"""
VOLKSBAD slot monitor.

Checks the Volksbuehne booking page and sends a phone push (via ntfy)
when bookable time-slots appear for the dates you care about.

Designed to run on GitHub Actions on a cron schedule. State is kept in
state.json (committed back to the repo by the workflow) so you only get
pinged when something NEW becomes available - not every single run.

Config comes from environment variables (set as repo secrets/variables):
  NTFY_TOPIC    full ntfy URL, e.g. https://ntfy.sh/volksbad-7f3q9k   (required)
  BOOKING_URL   the page to watch (defaults to the production page)
  TARGET_DATES  optional comma list, e.g. 2026-08-07,2026-08-08
                leave empty to be alerted for ANY available slot
"""

import os
import sys
import json
import pathlib
import datetime

import requests
from bs4 import BeautifulSoup

BOOKING_URL = os.environ.get(
    "BOOKING_URL",
    "https://www.volksbuehne-berlin.de/produktionen/volksbad/",
)
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")  # e.g. https://ntfy.sh/your-secret-topic
TARGET_DATES = [d.strip() for d in os.environ.get("TARGET_DATES", "").split(",") if d.strip()]
STATE_FILE = pathlib.Path("state.json")

# A descriptive, honest User-Agent. Put a real contact in it so the site
# operator can reach you rather than just blocking an anonymous scraper.
USER_AGENT = "VolksbadMonitor/1.0 (personal ticket alert; contact: you@example.com)"


def fetch(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.text


def check_availability(html: str) -> set[str]:
    """
    Detect that booking has GONE LIVE, by watching the public production page.

    We deliberately watch the production page (BOOKING_URL) rather than the
    Eventim shop: the shop disallows automated access (robots) and runs bot
    protection, whereas the production page permits it. Today that page only
    shows a placeholder ("... ab 4. August verfügbar") and links nowhere to
    the shop. When booking opens it will expose a real ticket link / CTA.
    Either of those strong signals -> booking is live -> alert. A human then
    clicks through to the shop to actually book.

    This works against the real page as it exists today (returns empty = no
    alarm). You shouldn't need to touch it, but if the site later adds a
    global nav link to the shop on every page, scope `soup` to the main
    content container to avoid a false alarm (see README).
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True).lower()

    if "volksbad" not in text:  # unexpected page / redirect: stay silent
        return set()

    signals: set[str] = set()

    # Strong signal 1: a link to the ticket shop appears on the page.
    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if "ticket.volksbuehne-berlin.de" in href or "eventim" in href:
            signals.add("ticket-link")
            break

    # Strong signal 2: an explicit booking call-to-action appears.
    for cue in ("tickets buchen", "jetzt buchen", "zum ticket",
                "zeitfensterticket buchen", "in den warenkorb"):
        if cue in text:
            signals.add("booking-cta")
            break

    # Only strong signals trigger an alert. (The "ab 4. August" placeholder
    # simply vanishing is too easy to trip on a copy edit, so we don't use it.)
    return signals


def load_state() -> set[str]:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_state(state: set[str]) -> None:
    STATE_FILE.write_text(json.dumps(sorted(state)))


def notify(title: str, message: str) -> None:
    if not NTFY_TOPIC:
        print("NTFY_TOPIC not set; would have notified:", title, "-", message)
        return
    requests.post(
        NTFY_TOPIC,
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "swimmer"},
        timeout=30,
    )


def main() -> int:
    try:
        html = fetch(BOOKING_URL)
    except Exception as exc:  # network hiccup: log and exit 0 so the run isn't "failed"
        print(datetime.datetime.now().isoformat(), "fetch error:", exc)
        return 0

    available = check_availability(html)
    if TARGET_DATES:
        available = {a for a in available if any(d in a for d in TARGET_DATES)}

    previous = load_state()
    newly_open = available - previous  # only alert on the unavailable -> available flip

    if newly_open:
        notify(
            "VOLKSBAD slots available!",
            "Bookable now: " + ", ".join(sorted(newly_open)) + f"\n{BOOKING_URL}",
        )

    save_state(available)
    print(
        datetime.datetime.now().isoformat(),
        "available:", sorted(available),
        "| new:", sorted(newly_open),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
