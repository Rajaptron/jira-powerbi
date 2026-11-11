"""Read a PRD (Word, PDF, Markdown or text) and pull out its features and requirements."""
import html
import io
import re
import zipfile
from html.parser import HTMLParser
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# A requirement id at the start of a line / cell: REQ-12, FR-1.2, NFR 3, US-04, BR12, R1.1, 3.2.1 (numbered clause)
ID_RE = re.compile(r"^\s*\[?((?:REQ|FR|NFR|BR|UR|SR|US|UC|AC|R)[-_ .]?\d+(?:\.\d+)*|\d+(?:\.\d+){1,3})\]?[\s:.)\-–]+", re.I)
# Words that make a sentence read like a requirement.
REQ_WORDS = re.compile(r"\b(shall|must|should|will be able to|needs? to|is required to|as an? .+?,? i want|so that|"
                       r"the system|the user can|users? can|allow(s)? (the )?users?|support(s)?|provide(s)?|enable(s)?)\b", re.I)
REQ_HEADINGS = re.compile(r"requirement|feature|user stor|scope|acceptance|functional|capabilit|use case|epic", re.I)
# Sections that describe the product rather than require something: only explicit ids count there.
CONTEXT_HEADINGS = re.compile(r"overview|background|introduction|goals?|objectives?|success metrics?|kpis?|summary|"
                              r"problem|personas?|assumptions?|risks?|dependencies|timeline|milestones?", re.I)
SKIP_HEADINGS = re.compile(r"out of scope|non[- ]goals?|appendix|glossary|revision|change log|references?|open questions?", re.I)


# ---------- reading the file into blocks: (kind, level, text) / ("row", cells) ----------
def blocks_from_docx(data):
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml"))
    body = root.find(W + "body")
    out = []
    for el in body:
        if el.tag == W + "p":
            text = "".join(t.text or "" for t in el.iter(W + "t")).strip()
            if not text:
                continue
            style = el.find(f"{W}pPr/{W}pStyle")
            style = style.get(W + "val", "") if style is not None else ""
            heading = re.match(r"(?i)(heading|title)\s*(\d*)", style)
            if heading:
                out.append(("heading", int(heading.group(2) or 1), text))
            elif el.find(f"{W}pPr/{W}numPr") is not None or "list" in style.lower():
                out.append(("item", 0, text))
            else:
                out.append(("para", 0, text))
        elif el.tag == W + "tbl":
            for tr in el.iter(W + "tr"):
                cells = [" ".join("".join(t.text or "" for t in p.iter(W + "t")).strip() for p in tc.iter(W + "p")).strip()
                         for tc in tr.findall(W + "tc")]
                out.append(("row", 0, cells))
            out.append(("endtable", 0, ""))
    return out


def blocks_from_text(text, markdown=True):
    out, in_table = [], False
    plain = not any(l.lstrip().startswith("#") for l in text.splitlines())  # PDF / plain text: no markdown headings
    numbered_heading = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+([A-Z][^.!?:;]{1,70})$")
    first = True
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if in_table:
                out.append(("endtable", 0, "")); in_table = False
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) or not any(cells):
                continue  # divider, or a stray "|" left over from a wrapped row
            out.append(("row", 0, cells)); in_table = True
            continue
        if in_table:
            out.append(("endtable", 0, "")); in_table = False
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        nh = numbered_heading.match(line) if plain else None
        if plain and first and len(line.split()) <= 12 and not line.endswith("."):
            out.append(("heading", 1, line)); first = False  # the document title
            continue
        first = False
        if nh and len(nh.group(2).split()) <= 8:
            out.append(("heading", nh.group(1).count(".") + 2, nh.group(2).strip()))
        elif m:
            out.append(("heading", len(m.group(1)), m.group(2).strip()))
        elif re.match(r"^([-*•▪◦●]|\d+[.)]|[a-z][.)])\s+", line):
            out.append(("item", 0, re.sub(r"^([-*•▪◦●]|\d+[.)]|[a-z][.)])\s+", "", line)))
        elif len(line) < 80 and not line.endswith((".", ",", ";", ":")) and (line.isupper() or line.istitle()) and len(line.split()) <= 8:
            out.append(("heading", 2, line))  # plain-text / PDF headings: short Title Case lines
        else:
            out.append(("para", 0, line))
    return out


