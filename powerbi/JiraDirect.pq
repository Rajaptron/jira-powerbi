// OPTIONAL — pull Jira straight into Power BI without the local app (Windows / Power BI Desktop).
// Home → Transform data → New Source → Blank Query → Advanced Editor → paste this.
// First create three text parameters (Home → Manage Parameters):
//   JiraUrl  e.g. https://yourcompany.atlassian.net
//   JiraJql  e.g. project = AIB AND (statusCategory != Done OR updated >= -180d)
//   StoryPointsField  e.g. customfield_10016   (find it in the app's Refresh Info sheet)
// When prompted for credentials choose "Basic": user = your Jira email, password = your API token.
// Note: the local app gives you more (worklogs/timesheets, status buckets, sprint parsing);
// this query is a lighter alternative for issues only.
let
    Fields = {"summary", "status", "assignee", "issuetype", "priority", "components", "project",
              "created", "updated", "resolutiondate", StoryPointsField},
    GetPage = (token as nullable text) =>
        let
            Body = [jql = JiraJql, fields = Fields, maxResults = 100]
                   & (if token = null then [] else [nextPageToken = token]),
            Response = Json.Document(Web.Contents(JiraUrl, [
                RelativePath = "rest/api/3/search/jql",
                Headers = [#"Content-Type" = "application/json", Accept = "application/json"],
                Content = Json.FromValue(Body)]))
        in
            Response,
    Pages = List.Generate(
        () => GetPage(null),
        each _ <> null,
        each if Record.HasFields(_, "nextPageToken") and _[nextPageToken] <> null
             then GetPage(_[nextPageToken]) else null),
    Issues = List.Combine(List.Transform(Pages, each _[issues])),
    Rows = List.Transform(Issues, (i) =>
        let f = i[fields],
            status = f[status],
            category = status[statusCategory][key],
            name = Text.Lower(status[name]),
            bucket = if category = "done" then "Done"
                else if List.AnyTrue(List.Transform({"test", "qa", "verif", "uat"}, each Text.Contains(name, _))) then "In Testing"
                else if List.AnyTrue(List.Transform({"refin", "groom", "triage", "draft", "backlog"}, each Text.Contains(name, _))) then "In Refinement"
                else if category = "new" then "To Do"
                else "In Development"
        in [
            Key = i[key],
            Summary = f[summary],
            Project = f[project][name],
            Service = if List.Count(f[components]) > 0 then f[components]{0}[name] else "(No component)",
            IssueType = f[issuetype][name],
            Status = status[name],
            Bucket = bucket,
            Assignee = if f[assignee] = null then "Unassigned" else f[assignee][displayName],
            Priority = if f[priority] = null then null else f[priority][name],
            StoryPoints = Record.FieldOrDefault(f, StoryPointsField, 0),
            Created = Date.From(DateTimeZone.FromText(f[created])),
            Resolved = if f[resolutiondate] = null then null else Date.From(DateTimeZone.FromText(f[resolutiondate]))
        ]),
    Table = Table.FromRecords(Rows),
    Typed = Table.TransformColumnTypes(Table, {{"StoryPoints", type number}, {"Created", type date}, {"Resolved", type date}})
in
    Typed
