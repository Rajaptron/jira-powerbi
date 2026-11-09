"""Turns a Jira changelog into status transitions, time-in-status and per-ticket cycle metrics."""
import datetime as dt


def parse_ts(value):
    """
    Jira timestamps: usually text like 2026-09-01T10:00:00.000+0000, but the Cloud bulk changelog
    sends epoch milliseconds (1727776800000). Returns naive UTC, or None.
    """
    if value is None or value == "":
        return None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        value = int(value.strip())
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if abs(value) > 1e11 else float(value)
        return dt.datetime(1970, 1, 1) + dt.timedelta(seconds=seconds)
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return dt.datetime.strptime(value, fmt).astimezone(dt.timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    return None


def _fmt(ts):
    return ts.strftime("%Y-%m-%d %H:%M") if ts else ""


def _days(a, b):
    return round((b - a).total_seconds() / 86400.0, 2)


def analyse(issue, histories, category_of, now=None):
    """
    issue: the flattened issue row (needs Key, Assignee, Status, `_created_ts`, `_resolved_ts`).
    histories: Jira changelog histories (any order).
    category_of: function(status_name) -> Jira status category key ('new' / 'indeterminate' / 'done').
    Returns (transitions, durations, extras). Buckets are added later by board.apply().
    """
    now = now or dt.datetime.utcnow()
    created = issue["_created_ts"] or now
    events, desc_edits = [], []
    for h in histories:
        ts = parse_ts(h.get("created"))
        if not ts:
            continue
        by = (h.get("author") or {}).get("displayName", "")
        status_item = assignee_item = None
        for item in h.get("items", []):
            field = item.get("fieldId") or item.get("field")
            if field == "status":
                status_item = item
            elif field == "assignee":
                assignee_item = item
            elif field == "description":
                desc_edits.append({"At": _fmt(ts), "By": by})
        if status_item or assignee_item:
            events.append((ts, by, status_item, assignee_item))
    events.sort(key=lambda e: e[0])
    desc_edits.sort(key=lambda e: e["At"])

    # Who was assigned before the first recorded assignee change?
    first_assign = next((a for _, _, _, a in events if a), None)
    assignee = (first_assign.get("fromString") if first_assign else issue["Assignee"]) or "Unassigned"
    first_status = next((s for _, _, s, _ in events if s), None)
    status = first_status.get("fromString") if first_status else issue["Status"]

    transitions, durations = [], []
    seg_start, seg_assignee = created, assignee
    dev_start = done_at = None
    closed_by = ""
    for ts, by, s_item, a_item in events:
        if a_item:
            assignee = a_item.get("toString") or "Unassigned"
        if not s_item:
            continue
        to_status = s_item.get("toString") or ""
        if category_of(status) != "done":
            durations.append({"Key": issue["Key"], "Status": status, "Assignee": seg_assignee,
                              "Entered": _fmt(seg_start), "Left": _fmt(ts), "Days": _days(seg_start, ts)})
        transitions.append({
            "Key": issue["Key"], "Project": issue["Project"], "Service": issue["Service"],
            "IssueType": issue["IssueType"], "StoryPoints": issue["StoryPoints"],
            "Date": _fmt(ts), "MovedBy": by, "Assignee": assignee, "From": status, "To": to_status,
        })
        to_cat = category_of(to_status)
        if to_cat == "indeterminate" and dev_start is None:
            dev_start = ts
        if to_cat == "done":
            done_at, closed_by = ts, assignee
        elif category_of(status) == "done":
            done_at, closed_by = None, ""  # reopened after being done
        status, seg_start, seg_assignee = to_status, ts, assignee

    if category_of(status) != "done":  # still open: time so far in the current status
        durations.append({"Key": issue["Key"], "Status": status, "Assignee": seg_assignee,
                          "Entered": _fmt(seg_start), "Left": "", "Days": _days(seg_start, now)})

    if done_at is None and issue.get("StatusCategoryKey") == "done" and issue.get("_resolved_ts"):
        done_at, closed_by = issue["_resolved_ts"], issue["Assignee"]
    extras = {
        "DoneDate": done_at.strftime("%Y-%m-%d") if done_at else "",
        "ClosedBy": closed_by if done_at else "",
        "DevStarted": dev_start.strftime("%Y-%m-%d") if dev_start else "",
        "CycleDays": _days(dev_start, done_at) if dev_start and done_at else None,
        "LeadDays": _days(created, done_at) if done_at else None,
        "DescriptionEdits": desc_edits,  # when (and by whom) the description changed — used for BA rework
    }
    return transitions, durations, extras
