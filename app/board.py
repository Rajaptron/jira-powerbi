"""Board setup: which buckets exist, which Jira status goes in which bucket, and which statuses are hidden.

Everything bucket-related is (re)computed here from the raw statuses, so changing the setup in Settings
re-labels the existing data instantly, without another trip to Jira.
"""
import re

PRESETS = {
    "product-dev-qa": [
        {"name": "Product", "color": "#8b5cf6"},
        {"name": "Development", "color": "#3b82f6"},
        {"name": "QA", "color": "#f59e0b"},
        {"name": "Done", "color": "#22a06b", "done": True},
    ],
    "detailed": [
        {"name": "In Refinement", "color": "#a78bfa"},
        {"name": "To Do", "color": "#94a3b8"},
        {"name": "In Development", "color": "#3b82f6"},
        {"name": "In Testing", "color": "#f59e0b"},
        {"name": "Done", "color": "#22a06b", "done": True},
    ],
}
DEFAULT_PRESET = "product-dev-qa"
OTHER = "Other"
OTHER_COLOR = "#9ca3af"

# Keyword guesses used only for statuses the user hasn't placed yet. First match wins.
_GUESSES = {
    "product-dev-qa": [
        (r"\b(qa|uat)\b|test|verif|acceptance|staging", "QA"),
        (r"draft|\bpm\b|product|refin|groom|triage|discover|idea|backlog|analysis|needs? info|design", "Product"),
        (r"dev|progress|peer|code|review|build|implement|ready", "Development"),
        (r"block|on hold|waiting|impeded", "Development"),  # stuck work is usually mid-delivery
    ],
    "detailed": [
        (r"\b(qa|uat)\b|test|verif|acceptance|staging", "In Testing"),
        (r"refin|groom|discover|triage|draft|needs? info|analysis|backlog|idea", "In Refinement"),
    ],
}
_CATEGORY_FALLBACK = {
    "product-dev-qa": {"new": "Product", "indeterminate": "Development"},
    "detailed": {"new": "To Do", "indeterminate": "In Development"},
}
_DONE_NAMES = {"done", "closed", "resolved", "released", "complete", "completed"}


def buckets_of(cfg):
    buckets = cfg.get("buckets") or PRESETS[DEFAULT_PRESET]
    if not any(b.get("done") for b in buckets):  # there must always be somewhere for finished work
        buckets = list(buckets) + [{"name": "Done", "color": "#22a06b", "done": True}]
    return buckets


def _preset_like(buckets):
    names = [b["name"] for b in buckets]
    for key, preset in PRESETS.items():
        if names == [b["name"] for b in preset]:
            return key
    return None


def resolver(cfg, categories):
    """Return (bucket_of(status) -> name, category_of(status) -> 'new'|'indeterminate'|'done')."""
    buckets = buckets_of(cfg)
    names = {b["name"] for b in buckets}
    done_bucket = next(b["name"] for b in buckets if b.get("done"))
    explicit = dict(cfg.get("status_map") or {})          # older setting, still honoured
    explicit.update(cfg.get("status_buckets") or {})
    preset = _preset_like(buckets)

    def category_of(status):
        cat = categories.get(status)
        if not cat and (status or "").strip().lower() in _DONE_NAMES:
            cat = "done"
        return cat or ""

    def bucket_of(status):
        if explicit.get(status) in names:
            return explicit[status]
        cat = category_of(status)
        if cat == "done":
            return done_bucket
        if preset:
            lowered = (status or "").lower()
            for pattern, bucket in _GUESSES[preset]:
                if re.search(pattern, lowered):
                    return bucket
            return _CATEGORY_FALLBACK[preset].get(cat, OTHER)
        return OTHER  # custom buckets: unplaced statuses stand out until the user maps them
    return bucket_of, category_of


def apply(data, cfg):
    """Re-label every ticket, status change and time-in-status row with the configured buckets."""
    meta = data["meta"]
    categories = dict(meta.get("status_categories") or {})
    for i in data["issues"]:  # current statuses always carry their own category
        if i.get("StatusCategoryKey"):
            categories.setdefault(i["Status"], i["StatusCategoryKey"])
    bucket_of, category_of = resolver(cfg, categories)
    buckets = buckets_of(cfg)
    used = {bucket_of(i["Status"]) for i in data["issues"]}
    order = [b["name"] for b in buckets]
    colors = {b["name"]: b.get("color") or OTHER_COLOR for b in buckets}
    if OTHER in used and OTHER not in order:
        order.insert(len(order) - 1, OTHER)
        colors[OTHER] = OTHER_COLOR
    rank = {name: n for n, name in enumerate(order)}
    done_bucket = next(b["name"] for b in buckets if b.get("done"))
    hidden = set(cfg.get("hidden_statuses") or [])

    for i in data["issues"]:
        i["Bucket"] = bucket_of(i["Status"])
        i["Hidden"] = i["Status"] in hidden
    reopened = {}
    for t in data.get("transitions") or []:
        t["FromBucket"], t["ToBucket"] = bucket_of(t["From"]), bucket_of(t["To"])
        t["BackwardMove"] = rank.get(t["ToBucket"], 0) < rank.get(t["FromBucket"], 0)
        if t["BackwardMove"]:
            reopened[t["Key"]] = reopened.get(t["Key"], 0) + 1
    for s in data.get("status_time") or []:
        s["Bucket"] = bucket_of(s["Status"])
    for i in data["issues"]:
        i["TimesReopened"] = reopened.get(i["Key"], 0)

    data["buckets"] = order
    data["bucket_colors"] = colors
    data["done_bucket"] = done_bucket
    statuses = {i["Status"] for i in data["issues"]} | {s for t in data.get("transitions") or [] for s in (t["From"], t["To"])}
    flow = workflow_position(data, category_of)
    custom = {s: n for n, s in enumerate(cfg.get("status_order") or [])}
    data["statuses"] = sorted(
        ({"Status": s, "Category": category_of(s), "Bucket": bucket_of(s), "Hidden": s in hidden} for s in statuses),
        key=lambda r: (rank.get(r["Bucket"], 99), custom.get(r["Status"], 999), flow.get(r["Status"], 0.5), r["Status"]))
    return data


def workflow_position(data, category_of):
    """Where each status usually sits in a ticket's journey (0 = start, 1 = end), learnt from the history."""
    paths = {}
    for t in data.get("transitions") or []:
        paths.setdefault(t["Key"], [t["From"]]).append(t["To"])
    seen = {}
    for path in paths.values():
        for n, s in enumerate(path):
            seen.setdefault(s, []).append(n / max(len(path) - 1, 1))
    pos = {s: sum(v) / len(v) for s, v in seen.items()}
    base = {"new": 0.0, "indeterminate": 0.5, "done": 1.0}  # statuses never seen moving: use the category
    for i in data["issues"]:
        pos.setdefault(i["Status"], base.get(category_of(i["Status"]), 0.5))
    return pos
