"""Pulls issues + worklogs from Jira and flattens them into report-ready tables."""
import datetime as dt
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor

from . import board, history
from .jira_client import JiraClient, JiraError

WORKERS = 8  # parallel Jira calls for per-ticket extras (Jira Cloud allows this comfortably; 429s are retried)

STORY_POINT_FIELD_NAMES = ["story points", "story point estimate", "story points estimate"]


def chosen_projects(cfg):
    return sorted({p.strip() for p in cfg.get("projects", []) if p.strip()})


def window_clause(cfg):
    """All open tickets, plus closed ones touched in the last N days."""
    days = int(cfg.get("updated_within_days") or 0)
    return "(statusCategory != Done OR updated >= -%dd)" % days if days else ""


def build_jql(cfg, projects=None):
    projects = chosen_projects(cfg) if projects is None else projects
    custom = cfg.get("jql", "").strip()
    if custom:
        # A custom JQL narrows the ticked projects. Jira Cloud refuses "unbounded" searches (no project or other
        # restriction), so the project clause is added unless the JQL already names a project itself.
        m = re.search(r"\s*\bORDER\s+BY\b.*$", custom, re.I | re.S)
        body, order = (custom[:m.start()].strip(), m.group(0).strip()) if m else (custom, "ORDER BY updated DESC")
        if projects and not re.search(r"\bproject\b", body, re.I):
            body = 'project in (%s)%s' % (", ".join('"%s"' % p for p in projects), " AND (%s)" % body if body else "")
        if not body:
            raise ValueError("The custom JQL needs a condition (e.g. project = KEY), or tick a project in Settings.")
        return body + " " + order
    if not projects:
        # Never fall back to "everything": on a big Jira that's tens of thousands of tickets.
        raise ValueError("Pick at least one project in Settings → What to pull (or enter a custom JQL).")
    clauses = ["project in (%s)" % ", ".join('"%s"' % p for p in projects)]
    if window_clause(cfg):
        clauses.append(window_clause(cfg))
    return " AND ".join(clauses) + " ORDER BY updated DESC"


# ---------- value helpers ----------
def _name(obj):
    if not obj:
        return ""
    if isinstance(obj, dict):
        return obj.get("displayName") or obj.get("name") or obj.get("value") or obj.get("title") or ""
    return str(obj)


def field_to_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(t for t in (field_to_text(v) for v in value) if t)
    if isinstance(value, dict):
        if "value" in value and "child" in value:
            return "%s / %s" % (value["value"], field_to_text(value["child"]))
        return _name(value)
    return str(value)


def description_text(value):
    """Plain text from a description: Jira Cloud sends a document tree (ADF), Server/DC sends a string."""
    if not value:
        return ""
    if isinstance(value, str):
        return re.sub(r"\s+", " ", re.sub(r"[{}*_#|\[\]]|h\d\.", " ", value)).strip()
    parts = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                parts.append(node.get("text", ""))
            for child in node.get("content") or []:
                walk(child)
            if node.get("type") in ("paragraph", "heading", "listItem", "tableRow"):
                parts.append(" ")
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _date(value):
    return value[:10] if value else ""


def _hours(seconds):
    return round(seconds / 3600.0, 2) if seconds else 0.0


_SERVER_SPRINT_RE = re.compile(r"(\w+)=([^,\]]*)")


def parse_sprint(value):
    """Return (name, state, start, end) of the most relevant sprint: active > future > latest closed."""
    if not value:
        return "", "", "", ""
    sprints = []
    for s in value if isinstance(value, list) else [value]:
        if isinstance(s, dict):
            sprints.append((s.get("name", ""), (s.get("state") or "").lower(),
                            _date(s.get("startDate")), _date(s.get("completeDate") or s.get("endDate"))))
        else:  # Server/DC serialises sprints as "...[id=1,state=ACTIVE,name=Sprint 1,...]"
            attrs = dict(_SERVER_SPRINT_RE.findall(str(s)))
            clean = lambda v: "" if v in ("", "<null>") else v[:10]
            sprints.append((attrs.get("name", ""), attrs.get("state", "").lower(),
                            clean(attrs.get("startDate", "")), clean(attrs.get("completeDate") or attrs.get("endDate", ""))))
    for wanted in ("active", "future"):
        for sprint in sprints:
            if sprint[1] == wanted:
                return sprint
    return sprints[-1]


