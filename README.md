# Jira Insights — Jira → Dashboard + Power BI

A small app that runs on your own computer. You give it your **Jira URL + API token**, it pulls your tickets
and worklogs, and gives you:

1. **A live dashboard in your browser** (works on Mac and Windows, no Power BI needed)
2. **`output/jira_data.xlsx`** — a clean, Power BI–ready workbook that's rewritten on every refresh

## What you can see

| Question | Where |
|---|---|
| How many tickets per service, and how many in each status | Overview → *Tickets by service and status*, *Service × status matrix* |
| Story points per service (select one or several services) | Services filter + *Measure: Story points* |
| Done / In Development / In Testing / In Refinement split | Overview (every chart and matrix) |
| Who is working on which ticket | *Who's working on what* |
| How many people are working on the project | Overview KPI + *Team & resources* |
| Per-person productivity: tickets closed, story points delivered, velocity per sprint/week, cycle & lead time, time spent in each status, status-to-status moves, tickets sent back from testing, plus breakdowns by service and by bucket | *Team & resources → Productivity report* (pick a person and a period); open work per person × bucket and person × service on *Team overview* |
| Can we finish this by date X? | *Planning* |
| Timesheets (hours per person per day/week, per ticket) | *Timesheet* (export to CSV) |
| Monthly progress report for stakeholders: summary, burndown, created-vs-completed trend, points delivered, progress per service, biggest deliveries, items needing attention | *Stakeholder report* (pick a period; print / save as PDF or export images) |
| Any ticket list, searchable, exportable | *All tickets* |

Filters (projects, services, ticket types, labels, assignees, sprint) apply to every tab. They're searchable
multi-selects; hover an option and click **only** to pick just that one. Picking projects narrows every other list
to what exists in those projects. **Show by** switches the overview charts between buckets and raw Jira statuses.

**Click any number** — a KPI tile, a bar segment, a matrix cell, a person's count, a timesheet total — and the
matching tickets open in a panel at the bottom of the screen (searchable, sortable, exportable to CSV). Press Esc
or ✕ to close it.

**Images for PowerPoint:** every chart/table has **📋 Copy image** (then paste into the slide with Cmd+V / Ctrl+V)
and **⬇ PNG**. **📷 Capture view** in the header saves the whole current tab. Images are always light-themed
and include the active filters and the data date. (The image feature loads a small library from the internet
the first time.)

## Start it

**Mac:** double-click `start.command` (first run sets itself up, ~30 s). Or in Terminal:

```bash
./start.command
```

**Windows:** double-click `start.bat`.

Needs Python 3.9+ (Mac has it built-in; on Windows install from python.org). The dashboard opens at
http://127.0.0.1:8765 — it only listens on your own machine.

## Moving it to another laptop

Copy the `jira-insights` folder without `.venv`, `output` and `config.json` (the zip built for sharing already
leaves them out). On the new laptop, unzip it and double-click `start.command` (Mac) or `start.bat` (Windows), then
enter the Jira URL and token in Settings. Everyone uses their own API token, so never share `config.json`.

- **Mac, first open:** if macOS says *"start.command" Not Opened* (files copied from another computer get this),
  click **Done**, then open Terminal, type `bash ` (with a space), drag `start.command` into the window and press
  Return. Or: System Settings → Privacy & Security → scroll down → **Open Anyway** next to "start.command".
- **Windows:** install Python 3 from python.org first and tick **"Add python.exe to PATH"**.

## First-time setup (in the app → Settings)

1. **Jira URL** — e.g. `https://yourcompany.atlassian.net` (pasting a board/issue link is fine too).
2. **Email + API token** — create a token at https://id.atlassian.com/manage-profile/security/api-tokens.
   Jira Server/Data Center: leave email empty and paste a Personal Access Token.
3. **Save & test connection** → tick the projects you want.
4. **A "service" is…** — choose how your org splits work: Component, Project, Label, Epic, or any custom field
   (e.g. a "Team" or "Service" field).
5. **Save & refresh data.** After the first refresh, check *Status mapping* — the app auto-maps statuses
   (e.g. *Verify*, *QA* → In Testing; *Ready for Refinement* → In Refinement) and you can override any of them.

Want to look around first? Click **Load demo data**.

The token is saved only in `config.json` on this computer (owner-read-only) and is sent only to your Jira URL.

## Power BI

Power BI Desktop is Windows-only. On a Mac, use the browser dashboard, or copy `jira_data.xlsx` to a Windows PC /
upload it to Power BI Service.

