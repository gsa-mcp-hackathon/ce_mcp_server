"""Build the CE MCP hackathon deck on the GSA sample-deck template.

Keeps the template's master, layouts, title slide and black title bar;
everything else is composed with native shapes, tables and charts.
Eval numbers come from eval_numbers.json so the rerun can be swapped in.
"""
import copy
import json
import sys

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE, MSO_CONNECTOR
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt
from lxml import etree

TEMPLATE, OUT, NUMBERS = sys.argv[1], sys.argv[2], sys.argv[3]
N = json.load(open(NUMBERS))

# ── palette ────────────────────────────────────────────────────────────────
def rgb(h):
    return RGBColor.from_string(h)

INK, MUTED, RULE = "1A1A1A", "5F6368", "D0D4D9"
TINT = "F3F4F6"
ACCENT = "0B6E69"           # teal: the server
ACCENT_T = "E3F1F0"
V = {  # verdict colors: (strong, tint)
    "pass": ("1E7B34", "E6F2E9"),
    "warn": ("9A5B00", "FDF1DE"),
    "block": ("B3261E", "FBE9E7"),
    "ask": ("1F5FAD", "E7EFFA"),
}
FONT = "Arial"
MONO = "Courier New"

prs = Presentation(TEMPLATE)
SW, SH = prs.slide_width, prs.slide_height
title_only = [l for l in prs.slide_layouts if l.name == "TITLE_ONLY"][0]
title_src = copy.deepcopy(
    [s for s in prs.slides[1].shapes if s.is_placeholder][0]._element
)
orig_ids = list(prs.slides._sldIdLst)[1:]  # template slides 2..12, removed at end


# ── helpers ────────────────────────────────────────────────────────────────
def new_slide(title, notes=""):
    s = prs.slides.add_slide(title_only)
    for ph in list(s.placeholders):
        ph._element.getparent().remove(ph._element)
    el = copy.deepcopy(title_src)
    s.shapes._spTree.insert(2, el)
    t = [sh for sh in s.shapes if sh.is_placeholder][0]
    tf = t.text_frame
    p = tf.paragraphs[0]
    for r in list(p.runs)[1:]:
        r._r.getparent().remove(r._r)
    p.runs[0].text = title
    p.runs[0].font.size = Pt(22)
    bp = tf._txBody.find(qn("a:bodyPr"))
    for c in list(bp):
        bp.remove(c)
    bp.set("anchor", "ctr")
    if notes:
        s.notes_slide.notes_text_frame.text = notes
    return s


def _fmt(run, size, color, bold=False, italic=False, font=FONT):
    f = run.font
    f.size, f.bold, f.italic, f.name = Pt(size), bold, italic, font
    f.color.rgb = rgb(color)


def text(s, x, y, w, h, paras, size=12, color=INK, bold=False, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, font=FONT, margin=0.0, space_after=4, name=None):
    """paras: str | list of (str | list of (text, {opts}))"""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    m = Inches(margin)
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = m
    if isinstance(paras, str):
        paras = [paras]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        runs = para if isinstance(para, list) else [(para, {})]
        bullet = False
        for txt, o in runs:
            bullet = bullet or o.get("bullet", False)
            r = p.add_run()
            r.text = txt
            _fmt(r, o.get("size", size), o.get("color", color), o.get("bold", bold),
                 o.get("italic", False), o.get("font", font))
        if bullet:
            pPr = p._p.get_or_add_pPr()
            pPr.set("marL", str(Inches(0.18)))
            pPr.set("indent", str(-Inches(0.18)))
            bu = etree.SubElement(pPr, qn("a:buChar"))
            bu.set("char", "•")
    return tb


def box(s, x, y, w, h, fill=TINT, line=None, shape=MSO_SHAPE.ROUNDED_RECTANGLE,
        radius=0.06, name=None):
    sh = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        sh.name = name
    sh.shadow.inherit = False
    if fill:
        sh.fill.solid()
        sh.fill.fore_color.rgb = rgb(fill)
    else:
        sh.fill.background()
    if line:
        sh.line.color.rgb = rgb(line)
        sh.line.width = Pt(1)
    else:
        sh.line.fill.background()
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        sh.adjustments[0] = radius
    sh.text_frame.text = ""
    return sh


def pill(s, x, y, label, kind, w=0.95):
    strong, _ = V[kind]
    b = box(s, x, y, w, 0.26, fill=strong, radius=0.5)
    tf = b.text_frame
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = label
    _fmt(r, 10, "FFFFFF", bold=True)
    return b


