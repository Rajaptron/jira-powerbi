"""The traceability (RTM) report as a formatted Excel workbook: a summary per service and PRD feature, the full
requirement → user story matrix, and the requirements no user story covers.

The dashboard works out the coverage and sends it here as `report`:
{title, generated, services: [{service, prd, source_url, rtm_file, totals, features: [...], rows: [...]}]}
"""
import math
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

NAVY = "1F3A8A"
FILL = {"covered": "D1FADF", "partial": "FEF0C7", "not": "FEE4E2", "excluded": "EAECF0"}
INK = {"covered": "05603A", "partial": "93370D", "not": "B42318", "excluded": "475467"}
HEAD_FILL, HEAD_FONT = PatternFill("solid", fgColor=NAVY), Font(bold=True, color="FFFFFF")
_side = Side(style="thin", color="D0D5DD")
BORDER = Border(left=_side, right=_side, top=_side, bottom=_side)
WRAP = Alignment(wrap_text=True, vertical="top")
MUTED = Font(color="667085")


def _fill(key):
    return PatternFill("solid", fgColor=FILL[key])


def _header(ws, row, cols):
    for c, name in enumerate(cols, 1):
        cell = ws.cell(row=row, column=c, value=name)
        cell.fill, cell.font, cell.border = HEAD_FILL, HEAD_FONT, BORDER
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[row].height = 30


def _widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _fit_height(ws, row, widths):
    # openpyxl can't auto-fit, so size the row to its longest wrapped cell.
    lines = 1
    for i, w in enumerate(widths, 1):
        text = str(ws.cell(row=row, column=i).value or "")
        lines = max(lines, sum(max(1, math.ceil(len(part) / max(w - 1, 1))) for part in text.split("\n")))
    ws.row_dimensions[row].height = min(15 * lines + 3, 400)


def _counts(t):
    in_scope = t["reqs"] - t["excluded"]
    return [t["covered"] - t["partial"], t["partial"], t["not"], t["excluded"], (t["covered"] / in_scope) if in_scope else 0]


def _summary(ws, rep):
    ws.title = "Summary"
    ws.sheet_view.showGridLines = False
    ws["A1"] = rep["title"]
    ws["A1"].font = Font(bold=True, size=16, color=NAVY)
    ws["A2"] = "Generated %s" % rep["generated"]
    ws["A2"].font = Font(italic=True, color="667085")
    cols = ["PRD feature", "Jira feature", "User stories", "Requirements", "Covered", "Partially covered",
            "Not covered", "Not counted", "% covered"]
    widths = [38, 44, 12, 14, 11, 11, 12, 12, 11]
    _widths(ws, widths)
    r = 4
    for s in rep["services"]:
        t = s["totals"]
        ws.cell(row=r, column=1, value=s["service"]).font = Font(bold=True, size=14, color=NAVY)
        r += 1
        prd = ws.cell(row=r, column=1, value="PRD: %s" % s["prd"])
        prd.font = MUTED
        if s.get("source_url"):
            prd.hyperlink, prd.font = s["source_url"], Font(color="1D4ED8", underline="single")
        if s.get("rtm_file"):
            r += 1
            ws.cell(row=r, column=1, value="Story links imported from %s" % s["rtm_file"]).font = MUTED
        # Headline numbers.
        r += 2
        full, part, gap, excl, pct = _counts(t)
        for c, (label, value, key) in enumerate([("PRD requirements", t["reqs"], None), ("Covered", t["covered"], "covered"),
                                                 ("Not covered", t["not"], "not"), ("% covered", pct, None)]):
            col = [1, 2, 4, 5][c]
            lab = ws.cell(row=r, column=col, value=label)
            lab.font = Font(bold=True, color="475467")
            val = ws.cell(row=r + 1, column=col, value=value)
            val.font = Font(bold=True, size=18, color=INK[key] if key else NAVY)
            if label == "% covered":
                val.number_format = "0%"
            val.alignment = Alignment(horizontal="left")
        ws.row_dimensions[r + 1].height = 26
        if part or excl:
            notes = (["%d of the covered requirements %s only partially covered." % (part, "is" if part == 1 else "are")] if part else []) + \
                    (["%d %s not counted (external dependency or out of scope)." % (excl, "requirement is" if excl == 1 else "requirements are")] if excl else [])
            ws.cell(row=r + 2, column=1, value=" ".join(notes)).font = MUTED
        r += 4
        # Per PRD feature.
        _header(ws, r, cols)
        for f in s["features"] + [dict(s["totals"], name="Total", jira=[], stories=sum(x["stories"] for x in s["features"]))]:
            r += 1
            vals = [f["name"], "\n".join(f["jira"]) or ("" if f["name"] == "Total" else "Not mapped"), f["stories"], f["reqs"]] + _counts(f)
            for c, v in enumerate(vals, 1):
                cell = ws.cell(row=r, column=c, value=v)
                cell.border, cell.alignment = BORDER, WRAP
                if f["name"] == "Total":
                    cell.font, cell.fill = Font(bold=True), PatternFill("solid", fgColor="F2F4F7")
            ws.cell(row=r, column=9).number_format = "0%"
            if f["not"]:
                ws.cell(row=r, column=7).fill, ws.cell(row=r, column=7).font = _fill("not"), Font(bold=True, color=INK["not"])
            if not f["jira"] and f["name"] != "Total":
                ws.cell(row=r, column=2).font = Font(italic=True, color=INK["not"])
            _fit_height(ws, r, widths)
        r += 3