# ---------- main fetch ----------
def discover_fields(client):
    fields = client.fields()
    by_name = {}
    for f in fields:
        by_name.setdefault(f.get("name", "").lower(), f["id"])
    sp_ids = [by_name[n] for n in STORY_POINT_FIELD_NAMES if n in by_name]
    sprint_id = by_name.get("sprint")
    return by_name, sp_ids, sprint_id


def field_ids_named(fields, name):
    """Every field id with this name. Team-managed projects each have their own copy of e.g. "Service"."""
    name = (name or "").strip().lower()
    return [f["id"] for f in fields if f.get("name", "").strip().lower() == name]


def _first_value(value):
    """One service per ticket: for multi-select fields take the first choice (same rule as components)."""
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    return field_to_text(value)


def service_of(issue_fields, source, custom_id):
    f = issue_fields
    if source == "project":
        return _name(f.get("project")) or "(No project)"
    if source == "component":
        comps = f.get("components") or []
        return comps[0]["name"] if comps else "(No component) " + _name(f.get("project"))
    if source == "label":
        labels = f.get("labels") or []
        return labels[0] if labels else "(No label)"
    if source == "epic":
        parent = f.get("parent") or {}
        return field_to_text((parent.get("fields") or {}).get("summary")) or "(No epic)"
    if source.startswith("field:") and custom_id:
        ids = custom_id if isinstance(custom_id, list) else [custom_id]
        return next((v for v in (_first_value(f.get(i)) for i in ids) if v), "") or "(Not set)"
    return "(Unknown)"


