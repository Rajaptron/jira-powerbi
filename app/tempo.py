"""Worklogs from Tempo Timesheets, for teams that log time in Tempo instead of Jira's own worklog.

Jira Cloud: Tempo's REST API (api.tempo.io) with a Tempo API token. It returns only issue ids and
Atlassian account ids, so those are matched to the pulled tickets and looked up in Jira for names.
Jira Server/Data Center: the Tempo plugin inside Jira, with the normal Jira login.
"""
import datetime as dt

from .jira_client import JiraClient, JiraError

TEMPO_API = "https://api.tempo.io"  # the Tempo token is only ever sent here


def _client(cfg):
    token = (cfg.get("tempo_token") or "").strip()
    if not token:
        raise ValueError("No Tempo API token is saved yet. Create one in Tempo → Settings → API integration → "
                         "New token, and paste it in Settings → What to pull.")
    return JiraClient(TEMPO_API, "", token)


def _tempo_get(client, path, params):
    try:
        return client.get(path, params)
    except JiraError as e:
        if e.status in (401, 403):
            raise JiraError("Tempo refused the API token (%s). Create a new one in Tempo → Settings → API integration "
                            "and paste it in Settings → What to pull." % e.status, e.status)
        raise JiraError(str(e).replace("Jira returned", "Tempo returned"), e.status)


def test(cfg):
    """Checks the Tempo token with the smallest possible call."""
    _tempo_get(_client(cfg), "/4/worklogs", {"limit": 1})
    return True


def _names_cloud(jira, account_ids):
    names = {}
    ids = sorted(account_ids)
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        try:
            page = jira.get(jira.api + "/user/bulk", {"accountId": chunk, "maxResults": 200})
            names.update({u["accountId"]: u["displayName"] for u in page.get("values", []) if u.get("displayName")})
        except JiraError:
            pass  # no "browse users" permission for the bulk call: try one by one below
        for a in chunk:
            if a not in names:
                try:
                    names[a] = jira.get(jira.api + "/user", {"accountId": a}).get("displayName") or "(Unknown user)"
                except JiraError:
                    names[a] = "(Unknown user)"
    return names


def _names_server(jira, keys):
    names = {}
    for k in keys:
        try:
            u = jira.get(jira.api + "/user", {"key": k})
        except JiraError:
            try:
                u = jira.get(jira.api + "/user", {"username": k})
            except JiraError:
                u = {}
        names[k] = u.get("displayName") or k
    return names


def _cloud_logs(cfg, jira, issues, cutoff, today, progress):
    tempo = _client(cfg)
    by_id = {i["Id"]: i for i in issues if i.get("Id")}
    project_ids = sorted({i.get("ProjectId") for i in issues if i.get("ProjectId")})
    raw, offset = [], 0
    while True:
        params = {"from": cutoff, "to": today, "offset": offset, "limit": 5000}
        if project_ids:
            params["projectId"] = project_ids
        page = _tempo_get(tempo, "/4/worklogs", params)
        results = page.get("results", [])
        raw.extend(r for r in results if str((r.get("issue") or {}).get("id", "")) in by_id)
        progress("Reading Tempo worklogs... %d so far" % len(raw))
        if not results or not (page.get("metadata") or {}).get("next"):
            break
        offset += len(results)
    names = _names_cloud(jira, {(r.get("author") or {}).get("accountId") for r in raw} - {None})
    return [(by_id[str(r["issue"]["id"])], names.get((r.get("author") or {}).get("accountId"), "(Unknown)"),
             r.get("startDate", ""), r.get("timeSpentSeconds") or 0) for r in raw]


def _server_logs(cfg, jira, issues, cutoff, today, progress):
    by_key = {i["Key"]: i for i in issues}
    projects = sorted({i["Key"].rsplit("-", 1)[0] for i in issues})
    progress("Reading Tempo worklogs from Jira...")
    try:
        raw = jira.post("/rest/tempo-timesheets/4/worklogs/search", {"from": cutoff, "to": today, "projectKey": projects})
    except JiraError as e:
        if e.status == 404:
            raise JiraError("Tempo Timesheets wasn't found on this Jira server, so time can't come from Tempo. "
                            "Switch \"Time logged in\" back to Jira worklogs in Settings.", 404)
        raise
    raw = [r for r in raw or [] if (r.get("issue") or {}).get("key") in by_key]
    who = lambda r: r.get("worker") or (r.get("author") or {}).get("key") or ""
    names = _names_server(jira, {who(r) for r in raw} - {""})
    return [(by_key[r["issue"]["key"]], names.get(who(r), who(r) or "(Unknown)"),
             (r.get("started") or r.get("dateStarted") or "")[:10], r.get("timeSpentSeconds") or 0) for r in raw]


def apply(data, cfg, progress=lambda msg: None):
    """Replaces the worklogs table with Tempo's, for every pulled ticket in the timesheet window."""
    if cfg.get("worklog_source") != "tempo" or not cfg.get("include_worklogs", True) or data["meta"].get("demo"):
        return data
    today = dt.date.today()
    cutoff = (today - dt.timedelta(days=int(cfg.get("worklog_days") or 90))).isoformat()
    jira = JiraClient(cfg["jira_url"], cfg.get("email", ""), cfg["token"])
    fetch = _cloud_logs if jira.is_cloud else _server_logs
    logs = fetch(cfg, jira, data["issues"], cutoff, today.isoformat(), progress)
    data["worklogs"] = [{"Key": i["Key"], "Service": i["Service"], "Author": name, "Date": day,
                         "Hours": round(secs / 3600.0, 2)} for i, name, day, secs in logs if day >= cutoff]
    # Tickets whose Jira time is empty show the Tempo hours (within the window) instead.
    hours = {}
    for w in data["worklogs"]:
        hours[w["Key"]] = hours.get(w["Key"], 0) + w["Hours"]
    for i in data["issues"]:
        if i.get("HoursFromTempo") or not i.get("HoursLogged"):
            i["HoursLogged"] = round(hours.get(i["Key"], 0), 2)
            i["HoursFromTempo"] = True
    data["meta"] = dict(data["meta"], worklog_source="tempo")
    progress("Tempo: %d worklog entries, %.0f hours." % (len(data["worklogs"]), sum(hours.values())))
    return data
