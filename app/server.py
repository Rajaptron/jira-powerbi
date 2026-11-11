"""Local web app: setup screen, refresh trigger, JSON API for the dashboard, Excel download.

Run:  python -m app.server            (opens http://127.0.0.1:8765)
      python -m app.server --refresh  (headless refresh, e.g. from cron before a Power BI refresh)
"""
import argparse
import base64
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import board, demo, pipeline, prd
from .excel_export import write_workbook
from .jira_client import JiraClient, JiraError

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
OUTPUT = ROOT / "output"
CONFIG_PATH = ROOT / "config.json"
DATA_JSON = OUTPUT / "jira_data.json"
DATA_XLSX = OUTPUT / "jira_data.xlsx"
RTM_JSON = OUTPUT / "rtm.json"  # per-service requirements (from uploaded PRDs) and their ticket links

DEFAULT_CONFIG = {
    "jira_url": "", "email": "", "token": "",
    "projects": [], "jql": "", "updated_within_days": 180,
    "service_source": "component", "status_map": {},
    "include_worklogs": True, "worklog_days": 90, "include_history": True,
    "buckets": None, "status_buckets": {}, "hidden_statuses": [], "status_order": [], "planning": {},
    "roles": {}, "defect_types": ["Bug"], "stages": {},
}

state = {"running": False, "message": "", "error": ""}
lock = threading.Lock()


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    os.chmod(CONFIG_PATH, 0o600)  # holds the API token: owner-only


def save_data(data):
    OUTPUT.mkdir(exist_ok=True)
    DATA_JSON.write_text(json.dumps(data))
    write_workbook(data, DATA_XLSX)


def load_rtm():
    return json.loads(RTM_JSON.read_text()) if RTM_JSON.exists() else {}


def save_rtm(rtm):
    OUTPUT.mkdir(exist_ok=True)
    RTM_JSON.write_text(json.dumps(rtm, indent=1))


def run_refresh(use_demo=False, scope=None, full=False):
    def progress(msg):
        state["message"] = msg
        print("  " + msg, flush=True)

    try:
        if scope is not None:
            # Widget refresh: only tickets in that widget's scope that changed since the last full refresh.
            old = json.loads(DATA_JSON.read_text())
            if old["meta"].get("demo"):
                raise ValueError("This is demo data, so there's nothing live to refresh. Connect Jira in Settings first.")
            cfg = load_config()
            if old["meta"].get("config_fingerprint") != pipeline.fingerprint(cfg):
                raise ValueError("Settings changed since the last full reload (e.g. where services come from). "
                                 "Click Full reload first so every ticket uses the new setting.")
            cfg["jql"] = pipeline.scoped_jql(cfg, old, scope)
            data, changed = pipeline.merge(old, pipeline.fetch(cfg, progress))
            data = board.apply(data, cfg)
            save_data(data)
            state["changed"] = changed
            progress("Updated %d changed ticket%s from Jira." % (changed, "" if changed == 1 else "s"))
            state["error"] = ""
            return
        started = time.time()
        cfg = load_config()
        if not use_demo and not full and DATA_JSON.exists():
            # Quick refresh: only what changed since last time (falls back to a full pull when settings changed).
            quick = pipeline.quick_refresh(cfg, json.loads(DATA_JSON.read_text()), progress)
            if quick:
                data, changed, removed = quick
                progress("Writing Excel workbook for Power BI...")
                save_data(data)
                progress("Quick refresh done in %ds: %d changed ticket%s%s · %d tickets in total." % (
                    time.time() - started, changed, "" if changed == 1 else "s",
                    ", %d removed" % removed if removed else "", len(data["issues"])))
                state["error"] = ""
                return
        data = demo.generate(cfg=cfg) if use_demo else pipeline.fetch(cfg, progress)
        progress("Writing Excel workbook for Power BI...")
        save_data(data)
        progress("Full refresh done in %ds: %d tickets." % (time.time() - started, len(data["issues"])))
        state["error"] = ""
    except (JiraError, ValueError) as e:
        state["error"] = str(e)
    except Exception as e:  # surface anything unexpected in the UI instead of a silent failure
        traceback.print_exc()
        state["error"] = "Unexpected error: %s" % e
    finally:
        state["running"] = False


def start_refresh(use_demo=False, scope=None, full=False):
    with lock:
        if state["running"]:
            return False
        state.update(running=True, message="Starting...", error="", changed=None)
    threading.Thread(target=run_refresh, args=(use_demo, scope, full), daemon=True).start()
    return True


