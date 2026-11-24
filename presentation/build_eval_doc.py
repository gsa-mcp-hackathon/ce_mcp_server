"""Write the CE MCP evaluation document into the GSA sample evaluation template.

Keeps the template's styles, page setup and list numbering by cloning its own
paragraphs as prototypes. Eval numbers come from the deck's eval_numbers.json.
usage: build_doc.py template.docx out.docx eval_numbers.json
"""
import copy
import json
import sys

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Inches

TEMPLATE, OUT, NUMBERS = sys.argv[1:4]
N = json.load(open(NUMBERS))
B, R = N["baseline"], N.get("rerun")

doc = Document(TEMPLATE)
body = doc.element.body
paras = doc.paragraphs


def find(prefix):
    for p in paras:
        if p.text.strip().startswith(prefix):
            return copy.deepcopy(p._p)
    raise KeyError(prefix)


P = {
    "title": find("Sample Evaluation Document"),
    "subtitle": find("GSA MCP and AI Agent"),
    "note": find("This document is a worked example"),
    "field": find("Server under test"),
    "h3": find("1. Overview"),
    "h4": find("Framework & reproducibility"),
    "h5": find("Data classification"),
    "body": find("The goal of this evaluation"),
    "code": find("uv run eval/phoenix"),
    "bullet": find("Grow and stratify"),
    "metric": find("Correctness rate"),
}
tbl_proto = copy.deepcopy(doc.tables[0]._tbl)

# clear the body, keep the section properties
sect = body.find(qn("w:sectPr"))
for el in list(body):
    if el is not sect:
        body.remove(el)


def _base_rpr(p_el):
    r = p_el.find(qn("w:r"))
    rpr = r.find(qn("w:rPr")) if r is not None else None
    return copy.deepcopy(rpr) if rpr is not None else None


def para(kind, *runs):
    """runs: str or (text, {'b':bool,'i':bool,'code':bool})"""
    proto = P[kind]
    el = copy.deepcopy(proto)
    base = _base_rpr(proto)
    for r in el.findall(qn("w:r")):
        el.remove(r)
    for h in el.findall(qn("w:hyperlink")):
        el.remove(h)
    for item in runs:
        txt, o = (item, {}) if isinstance(item, str) else item
        r = OxmlElement("w:r")
        rpr = copy.deepcopy(base) if base is not None else OxmlElement("w:rPr")
        for tag in ("w:b", "w:i", "w:bCs", "w:iCs"):
            for x in rpr.findall(qn(tag)):
                rpr.remove(x)
        if o.get("b"):
            rpr.append(OxmlElement("w:b"))
        if o.get("i"):
            rpr.append(OxmlElement("w:i"))
        if o.get("code"):
            f = OxmlElement("w:rFonts")
            for a in ("w:ascii", "w:hAnsi", "w:cs"):
                f.set(qn(a), "Courier New")
            for x in rpr.findall(qn("w:rFonts")):
                rpr.remove(x)
            rpr.insert(0, f)
        r.append(rpr)
        t = OxmlElement("w:t")
        t.text = txt
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        r.append(t)
        el.append(r)
    body.insert(list(body).index(sect), el)
    return el


def labelled(kind, label, text_):
    return para(kind, (label, {"b": True}), text_)


