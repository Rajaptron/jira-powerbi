"""Synthetic dataset so the dashboard and Power BI model can be tried without Jira access."""
import datetime as dt
import random

from . import board, history

SERVICES = ["Page Builder", "Publishing", "Forms & Leads", "Analytics", "Billing", "AI Copywriter"]
PROJECT_OF = {"Page Builder": "Builder", "Publishing": "Builder", "Forms & Leads": "Growth",
              "Analytics": "Growth", "Billing": "Platform", "AI Copywriter": "AI Builder"}
PEOPLE = ["Aarav Shah", "Priya Nair", "Kannan S", "Maya Lopez", "Dev Patel", "Sofia Chen",
          "Liam O'Brien", "Neha Gupta", "Omar Haddad", "Grace Kim"]
# A Product → Development → QA workflow.
WORKFLOW = [("Draft", "new"), ("PM Review", "new"), ("Ready for Refinement", "new"),
            ("Ready for Development", "new"), ("In Development", "indeterminate"), ("Peer Review", "indeterminate"),
            ("Ready for Test", "indeterminate"), ("In Testing", "indeterminate"), ("Done", "done")]
WEIGHTS = [4, 4, 7, 12, 16, 7, 6, 9, 35]
CATEGORY = dict(WORKFLOW)
PATH = [name for name, _ in WORKFLOW]
TYPES = ["Story", "Story", "Story", "Bug", "Task"]
LABELS = ["frontend", "backend", "api", "ux", "tech-debt", "customer-request", "performance"]
VERBS = ["Add", "Fix", "Refactor", "Improve", "Migrate", "Validate", "Support", "Optimise"]
NOUNS = ["template picker", "publish webhook", "form validation", "conversion report", "invoice PDF",
         "headline generator", "domain mapping", "A/B test toggle", "lead export", "usage limits"]
# A "Service" custom field that groups components differently, plus a "Team" field.
SERVICE_FIELD = {"Page Builder": "Builder Platform", "Publishing": "Builder Platform", "Forms & Leads": "Lead Capture",
                 "Analytics": "Insights", "Billing": "Billing and Metering (BMS)", "AI Copywriter": "AI Services"}
# Each service is an Epic, split into Features (high-level PRD requirements); stories sit under a Feature and are
# about one of its topics (so a PRD can be matched against them). A few PRD requirements are left without a story.
FEATURE_TOPICS = {
    "Billing": {"Invoices": ["invoice PDF for successful payments", "invoice download page", "tax and discount lines on invoices",
                             "billing address on invoices"],
                "Usage limits": ["usage limit warning at 80 percent"],
                "Payment methods": ["card payment webhook", "update saved card"],
                "Refunds": ["refund flow"]},
    "Forms & Leads": {"Form builder": ["drag and drop form fields", "email field validation", "reorder form fields", "form templates"],
                      "Lead export": ["lead CSV export"],
                      "Spam protection": ["form spam protection"]},
    "Page Builder": {"Templates": ["template picker"], "Editing": ["undo and redo", "section copy paste", "image cropping"],
                     "Preview": ["mobile preview"]},
    "Publishing": {"Domains": ["custom domain mapping", "SSL certificates"], "Publish flow": ["publish webhook", "scheduled publishing"]},
    "Analytics": {"Reports": ["conversion report", "traffic sources chart", "CSV report export"], "Experiments": ["A/B test toggle"]},
    "AI Copywriter": {"Copy generation": ["headline generator", "copy suggestions", "tone of voice setting"], "Credits": ["AI usage credits"]},
}
TOPICS = {svc: [t for ts in feats.values() for t in ts] for svc, feats in FEATURE_TOPICS.items()}
FEATURE_OF = {(svc, t): f for svc, feats in FEATURE_TOPICS.items() for f, ts in feats.items() for t in ts}
SPRINT_NOW = 42  # active sprint; two-week sprints


def sprint_dates(n, today):
    end = today + dt.timedelta(days=7 - (SPRINT_NOW - n) * 14)
    return (end - dt.timedelta(days=13)).isoformat(), end.isoformat()