1. **Get data → Excel workbook →** `jira-insights/output/jira_data.xlsx`. Tick the **tables** (not sheets):
   `Issues`, `Worklogs`, `People`, `StatusChanges`, `TimeInStatus`, `Buckets`, `StatusMap`, `RefreshInfo`, `ServiceSummary`.
2. **Model view** — relationships:
   - `Worklogs[Key]` → `Issues[Key]` (many-to-one)
   - `Issues[Bucket]` → `Buckets[Bucket]` (many-to-one); in `Buckets`, set *Sort by column* = `SortOrder`
3. **Measures** — paste from [`powerbi/measures.dax`](powerbi/measures.dax) (Tickets, Open, % Done, Story Points,
   Resources, Hours Logged, …).
4. Suggested visuals:
   - Slicers: `Issues[Service]` (multi-select), `Issues[Sprint]`, `Issues[Assignee]`
   - Stacked bar: Axis `Service`, Legend `Buckets[Bucket]`, Values `Tickets` or `Story Points`
   - Matrix: Rows `Service`, Columns `Bucket`, Values `Tickets`, `Story Points`
   - Table "who's on what": `Assignee`, `Key`, `Summary`, `Status`, `StoryPoints` filtered to `Bucket <> Done`
   - Matrix "timesheet": Rows `Worklogs[Author]`, Columns `Worklogs[Date]`, Values `Hours Logged`
   - Cards: `Resources`, `Open Tickets`, `% Points Done`, `Hours Logged`
5. **Refreshing:** click *Refresh from Jira* in the app (or run the command below), then *Refresh* in Power BI.
   The file path never changes, so the report just picks up the new data.

```bash
./start.command --refresh
```

That command refreshes without opening the UI — handy for a scheduled job before your Power BI refresh.

Prefer to skip the app and query Jira directly from Power BI? See [`powerbi/JiraDirect.pq`](powerbi/JiraDirect.pq)
(issues only — no worklogs or status buckets editing).

## Board setup: buckets and statuses (Settings → 3)

Buckets group Jira statuses into the stages you report on. The default is **Product / Development / QA / Done**:

| Bucket | Typical statuses |
|---|---|
| Product | Draft, PM Review, Ready for Refinement |
| Development | Ready for Development, In Development, Peer Review |
| QA | Ready for Test, In Testing |
| Done | anything Jira marks as done |

You can rename, recolour, reorder, add or delete buckets, pick which bucket each status belongs to, and untick
**Show** for statuses you don't want on the board. Tickets in those statuses then don't appear on any screen or in
the Excel file. Applying the setup re-labels the data already pulled, so you don't need a new Jira refresh. Statuses
nobody has placed are guessed from their names; with custom bucket names they land in **Other** until you place them.

## Bucket flow & churn

On the Overview and in the stakeholder report: a cumulative-flow chart (how much work sat in each bucket on each
day) and a churn table per bucket. The table shows tickets at the start, new tickets, moved in, moved out, sent back,
at the end and the net change.

## Planning

The **Planning** tab answers "what can these people finish by this date?":

1. **What to plan:** the top filters, the buckets that count as remaining work (all except the first one by
   default), and any tickets you untick. Unestimated tickets count as a number of points you choose.
2. **Who:** each person's velocity is the average story points (or tickets) they finished per sprint over their last
   3/6/9 finished sprints. Set availability % and leave days per person.
3. **When:** start and target dates, normal vs planned hours per day (planning 10h days scales velocity ×1.25),
   Saturday/Sunday working, and holidays.

The forecast shows working days, team capacity, % achievable, the projected finish date, the gap, the effect of
weekend working, and what it would take (hours/day or extra people) to hit the target. It ends with a list of every
ticket and the date it's projected to be done. Your planning choices are remembered in this browser.

## Pulling only the projects you need