def table(rows, widths, bold_first_col=False, colors=None):
    """A table built from the template's own table: same borders and header look."""
    t = copy.deepcopy(tbl_proto)
    trs = t.findall(qn("w:tr"))
    head_proto, row_proto = trs[0], trs[1]
    for tr in trs:
        t.remove(tr)
    grid = t.find(qn("w:tblGrid"))
    for gc in list(grid):
        grid.remove(gc)
    tw = int(sum(widths) * 1440)
    tblPr = t.find(qn("w:tblPr"))
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is not None:
        tblW.set(qn("w:w"), str(tw))
        tblW.set(qn("w:type"), "dxa")
    for w in widths:
        gc = OxmlElement("w:gridCol")
        gc.set(qn("w:w"), str(int(w * 1440)))
        grid.append(gc)
    for i, row in enumerate(rows):
        proto = head_proto if i == 0 else row_proto
        tr = copy.deepcopy(proto)
        tcs = tr.findall(qn("w:tc"))
        tc_proto = tcs[0] if i == 0 else tcs[-1]
        for tc in tcs:
            tr.remove(tc)
        for j, val in enumerate(row):
            tc = copy.deepcopy(tc_proto)
            tcPr = tc.find(qn("w:tcPr"))
            tcW = tcPr.find(qn("w:tcW")) if tcPr is not None else None
            if tcW is not None:
                tcW.set(qn("w:w"), str(int(widths[j] * 1440)))
                tcW.set(qn("w:type"), "dxa")
            ps = tc.findall(qn("w:p"))
            p0 = ps[0]
            for extra in ps[1:]:
                tc.remove(extra)
            base = _base_rpr(p0)
            for r in p0.findall(qn("w:r")):
                p0.remove(r)
            r = OxmlElement("w:r")
            rpr = copy.deepcopy(base) if base is not None else OxmlElement("w:rPr")
            for x in rpr.findall(qn("w:i")):
                rpr.remove(x)
            if i == 0 or (bold_first_col and j == 0) or (colors and (i, j) in colors):
                if rpr.find(qn("w:b")) is None:
                    rpr.append(OxmlElement("w:b"))
            if colors and (i, j) in colors:
                c = OxmlElement("w:color")
                c.set(qn("w:val"), colors[(i, j)])
                for x in rpr.findall(qn("w:color")):
                    rpr.remove(x)
                rpr.append(c)
            r.append(rpr)
            tt = OxmlElement("w:t")
            tt.text = str(val)
            tt.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            r.append(tt)
            p0.append(r)
            if i < len(rows) - 1:
                pPr = p0.find(qn("w:pPr"))
                if pPr is None:
                    pPr = OxmlElement("w:pPr"); p0.insert(0, pPr)
                if pPr.find(qn("w:keepNext")) is None:
                    pPr.insert(0, OxmlElement("w:keepNext"))
            tr.append(tc)
        t.append(tr)
    body.insert(list(body).index(sect), t)
    para("body", "")
    return t


GREEN, RED = "1E7B34", "B3261E"

# ── header ─────────────────────────────────────────────────────────────────
para("title", "Evaluation Document: CE Survey MCP Server")
para("subtitle", "GSA MCP and AI Agent Hackathon")
para("note", ("How well a model with no other context uses this server to answer questions about "
              "the BLS Consumer Expenditure Interview Survey, measured end to end on the real server.",
              {"i": True}))
labelled("field", "Server under test: ", "CE Survey MCP Server (ce-mcp), BLS Consumer Expenditure "
         "Interview Survey public-use microdata, 2025 release")
labelled("field", "Evaluation framework: ", "ce-eval (src/ce_eval/), built for this server; cases under eval/cases/")
labelled("field", "Agent model: ", "Claude Sonnet through a headless claude -p session, with the server "
         "over stdio as its only tool and the minimal system prompt (eval/prompts/minimal.md)")
labelled("field", "Judge model: ", "Claude Haiku, accepted only after agreeing with all 13 labelled examples")
labelled("field", "Dataset: ", N.get("doc_dataset", "104 cases in five sets, three runs each: 312 scored turns"))
labelled("field", "Dates: ", N.get("doc_dates", "Baseline run Nov 21, 2026. A second run after the fixes "
         "started Nov 23, 2026 and was not finished when this was written."))

# ── 1 ──────────────────────────────────────────────────────────────────────
para("h3", "1. Overview and Objectives")
para("body", "Large language models answer questions about complex survey microdata with confident, "
     "plausible and wrong numbers. In the CE Interview Survey a household is interviewed up to four "
     "times, a year's spending is built from previous-quarter and current-quarter columns, state "
     "weights stopped after 2023, and the vehicle file lists vehicles owned rather than bought. A "
     "model that misses any of these still produces a figure that looks right.")
para("body", "The goal of this evaluation is to measure whether a model using this server gives the "
     "user either the right figure with its cautions, or a plain statement that the figure cannot be "
     "computed, and never a silent wrong answer: a figure that is not what was asked and is not said "
     "to be. The silent wrong answer rate is the headline measure.")
para("body", "The server exposes 11 tools in three groups. Six lookup tools (describe_survey, "
     "list_datasets, describe_table, search_variables, describe_variable, list_geographies) answer "
     "from per-table data contracts (ODCS sidecars) and a ce: vocabulary. Three capture tools "
     "(capture_guide, submit_request_record, capture_request) turn the user's message into a request "
     "record, which code checks. Two processing tools (evaluate_request, run_request) judge each "
     "statistic against 55 rules held as data (pass, warn, substitute or block), compute it with the "
     "final weight and replicate-weight standard errors, and write the reply text. The model "
     "describes the request; the server decides how, or whether, to compute it.")

# ── 2 ──────────────────────────────────────────────────────────────────────
para("h3", "2. Testing Methodology")
para("h4", "Framework & reproducibility")
para("body", "The evaluation is the ce-eval package. It never runs inside the server. Cases, pass "
     "marks, judge examples and agent prompts are versioned files under eval/. Every run writes a "
     "results file with the full transcripts, so a run can be scored again without calling a model, "
     "and an interrupted run resumes where it stopped.")