def blocks_from_pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("PDF support isn't installed yet. Close the app and start it again (it installs it), "
                         "or upload the PRD as Word (.docx) instead.")
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    # PDF text often wraps sentences across lines: join lines that don't look like a new item or heading.
    lines, merged = text.splitlines(), []
    for line in lines:
        s = line.strip()
        if merged and s and merged[-1] and not re.match(r"^([-*•▪◦●|]|\d+[.)]|#|[A-Z][A-Z ]{3,}$)", s) and not merged[-1].startswith("|") and \
                not ID_RE.match(s) and not merged[-1].endswith((".", ":", ";")) and len(merged[-1]) > 60:
            merged[-1] += " " + s
        else:
            merged.append(s)
    return blocks_from_text("\n".join(merged), markdown=False)


class _StorageReader(HTMLParser):
    """Confluence "storage format" (XHTML plus ac:/ri: macro tags) -> the same blocks as a Word file."""
    HIDDEN = ("ac:plain-text-body", "ac:task-id", "ac:task-status", "ac:placeholder", "style", "script")
    BLOCK = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "ac:task-body", "blockquote", "pre"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.buf, self.stack = [], [], []  # stack of open block tags
        self.row, self.skip, self.macro, self.params = None, 0, [], []

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        if not text or not self.stack or "td" in self.stack or "th" in self.stack:
            return text  # text in a table cell becomes part of the row
        tag = self.stack[-1]
        if tag[0] == "h" and tag[1:].isdigit():
            self.out.append(("heading", int(tag[1:]), text))
        elif tag in ("li", "ac:task-body") or "li" in self.stack:
            self.out.append(("item", 0, text))
        else:
            self.out.append(("para", 0, text))
        return text

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "ac:structured-macro":
            self.macro.append(a.get("ac:name", ""))
        if tag == "ac:parameter":
            # Macro settings aren't content — except the Jira macro's issue key, which is a link to a ticket.
            keep = bool(self.macro) and self.macro[-1] == "jira" and a.get("ac:name") == "key"
            self.params.append(keep)
            self.skip += 0 if keep else 1
            self.buf.append(" ")
            return
        if tag in self.HIDDEN:
            self.skip += 1
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.buf = []
            self.stack.append(tag)
        elif tag in self.BLOCK:
            if self.stack:
                text = self._flush()  # e.g. text of a list item before its nested list
                if text and ("td" in self.stack or "th" in self.stack):
                    self.buf.append(text + " ")
            self.stack.append(tag)
        elif tag == "br":
            self.buf.append(" ")

    def handle_endtag(self, tag):
        if tag == "ac:structured-macro" and self.macro:
            self.macro.pop()
        if tag == "ac:parameter":
            if self.params and not self.params.pop():
                self.skip = max(0, self.skip - 1)
            self.buf.append(" ")
            return
        if tag in self.HIDDEN:
            self.skip = max(0, self.skip - 1)
        if tag in ("td", "th") and tag in self.stack:
            text = self._flush()
            while self.stack and self.stack.pop() != tag:
                pass
            if self.row is not None:
                self.row.append(text)
            elif self.stack and self.stack[-1] in ("td", "th"):
                self.buf.append(text + " ")
        elif tag in self.BLOCK and tag in self.stack:
            text = self._flush()
            while self.stack and self.stack.pop() != tag:
                pass
            if text and ("td" in self.stack or "th" in self.stack):
                self.buf.append(text + " ")  # paragraphs and bullets inside a table cell join the cell text
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.out.append(("row", 0, self.row))
            self.row = None
        elif tag == "table":
            self.out.append(("endtable", 0, ""))

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def handle_entityref(self, name):
        self.handle_data(html.unescape("&%s;" % name))


def blocks_from_confluence(storage):
    reader = _StorageReader()
    reader.feed(storage)
    reader.close()
    return reader.out


def read_blocks(filename, data):
    name = (filename or "").lower()
    if name.endswith(".docx"):
        return blocks_from_docx(data)
    if name.endswith(".pdf"):
        return blocks_from_pdf(data)
    if name.endswith((".doc", ".pages", ".rtf")):
        raise ValueError("Please save the PRD as Word (.docx), PDF, Markdown or text and upload that.")
    return blocks_from_text(data.decode("utf-8", errors="replace"))


# ---------- turning blocks into features + requirements ----------
def _clean(text):
    return re.sub(r"\s+", " ", text).strip(" -–•*")