On a big Jira (e.g. 20,000 tickets) don't pull everything: in **Settings → What to pull**, tick only the projects
you report on (there's a search box when the list is long). Under the list the app shows roughly how many tickets
that will be, per project, before you pull anything. Nothing is pulled until at least one project is ticked, or you
enter a custom JQL.

- **Adding a project later** pulls only that project. The others just get their quick refresh.
- **Unticking a project** removes its tickets locally. Nothing is downloaded for that.
- **Closed tickets** are limited to those updated in the last N days (default 180). All open tickets are always
  included. Lower N for an even smaller pull.

## Fast refreshes

- **Refresh from Jira** is a *quick* refresh: it asks Jira only for tickets changed since the last refresh, then
  drops tickets that were deleted or no longer match. Usually seconds, even on big projects.
- **Full reload** pulls everything again. It runs automatically when you change what to pull in Settings
  (projects, JQL, service setting, worklog window), and is worth doing about once a week.
- Full reloads are faster too: status history comes from Jira Cloud's bulk endpoint (1,000 tickets per call),
  long timesheets/histories are fetched in parallel, responses are compressed, and Jira rate limits are retried
  automatically. In a test with 1,500 tickets and realistic network delay, a full pull went from ~100 s to ~6 s and a
  quick refresh took about 1 s.

From a terminal: `./start.command --refresh` (quick) or `./start.command --refresh --full`.

## Refreshing a single widget

Every chart and table has a **↻ Refresh** button. It asks Jira only for tickets that are in that widget's current
scope (the selected projects, services and ticket types) **and have changed since the last full refresh**, then
updates them in place. It takes seconds instead of minutes. All widgets showing those tickets update together, and the header shows
the time of the last live update. Use **Refresh from Jira** (top) for a full reload, e.g. once a day: tickets
that were deleted or moved to another project only disappear on a full refresh.

## Stakeholder report

Pick a **report period** (last month, this month so far, last 3 months, this/last quarter, year to date or custom dates)
and use the filters at the top to choose projects, services and ticket types. *Measure* switches the charts between
ticket counts and story points. Every number is compared with the previous period (previous month / quarter, or the
same dates last year for year to date).

- **Team velocity** — points (or tickets) delivered per finished sprint, the average, and a per-service breakdown.
- **Burndown** — work still open on each day, a straight-line "ideal" burn to zero, and total scope (rises when new
  work is added mid-period).
- **Created vs completed** and **story points delivered** per week (per month for periods over ~4 months).
- **Progress by service** — open at start, created, completed, points, open at end, completion rate, cycle time and
  current status mix, plus a card with a mini burndown for every service.
- **🖨 Print / Save as PDF** prints just the report (light theme, no buttons). **📷 Whole report as image** or each
  chart's **Copy image** drops it straight into PowerPoint.

Completion rate = tickets completed in the period ÷ (open at the start + created during the period).

## Traceability (RTM)

The **Traceability** tab traces PRD requirements to Jira tickets, one view per service plus an all-services overview.

1. Pick a service in the strip at the top of the tab and click **Upload PRD**. Word (.docx), PDF, Markdown and text
   all work. Requirements are read from requirement tables (ID / Requirement / Priority columns), ids such as
   `REQ-1`, `FR-2.1` and `US-04`, and bullet points under headings like "Requirements", "Features" or "User stories".
   Sections like Overview, Goals, Out of scope and Open questions are skipped, and each heading becomes a feature.

   **PRD in Confluence?** Paste the page link (copy it from the browser's address bar) into the box under the
   buttons and click **Read from Confluence**. The app reads the page with your Jira login, so the page must be on
   the same Atlassian site as Jira (e.g. `https://yoursite.atlassian.net/wiki/spaces/KEY/pages/123456/…`; short
   `/wiki/x/…` links work too), and your account needs permission to view it. Tables, bullets, task lists and info
   panels are all read, and Jira issues embedded in the page are linked straight to those tickets. After the PRD
   changes, click **⟳ Re-read page**; links you confirmed are kept for requirement ids that still exist.
2. **The four steps the page follows** (the "How the PRD maps to Jira" table):
   ① each PRD requirement belongs to a **PRD feature** (a section of the PRD) → ② that PRD feature is mapped to a
   **Jira feature** (the issue your user stories sit under: a Feature, or an Epic when stories hang straight off
   epics). The app suggests one; × to remove it, or pick the right one from the list → ③ the **user stories** under
   that Jira feature → ④ how many of the feature's requirements are **covered** and how many are **not covered**.
3. **Covered or not covered.** The cards at the top show how many PRD requirements are covered by a user story and
   how many are not. Click a card, or a number in the table, to see exactly those requirements. A requirement is
   **covered** only when a user story is linked to it; the app never counts a guess as covered.
   - **Add a story:** type the story key (or pick one from the list) under *User stories* and press Enter. For a
     requirement that is not covered, the closest stories are shown as *Possible match*: ✓ links it, × hides it.
   - **Status:** set *Partially covered*, *Not covered*, *External dependency* or *Out of scope* by hand when needed
     (external and out of scope are not counted).
   - **Comment:** click the comment box to say why (e.g. what is missing).
   Jira work that no requirement mentions (search, filters, refresh, column settings and so on) is simply not shown.
4. **Already have a traceability matrix in Excel?** Click **Import RTM (Excel/CSV)** after adding the PRD. The app
   finds the sheet with a requirement-id column (e.g. "PRD Req ID") and a Jira-story column. Its story links, coverage
   status (Fully / Partially covered, Critical gap, Design drift, External dependency) and gap notes are loaded as
   each requirement's stories, status and comment. Requirements in the PRD but missing from your sheet are flagged.
5. **PRDs with requirement tables in PDF** (an ID column like `CIS-ROM-01` or `NFR AVAIL 001` next to "Requirement +
   Explanation") are read row by row, with the title kept apart from the explanation. Functional, non-functional and
   acceptance-criteria tables are told apart, and only functional requirements are traced until you tick the others
   under **Trace**.
6. **Add or remove PRD features.** In the "How the PRD maps to Jira" table, **+ Add PRD feature** adds a new row
   (then pick its Jira feature). Each row has **+ requirement** (asks for the ID, title and text) and **🗑 remove**
   (removes the feature and its requirements, with their story links and comments, after asking you).
7. **Download the whole traceability:**
   - **⬇ Excel**: a formatted workbook with three sheets: *Summary* (headline numbers and coverage per PRD feature),
     *Traceability* (every requirement with its Jira feature, user stories and their status, coverage status and
     comment, coloured and filterable) and *Not covered*.
   - **⬇ PDF**: opens a print-ready report (summary with coverage bars, the requirements not covered, then the full
     traceability grouped by PRD feature). In the print window choose **Save as PDF**; turning off "Headers and
     footers" gives a cleaner page. If nothing opens, allow pop-ups for the app.
   - On the all-services page the same buttons download every service in one file. **CSV** saves the list you are
     looking at. Your links, statuses and comments are saved in `output/rtm.json`.

Sample PRDs to try with the demo data are in `samples/`.

## Role reports: QA, Dev, BA, PM

Set each person's role in **Settings → 4. Team roles** (also: which issue types are defects, default *Bug*, and which
statuses start/end the BA and PM steps). Then open **Team & resources → Productivity report** and pick a role.

| Role | Measures |
|---|---|
| QA | stories tested (moved QA → Done by them), defects logged, defects per story, tickets reopened, **leaked defects** (logged after a story they tested was Done) and leakage %, story points tested in the last sprint |
| Dev | stories delivered (developer = assignee longest in the Development bucket), defects linked to their stories, defects per story point, times reopened, velocity over the last 3 sprints |
| BA | stories created per service and per feature, average days Draft → PM Review, stories whose **description changed after development started** |
| PM | tickets moved PM Review → Ready for Refinement, average / fastest / slowest days, weekly trend, tickets waiting longest |

Defects are tied to stories through Jira issue links (any type, either direction) or the defect's parent.

**Copy Jira query / Open in Jira** appear on every expanded ticket list (and on *All tickets*). Each gives you a
`key in (…)` JQL for exactly the tickets on screen, so you can open them in Jira or save them as a Jira filter.

## How the productivity numbers work

- **Closed by** — the person a ticket was assigned to at the moment it moved to Done (taken from the ticket's status history).
- **Velocity** — story points of tickets closed, per sprint (the ticket's sprint) or per calendar week.
- **Cycle time** — first move into development/testing → Done. **Lead time** — created → Done.
- **Time in each status** — how long tickets sat in each status while assigned to that person.
- **Sent back** — a move from testing (or Done) back to development / to-do.
- **Status changes made** — status moves the person clicked themselves.

Use these to start conversations, not to rank people: story points, ticket size and who-gets-assigned-what vary a lot.

## Notes and limits

- Tickets with several components/labels are counted under the **first** one, so totals never double-count.
- Story points are detected automatically ("Story Points" / "Story point estimate" field).
- Sprint shows the active sprint if the ticket is in one, otherwise the most recent.
- Timesheets come from Jira worklogs. Tempo timesheets stored outside Jira worklogs aren't included.
- Large projects: the first refresh of ~10k tickets takes a few minutes (status history makes it slower; turn it off by
  setting `"include_history": false` in `config.json`).

## Files

```
start.command / start.bat   launchers
app/server.py               local web server + `--refresh` CLI
app/pipeline.py             Jira fetch, status → bucket mapping, people roll-up
app/jira_client.py          Jira REST client (Cloud + Server/DC)
app/excel_export.py         Power BI workbook writer
app/rtm_export.py           traceability Excel report
app/static/index.html       dashboard
powerbi/                    DAX measures + optional direct Power Query
output/                     jira_data.xlsx / jira_data.json (generated)
```