for c in ["uv run ce-eval gold                  # gold records, no model",
          "uv run ce-eval run --driver oracle   # scripted model, must score 100%",
          "uv run ce-eval judge-check --judge haiku",
          "uv run ce-eval run --driver claude-cli --model sonnet \\",
          "    --workers 4 --judge haiku",
          "uv run ce-eval rescore eval/results/<file>.json"]:
    para("code", c)
para("body", "The first model run is archived with its transcripts in eval/baseline/ and can be "
     "rescored from them. The frozen outcomes of the gold records (eval/cases/frozen/) are rewritten "
     "only by ce-eval freeze, so any change to a rule or a default that moves a gold outcome shows up "
     "as a reviewed diff.")

para("h4", "Golden dataset")
para("body", "Each case is a user message, the record a careful model should write (gold), and "
     "what code must make of it (expect: the status, and for each statistic its verdict, method and "
     "rules). Each case is tagged with the data nuances it tests, from a catalogue of 42 "
     "(eval/cases/nuances.yaml).")
table([["Set", "Cases", "What it tests"],
       ["core", "16", "Everyday requests: means, totals, counts, ratios, by region or tenure"],
       ["traps", "54", "One nuance each: households and interviews, owned and bought, state weights, the 2025 gap"],
       ["rewordings", "15", "The same request in other words, terse, long or with typos"],
       ["conversations", "12", "Follow-ups, an answered question, a change of topic"],
       ["out_of_scope", "9", "Requests the survey cannot answer, which should be declined"]],
      [1.3, 0.6, 4.6], bold_first_col=True)
para("body", "106 cases exist; the two that test server mechanics only are not put to a model, "
     "leaving 104. Each runs three times.")

para("h4", "Three levels of scoring")
labelled("metric", "Record: ", "did the model store the right request (status, ambiguities raised, "
         "no choices the user never made)?")
labelled("metric", "Outcome: ", "does that record compute the right thing? Two records can differ "
         "and compute the same; signature.py decides when they do.")
labelled("metric", "Answer: ", "what did the user read? Figures, cautions, block reasons, and "
         "nothing the server did not give.")

para("h4", "Evaluators")
para("body", "Most checks are deterministic. An LLM judge is used only for what words alone cannot "
     "settle.")
table([["Checker", "Decides", "How"],
       ["Outcome equivalence", "The model's record computes what the gold computes", "Code (signature.py)"],
       ["Number fidelity", "Every figure in the answer is one the server returned", "Code"],
       ["Block honesty", "A blocked statistic is answered by its reason, with no figure of the model's own", "Code, judge for paraphrase"],
       ["Caution retention", "Each caution reaches the user, in any words", "Code, then judge"],
       ["Tool order", "Run only a captured request; answers, follow-ups and corrections name the request they belong to", "Code (trajectory.py)"],
       ["Disclosed substitution", "A figure that is not what was asked is said openly", "Judge"]],
      [1.55, 3.1, 1.85], bold_first_col=True)
para("body", "The judge quotes the sentence that carries its evidence before giving a one-word "
     "verdict. Before a run uses it, it must agree with every labelled example in "
     "eval/judge_checks.yaml (13 examples); Haiku agreed with all 13.")

para("h4", "Test design rationale")
para("body", "The trap set targets the failure modes that matter for survey data: a wrong unit, a "
     "wrong column, a geography the design does not support, a near thing taken for the thing "
     "asked. Rewordings test that the same request gives the same outcome however it is put. Three "
     "runs measure consistency rather than a single lucky pass. The oracle driver, a scripted model "
     "that follows the gold records, must score perfectly; each checker has a switchable fault that "
     "the oracle can inject, which proves the checker catches it. 602 tests, including the gold set "
     "with no model, run in CI.")

# ── 3 ──────────────────────────────────────────────────────────────────────
para("h3", "3. Performance Metrics")
para("h4", "Metrics tracked")
for lab, d in [("Silent wrong answer rate: ", "figures that are not what was asked and are not said to be"),
               ("Status accuracy: ", "the request ends captured, asked about, or declined as the gold expects"),
               ("Outcome equivalence: ", "the stored record computes the gold's statistic"),
               ("Stated choices the user never made: ", "a weight, unit or period the model put in the user's mouth"),
               ("Ambiguities raised: ", "readings that change the number are put to the user"),
               ("Cautions kept, number fidelity, block honesty: ", "what the user read matches what the server said"),
               ("Consistency: ", "the same outcome in all three runs"),
               ("Operational: ", "lookups per turn, seconds per turn and cost per turn, from the transcripts")]:
    labelled("bullet", lab, d)