def store_prd(service, parsed, source):
    """Saves a service's PRD requirements; links and rejections survive for requirement ids that still exist."""
    rtm, service = load_rtm(), service or "(All)"
    old = rtm.get(service) or {}
    ids = {r["id"] for r in parsed["requirements"]}
    rtm[service] = dict(source, uploaded_at=time.strftime("%Y-%m-%d %H:%M"),
                        features=parsed["features"], requirements=parsed["requirements"],
                        links={k: v for k, v in (old.get("links") or {}).items() if k in ids},
                        rejected={k: v for k, v in (old.get("rejected") or {}).items() if k in ids})
    save_rtm(rtm)
    return rtm[service]


def _jira_login():
    cfg = load_config()
    if not cfg.get("jira_url") or not cfg.get("token"):
        raise ValueError("Enter the Jira URL and API token first, then click Save & test connection.")
    return cfg["jira_url"], cfg.get("email", ""), cfg["token"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/config":
            cfg = load_config()
            cfg["has_token"] = bool(cfg.pop("token"))
            return self._send(200, cfg)
        if path == "/api/board-presets":
            return self._send(200, {"presets": board.PRESETS, "default": board.DEFAULT_PRESET})
        if path == "/api/rtm":
            return self._send(200, load_rtm())
        if path == "/api/status":
            return self._send(200, state)
        if path == "/api/data":
            if not DATA_JSON.exists():
                return self._send(404, {"error": "No data yet"})
            return self._send(200, DATA_JSON.read_bytes())
        if path == "/download/jira_data.xlsx" and DATA_XLSX.exists():
            self.send_response(200)
            data = DATA_XLSX.read_bytes()
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="jira_data.xlsx"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        return self._send(404, {"error": "Not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            body = self._body()
        except ValueError:
            return self._send(400, {"error": "Invalid JSON"})
        if path == "/api/config":
            cfg = load_config()
            for k in DEFAULT_CONFIG:
                if k in body and not (k == "token" and not body[k]):  # blank token = keep existing
                    cfg[k] = body[k]
            save_config(cfg)
            rederived = False
            if DATA_JSON.exists():
                data = json.loads(DATA_JSON.read_text())
                changed = False
                src = cfg.get("service_source") or "component"
                if "service_source" in body and not data["meta"].get("demo") and data["meta"].get("service_source") != src \
                        and pipeline.can_rederive(data, src):
                    # Every ticket already carries this field: switch Services over without asking Jira again.
                    data, changed, rederived = pipeline.rederive_services(data, src, cfg), True, True
                if {"buckets", "status_buckets", "hidden_statuses", "status_order", "status_map"} & set(body):
                    data, changed = board.apply(data, cfg), True
                if changed:
                    save_data(data)
            return self._send(200, {"ok": True, "rederived": rederived})
        if path == "/api/test":
            cfg = load_config()
            try:
                client = JiraClient(cfg["jira_url"], cfg.get("email", ""), cfg["token"])
                me = client.myself()
                note = ""
                if client.base_url != cfg["jira_url"].strip().rstrip("/"):
                    # http:// → https://, or a renamed site: save the real address so searches aren't redirected.
                    cfg["jira_url"] = client.base_url
                    save_config(cfg)
                    note = " Jira URL updated to %s." % client.base_url
                projects = sorted(({"key": p["key"], "name": p["name"]} for p in client.projects()),
                                  key=lambda p: p["key"])
                fields = sorted(f["name"] for f in client.fields() if f.get("custom"))
                return self._send(200, {"ok": True, "user": (me.get("displayName") or "") + note,
                                        "projects": projects, "custom_fields": fields})
            except (JiraError, KeyError) as e:
                return self._send(200, {"ok": False, "error": str(e) or "Jira URL and token are required."})
        if path == "/api/rtm/upload":
            # A PRD for one service: pull out its requirements; keep links for requirement ids that survive.
            try:
                data = base64.b64decode(body.get("data_b64") or "")
                parsed = prd.parse(body.get("filename", ""), data)
            except (ValueError, KeyError, zipfile.BadZipFile) as e:
                return self._send(200, {"ok": False, "error": str(e) or "That file couldn't be read."})
            return self._send(200, {"ok": True, "rtm": store_prd(body.get("service"), parsed, {"file": body.get("filename", "")})})
        if path == "/api/rtm/confluence":
            # Same as an upload, but the PRD is read live from a Confluence page with the Jira login.
            try:
                page = JiraClient(*_jira_login()).confluence_page(body.get("url") or "")
                parsed = prd.parse_confluence(page["storage"])
            except (ValueError, JiraError) as e:
                return self._send(200, {"ok": False, "error": str(e)})
            source = {"file": "Confluence: %s" % (page["title"] or "page"), "source_url": page["url"],
                      "page_version": page.get("version")}
            return self._send(200, {"ok": True, "rtm": store_prd(body.get("service"), parsed, source)})
        if path == "/api/rtm/import":
            # A hand-made traceability matrix: its story links become confirmed links, its verdicts and notes the
            # assessment of each requirement.
            rtm, service = load_rtm(), body.get("service") or ""
            doc = rtm.get(service)
            if not doc:
                return self._send(200, {"ok": False, "error": "Add this service's PRD first, then import its traceability matrix."})
            try:
                found = prd.read_rtm(body.get("filename", ""), base64.b64decode(body.get("data_b64") or ""),
                                     [r["id"] for r in doc["requirements"]])
            except (ValueError, KeyError, zipfile.BadZipFile) as e:
                return self._send(200, {"ok": False, "error": str(e) or "That file couldn't be read."})
            links, rejected, assess = doc.setdefault("links", {}), doc.setdefault("rejected", {}), doc.setdefault("assess", {})
            for rid, row in found["rows"].items():
                if row["keys"]:
                    links[rid] = row["keys"]
                    rejected[rid] = [k for k in rejected.get(rid, []) if k not in row["keys"]]
                if row["assessment"] or row["note"]:
                    assess[rid] = {"status": row["assessment"], "note": row["note"]}
            doc["rtm_file"] = "%s (sheet %s) · imported %s" % (body.get("filename", ""), found["sheet"], time.strftime("%Y-%m-%d %H:%M"))
            save_rtm(rtm)
            return self._send(200, {"ok": True, "rtm": doc, "sheet": found["sheet"], "matched": len(found["rows"]),
                                    "unknown": found["unknown"], "not_in_file": found["not_in_file"]})
        if path == "/api/rtm/save":
            rtm = load_rtm()
            if body.get("delete"):
                rtm.pop(body.get("service"), None)
            else:
                rtm[body["service"]] = body["doc"]
            save_rtm(rtm)
            return self._send(200, {"ok": True})
        if path == "/api/fields":
            try:
                projects = [p for p in body.get("projects") or [] if p]
                if projects:  # only fields actually used in these projects, most used first
                    return self._send(200, dict(ok=True, **pipeline.project_fields(load_config(), projects)))
                client = JiraClient(*_jira_login())
                return self._send(200, {"ok": True, "custom_fields": sorted({f["name"] for f in client.fields() if f.get("custom")})})
            except (JiraError, ValueError) as e:
                return self._send(200, {"ok": False, "error": str(e)})
        if path == "/api/estimate":
            try:
                projects = [p for p in body.get("projects") or [] if p]
                cfg = dict(load_config(), updated_within_days=body.get("updated_within_days", load_config().get("updated_within_days")))
                return self._send(200, dict(ok=True, **pipeline.estimate(cfg, projects)))
            except (JiraError, ValueError) as e:
                return self._send(200, {"ok": False, "error": str(e)})
        if path == "/api/service-preview":
            try:
                return self._send(200, dict(ok=True, **pipeline.preview_services(
                    load_config(), body.get("projects") or [], body.get("service_source") or "component")))
            except (JiraError, ValueError) as e:
                return self._send(200, {"ok": False, "error": str(e)})
        if path == "/api/refresh":
            if body.get("scope") is not None and not DATA_JSON.exists():
                return self._send(400, {"started": False, "error": "Do a full refresh first."})
            started = start_refresh(use_demo=bool(body.get("demo")), scope=body.get("scope"), full=bool(body.get("full")))
            return self._send(200 if started else 409, {"started": started})
        return self._send(404, {"error": "Not found"})


def main():
    parser = argparse.ArgumentParser(description="Jira Insights for Power BI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--refresh", action="store_true", help="refresh data and exit (no UI); quick when possible")
    parser.add_argument("--full", action="store_true", help="with --refresh: reload everything from Jira")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.refresh:
        state["running"] = True
        run_refresh(full=args.full)
        if state["error"]:
            print("ERROR: " + state["error"], file=sys.stderr)
            sys.exit(1)
        print("Excel for Power BI: %s" % DATA_XLSX)
        return

    url = "http://127.0.0.1:%d" % args.port
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)  # local machine only
    except OSError:
        # Already running (e.g. launched twice): just open the existing dashboard.
        print("Jira Insights is already running at %s — opening it." % url)
        if not args.no_browser:
            webbrowser.open(url)
        return
    print("Jira Insights running at %s  (Ctrl+C to stop)" % url)
    print("Power BI data file: %s" % DATA_XLSX)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