def arrow(s, x1, y1, x2, y2, color=MUTED, width=1.5):
    c = s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(x1), Inches(y1),
                               Inches(x2), Inches(y2))
    c.line.color.rgb = rgb(color)
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    te = etree.SubElement(ln, qn("a:tailEnd"))
    te.set("type", "triangle")
    te.set("w", "med")
    te.set("len", "med")
    return c


def card(s, x, y, w, h, head, body, color=ACCENT, fill=TINT, head_size=13,
         body_size=11, mono_body=False):
    box(s, x, y, w, h, fill=fill)
    text(s, x + 0.15, y + 0.12, w - 0.3, 0.3, head, size=head_size, color=color,
         bold=True)
    text(s, x + 0.15, y + 0.45, w - 0.3, h - 0.55, body, size=body_size,
         color=INK, font=MONO if mono_body else FONT)


def table(s, x, y, w, rows, col_w, size=10, head_fill=INK, row_h=0.26,
          colors=None):
    shp = s.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y),
                             Inches(w), Inches(row_h * len(rows)))
    t = shp.table
    tblPr = t._tbl.tblPr
    for attr in ("bandRow", "firstRow"):
        tblPr.set(attr, "0")
    sid = tblPr.find(qn("a:tableStyleId"))
    if sid is not None:
        sid.text = "{5940675A-B579-460E-94D1-54222C63F5DA}"  # No Style, Table Grid
    for j, cw in enumerate(col_w):
        t.columns[j].width = Inches(cw)
    for i, row in enumerate(rows):
        t.rows[i].height = Inches(row_h)
        for j, val in enumerate(row):
            c = t.cell(i, j)
            c.margin_left = c.margin_right = Inches(0.06)
            c.margin_top = c.margin_bottom = Inches(0.02)
            c.vertical_anchor = MSO_ANCHOR.MIDDLE
            c.fill.solid()
            if i == 0:
                c.fill.fore_color.rgb = rgb(head_fill)
            else:
                c.fill.fore_color.rgb = rgb("FFFFFF" if i % 2 else TINT)
            tf = c.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            r = p.add_run()
            r.text = str(val)
            col = "FFFFFF" if i == 0 else INK
            bold = i == 0
            if colors and i > 0 and (i, j) in colors:
                col, bold = colors[(i, j)], True
            _fmt(r, size, col, bold=bold)
    # thin grid
    for i in range(len(rows)):
        for j in range(len(rows[0])):
            tcPr = t.cell(i, j)._tc.get_or_add_tcPr()
            for side in ("a:lnL", "a:lnR", "a:lnT", "a:lnB"):
                ln = etree.SubElement(tcPr, qn(side))
                ln.set("w", "6350")
                sf = etree.SubElement(ln, qn("a:solidFill"))
                etree.SubElement(sf, qn("a:srgbClr")).set("val", RULE)
    return shp


def stat(s, x, y, w, big, label, color=ACCENT, big_size=34):
    text(s, x, y, w, 0.6, big, size=big_size, color=color, bold=True)
    text(s, x, y + 0.62, w, 0.6, label, size=11, color=MUTED)


# ── 1. Title (template slide 1, edited) ────────────────────────────────────
s1 = prs.slides[0]
ph = {p.placeholder_format.idx: p for p in s1.placeholders}
ph[0].text_frame.paragraphs[0].runs[0].text = "CE Survey MCP Server"
sub = ph[1].text_frame.paragraphs[0]
sub.runs[0].text = ("A correct figure or an honest refusal, from BLS Consumer "
                    "Expenditure microdata")
p2 = ph[1].text_frame.add_paragraph()
p2.alignment = PP_ALIGN.CENTER
r = p2.add_run()
r.text = "GSA MCP Hackathon  ·  Dataset Access Server  ·  November 2026"
_fmt(r, 14, MUTED)
p2.space_before = Pt(10)
s1.notes_slide.notes_text_frame.text = (
    "Dataset Access Server track. The problem: large language models give confident, "
    "plausible and wrong numbers from complex survey microdata. This server makes the "
    "model describe the request and lets code decide how, or whether, to compute it."
)

# ── 2. Challenge ───────────────────────────────────────────────────────────
s = new_slide("The challenge: survey microdata invites confident wrong answers",
    "Every one of these is a mistake we measured a strong model making. Each answer "
    "looks fine: right units, plausible size. Nothing tells the reader it is wrong.")
text(s, 0.4, 0.8, 9.2, 0.5,
     "The CE Interview Survey is the source for how US households spend. Its public "
     "microdata is easy to query and easy to get wrong.", size=13, color=INK)