def fetch(cfg, progress=lambda msg: None):
    if not cfg.get("jira_url") or not cfg.get("token"):
        raise ValueError("Jira URL and API token aren't set yet. Open Settings in the app first.")
    client = JiraClient(cfg["jira_url"], cfg.get("email", ""), cfg["token"])
    started_utc = dt.datetime.utcnow()
    progress("Connecting to %s..." % client.base_url)
    client.myself()

    progress("Discovering fields (story points, sprint)...")
    by_name, sp_ids, sprint_id = discover_fields(client)
    source = cfg.get("service_source", "component")
    custom_id = None
    if source.startswith("field:"):
        custom_id = field_ids_named(client.fields(), source[6:])
        if not custom_id:
            raise ValueError("Custom field '%s' was not found in Jira." % source[6:])

    want = ["summary", "status", "assignee", "reporter", "issuetype", "priority", "components",
            "labels", "project", "created", "updated", "resolutiondate", "parent", "timespent",
            "timeoriginalestimate", "timeestimate", "worklog", "issuelinks", "creator", "description"] + sp_ids
    if sprint_id:
        want.append(sprint_id)
    if custom_id:
        want.extend(custom_id)

    jql = build_jql(cfg)
    # Also bring back the custom fields these projects actually use (e.g. "Service", "Team"), so the
    # dashboard can switch where Services come from without another trip to Jira.
    extra = {}
    if cfg.get("collect_fields", True):
        progress("Finding the fields your projects use...")
        try:
            usage, _ = field_usage(client, jql, sample=100)
            extra = {r["name"]: r["ids"] for r in usage if r["used_pct"] >= 5}
            extra = dict(list(extra.items())[:25])
            for ids in extra.values():
                want.extend(i for i in ids if i not in want)
        except JiraError:
            extra = {}
    progress("Running JQL: %s" % jql)
    include_worklogs = cfg.get("include_worklogs", True)
    worklog_cutoff = (dt.date.today() - dt.timedelta(days=int(cfg.get("worklog_days") or 90))).isoformat()
    today = dt.date.today()

    include_history = cfg.get("include_history", True)
    progress("Reading workflow statuses...")
    categories = {s["name"]: (s.get("statusCategory") or {}).get("key", "") for s in client.statuses()}
    _, category_of = board.resolver(cfg, categories)

    issues, worklogs, transitions, status_time = [], [], [], []
    # Jira Cloud can return status history for 1,000 tickets per call, which is far quicker than
    # expanding the changelog inside every search page. Server/Data Center still uses the expand.
    use_bulk = include_history and client.is_cloud
    raws = list(client.search(jql, want, progress, expand="changelog" if include_history and not use_bulk else None))
    histories = {}
    if include_history:
        histories = _histories(client, raws, use_bulk, progress)
    extra_worklogs = {}
    if include_worklogs:
        truncated = [r["key"] for r in raws if (r["fields"].get("worklog") or {}).get("total", 0)
                     > len((r["fields"].get("worklog") or {}).get("worklogs", []))]
        if truncated:
            progress("Reading worklogs for %d tickets with long timesheets..." % len(truncated))
            extra_worklogs = _parallel(client.all_worklogs, truncated)

    for raw in raws:
        f = raw["fields"]
        status = f.get("status") or {}
        category = (status.get("statusCategory") or {}).get("key", "")
        sp = next((f.get(i) for i in sp_ids if f.get(i) is not None), None)
        sprint_name, sprint_state, sprint_start, sprint_end = parse_sprint(f.get(sprint_id)) if sprint_id else ("", "", "", "")
        parent = f.get("parent") or {}
        created = _date(f.get("created"))
        resolved = _date(f.get("resolutiondate"))
        service = service_of(f, source, custom_id)
        issue = {
            "Key": raw["key"],
            "Id": str(raw.get("id", "")),
            "URL": "%s/browse/%s" % (client.base_url, raw["key"]),
            "Summary": f.get("summary", ""),
            "Project": _name(f.get("project")),
            "Service": service,
            "IssueType": _name(f.get("issuetype")),
            "Status": status.get("name", ""),
            "StatusCategory": (status.get("statusCategory") or {}).get("name", ""),
            "StatusCategoryKey": category,
            "Bucket": "",
            "Assignee": _name(f.get("assignee")) or "Unassigned",
            "Reporter": _name(f.get("reporter")),
            "Priority": _name(f.get("priority")),
            "StoryPoints": float(sp) if isinstance(sp, (int, float)) else 0.0,
            "Sprint": sprint_name,
            "SprintState": sprint_state,
            "SprintStart": sprint_start,
            "SprintEnd": sprint_end,
            "Parent": ("%s %s" % (parent.get("key", ""), (parent.get("fields") or {}).get("summary", ""))).strip(),
            "ParentKey": parent.get("key", ""),
            "Creator": _name(f.get("creator")),
            "Description": description_text(f.get("description"))[:600],  # start of it, for PRD matching
            # Linked tickets (any link type, either direction) — how defects are tied to stories.
            "Links": sorted({(l.get("outwardIssue") or l.get("inwardIssue") or {}).get("key", "")
                             for l in f.get("issuelinks") or []} - {""}),
            "Labels": ", ".join(f.get("labels") or []),
            "Components": ", ".join(c["name"] for c in f.get("components") or []),
            "Fields": {n: v for n, v in ((n, next((x for x in (_first_value(f.get(i)) for i in ids) if x), ""))
                                          for n, ids in extra.items()) if v},
            "Created": created,
            "Updated": _date(f.get("updated")),
            "Resolved": resolved,
            "AgeDays": (today - dt.date.fromisoformat(created)).days if created and not resolved else None,
            "HoursLogged": _hours(f.get("timespent")),
            "OriginalEstimateHours": _hours(f.get("timeoriginalestimate")),
            "RemainingHours": _hours(f.get("timeestimate")),
        }
        if include_history:
            issue["_created_ts"] = history.parse_ts(f.get("created"))
            issue["_resolved_ts"] = history.parse_ts(f.get("resolutiondate"))
            moves, spans, extras = history.analyse(issue, histories.get(raw["key"], []), category_of)
            transitions.extend(moves)
            status_time.extend(spans)
            issue.update(extras)
            del issue["_created_ts"], issue["_resolved_ts"]
        issues.append(issue)

        if include_worklogs and f.get("timespent"):
            wl = f.get("worklog") or {}
            logs = extra_worklogs.get(raw["key"]) or wl.get("worklogs", [])
            for log in logs:
                day = _date(log.get("started"))
                if day < worklog_cutoff:
                    continue
                worklogs.append({
                    "Key": raw["key"],
                    "Service": service,
                    "Author": _name(log.get("author")),
                    "Date": day,
                    "Hours": _hours(log.get("timeSpentSeconds")),
                })

    progress("Done: %d issues, %d worklog entries, %d status changes." % (len(issues), len(worklogs), len(transitions)))
    return board.apply({
        "meta": {
            "refreshed_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "synced_utc": started_utc.isoformat(timespec="seconds"),
            "jira_url": client.base_url,
            "jql": jql,
            "service_source": source,
            "story_points_fields": sp_ids,
            "worklog_days": int(cfg.get("worklog_days") or 90),
            "demo": False,
            "has_history": bool(include_history),
            "status_categories": categories,
            "config_fingerprint": fingerprint(cfg),
            "projects_synced": chosen_projects(cfg),
            "service_fields": sorted(extra),
        },
        "issues": issues,
        "worklogs": worklogs,
        "transitions": transitions,
        "status_time": status_time,
    }, cfg)


