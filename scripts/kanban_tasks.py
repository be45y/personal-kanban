#!/usr/bin/env python3
"""Print open tasks from the personal kanban board (read-only).

Signs in to the board's Supabase project as you, reads your board, and prints
the cards that are not in Done, grouped by urgency. Used by the morning brief.

Credentials come from the macOS Keychain, never from this file. One-time setup
(you'll be prompted for your kanban password; it is not echoed):

    security add-generic-password -s personal-kanban -a you@example.com -w

Usage:
    python3 scripts/kanban_tasks.py           # Markdown for the brief
    python3 scripts/kanban_tasks.py --json    # machine-readable
"""
import datetime as dt
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request

# Public values, same as index.html (the publishable key is safe to ship).
SUPABASE_URL = "https://tgofzytwbschhcdyvjad.supabase.co"
SUPABASE_KEY = "sb_publishable_wXRs7bhhdYZDuQn4Lj7umw_eAE5lbP9"
KEYCHAIN_SERVICE = "personal-kanban"
TIMEOUT = 20  # free-tier Supabase can be slow to wake
PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def fail(msg):
    print(f"kanban_tasks: {msg}", file=sys.stderr)
    sys.exit(1)


def keychain_credentials():
    """Return (email, password) from the Keychain item, or exit with a hint."""
    try:
        meta = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            capture_output=True, text=True, check=True,
        ).stdout
        password = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=True,
        ).stdout.rstrip("\n")
    except (subprocess.CalledProcessError, FileNotFoundError):
        fail(
            f"no Keychain item '{KEYCHAIN_SERVICE}'. Create it with:\n"
            f"  security add-generic-password -s {KEYCHAIN_SERVICE} -a <your email> -w"
        )
    m = re.search(r'"acct"<blob>="([^"]*)"', meta)
    if not m or not m.group(1):
        fail(f"Keychain item '{KEYCHAIN_SERVICE}' has no account (email) set.")
    return m.group(1), password


def request(method, path, token=None, body=None):
    headers = {"apikey": SUPABASE_KEY, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SUPABASE_URL + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        fail(f"{method} {path.split('?')[0]} failed ({e.code}): {detail}")
    except (urllib.error.URLError, TimeoutError) as e:
        fail(f"could not reach Supabase: {e}")


def fetch_board():
    email, password = keychain_credentials()
    auth = request("POST", "/auth/v1/token?grant_type=password",
                   body={"email": email, "password": password})
    token = auth.get("access_token")
    if not token:
        fail("sign-in returned no access token.")
    rows = request("GET", "/rest/v1/kanban_state?select=state", token=token)
    if not rows:
        fail("signed in, but no board was found for this account.")
    return rows[0].get("state") or {}


def open_tasks(board, today):
    cols = {c.get("id"): c.get("title", "") for c in board.get("columns", [])}
    projects = {p.get("id"): p.get("name") for p in board.get("projects", [])}
    tasks = []
    for card in board.get("cards", []):
        col_id = card.get("col")
        col_title = cols.get(col_id, col_id or "")
        if col_id == "done" or col_title.strip().lower() == "done":
            continue
        due = card.get("due") or None
        try:
            due_date = dt.date.fromisoformat(due) if due else None
        except ValueError:
            due_date = None
        tasks.append({
            "title": (card.get("title") or "(untitled)").strip(),
            "column": col_title,
            "project": projects.get(card.get("project")),
            "priority": card.get("priority") or None,
            "due": due_date.isoformat() if due_date else None,
            "days_until_due": (due_date - today).days if due_date else None,
        })
    tasks.sort(key=lambda t: (
        t["due"] is None, t["due"] or "", PRIORITY_ORDER.get(t["priority"], 3), t["title"].lower()
    ))
    return tasks


def bucket(t):
    d = t["days_until_due"]
    if d is not None and d < 0:
        return "Overdue"
    if d == 0:
        return "Due today"
    if d is not None and d <= 7:
        return "Due this week"
    if t["column"].strip().lower() == "in progress":
        return "In progress (no near due date)"
    return None


def to_markdown(tasks):
    if not tasks:
        return "No open tasks on the kanban board."
    order = ["Overdue", "Due today", "Due this week", "In progress (no near due date)"]
    groups = {k: [] for k in order}
    later = 0
    for t in tasks:
        b = bucket(t)
        if b:
            groups[b].append(t)
        else:
            later += 1
    lines = []
    for name in order:
        if not groups[name]:
            continue
        lines.append(f"### {name}")
        for t in groups[name]:
            bits = [t["column"]]
            if t["priority"]:
                bits.append(t["priority"].capitalize())
            if t["project"]:
                bits.append(t["project"])
            if t["due"]:
                bits.append(f"due {t['due']}")
            lines.append(f"- {t['title']} ({', '.join(bits)})")
        lines.append("")
    if later:
        lines.append(f"Plus {later} other open task{'s' if later != 1 else ''} with no due date this week.")
    return "\n".join(lines).rstrip() or "No open tasks on the kanban board."


def main():
    today = dt.date.today()
    tasks = open_tasks(fetch_board(), today)
    if "--json" in sys.argv[1:]:
        print(json.dumps({"date": today.isoformat(), "tasks": tasks}, indent=2))
    else:
        print(to_markdown(tasks))


if __name__ == "__main__":
    main()
