"""Thin Jira REST client (Cloud + Server/Data Center) using only the standard library."""
import base64
import gzip
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class JiraError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class JiraClient:
    def __init__(self, base_url, email, token, timeout=60):
        self.base_url = base_url.strip().rstrip("/")
        if not self.base_url.startswith("http"):
            self.base_url = "https://" + self.base_url
        # Strip anything after the site root (people often paste a board/issue URL).
        parsed = urllib.parse.urlparse(self.base_url)
        self.base_url = "%s://%s" % (parsed.scheme, parsed.netloc)
        self.is_cloud = parsed.netloc.endswith(".atlassian.net")
        self.api = "/rest/api/3" if self.is_cloud else "/rest/api/2"
        self.timeout = timeout
        if email:
            raw = ("%s:%s" % (email.strip(), token.strip())).encode()
            self.auth_header = "Basic " + base64.b64encode(raw).decode()
        else:
            # Server/Data Center personal access token.
            self.auth_header = "Bearer " + token.strip()
        self._ctx = ssl.create_default_context()

    # ---------- low level ----------
    def _request(self, method, path, params=None, body=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(5):
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Authorization", self.auth_header)
            req.add_header("Accept", "application/json")
            req.add_header("Accept-Encoding", "gzip")  # JSON compresses ~10x: much faster on big pulls
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                    payload = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        payload = gzip.decompress(payload)
                    return json.loads(payload) if payload else {}
            except urllib.error.HTTPError as e:
                if e.code in (429, 502, 503, 504) and attempt < 4:
                    # Rate limited / busy: wait as Jira asks (or back off) and try again.
                    wait = e.headers.get("Retry-After")
                    time.sleep(min(float(wait) if wait and wait.replace(".", "", 1).isdigit() else 2 ** attempt, 30))
                    continue
                detail = e.read().decode(errors="replace")[:500]
                if e.code == 401:
                    raise JiraError("Authentication failed (401). Check the email and API token.", 401)
                if e.code == 403:
                    raise JiraError("Access denied (403). Your account can't read this data.", 403)
                raise JiraError("Jira returned HTTP %s for %s: %s" % (e.code, path, detail), e.code)
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise JiraError("Could not reach %s (%s). Check the Jira URL." % (self.base_url, e.reason))

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def post(self, path, body):
        return self._request("POST", path, body=body)

    # ---------- API helpers ----------
    def myself(self):
        return self.get(self.api + "/myself")

    def fields(self):
        return self.get(self.api + "/field")

    def projects(self):
        if self.is_cloud:
            out, start = [], 0
            while True:
                page = self.get(self.api + "/project/search", {"startAt": start, "maxResults": 100})
                out.extend(page.get("values", []))
                if page.get("isLast", True) or not page.get("values"):
                    return out
                start += len(page["values"])
        return self.get(self.api + "/project")

    def statuses(self):
        return self.get(self.api + "/status")

    def changelog(self, issue_key):
        """Full status/assignee history for one issue (Cloud pages it; Server returns it via expand)."""
        out, start = [], 0
        while True:
            page = self.get("%s/issue/%s/changelog" % (self.api, issue_key), {"startAt": start, "maxResults": 100})
            values = page.get("values", [])
            out.extend(values)
            start += len(values)
            if page.get("isLast", True) or not values:
                return out

    def count(self, jql):
        """How many tickets match (Cloud: approximate count, which is instant)."""
        if self.is_cloud:
            return int(self.post(self.api + "/search/approximate-count", {"jql": jql}).get("count", 0))
        return int(self.post(self.api + "/search", {"jql": jql, "maxResults": 0}).get("total", 0))

    def issue_keys(self, jql):
        """
        Every ticket matching the JQL, as (keys, ids) — cheap, because no other fields come back.
        Jira Cloud's search may return only the numeric id (no key) when so few fields are asked for,
        so callers should match on whichever set is filled.
        """
        keys, ids = set(), set()

        def take(issues):
            for i in issues:
                if i.get("key"):
                    keys.add(i["key"])
                if i.get("id"):
                    ids.add(str(i["id"]))

        if self.is_cloud:
            token = None
            while True:
                body = {"jql": jql, "fields": ["key"], "maxResults": 5000}
                if token:
                    body["nextPageToken"] = token
                page = self.post(self.api + "/search/jql", body)
                take(page.get("issues", []))
                token = page.get("nextPageToken")
                if not token or not page.get("issues"):
                    return keys, ids
        start = 0
        while True:
            page = self.post(self.api + "/search", {"jql": jql, "fields": ["key"], "startAt": start, "maxResults": 1000})
            issues = page.get("issues", [])
            take(issues)
            start += len(issues)
            if not issues or start >= page.get("total", 0):
                return keys, ids

    def bulk_changelogs(self, issue_ids):
        """Cloud only: status + assignee history for up to 1,000 tickets per call. Returns {issue id: histories}."""
        out, token = {}, None
        while True:
            body = {"issueIdsOrKeys": list(issue_ids), "fieldIds": ["status", "assignee", "description"], "maxResults": 1000}
            if token:
                body["nextPageToken"] = token
            page = self.post(self.api + "/changelog/bulkfetch", body)
            for log in page.get("issueChangeLogs", []):
                out.setdefault(str(log.get("issueId")), []).extend(log.get("changeHistories", []))
            token = page.get("nextPageToken")
            if not token:
                return out

    def search(self, jql, fields, progress=None, expand=None):
        """Yield every issue matching the JQL, handling both pagination styles."""
        fetched = 0
        if self.is_cloud:
            token = None
            while True:
                body = {"jql": jql, "fields": fields, "maxResults": 100}
                if expand:
                    body["expand"] = expand
                if token:
                    body["nextPageToken"] = token
                page = self.post(self.api + "/search/jql", body)
                issues = page.get("issues", [])
                for issue in issues:
                    yield issue
                fetched += len(issues)
                if progress:
                    progress("Fetched %d issues..." % fetched)
                token = page.get("nextPageToken")
                if not token or not issues:
                    return
        else:
            start = 0
            while True:
                body = {"jql": jql, "fields": fields, "startAt": start, "maxResults": 100}
                if expand:
                    body["expand"] = [expand]
                page = self.post(self.api + "/search", body)
                issues = page.get("issues", [])
                for issue in issues:
                    yield issue
                fetched += len(issues)
                if progress:
                    progress("Fetched %d of %s issues..." % (fetched, page.get("total", "?")))
                start += len(issues)
                if not issues or start >= page.get("total", 0):
                    return

    def all_worklogs(self, issue_key):
        out, start = [], 0
        while True:
            page = self.get("%s/issue/%s/worklog" % (self.api, issue_key),
                            {"startAt": start, "maxResults": 1000})
            logs = page.get("worklogs", [])
            out.extend(logs)
            start += len(logs)
            if not logs or start >= page.get("total", 0):
                return out