def visible(data):
    """The dataset without tickets whose status is hidden in the board setup."""
    keys = {i["Key"] for i in data["issues"] if i.get("Hidden")}
    if not keys:
        return data
    out = dict(data)
    for table in ("issues", "worklogs", "transitions", "status_time"):
        out[table] = [r for r in data.get(table) or [] if r["Key"] not in keys]
    return out


def people_table(data):
    """One row per person: open work per bucket, delivery and time logged."""
    done = data.get("done_bucket", "Done")
    open_buckets = [b for b in data.get("buckets", []) if b != done]
    people = {}

    def row(name):
        r = people.get(name)
        if r is None:
            r = people[name] = {"Person": name, "ActiveTickets": 0}
            r.update({"Open in " + b: 0 for b in open_buckets})
            r.update({"OpenStoryPoints": 0.0, "TicketsClosed": 0, "StoryPointsDelivered": 0.0, "AvgCycleDays": None,
                      "TimesReopened": 0, "HoursLogged": 0.0, "Services": set(), "_cycles": []})
        return r

    for i in data["issues"]:
        if i["Assignee"] == "Unassigned":
            continue
        r = row(i["Assignee"])
        r["Services"].add(i["Service"])
        if i["Bucket"] != done:
            r["ActiveTickets"] += 1
            r["OpenStoryPoints"] += i["StoryPoints"]
            r["Open in " + i["Bucket"]] = r.get("Open in " + i["Bucket"], 0) + 1
        r["TimesReopened"] += i.get("TimesReopened") or 0
    # Credit closed tickets to whoever held them when they reached Done.
    for i in data["issues"]:
        closer = i.get("ClosedBy") or (i["Assignee"] if i["Bucket"] == done and "ClosedBy" not in i else "")
        if not closer or closer == "Unassigned":
            continue
        r = row(closer)
        r["TicketsClosed"] += 1
        r["StoryPointsDelivered"] += i["StoryPoints"]
        if i.get("CycleDays") is not None:
            r["_cycles"].append(i["CycleDays"])
    for w in data["worklogs"]:
        r = row(w["Author"])
        r["HoursLogged"] += w["Hours"]
        r["Services"].add(w["Service"])
    out = []
    for r in sorted(people.values(), key=lambda x: x["Person"].lower()):
        r["Services"] = ", ".join(sorted(r["Services"]))
        cycles = r.pop("_cycles")
        r["AvgCycleDays"] = round(sum(cycles) / len(cycles), 1) if cycles else None
        r["HoursLogged"] = round(r["HoursLogged"], 2)
        out.append(r)
    return out


# ---------- partial ("refresh this widget") updates ----------
def _quote(values):
    return ", ".join('"%s"' % v.replace("\\", "\\\\").replace('"', '\\"') for v in values)