# Who does what in the demo team (the real app gets this from Settings → Team roles).
DEMO_ROLES = {"Priya Nair": "PM", "Maya Lopez": "BA", "Grace Kim": "BA", "Kannan S": "QA", "Neha Gupta": "QA",
              "Aarav Shah": "Dev", "Dev Patel": "Dev", "Sofia Chen": "Dev", "Liam O'Brien": "Dev", "Omar Haddad": "Dev"}
BY_ROLE = {r: [p for p, x in DEMO_ROLES.items() if x == r] for r in ("PM", "BA", "QA", "Dev")}
BUG_PATH = ["Ready for Development", "In Development", "Ready for Test", "In Testing", "Done"]


def _ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _history(rnd, created, end, current, people, bounce, path=PATH, desc_after_dev=False):
    """
    Fake Jira changelog walking `path` up to `current`. Each move is made by the role that would make it:
    BA writes and sends to PM review, PM approves, Dev develops, QA tests / passes / sends back.
    """
    first = 0 if path is BUG_PATH else min(rnd.randint(0, 1), path.index(current))
    steps = path[first:path.index(current) + 1]
    if bounce and "In Testing" in steps[:-1]:
        at = steps.index("In Testing") + 1
        steps = steps[:at] + ["In Development", "Peer Review" if path is PATH else "Ready for Test", "Ready for Test", "In Testing"][:4 if path is PATH else 3] + steps[at:]
    span = max((end - created).total_seconds(), 3600 * len(steps))
    mover = {"PM Review": people["ba"], "Ready for Refinement": people["pm"], "Ready for Development": people["ba"],
             "In Development": people["dev"], "Peer Review": people["dev"], "Ready for Test": people["dev"],
             "In Testing": people["qa"], "Done": people["qa"]}
    out, prev, dev_at = [], steps[0], None
    for n, status in enumerate(steps[1:], start=1):
        ts = created + dt.timedelta(seconds=span * (n - rnd.uniform(0.1, 0.6)) / (len(steps) - 1))
        items = [{"field": "status", "fromString": prev, "toString": status}]
        if status == "In Development" and prev == "Ready for Development" and people["dev"] != "Unassigned":
            items.append({"field": "assignee", "fromString": None, "toString": people["dev"]})
        by = people["qa"] if (prev == "In Testing") else mover.get(status, people["ba"])
        out.append({"author": {"displayName": by}, "created": _ts(ts), "items": items})
        if status == "Ready for Development" and dev_at is None:
            dev_at = ts
        prev = status
    if desc_after_dev and dev_at:
        out.append({"author": {"displayName": people["ba"]}, "created": _ts(dev_at + dt.timedelta(hours=rnd.randint(4, 72))),
                    "items": [{"field": "description", "fromString": "", "toString": ""}]})
    out.append({"author": {"displayName": people["ba"]}, "created": _ts(created + dt.timedelta(minutes=30)),
                "items": [{"field": "description", "fromString": "", "toString": ""}]})  # the BA polishing the draft
    return out