stat(s, 0.4, 1.45, 1.9, "48", "tables in the 2025 Interview release")
stat(s, 0.4, 2.6, 1.9, "42", "documented data nuances that change a figure")
stat(s, 0.4, 3.75, 1.9, "4", "interviews per household, a year apart in time")
traps = [
    ("“How many households?”", "Counts interviews: each household is interviewed up to four times."),
    ("“Average spending”", "Reads one quarter column: the measure is the previous + current quarter pair, by months in scope."),
    ("“Spending in Texas”", "No state weights exist after 2023: a state figure rests on the national weight."),
    ("“Households that bought a Toyota”", "The vehicle file lists vehicles owned, not vehicles bought."),
]
for i, (h, b) in enumerate(traps):
    cx, cy = 2.55 + (i % 2) * 3.6, 1.45 + (i // 2) * 1.45
    card(s, cx, cy, 3.45, 1.25, h, b, color=V["block"][0], fill=V["block"][1])
text(s, 2.55, 4.4, 7.05, 0.5,
     "Each answer looks plausible. None is flagged. The figure reaches a briefing anyway.",
     size=12, color=MUTED, bold=True)

# ── 3. Solution ────────────────────────────────────────────────────────────
s = new_slide("The solution: the model describes the request, the server decides",
    "The key split: the model never computes and never chooses a method. It writes a "
    "record of what the user asked, quoting the user's words. Code checks it, judges it "
    "against the survey's rules, computes it, and writes the reply text.")
steps = [
    ("1  Look up", "describe_survey, search_variables, list_geographies: names come only from the server"),
    ("2  Capture", "The model writes a request record, quoting the user's words as evidence"),
    ("3  Check", "Code validates names, quotes, required choices; asks the user when a reading changes the number"),
    ("4  Judge", "55 rules held as data rows give each statistic a verdict, before any data is read"),
    ("5  Compute", "One DuckDB query per statistic, the final weight, replicate-weight standard errors"),
]
bw, gap = 1.68, 0.2
for i, (h, b) in enumerate(steps):
    x = 0.4 + i * (bw + gap)
    model_side = i < 2
    fill = TINT if model_side else ACCENT_T
    col = INK if model_side else ACCENT
    card(s, x, 0.95, bw, 1.75, h, b, color=col, fill=fill, head_size=13, body_size=10.5)
    if i < 4:
        arrow(s, x + bw + 0.02, 1.82, x + bw + gap - 0.02, 1.82)
text(s, 0.4, 2.78, 3.5, 0.3, "the client's model", size=10, color=MUTED)
text(s, 4.2, 2.78, 5.4, 0.3, "the server's code: same result whichever model asks",
     size=10, color=ACCENT, bold=True)
verd = [("PASS", "pass", "computed as asked"),
        ("WARN", "warn", "computed, with each caution under its figure"),
        ("SUBSTITUTE", "warn", "the sound method replaces the one asked, and says so"),
        ("BLOCK", "block", "no figure; the reason, and what could be computed instead")]
for i, (lab, k, d) in enumerate(verd):
    y = 3.35 + i * 0.42
    pill(s, 0.4, y, lab, k, w=1.15)
    text(s, 1.7, y - 0.02, 7.8, 0.3, d, size=12, anchor=MSO_ANCHOR.MIDDLE)

# ── 4. Architecture ────────────────────────────────────────────────────────
s = new_slide("Architecture: eight packages, dependencies point one way",
    "Batch side on the left builds the data and the metadata. The server on the right "
    "only reads. A test guards that the server never imports the loader or the sidecar "
    "generator, so it starts fast and its surface stays small. The evaluation package "
    "drives the real server from outside.")
text(s, 0.4, 0.78, 2.6, 0.3, "BUILD (batch)", size=10, color=MUTED, bold=True)
text(s, 3.65, 0.78, 2.6, 0.3, "STORED", size=10, color=MUTED, bold=True)
text(s, 6.6, 0.78, 3.0, 0.3, "SERVE (read only)", size=10, color=ACCENT, bold=True)
def pkg(x, y, w, h, name, desc, fill=TINT, col=INK):
    box(s, x, y, w, h, fill=fill)
    text(s, x + 0.12, y + 0.07, w - 0.24, 0.28, name, size=11.5, color=col, bold=True, font=MONO)
    text(s, x + 0.12, y + 0.36, w - 0.24, h - 0.4, desc, size=10, color=INK)
pkg(0.4, 1.08, 2.6, 0.8, "ce_ingest", "BLS SAS files → partitioned Parquet / Delta, profiles")
pkg(0.4, 2.0, 2.6, 0.8, "ce_sidecars", "Generates a data contract per table from the BLS dictionary + overrides")
pkg(0.4, 2.92, 2.6, 0.95, "ce_store", "Partition types and the read expression; shared by loader and server")
pkg(3.65, 1.08, 2.4, 0.8, "data/parquet", "2025 microdata, 48 tables", fill="FFFFFF")
pkg(3.65, 2.0, 2.4, 1.72, "sidecars/", "vocabulary.yaml: what each ce: property means\nmaster: survey facts, weights, variance\n48 table sidecars (ODCS)", fill="FFFFFF")
for sh in list(s.shapes)[-4:]:
    pass
pkg(6.6, 1.08, 3.0, 0.62, "ce_mcp_server", "FastMCP: 11 tools, 3 resources", fill=ACCENT_T, col=ACCENT)
pkg(6.6, 1.8, 3.0, 0.62, "ce_meta", "Lookups, sidecars first", fill=ACCENT_T, col=ACCENT)
pkg(6.6, 2.52, 3.0, 0.62, "ce_capture", "Request records: check, store, resolve", fill=ACCENT_T, col=ACCENT)
pkg(6.6, 3.24, 3.0, 0.62, "ce_measures", "Verdicts, DuckDB query, replies", fill=ACCENT_T, col=ACCENT)
arrow(s, 3.02, 1.48, 3.63, 1.48)
arrow(s, 3.02, 2.4, 3.63, 2.4)
arrow(s, 6.07, 2.4, 6.58, 2.4)
text(s, 6.0, 2.48, 0.7, 0.3, "read", size=9, color=MUTED, align=PP_ALIGN.CENTER)
box(s, 0.4, 4.05, 5.65, 0.95, fill=TINT)
text(s, 0.52, 4.12, 5.4, 0.28, "ce_eval", size=11.5, bold=True, font=MONO)
text(s, 0.52, 4.42, 5.4, 0.55, "Drives the real server over stdio as a headless model would; never imported by the server",
     size=10)
box(s, 6.6, 4.05, 3.0, 0.95, fill=TINT)
text(s, 6.72, 4.12, 2.8, 0.28, "One codebase, four hosts", size=11.5, bold=True)
text(s, 6.72, 4.42, 2.8, 0.55, "stdio locally · Databricks Apps · IBM watsonx Orchestrate · cloud.gov", size=10)

# ── 5. Decision 1: sidecars ────────────────────────────────────────────────
s = new_slide("Decision 1: survey knowledge lives in data contracts, not prompts",
    "Each table has an Open Data Contract Standard sidecar, generated, never hand-edited. "
    "The ce: vocabulary gives each fact a meaning the rules can act on. A fact still under "
    "review is not hidden: it becomes a caution in the reply.")
text(s, 0.4, 0.85, 4.4, 3.9, [
    [("Generated, not written. ", {"bold": True}), ("ce-sidecars generate builds each table's sidecar from the BLS data dictionary, the stored table and a reviewed overrides file. Decisions go in overrides; the sidecar is rebuilt.", {})],
    [("A vocabulary the rules read. ", {"bold": True}), ("ce:role, ce:unit, ce:level, ce:calendarPair, ce:topcoded, ce:additiveOver: each property says what it does to a mean, a total or a count.", {})],
    [("Uncertainty is visible. ", {"bold": True}), ("A VERIFY value or a needs-review tag reaches the user as “still under review”, under the figure it affects.", {})],
    [("Validated as a set. ", {"bold": True}), ("ce-sidecars validate fails the build on any broken reference; a failed set blocks every statistic.", {})],
], size=12, space_after=9)
box(s, 5.1, 0.85, 4.5, 3.45, fill="1F2328", radius=0.03)
text(s, 5.25, 0.95, 4.2, 0.3, "sidecars/ce-interview-fmli.odcs.yaml", size=10, color="9DA5AE", font=MONO)
code = ("- name: totexpcq\n"
        "  businessName: Total expenditures,\n"
        "                current quarter\n"
        "  tags: [needs-review]\n"
        "  customProperties:\n"
        "  - property: ce:role\n"
        "    value: measure\n"
        "  - property: ce:unit\n"
        "    value: usd_quarterly\n"
        "  - property: ce:calendarPair\n"
        "    value: totexppq\n"
        "  - property: ce:level\n"
        "    value: consumer_unit\n"
        "  - property: ce:topcoded\n"
        "    value: VERIFY")
text(s, 5.25, 1.3, 4.2, 3.6, code.split("\n"), size=11, color="E6EDF3", font=MONO, space_after=0)

# ── 6. Decision 2: record ──────────────────────────────────────────────────
s = new_slide("Decision 2: the model writes a record, and code checks every word",
    "This is the actual record from the Texas demo. Every name came from a lookup answer "
    "in the same conversation; every quote is found in the user's text by code. The model "
    "states only what the user said; defaults are filled in by code and labelled as such.")
box(s, 0.4, 0.85, 4.6, 3.65, fill="1F2328", radius=0.03)
text(s, 0.55, 0.95, 4.3, 0.3, "submit_request_record → record", size=10, color="9DA5AE", font=MONO)
rec = ('{"nodes": [{\n'
       '  "id": "n1", "kind": "estimate",\n'
       '  "statistic": "mean",\n'
       '  "measure": {"table": "fmli",\n'
       '              "column": "totexpcq"},\n'
       '  "filters": [{"table": "fmli",\n'
       '    "column": "state",\n'
       '    "comparison": "eq", "value": "48"}],\n'
       '  "period": {"text": "in 2025",\n'
       '             "kind": "year", "values": [2025]},\n'
       '  "stated": {"gap_2025": {\n'
       '    "value": "as_released",\n'
       '    "evidence": "as released"}}}],\n'
       ' "answers": [{"asks": "Average total\n'
       '   spending per household in Texas"}]}')
text(s, 0.55, 1.28, 4.3, 3.7, rec.split("\n"), size=10, color="E6EDF3", font=MONO, space_after=0)
checks = [
    ("Names", "every table and column appeared in a lookup answer"),
    ("Evidence", "every quote is located in the user's message"),
    ("Required choices", "2025 “as released” or “adjusted” must be stated, or the user is asked"),
    ("Near is not the same", "“bought” where the data holds “owned” goes back to the user"),
    ("Defaults", "weight, time basis, missing codes are filled by code and labelled"),
]
for i, (h, b) in enumerate(checks):
    y = 0.9 + i * 0.66
    box(s, 5.3, y, 0.42, 0.42, fill=ACCENT, shape=MSO_SHAPE.OVAL)
    text(s, 5.3, y, 0.42, 0.42, "✓", size=14, color="FFFFFF", bold=True,
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 5.85, y - 0.02, 3.8, 0.62, [[(h + "  ", {"bold": True}), (b, {})]], size=11.5)
text(s, 5.3, 4.3, 4.3, 0.7, "Status back to the model: captured · needs_clarification · check_failed · not_captured",
     size=10.5, color=MUTED)

# ── 7. Decision 3: rules as data ───────────────────────────────────────────
s = new_slide("Decision 3: rules are data rows, and verdicts read no data",
    "Rules are rows, read by one evaluator. evaluate_request gives every verdict without "
    "opening a table, so a block costs nothing. A test ties RULES_VERSION to a fingerprint "
    "of the rule tables, so a rule cannot change silently.")
rows = [["Rule", "Fires when", "Verdict"],
        ["geography_unsupported", "metro area (PSU) or below the state", "block"],
        ["geography_limited", "a state: no state weights after 2023", "warn"],
        ["gap_2025_as_released", "2025 with the Q4 collection gap, as released", "warn"],
        ["unverified_fact", "a column fact still under review", "warn"],
        ["adjusted_calendar_pair", "an adjustment the column pair cannot take", "block"],
        ["set_failure", "the sidecar set failed validation", "block"]]
vc = {"block": V["block"][0], "warn": V["warn"][0]}
colors = {(i, 2): vc[r[2]] for i, r in enumerate(rows) if i > 0}
table(s, 0.4, 0.9, 5.7, rows, [2.05, 2.85, 0.8], size=10, row_h=0.42, colors=colors)
text(s, 0.4, 3.95, 5.7, 0.3, "6 of the 55 rules, as written in src/ce_measures/verdict.py",
     size=10, color=MUTED)
stat(s, 6.5, 0.9, 3.1, "55", "rules across the shared table, three measures and combines")
text(s, 6.5, 2.2, 3.1, 2.8, [
    [("No data, no cost. ", {"bold": True}), ("Verdicts are decided from sidecar facts alone.", {})],
    [("Versioned. ", {"bold": True}), ("Each outcome record names the rules, sidecar and vocabulary versions it used.", {})],
    [("One voice. ", {"bold": True}), ("The reply text is composed from the outcome, so each caution sits under its figure.", {})],
], size=11.5, space_after=8)

# ── 8. Decision 4: MCP design ──────────────────────────────────────────────
s = new_slide("Decision 4: tools built around the user's goal, not the tables",
    "No tool wraps a table or a SQL endpoint. The tools follow the job: find, describe the "
    "request, judge, compute. Inputs are pydantic models; the record's JSON Schema is "
    "generated from them and a test fails if it goes stale. Descriptions tell the model "
    "what to do next, including that a block is the answer.")
groups = [
    ("Find", "6 lookup tools", ["describe_survey", "list_datasets", "describe_table",
                               "search_variables", "describe_variable", "list_geographies"]),
    ("Describe", "3 capture tools", ["capture_guide", "submit_request_record",
                                     "capture_request"]),
    ("Judge and compute", "2 processing tools", ["evaluate_request", "run_request"]),
]
for i, (h, sub_, tools) in enumerate(groups):
    x = 0.4 + i * 3.1
    box(s, x, 0.9, 2.9, 2.55, fill=ACCENT_T if i else TINT)
    text(s, x + 0.15, 1.0, 2.6, 0.3, h, size=14, bold=True, color=ACCENT)
    text(s, x + 0.15, 1.32, 2.6, 0.3, sub_, size=10, color=MUTED)
    text(s, x + 0.15, 1.65, 2.6, 1.75, tools, size=11, font=MONO, space_after=3)
for i in range(2):
    arrow(s, 0.4 + (i + 1) * 3.1 - 0.19, 2.15, 0.4 + (i + 1) * 3.1 - 0.01, 2.15)
cols = [
    ("Validated inputs", "pydantic models and generated JSON Schemas for every record"),
    ("Precise descriptions", "each says when to call it and what to do with each status"),
    ("A block is an answer", "the model passes on the reason and offers what can be computed"),
]
for i, (h, b) in enumerate(cols):
    x = 0.4 + i * 3.1
    text(s, x, 3.65, 2.9, 1.3, [[(h, {"bold": True})], b], size=11.5)

# ── 9. Demo ────────────────────────────────────────────────────────────────
s = new_slide("Demo: four questions, four kinds of answer",
    "All four were run on the live server on Nov 23, 2026; reply text is verbatim, trimmed. "
    "Texas: the figure comes with the caution that no state weights exist after 2023. "
    "Metro areas: blocked before any data is read. Toyota: the server asks rather than "
    "counting owners as buyers; in the baseline the model got this nuance right 44% of the time.")
demos = [
    ("pass", "PASS + CAUTIONS", "“Average total spending per household in 2025, as released, with its standard error.”",
     "$81,370 (standard error $988)\n16,754 interviews · 9,925 households\nCaution: 2025 as released; Q4 interviews stand for one third of the usual population."),
    ("warn", "WARN", "“Average total spending per household in Texas in 2025, as released.”",
     "$77,997 (standard error $2,903)\nCaution: No state weights exist after 2023, so a state figure rests on the national weight; region or division is reliable."),
    ("block", "BLOCK", "“Average total spending per household by primary sampling unit in 2025, as released.”",
     "Cannot compute: The survey design does not support estimates at that geography level."),
    ("ask", "ASKS THE USER", "“How many households bought a Toyota in 2025, as released?”",
     "The vehicle file lists vehicles owned, not purchases. Do you want households that own a Toyota, or only those that bought one in 2025?"),
]
for i, (k, lab, q, a) in enumerate(demos):
    x, y = 0.4 + (i % 2) * 4.7, 0.82 + (i // 2) * 2.2
    box(s, x, y, 4.5, 2.05, fill=V[k][1])
    pill(s, x + 0.15, y + 0.13, lab, k, w=1.45)
    text(s, x + 0.15, y + 0.47, 4.2, 0.5, q, size=10.5, color=INK, bold=True)
    text(s, x + 0.15, y + 0.98, 4.2, 1.05, a.split("\n"), size=10, color=INK, space_after=2)

# ── 10. Evaluation setup ───────────────────────────────────────────────────
s = new_slide("Evaluation: 104 cases, 42 nuances, three runs, scored three ways",
    "The headless driver starts claude -p in an empty folder with this server over stdio "
    "as its only tool. The scripted oracle model follows the gold records and must score "
    "perfectly; each checker has a switchable fault to prove it catches what it should. "
    "The judge must agree with every labelled example before its votes are used.")
sets = [["Case set", "Cases", "Tests"],
        ["core", "16", "everyday requests"],
        ["traps", "54", "one nuance each"],
        ["rewordings", "15", "same request, other words"],
        ["conversations", "12", "follow-ups, answers"],
        ["out_of_scope", "9", "should be declined"]]
table(s, 0.4, 0.9, 4.3, sets, [1.35, 0.6, 2.35], size=10.5, row_h=0.36)
text(s, 0.4, 3.18, 4.3, 1.8, [
    [("Driver ", {"bold": True}), ("headless claude -p (Sonnet), server as its only tool", {})],
    [("Judge ", {"bold": True}), ("Haiku, checked on labelled examples first", {})],
    [("Oracle ", {"bold": True}), ("a scripted model that must score 100%", {})],
    [("CI ", {"bold": True}), ("602 tests; gold set run with no model", {})],
], size=11, space_after=5)
levels = [("Record", "Did the model store the right request?", "status accuracy, ambiguities raised, stated choices"),
          ("Outcome", "Does that record compute the right thing?", "two records can differ and compute the same"),
          ("Answer", "What did the user actually read?", "figures, cautions kept, block reasons, nothing invented")]
for i, (h, q, d) in enumerate(levels):
    y = 0.9 + i * 1.1
    box(s, 5.0, y, 4.6, 0.95, fill=ACCENT_T)
    text(s, 5.15, y + 0.08, 1.2, 0.8, h, size=15, color=ACCENT, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, 6.4, y + 0.1, 3.1, 0.8, [[(q, {"bold": True})], d], size=10.5, anchor=MSO_ANCHOR.MIDDLE)
box(s, 5.0, 4.25, 4.6, 0.75, fill=INK)
text(s, 5.15, 4.3, 4.3, 0.65, [[("Headline measure: ", {"bold": True, "color": "FFFFFF"}),
     ("the silent wrong answer: a figure that is not what was asked, and is not said to be.", {"color": "FFFFFF"})]],
     size=11, anchor=MSO_ANCHOR.MIDDLE)

# ── 11. Results headline ───────────────────────────────────────────────────
B, R = N["baseline"], N.get("rerun")
has_r = bool(R)
s = new_slide("Results: " + N["headline"],
    N.get("notes_results", ""))
cal = N["callouts"]
for i, c in enumerate(cal):
    x = 0.4 + i * 1.6
    stat(s, x, 0.85, 1.5, c["big"], c["label"], color=V[c.get("kind", "pass")][0], big_size=28)
cd = CategoryChartData()
keys = N["chart_keys"]
cd.categories = [B["measures"][k]["label"] for k in keys]
cd.add_series(B["name"], [B["measures"][k]["value"] for k in keys])
if has_r:
    cd.add_series(R["name"], [R["measures"][k]["value"] for k in keys])
gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.4), Inches(2.0),
                        Inches(9.2), Inches(3.2), cd)