def _matrix(ws, rep, only=None):
    multi = len(rep["services"]) > 1
    cols = (["Service"] if multi else []) + ["PRD feature", "Jira feature", "Req ID", "Title", "Requirement", "Priority",
                                            "Status", "User stories", "Story status", "Comment"]
    widths = ([16] if multi else []) + [24, 26, 14, 30, 60, 10, 18, 44, 22, 44]
    _widths(ws, widths)
    _header(ws, 1, cols)
    ws.freeze_panes = ws.cell(row=2, column=(5 if multi else 4))
    r = 1
    for s in rep["services"]:
        for x in s["rows"]:
            if only and x["group"] not in only:
                continue
            r += 1
            vals = ([s["service"]] if multi else []) + [
                x["feature"], x["jira"] or "Not mapped", x["id"], x["title"], x["text"], x["priority"], x["status"],
                "\n".join("%s – %s" % (st["key"], st["summary"]) if st["summary"] else st["key"] for st in x["stories"]),
                "\n".join("%s: %s" % (st["key"], st["status"] or "?") for st in x["stories"]), x["comment"]]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(row=r, column=c, value=v)
                cell.border, cell.alignment = BORDER, WRAP
            status = ws.cell(row=r, column=cols.index("Status") + 1)
            status.fill, status.font = _fill(x["group"]), Font(bold=True, color=INK[x["group"]])
            ws.cell(row=r, column=cols.index("Req ID") + 1).font = Font(bold=True)
            if x["stories"] and len(x["stories"]) == 1 and x["stories"][0].get("url"):
                ws.cell(row=r, column=cols.index("User stories") + 1).hyperlink = x["stories"][0]["url"]
            _fit_height(ws, r, widths)
    if r == 1:
        ws.cell(row=2, column=1, value="Every requirement is covered by a user story." if only else "No requirements.").font = MUTED
    ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(len(cols)), max(r, 2))


def _page_setup(ws):
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    if ws.title != "Summary":
        ws.print_title_rows = "1:1"


def rtm_workbook(report):
    """Returns the workbook as bytes."""
    wb = Workbook()
    _summary(wb.active, report)
    _matrix(wb.create_sheet("Traceability"), report)
    _matrix(wb.create_sheet("Not covered"), report, only={"not"})
    for ws in wb.worksheets:
        _page_setup(ws)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()