def generate(seed=7, cfg=None):
    rnd = random.Random(seed)
    today = dt.date.today()
    now = dt.datetime.utcnow()
    category_of = lambda s: CATEGORY.get(s, "")
    issues, worklogs, transitions, status_time = [], [], [], []

    def add(issue, log, created_dt, resolved):
        issue.update(_created_ts=created_dt, _resolved_ts=dt.datetime.combine(resolved, dt.time(17)) if resolved else None)
        moves, spans, extras = history.analyse(issue, log, category_of, now)
        transitions.extend(moves)
        status_time.extend(spans)
        issue.update(extras)
        del issue["_created_ts"], issue["_resolved_ts"]
        issues.append(issue)

    def base(key, service, status, assignee, reporter, created, resolved, itype, points, sprint_no, in_sprint):
        cat = CATEGORY[status]
        s_start, s_end = sprint_dates(sprint_no, today) if in_sprint else ("", "")
        return {
            "Key": key, "Id": key.split("-")[1], "URL": "https://example.atlassian.net/browse/" + key,
            "Summary": "%s %s" % (rnd.choice(VERBS), rnd.choice(TOPICS.get(service, NOUNS))),
            "Project": PROJECT_OF[service], "Service": service, "IssueType": itype,
            "Status": status, "StatusCategory": {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}[cat],
            "StatusCategoryKey": cat, "Bucket": "", "Assignee": assignee, "Reporter": reporter, "Creator": reporter,
            "Priority": rnd.choice(["Highest", "High", "Medium", "Medium", "Low"]), "StoryPoints": points,
            "Sprint": "Sprint %d" % sprint_no if in_sprint else "",
            "SprintState": ("active" if sprint_no == SPRINT_NOW else "closed") if in_sprint else "",
            "SprintStart": s_start, "SprintEnd": s_end, "Parent": "", "ParentKey": "", "Links": [],
            "Labels": ", ".join(sorted(rnd.sample(LABELS, rnd.choice([0, 1, 1, 2])))), "Components": service,
            "Fields": {k: v for k, v in (("Service", SERVICE_FIELD[service] if rnd.random() < 0.9 else ""),
                                         ("Team", "Squad %s" % "AB"[PEOPLE.index(assignee) % 2] if assignee in PEOPLE else "")) if v},
            "Created": created.isoformat(), "Updated": (resolved or today).isoformat(),
            "Resolved": resolved.isoformat() if resolved else "",
            "AgeDays": None if resolved else (today - created).days,
            "HoursLogged": 0.0, "OriginalEstimateHours": 0.0, "RemainingHours": 0.0, "Description": "",
        }

    # Epic per service, Feature per group of topics: DEMO-1, DEMO-2, …
    containers, next_key = {}, iter(range(1, 1000))
    for svc, feats in FEATURE_TOPICS.items():
        epic = "DEMO-%d" % next(next_key)
        containers[svc] = (epic, svc, "Epic", "")
        for f in feats:
            containers[(svc, f)] = ("DEMO-%d" % next(next_key), f, "Feature", epic)

    stories = []
    for n in range(1, 241):
        service = rnd.choice(SERVICES)
        status = rnd.choices(PATH, WEIGHTS)[0]
        cat = CATEGORY[status]
        people = {"ba": rnd.choice(BY_ROLE["BA"]), "pm": BY_ROLE["PM"][0], "qa": rnd.choice(BY_ROLE["QA"]),
                  "dev": rnd.choice(BY_ROLE["Dev"])}
        started = PATH.index(status) >= PATH.index("In Development")
        assignee = people["dev"] if started else "Unassigned"
        created = today - dt.timedelta(days=rnd.randint(3, 150))
        resolved = (created + dt.timedelta(days=rnd.randint(4, 30))) if cat == "done" else None
        if resolved and resolved > today:
            resolved = today
        sprint_no = SPRINT_NOW - ((today - resolved).days + 7) // 14 if resolved else SPRINT_NOW
        in_sprint = PATH.index(status) >= PATH.index("Ready for Development")
        key = "DEMO-%d" % (1000 + n)
        issue = base(key, service, status, assignee, people["ba"], created, resolved, rnd.choice(["Story", "Story", "Story", "Task"]),
                     float(rnd.choice([1, 2, 3, 3, 5, 5, 8, 13])), sprint_no, in_sprint)
        issue["Description"] = "As a user I want to %s so that my work is faster. Acceptance: works on desktop and mobile." % issue["Summary"].lower()
        topic = next((t for t in TOPICS[service] if issue["Summary"].endswith(" " + t)), "")
        fkey, fname, _, _ = containers[(service, FEATURE_OF[(service, topic)])]
        issue["Parent"], issue["ParentKey"] = "%s %s" % (fkey, fname), fkey
        if started:
            for _ in range(rnd.randint(1, 6)):
                day = created + dt.timedelta(days=rnd.randint(0, max(1, ((resolved or today) - created).days)))
                if day <= today and day.weekday() < 5:
                    h = rnd.choice([0.5, 1, 1.5, 2, 3, 4, 6]); issue["HoursLogged"] += h
                    worklogs.append({"Key": key, "Service": service, "Author": assignee, "Date": day.isoformat(), "Hours": h})
        start = dt.datetime.combine(created, dt.time(9)) + dt.timedelta(minutes=rnd.randint(0, 480))
        end = dt.datetime.combine(resolved, dt.time(17)) if resolved else now - dt.timedelta(hours=rnd.randint(1, 72))
        log = _history(rnd, start, max(end, start + dt.timedelta(hours=2)), status, people, rnd.random() < 0.2,
                       desc_after_dev=rnd.random() < 0.25)
        add(issue, log, start, resolved)
        stories.append((issue, people, start, end))

    # Bugs: logged by the story's QA while testing it, or after it was done (leaked), and linked to the story.
    n = 0
    for story, people, s_start, s_end in stories:
        if PATH.index(story["Status"]) < PATH.index("In Testing") or rnd.random() > 0.45:
            continue
        for _ in range(rnd.choice([1, 1, 2, 3])):
            n += 1
            leaked = story["Status"] == "Done" and rnd.random() < 0.3
            created_dt = (dt.datetime.combine(dt.date.fromisoformat(story["DoneDate"]), dt.time(10)) + dt.timedelta(days=rnd.randint(1, 20))
                          if leaked else s_start + (s_end - s_start) * rnd.uniform(0.6, 0.95))
            if created_dt > now:
                created_dt = now - dt.timedelta(hours=6)
            status = rnd.choice(BUG_PATH)
            resolved = None
            if status == "Done":
                resolved = min(created_dt.date() + dt.timedelta(days=rnd.randint(1, 10)), today)
            key = "DEMO-%d" % (2000 + n)
            bug = base(key, story["Service"], status, people["dev"], "Neha Gupta" if leaked and rnd.random() < 0.3 else people["qa"],
                       created_dt.date(), resolved, "Bug", 0.0, SPRINT_NOW, False)
            bug["Summary"] = "Bug: " + story["Summary"].lower()
            bug["Links"] = [story["Key"]]
            story["Links"].append(key)
            bend = dt.datetime.combine(resolved, dt.time(17)) if resolved else now - dt.timedelta(hours=2)
            add(bug, _history(rnd, created_dt, max(bend, created_dt + dt.timedelta(hours=2)), status, people, rnd.random() < 0.15,
                              path=BUG_PATH), created_dt, resolved)

    # The Epic and Feature issues themselves.
    for (fkey, name, itype, parent_key), svc in ((v, k if isinstance(k, str) else k[0]) for k, v in containers.items()):
        kids = [st for st, *_ in stories if st["ParentKey"] == fkey] if itype == "Feature" else \
               [st for st, *_ in stories if st["Service"] == svc]
        status = "Done" if kids and all(k["Status"] == "Done" for k in kids) else ("In Development" if kids else "Ready for Refinement")
        created = today - dt.timedelta(days=160)
        resolved = max(dt.date.fromisoformat(k["Resolved"]) for k in kids) if status == "Done" else None
        people = {"ba": BY_ROLE["BA"][0], "pm": BY_ROLE["PM"][0], "qa": BY_ROLE["QA"][0], "dev": BY_ROLE["Dev"][0]}
        item = base(fkey, svc, status, "Unassigned", BY_ROLE["PM"][0], created, resolved, itype, 0.0, SPRINT_NOW, False)
        item["Summary"] = name
        if parent_key:
            item["Parent"], item["ParentKey"] = "%s %s" % (parent_key, svc), parent_key
        start = dt.datetime.combine(created, dt.time(9))
        end = dt.datetime.combine(resolved, dt.time(17)) if resolved else now - dt.timedelta(hours=3)
        add(item, _history(rnd, start, end, status, people, False), start, resolved)

    data = {
        "meta": {"refreshed_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 "jira_url": "https://example.atlassian.net", "jql": "(demo)", "service_source": "component",
                 "story_points_fields": [], "worklog_days": 90, "demo": True, "has_history": True,
                 "status_categories": CATEGORY, "service_fields": ["Service", "Team"], "demo_roles": DEMO_ROLES},
        "issues": issues, "worklogs": worklogs, "transitions": transitions, "status_time": status_time,
    }
    return board.apply(data, cfg or {})