ch = gf.chart
ch.has_title = False
ch.has_legend = has_r
if has_r:
    ch.legend.position = XL_LEGEND_POSITION.TOP
    ch.legend.include_in_layout = False
    ch.legend.font.size = Pt(10)
    ch.legend.font.name = FONT
cols_ = ["B8BEC6", ACCENT]
for i, ser in enumerate(ch.series):
    ser.format.fill.solid()
    ser.format.fill.fore_color.rgb = rgb(cols_[i] if has_r else ACCENT)
    ser.invert_if_negative = False
pl = ch.plots[0]
pl.gap_width = 60
pl.overlap = -10
pl.has_data_labels = True
dl = pl.data_labels
dl.number_format = '0"%"'
dl.number_format_is_linked = False
dl.position = XL_LABEL_POSITION.OUTSIDE_END
dl.font.size = Pt(9)
dl.font.name = FONT
dl.font.color.rgb = rgb(INK)
va = ch.value_axis
va.minimum_scale, va.maximum_scale = 0, 110
va.has_major_gridlines = False
va.visible = False
from pptx.enum.chart import XL_TICK_LABEL_POSITION, XL_TICK_MARK
va.tick_label_position = XL_TICK_LABEL_POSITION.NONE
va.major_tick_mark = XL_TICK_MARK.NONE
va.format.line.fill.background()
ca = ch.category_axis
ca.tick_labels.font.size = Pt(10)
ca.tick_labels.font.name = FONT
ca.tick_labels.font.color.rgb = rgb(INK)
ca.format.line.color.rgb = rgb(RULE)
ca.reverse_order = True
ca.major_tick_mark = XL_TICK_MARK.NONE