def scoped_jql(cfg, data, scope):
    """JQL for tickets in the widget's scope that changed since the last full refresh."""
    base = re.sub(r"\s+ORDER\s+BY\s+.*$", "", build_jql(cfg), flags=re.I | re.S)
    meta = data["meta"]
    if meta.get("synced_utc"):
        since = dt.datetime.fromisoformat(meta["synced_utc"])
        elapsed = dt.datetime.utcnow() - since
    else:  # data from an older version: fall back to local refresh time
        elapsed = dt.datetime.now() - dt.datetime.strptime(meta["refreshed_at"], "%Y-%m-%d %H:%M")
    minutes = int(elapsed.total_seconds() // 60) + 15  # small overlap so nothing slips between runs
    clauses = ["(%s)" % base, "updated >= -%dm" % minutes]
    if scope.get("projects"):
        clauses.append("project in (%s)" % _quote(scope["projects"]))
    if scope.get("labels") and "(No label)" not in scope["labels"]:
        clauses.append("labels in (%s)" % _quote(scope["labels"]))
    if scope.get("types"):
        clauses.append("issuetype in (%s)" % _quote(scope["types"]))
    services = scope.get("services") or []
    if services and meta.get("service_source") == "component" and not any(v.startswith("(No ") for v in services):
        clauses.append("component in (%s)" % _quote(services))
    return " AND ".join(clauses) + " ORDER BY updated DESC"


def merge(old, new):
    """Replace every ticket that came back in `new` (plus its worklogs and history) inside `old`."""
    keys = {i["Key"] for i in new["issues"]}
    merged = dict(old)
    for table in ("issues", "worklogs", "transitions", "status_time"):
        kept = [r for r in old.get(table) or [] if r["Key"] not in keys]
        merged[table] = kept + list(new.get(table) or [])
    merged["meta"] = dict(old["meta"], updated_at=new["meta"]["refreshed_at"], last_partial_count=len(keys),
                          status_categories={**(old["meta"].get("status_categories") or {}),
                                             **(new["meta"].get("status_categories") or {})},
                          service_fields=sorted(set(old["meta"].get("service_fields") or [])
                                                | set(new["meta"].get("service_fields") or [])))
    return merged, len(keys)


def preview_services(cfg, projects, source, sample=300):
    """What the Services list will contain for these projects with this service setting (from recent tickets)."""
    if not cfg.get("jira_url") or not cfg.get("token"):
        raise ValueError("Enter the Jira URL and API token first, then click Save & test connection.")
    if not projects:
        raise ValueError("Tick at least one project first.")
    client = JiraClient(cfg["jira_url"], cfg.get("email", ""), cfg["token"])
    custom_id = None
    if source.startswith("field:"):
        custom_id = field_ids_named(client.fields(), source[6:])
        if not custom_id:
            raise ValueError("Custom field '%s' was not found in Jira." % source[6:])
    fields = ["components", "labels", "project", "parent"] + (custom_id or [])
    jql = "project in (%s) ORDER BY updated DESC" % _quote(projects)
    counts, seen = {}, 0
    for raw in client.search(jql, fields):
        name = service_of(raw["fields"], source, custom_id)
        counts[name] = counts.get(name, 0) + 1
        seen += 1
        if seen >= sample:
            break
    values = sorted(({"name": k, "count": v} for k, v in counts.items()), key=lambda r: -r["count"])
    unset = sum(r["count"] for r in values if r["name"].startswith("(No ") or r["name"] in ("(Not set)", "(Unknown)"))
    return {"values": values, "sampled": seen, "unset": unset}


# ---------- speed helpers ----------
def _parallel(fn, keys):
    """Run fn(key) for many tickets at once; returns {key: result}."""
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return dict(zip(keys, pool.map(fn, keys)))


def _histories(client, raws, use_bulk, progress):
    """Status/assignee history per ticket key, fetched in as few calls as possible."""
    out = {}
    if use_bulk:
        by_id = {str(r["id"]): r["key"] for r in raws}
        ids = list(by_id)
        chunks = [ids[n:n + 1000] for n in range(0, len(ids), 1000)]
        progress("Reading status history for %d tickets (%d bulk call%s)..." % (len(ids), len(chunks), "" if len(chunks) == 1 else "s"))
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for part in pool.map(client.bulk_changelogs, chunks):
                    for issue_id, hist in part.items():
                        if issue_id in by_id:
                            out[by_id[issue_id]] = hist
            return out
        except JiraError as e:
            if e.status not in (400, 404, 405):
                raise
            progress("Bulk history isn't available on this Jira, reading it ticket by ticket...")
            return _parallel(client.changelog, [r["key"] for r in raws])
    # Expanded with the search: only tickets whose history was cut short need a follow-up call.
    short = []
    for r in raws:
        log = r.get("changelog")
        if log is None or log.get("total", 0) > len(log.get("histories", [])):
            short.append(r["key"])
        else:
            out[r["key"]] = log.get("histories", [])
    if short:
        progress("Reading full history for %d tickets with long histories..." % len(short))
        out.update(_parallel(client.changelog, short))
    return out


def fingerprint(cfg):
    """Settings that change *which* data is pulled; if any of these change, a quick refresh isn't enough."""
    keys = ("jira_url", "email", "service_source", "include_history", "include_worklogs", "worklog_days")
    sig = {k: cfg.get(k) for k in keys}
    sig["jql"] = cfg["jql"].strip() if cfg.get("jql", "").strip() else window_clause(cfg)
    return hashlib.sha1(json.dumps(sig, sort_keys=True).encode()).hexdigest()[:12]


def touch_up(data, cfg):
    """Keep time-based numbers current for tickets that didn't change since the last pull."""
    today = dt.date.today()
    now = dt.datetime.utcnow()
    for i in data["issues"]:
        if i.get("Created") and not i.get("Resolved"):
            i["AgeDays"] = (today - dt.date.fromisoformat(i["Created"])).days
    for s in data.get("status_time") or []:
        if not s.get("Left") and s.get("Entered"):
            entered = dt.datetime.strptime(s["Entered"], "%Y-%m-%d %H:%M")
            s["Days"] = round((now - entered).total_seconds() / 86400.0, 2)
    cutoff = (today - dt.timedelta(days=int(cfg.get("worklog_days") or 90))).isoformat()
    data["worklogs"] = [w for w in data.get("worklogs") or [] if w["Date"] >= cutoff]
    return data


def quick_refresh(cfg, old, progress=lambda msg: None):
    """
    Pull only tickets changed since the last sync, then drop tickets that no longer match the JQL.
    Returns (data, changed, removed), or None when a full refresh is needed instead.
    """
    meta = old.get("meta") or {}
    if meta.get("demo") or not meta.get("synced_utc") or meta.get("config_fingerprint") != fingerprint(cfg):
        return None
    started = dt.datetime.utcnow()
    custom = bool(cfg.get("jql", "").strip())
    synced = set(meta.get("projects_synced") or [])
    wanted = set(chosen_projects(cfg))
    added = sorted(wanted - synced) if not custom else []
    kept = sorted(wanted & synced) if not custom else []
    data, changed = old, 0
    if custom or kept:
        progress("Looking for tickets changed since %s..." % meta.get("refreshed_at", "the last refresh"))
        jql = scoped_jql(cfg, old, {}) if custom else scoped_jql(dict(cfg, projects=kept), old, {})
        data, changed = merge(data, fetch(dict(cfg, jql=jql), progress))
    if added:
        # Newly ticked projects: pull just those in full; the rest of the data stays as it is.
        progress("Pulling newly added project%s %s..." % ("s" if len(added) > 1 else "", ", ".join(added)))
        data, n = merge(data, fetch(dict(cfg, jql=build_jql(cfg, added)), progress))
        changed += n
    progress("Checking for tickets that were deleted or moved out...")
    client = JiraClient(cfg["jira_url"], cfg.get("email", ""), cfg["token"])
    keys, ids = client.issue_keys(build_jql(cfg))
    before = len(data["issues"])
    if keys:
        keep = keys
    elif ids and all(i.get("Id") for i in data["issues"]):
        keep = {i["Key"] for i in data["issues"] if i["Id"] in ids}
    elif not keys and not ids:
        keep = set()  # the JQL genuinely matches nothing any more
    else:
        # Jira only sent ids and this data predates ids being stored: skip the clean-up rather than guess.
        progress("Skipping the deleted-ticket check this time (it runs after the next full reload).")
        keep = {i["Key"] for i in data["issues"]}
    for table in ("issues", "worklogs", "transitions", "status_time"):
        data[table] = [r for r in data.get(table) or [] if r["Key"] in keep]
    removed = before - len(data["issues"])
    data["meta"] = dict(data["meta"], refreshed_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                        synced_utc=started.isoformat(timespec="seconds"), config_fingerprint=fingerprint(cfg),
                        projects_synced=sorted(wanted), jql=build_jql(cfg))
    data["meta"].pop("updated_at", None)
    return board.apply(touch_up(data, cfg), cfg), changed, removed


def estimate(cfg, projects):
    """Roughly how many tickets a pull would bring back, per project."""
    if not cfg.get("jira_url") or not cfg.get("token"):
        raise ValueError("Save & test the connection first.")
    client = JiraClient(cfg["jira_url"], cfg.get("email", ""), cfg["token"])
    window = window_clause(cfg)
    jql = lambda p: 'project = "%s"%s' % (p, " AND " + window if window else "")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        counts = dict(zip(projects, pool.map(lambda p: client.count(jql(p)), projects)))
    return {"counts": counts, "total": sum(counts.values())}


# Field types that can't sensibly name a service (numbers, dates, sprints, ranking, dev links...).
_NOT_A_SERVICE = ("gh-sprint", "gh-lexo-rank", "devsummary", "gh-epic", "float", "datepicker", "datetime",
                  "vp-origin", "jsw-story-points", "sd-sla", "sd-request-feedback")


def field_usage(client, jql, sample=100, fields=None):
    """
    Custom fields filled in on the most recent `sample` tickets matching the JQL, most used first.
    Same-named fields (e.g. each team-managed project's own "Service") are merged under one name.
    """
    fields = fields if fields is not None else client.fields()
    usable = {f["id"]: f for f in fields if f.get("custom")
              and not any(t in str((f.get("schema") or {}).get("custom", "")) for t in _NOT_A_SERVICE)
              and (f.get("schema") or {}).get("type") not in ("number", "date", "datetime")}
    seen, used, examples, ids = 0, {}, {}, {}
    for raw in client.search(jql, ["*all"]):
        seen += 1
        for fid, f in usable.items():
            text = _first_value(raw["fields"].get(fid))
            if text:
                name = f.get("name", fid)
                used.setdefault(name, set()).add(raw["key"])
                ids.setdefault(name, set()).add(fid)
                ex = examples.setdefault(name, [])
                if text not in ex and len(ex) < 3:
                    ex.append(text[:40])
        if seen >= sample:
            break
    rows = [{"name": n, "used_pct": round(100.0 * len(k) / seen) if seen else 0, "examples": examples.get(n, []),
             "ids": sorted(ids[n])} for n, k in used.items()]
    return sorted(rows, key=lambda r: (-r["used_pct"], r["name"].lower())), seen


def project_fields(cfg, projects, sample=100):
    """Custom fields actually filled in on recent tickets of these projects, most used first."""
    if not cfg.get("jira_url") or not cfg.get("token"):
        raise ValueError("Save & test the connection first.")
    if not projects:
        raise ValueError("Tick at least one project first.")
    client = JiraClient(cfg["jira_url"], cfg.get("email", ""), cfg["token"])
    rows, seen = field_usage(client, "project in (%s) ORDER BY updated DESC" % _quote(projects), sample)
    return {"fields": [{k: r[k] for k in ("name", "used_pct", "examples")} for r in rows], "sampled": seen}


# ---------- choosing where "Service" comes from, without going back to Jira ----------
BUILTIN_SOURCES = ("component", "label", "epic", "project")


def service_from_row(issue, source):
    """Re-derive a ticket's Service from what's already stored on it (see fetch: Components, Labels, Parent, Fields)."""
    if source == "project":
        return issue.get("Project") or "(No project)"
    if source == "component":
        first = (issue.get("Components") or "").split(", ")[0]
        return first or "(No component) " + issue.get("Project", "")
    if source == "label":
        return (issue.get("Labels") or "").split(", ")[0] or "(No label)"
    if source == "epic":
        parent = issue.get("Parent") or ""
        return parent.split(" ", 1)[1] if " " in parent else "(No epic)"
    if source.startswith("field:"):
        return (issue.get("Fields") or {}).get(source[6:]) or "(Not set)"
    return "(Unknown)"


def can_rederive(data, source):
    """True when every ticket already carries what's needed to switch to this source offline."""
    if source in BUILTIN_SOURCES:
        return True
    return source.startswith("field:") and source[6:] in (data["meta"].get("service_fields") or [])


def rederive_services(data, source, cfg):
    by_key = {}
    for i in data["issues"]:
        i["Service"] = by_key[i["Key"]] = service_from_row(i, source)
    for table in ("worklogs", "transitions"):
        for r in data.get(table) or []:
            r["Service"] = by_key.get(r["Key"], r.get("Service"))
    data["meta"] = dict(data["meta"], service_source=source, config_fingerprint=fingerprint(cfg))
    return data