para("h4", "Results")
para("body", N.get("doc_results_intro",
     "The baseline run (Sonnet, Nov 21, 2026) met 8 of the 16 pass marks. Silent wrong answers were "
     "3 of 312 turns (1%). Every block reached the user with its reason and no figure of the model's "
     "own. The misses were in reading the request: households against interviews, owned against "
     "bought, and ambiguities left unraised."))
hdr = ["Measure", "Pass mark", "Baseline"] + (["Rerun"] if R else [])
rows, colors = [hdr], {}
for i, m in enumerate(N["scorecard"], start=1):
    row = [m["label"], m["mark"], m["baseline"]]
    colors[(i, 2)] = GREEN if m["baseline_met"] else RED
    if R:
        row.append(m.get("rerun", "–"))
        if "rerun_met" in m:
            colors[(i, 3)] = GREEN if m["rerun_met"] else RED
    rows.append(row)
table(rows, [3.4, 1.0, 1.0, 1.1] if R else [4.1, 1.2, 1.2], colors=colors)
para("body", "Green: pass mark met. Red: not met. Pass marks are version 1.1 of eval/pass_marks.yaml; "
     "none was lowered after the baseline, and three were tightened where it showed room.")
table([["Set", "Turns", "Silent wrong", "Status", "Outcome", "Numbers", "Cautions"],
       ["core", "48", "4%", "88%", "78%", "96%", "96%"],
       ["traps", "162", "1%", "96%", "94%", "95%", "98%"],
       ["rewordings", "45", "0%", "100%", "94%", "89%", "98%"],
       ["conversations", "30", "0%", "100%", "100%", "100%", "100%"],
       ["out_of_scope", "27", "0%", "100%", "100%", "93%", "n/a"],
       ["all", "312", "1%", "96%", "93%", "95%", "98%"]],
      [1.3, 0.65, 1.0, 0.8, 0.85, 0.9, 0.9], bold_first_col=True)
para("body", "Baseline by set. Operationally the run cost about $18 for three runs (about $0.06 a "
     "turn), took about 14 seconds a turn, and used a median of 3 lookups a turn against a mark of 6.")
if R:
    table(N["doc_rerun_by_set"], [1.3, 0.65, 1.0, 0.8, 0.85, 0.9, 0.9], bold_first_col=True)
    para("body", N["doc_rerun_ops"])
if not R:
    para("body", ("The second run, after the fixes below, was still running when this was written. "
                  "Its figures will be added beside the baseline's.", {"i": True}))

para("h4", "Failure analysis: the baseline" if R else "Failure analysis")
para("body", "25 of the 104 cases had a failed turn in some run; 13 failed in all three. Each was "
     "triaged to where its fix belongs (eval/baseline/README.md lists every case).")
table([["Where the cause lay", "What happened"],
       ["Reading the request", "“How many households did you interview” captured as a count of interviews, the one source of silent wrong answers in the core set; “bought a Toyota” counted as owners; “people” taken as members instead of asked about"],
       ["Handling a block", "After a block, the model captured another request in its place or worked out figures itself (a share from counts, a confidence interval from a standard error, a total of the groups). Each was said to be its own, but none was the server's"],
       ["The lookups", "search_variables did not surface expn_ovb.make for “Toyota” or memi.in_coll for “college student”; nothing said what the Diary Survey holds"],
       ["The rules", "A same-year caution fired on a comparison of two regions; a state filter got no state-weight caution; a quarterly-to-annual ratio was rescaled without a word"],
       ["The checkers", "A threshold offered in a suggestion was counted as a figure; a corrected wrong ID was scored as a tool-order fault"]],
      [1.6, 4.9], bold_first_col=True)

para("h4", "Fixes made after the baseline")
table([["Finding", "Fix", "Where"],
       ["Households read as interviews", "A household is a consumer unit however the user puts it", "capture instructions 1.1"],
       ["A near thing captured instead of asked about", "Near is not the same: put it to the user", "capture instructions 1.1"],
       ["Blocks worked around; model's own arithmetic", "A block is the answer; only the server's figures are results", "after_capture.md, tool descriptions"],
       ["Lookups missing columns", "Words read across a description and one code label; rare words weigh more; closest matches listed with what they lack", "search_variables"],
       ["Diary Survey never mentioned", "describe_survey returns what the survey does not hold", "describe_survey"],
       ["Caution rules misfiring or missing", "Same-year caution narrowed; a geography filter rated as a grouping; combines say which input was converted", "rules 1.1"]],
      [2.0, 3.0, 1.5], bold_first_col=True)