# ── 12. Scorecard ──────────────────────────────────────────────────────────
s = new_slide("Scorecard: every pass mark, baseline" + (" and rerun" if has_r else ""),
    N.get("notes_scorecard", ""))
hdr = ["Measure", "Pass mark", "Baseline"] + (["Rerun"] if has_r else [])
rows = [hdr]
colors = {}
for i, m in enumerate(N["scorecard"], start=1):
    row = [m["label"], m["mark"], m["baseline"]]
    colors[(i, 2)] = V["pass" if m["baseline_met"] else "block"][0]
    if has_r:
        row.append(m.get("rerun", "–"))
        if "rerun_met" in m:
            colors[(i, 3)] = V["pass" if m["rerun_met"] else "block"][0]
    rows.append(row)
cw = [4.6, 1.5, 1.5, 1.6] if has_r else [5.4, 1.9, 1.9]
table(s, 0.4, 0.78, 9.2, rows, cw, size=9.5, row_h=0.245, colors=colors)
text(s, 0.4, 5.0, 9.2, 0.3, N["scorecard_foot"], size=9.5, color=MUTED)

# ── 13. Triage ─────────────────────────────────────────────────────────────
s = new_slide("Every miss had a named cause, and each was fixed where it belongs",
    "We triaged every failed case and put each fix where it belongs: in the capture "
    "instructions, the tool descriptions, the lookups or the rules. No pass mark was "
    "lowered to meet a result.")
