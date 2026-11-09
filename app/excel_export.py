"""Writes the dataset as an Excel workbook of named tables — the file Power BI connects to."""
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

from .pipeline import people_table, visible


def _sheet(wb, title, rows, columns, table_name):
    ws = wb.create_sheet(title)
    ws.append(columns)
    flat = lambda v: ", ".join(map(str, v)) if isinstance(v, list) else (str(v) if isinstance(v, dict) else v)
    for r in rows:
        ws.append([flat(r.get(c)) for c in columns])
    if not rows:
        ws.append([None] * len(columns))  # Excel tables need at least one data row
    ref = "A1:%s%d" % (get_column_letter(len(columns)), ws.max_row)
    table = Table(displayName=table_name, ref=ref)
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(table)
    ws.freeze_panes = "A2"
    for idx, col in enumerate(columns, start=1):
        width = max([len(col)] + [len(str(r.get(col) or "")) for r in rows[:500]])
        ws.column_dimensions[get_column_letter(idx)].width = min(max(width + 2, 8), 60)
    return ws


def _service_summary(issues, done):
    by_service = {}
    for i in issues:
        s = by_service.setdefault(i["Service"], {"Service": i["Service"], "Tickets": 0, "StoryPoints": 0.0})
        s["Tickets"] += 1
        s["StoryPoints"] += i["StoryPoints"]
        s[i["Bucket"]] = s.get(i["Bucket"], 0) + 1
        s[i["Bucket"] + " SP"] = s.get(i["Bucket"] + " SP", 0) + i["StoryPoints"]
    rows = []
    for s in sorted(by_service.values(), key=lambda x: -x["Tickets"]):
        s["% Done"] = round(100.0 * s.get(done, 0) / s["Tickets"], 1) if s["Tickets"] else 0
        rows.append(s)
    return rows


def write_workbook(data, path):
    data = visible(data)
    buckets, done = data["buckets"], data.get("done_bucket", "Done")
    wb = Workbook()
    wb.remove(wb.active)
    issue_cols = ["Key", "Summary", "Project", "Service", "IssueType", "Status", "Bucket", "StatusCategory",
                  "Assignee", "Reporter", "Priority", "StoryPoints", "Sprint", "SprintState", "SprintEnd", "Parent",
                  "Labels", "Components", "Created", "Updated", "Resolved", "AgeDays", "HoursLogged",
                  "OriginalEstimateHours", "RemainingHours", "DoneDate", "ClosedBy", "DevStarted", "CycleDays",
                  "LeadDays", "TimesReopened", "Creator", "ParentKey", "Links", "URL"]

    summary_cols = ["Service", "Tickets", "StoryPoints"] + buckets + [b + " SP" for b in buckets] + ["% Done"]
    ws = _sheet(wb, "Service Summary", _service_summary(data["issues"], done), summary_cols, "ServiceSummary")
    ws.insert_rows(1, 2)
    meta = data["meta"]
    ws["A1"] = "Jira snapshot %s — %s%s" % (meta["refreshed_at"], meta["jira_url"],
                                            " (DEMO DATA)" if meta.get("demo") else "")
    ws["A1"].font = Font(bold=True, size=13)
    # Inserting rows shifts cells but not the table range; re-anchor it.
    ws.tables["ServiceSummary"].ref = "A3:%s%d" % (get_column_letter(len(summary_cols)), ws.max_row)
    ws.freeze_panes = "A4"

    issues_ws = _sheet(wb, "Issues", data["issues"], issue_cols, "Issues")
    for row in range(2, issues_ws.max_row + 1):  # make keys clickable
        cell = issues_ws.cell(row=row, column=1)
        url = issues_ws.cell(row=row, column=len(issue_cols)).value
        if url:
            cell.hyperlink = url
            cell.font = Font(color="0563C1", underline="single")

    _sheet(wb, "Worklogs", data["worklogs"], ["Key", "Service", "Author", "Date", "Hours"], "Worklogs")
    _sheet(wb, "People", people_table(data),
           ["Person", "ActiveTickets"] + ["Open in " + b for b in buckets if b != done] +
           ["OpenStoryPoints", "TicketsClosed", "StoryPointsDelivered", "AvgCycleDays", "TimesReopened",
            "HoursLogged", "Services"], "People")
    if data.get("transitions") is not None:
        _sheet(wb, "Status Changes", data["transitions"],
               ["Key", "Date", "From", "To", "FromBucket", "ToBucket", "BackwardMove", "MovedBy", "Assignee",
                "Project", "Service", "IssueType", "StoryPoints"], "StatusChanges")
        _sheet(wb, "Time In Status", data.get("status_time") or [],
               ["Key", "Status", "Bucket", "Assignee", "Entered", "Left", "Days"], "TimeInStatus")

    statuses = {}
    for i in data["issues"]:
        statuses.setdefault((i["Status"], i["Bucket"]), 0)
        statuses[(i["Status"], i["Bucket"])] += 1
    _sheet(wb, "Status Map", [{"Status": s, "Bucket": b, "Tickets": n} for (s, b), n in sorted(statuses.items())],
           ["Status", "Bucket", "Tickets"], "StatusMap")
    _sheet(wb, "Buckets", [{"Bucket": b, "SortOrder": n} for n, b in enumerate(buckets, 1)],
           ["Bucket", "SortOrder"], "Buckets")
    _sheet(wb, "Refresh Info", [{"Item": k, "Value": str(v)} for k, v in meta.items() if k != "status_categories"],
           ["Item", "Value"], "RefreshInfo")
    wb.save(path)