def _table_requirements(rows, feature):
    """Requirement tables: a header row naming an id / requirement / description column."""
    if not rows:
        return []
    head = [c.lower() for c in rows[0]]
    if ID_RE.match((rows[0][0] if rows[0] else "") + " "):
        # No header (or it was lost): rows that start with an id are requirements — id | text | … priority.
        return [{"id": c[0].strip(), "text": _clean(c[1]) if len(c) > 1 else "", "feature": feature,
                 "priority": _priority(" ".join(c[2:]))} for c in rows if c and ID_RE.match(c[0] + " ") and len(c) > 1 and len(c[1]) >= 8]
    if len(rows) < 2:
        return []
    find = lambda *words: next((n for n, h in enumerate(head) if any(w in h for w in words)), None)
    text_col = find("requirement", "description", "user story", "story", "feature", "capability", "detail")
    if text_col is None:
        return []
    id_col = find("id", "ref", "#", "no.", "number")
    if id_col == text_col:
        id_col = None
    prio_col = find("priority", "moscow", "must/should")
    feat_col = find("feature", "epic", "module", "area", "section", "theme")
    if feat_col == text_col:
        feat_col = None
    out = []
    for cells in rows[1:]:
        get = lambda n: cells[n].strip() if n is not None and n < len(cells) else ""
        text = _clean(get(text_col))
        if len(text) < 8:
            continue
        out.append({"id": get(id_col), "text": text, "feature": get(feat_col) or feature, "priority": get(prio_col)})
    return out


def extract(blocks):
    """Returns {"features": [...], "requirements": [{id, text, feature, priority, source}]}."""
    reqs, features = [], []
    context = False      # inside Overview / Goals / Background…
    path = {}            # heading level -> text
    feature = ""         # nearest feature-level heading
    in_req_section = False
    skipping = False
    table = []
    for kind, level, text in blocks:
        if kind == "row":
            table.append(text); continue
        if kind == "endtable":
            for r in _table_requirements(table, feature):
                reqs.append(dict(r, source="table"))
            table = []; continue
        if kind == "heading":
            path = {k: v for k, v in path.items() if k < level}
            path[level] = text
            skipping = bool(SKIP_HEADINGS.search(text))
            context = bool(CONTEXT_HEADINGS.search(text))
            # The document title ("… Requirements Document") says nothing about a section, so ignore level 1
            # when more than one heading level is in play.
            lowest = min(path)
            in_req_section = not context and any(REQ_HEADINGS.search(v) for k, v in path.items() if k > lowest or len(path) == 1)
            feature = _clean(re.sub(r"^\d+(\.\d+)*\.?\s*", "", path.get(max(path), text)))
            continue
        if skipping:
            continue
        text = _clean(text)
        m = ID_RE.match(text)
        has_id = bool(m) and not re.fullmatch(r"\d+(\.\d+)*", m.group(1) or "")  # "3.2" alone is a clause, not proof
        looks_like = bool(REQ_WORDS.search(text))
        if len(text) < 15:
            continue
        if context and not has_id:
            continue
        if has_id or (kind == "item" and (in_req_section or looks_like)) or (kind == "para" and in_req_section and looks_like):
            rid = m.group(1).upper().replace(" ", "-").replace("_", "-") if m else ""
            body = text[m.end():].strip() if m else text
            reqs.append({"id": rid, "text": body, "feature": feature, "priority": _priority(body), "source": kind})
    # De-duplicate and number anything without an id.
    seen, out, n = set(), [], 0
    for r in reqs:
        key = r["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        if not r["id"]:
            n += 1
            r["id"] = "REQ-%03d" % n
        if r["feature"] and r["feature"] not in features:
            features.append(r["feature"])
        out.append(r)
    ids = set()
    for r in out:  # ids must be unique for linking
        base, k = r["id"], 2
        while r["id"] in ids:
            r["id"] = "%s-%d" % (base, k); k += 1
        ids.add(r["id"])
    return {"features": features, "requirements": out}


def _priority(text):
    m = re.search(r"\b(must have|should have|could have|won'?t have|p[0-3]|high|medium|low)\b", text, re.I)
    return m.group(1).title() if m else ""


def parse(filename, data):
    return _checked(extract(read_blocks(filename, data)))


def parse_confluence(storage):
    if not storage.strip():
        raise ValueError("That Confluence page is empty (or only holds content the app can't read, like an embedded file).")
    return _checked(extract(blocks_from_confluence(storage)))


def _checked(result):
    if not result["requirements"]:
        raise ValueError("I couldn't find requirements in this document. Requirements are picked up from requirement "
                         "tables, numbered ids (REQ-1, FR-2.1…), and bullet points under headings like "
                         "'Requirements' or 'Features'. You can also add them by hand.")
    return result