rows = [["What the baseline showed", "Where the fix went", "The fix"],
        ["“Households” captured as interviews", "capture instructions", "a household is a consumer unit however the user puts it"],
        ["“Bought” captured as “owned”", "capture instructions", "near is not the same: put it to the user"],
        ["Block worked around; model did its own arithmetic", "tool descriptions", "a block is the answer; only the server's figures are results"],
        ["Search missed “Toyota”, “college student”", "search_variables", "words read across description and code labels; rare words weigh more"],
        ["Diary Survey never mentioned", "describe_survey", "says what the survey does not hold"],
        ["State filter got no caution", "rules 1.1", "a filter on a geography is rated as a grouping"],
        ["Quarterly ÷ annual rescaled silently", "rules 1.1", "the combine says which input was converted"]]
table(s, 0.4, 0.82, 9.2, rows, [3.3, 1.75, 4.15], size=10, row_h=0.44)
text(s, 0.4, 4.45, 9.2, 0.6, N["triage_foot"], size=11, color=ACCENT, bold=True)

# ── 14. Security ───────────────────────────────────────────────────────────
s = new_slide("Security considerations",
    "Public data and a read-only server keep the surface small. The one outbound client "
    "talks only to the Databricks workspace, with every value bound. All kits except "
    "Databricks expose /mcp without authentication by default, the hackathon posture; "
    "SECURITY.md says what to change before any other use.")