para("body", "The queries that found nothing in the baseline now return the right column first, and a "
     "test holds each of them (tests/meta/test_search_real.py). Four trap cases changed their "
     "expected outcome with the rule fixes, and their frozen outcomes were rewritten and reviewed.")

if R:
    para("h4", "Failures remaining after the fixes")
    para("body", "The rerun had 6 failed turns in 6 cases. None gave the user a figure that was not what "
         "was asked without saying so.")
    table(N["doc_rerun_failures"], [2.0, 3.3, 1.2], bold_first_col=True)

# ── 4 ──────────────────────────────────────────────────────────────────────
para("h3", "4. Security Considerations")
para("h5", "Data classification")
para("body", "The data is BLS public-use microdata: no names or addresses, and BLS has already applied "
     "its disclosure protection (top-coding, recoding, suppression). The server stores request and "
     "outcome records, which contain the user's message verbatim; they go to data/records/ (ignored "
     "by git) or, on Databricks, to Delta tables under workspace governance. Users should not put "
     "personal details in a question.")
para("h5", "Read-only data, no model-written SQL")
para("body", "No tool writes to the survey tables. The model never writes a query: it writes a record "
     "whose every table and column must have appeared in a lookup answer, and code builds the one "
     "DuckDB query from those checked names. The Databricks SQL client binds every value as a "
     "parameter.")
para("h5", "Outbound requests")
para("body", "The HTTP helper reaches one fixed base URL, rejects absolute and escaping paths "
     "(including encoded variants), disables redirects and environment proxies, caps response size "
     "and time, and returns sanitized errors. tests/test_http_security.py is a regression suite for "
     "that boundary.")
para("h5", "Secrets & configuration")
para("body", "Settings load from environment variables or a git-ignored .env through typed "
     "pydantic-settings fields. The Databricks clients check the workspace host and share one token "
     "source.")
para("h5", "Untrusted input & prompt injection")
para("body", "User messages and model output are treated as data. A record must validate against "
     "its JSON Schema, every quoted phrase must be found in the user's text, and every name must come "
     "from a lookup, so a model talked into an odd request still cannot reach a table or column the "
     "lookups did not offer. The out_of_scope set checks that unanswerable requests are declined. "
     "The case sets do not yet include adversarial prompt-injection cases; that is a known gap.")
para("h5", "Transport & authentication")
para("body", "Locally the server runs over stdio. On Databricks Apps it sits behind workspace SSO. "
     "The IBM and cloud.gov kits expose /mcp without authentication by default, the hackathon's "
     "public-data posture; SECURITY.md says to front the endpoint with authentication before any "
     "other use.")

# ── 5 ──────────────────────────────────────────────────────────────────────
para("h3", "5. Lessons Learned & Recommendations")
para("h4", "What worked")
para("body", "Putting the survey's methodology in data contracts and rules, rather than in the "
     "prompt, made the server's answers the same whichever model asked. Blocks held: in 416 turns across both runs "
     "the model never replaced a blocked statistic with a figure of its own without saying so. "
     "Scoring the outcome rather than the record avoided failing models for sound alternative "
     "readings. Archived transcripts let every fix be checked by rescoring, without paying for a new "
     "run. The scripted oracle with switchable faults caught harness bugs before any model was run, "
     "and running it found four faults in the server itself.")
para("h4", "Limitations")
para("body", "One agent model family (Sonnet) and one system prompt were measured. The rerun after the fixes was a single run of 104 turns, so its gains are not yet confirmed across repeats. The judge is "
     "itself a model, checked on 13 examples. There is no no-tool baseline in this run, because "
     "without the server a model has no access to the microdata at all. Only the 2025 release is "
     "loaded. Some sidecar facts are still under review, and the server says so in its cautions "
     "rather than settling them. The set has no adversarial injection cases.")
para("h4", "Recommendations for hackathon participants")
for b in ["Measure the silent wrong answer, not only accuracy: a refusal with a reason is a good answer.",
          "Let the model describe the request and let code decide the method; check every name and quote it writes.",
          "Keep domain rules as data with a version, so a change to a rule is reviewed like a change to code.",
          "Score at three levels: what was stored, what it computes, and what the user read.",
          "Prove the checkers first with a scripted model that injects one fault at a time.",
          "Run each case at least three times and report consistency.",
          "Fix each miss where it belongs (instructions, tool descriptions, lookups or rules) and keep the pass marks.",
          "Human-review judge verdicts on failures before trusting the scores."]:
    para("bullet", b)

doc.save(OUT)
print("wrote", OUT)