sec = [
    ("Public data, read only", "BLS public-use microdata; the tools never write to the survey tables. Only request and outcome records are stored."),
    ("No caller-chosen URLs", "Outbound HTTP goes to one fixed base URL; paths validated; no redirects, no proxies; capped size and time; errors sanitized."),
    ("No SQL from the model", "The model writes a record, never SQL. Code builds one query from checked names; warehouse values are bound parameters."),
    ("Auth by host", "Databricks Apps sits behind workspace SSO. Other kits are open by default for the hackathon; SECURITY.md lists what to close."),
]
for i, (h, b) in enumerate(sec):
    x, y = 0.4 + (i % 2) * 4.7, 1.1 + (i // 2) * 1.85
    card(s, x, y, 4.5, 1.6, h, b, color=ACCENT, fill=TINT, body_size=11.5)

# ── 15. Lessons ────────────────────────────────────────────────────────────
s = new_slide("Lessons learned and recommendations",
    "The pattern carries to any complex federal survey: CPS, ACS PUMS, NHIS, MEPS. Put the "
    "methodology in machine-readable contracts and rules, let the model describe, and "
    "measure silent wrong answers rather than accuracy alone.")
les = [
    ("1", "Put methodology in code, not the prompt", "A model will not hold 42 nuances reliably. Rules that read sidecar facts will, the same way for every model."),
    ("2", "Make “no” a first-class answer", "A block with its reason, and a question where the data is near but not the same, beat a figure with a footnote."),
    ("3", "Score the outcome, not the string", "Two records can compute the same thing. Score what the user read, and count silent wrong answers first."),
    ("4", "Metadata as contracts scales", "ODCS sidecars with a small vocabulary turn a survey's documentation into something rules and tests can check."),
]
for i, (n, h, b) in enumerate(les):
    x, y = 0.4 + (i % 2) * 4.7, 1.0 + (i // 2) * 2.0
    box(s, x, y, 4.5, 1.8, fill=TINT)
    box(s, x + 0.15, y + 0.17, 0.5, 0.5, fill=ACCENT, shape=MSO_SHAPE.OVAL)
    text(s, x + 0.15, y + 0.17, 0.5, 0.5, n, size=16, color="FFFFFF", bold=True,
         align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.8, y + 0.17, 3.55, 0.55, h, size=13, color=ACCENT, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, x + 0.8, y + 0.8, 3.55, 1.1, b, size=11.5)

# ── drop the template's sample slides ─────────────────────────────────────
lst = prs.slides._sldIdLst
for sid in orig_ids:
    prs.part.drop_rel(sid.get(qn("r:id")))
    lst.remove(sid)
prs.save(OUT)
print("wrote", OUT, len(prs.slides), "slides")
