#!/usr/bin/env python3
"""
researcher_audit.py (v2): audit an early-career researcher's public record.

Read-only and standard library only. Sources:
  ORCID      : self-claimed works (the most reliable identity anchor, when present)
  OpenAlex   : works, affiliations, author IDs, PI-anchored discovery, ID diagnostics
  Crossref   : author order and retraction/correction flags
  PubPeer    : post-publication comments
  Europe PMC : open-access full text -> co-first and corresponding-author footnotes

MODES
  Discover identity
    --find-author "First Last"                      candidate OpenAlex IDs
    --find-orcid "First Last" [--affiliation ORG] [--match-dois DOI ...]
                                                    candidate ORCIDs, ranked by overlap with known DOIs
    --diagnose-id A0000000000                       is this OpenAlex ID several people merged?

  Audit (collect DOIs from any combination of sources, then analyze)
    --name "First Last"          target's name (strongly recommended; used for matching)
    --orcid 0000-0000-0000-0000  add the works the target has claimed on ORCID
    --anchor-author A0000000000  add papers where a known collaborator (usually the PI)
                                 has a co-author matching --name. This defeats split IDs.
    --author-id A0000000000      add an OpenAlex ID's works (optionally filtered by --keyword)
    --dois DOI ...               add explicit DOIs
    --json                       machine-readable output

  Example
    python researcher_audit.py --name "First Last" --orcid 0000-0000-0000-0000 \\
        --anchor-author A0000000000 --dois 10.xxxx/extra

LESSONS BUILT IN
  * OpenAlex IDs fail in both directions: several people MERGED into one ID, and one
    person SPLIT across several IDs. Never trust a single ID. Anchor on ORCID and/or
    a PI's co-authorships, and use --diagnose-id to screen for merges.
  * The DOI -> ORCID link is often missing in Crossref metadata. Search ORCID by
    name + affiliation, then disambiguate the candidates by overlap with known DOIs.
  * author_position cannot show co-first authorship, and OpenAlex's is_corresponding
    is unreliable (it can mislabel equal-contribution authors as corresponding).
    Both are read from full-text footnotes here, whose wording and markup vary
    (e.g. "contribute this work equally"; footnotes labelled with digits).
  * A transient API error is NOT "not open access". Such papers are reported as
    fetch_error so you know to retry them.
  * Surname-only matching is unsafe (common surnames recur within one byline); use --name.
  * The likely PI is an inference from byline patterns, not a fact.

Optional: export RESEARCHER_AUDIT_MAILTO="you@example.org" (OpenAlex/Crossref polite pool).
"""
import argparse, html, json, os, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request
from collections import Counter

_MAILTO = os.environ.get("RESEARCHER_AUDIT_MAILTO", "").strip()
UA = "researcher-audit/2.0" + (f" (mailto:{_MAILTO})" if _MAILTO else "")
OA, CR = "https://api.openalex.org", "https://api.crossref.org"
EPMC, ORCID = "https://www.ebi.ac.uk/europepmc/webservices/rest", "https://pub.orcid.org/v3.0"
PUBPEER = "https://pubpeer.com/v3/publications?devkey=PubMed"

# --- Tunables (values chosen from observed API behaviour) --------------------
# Europe PMC and OpenAlex usually answer in < 5 s; 40 s tolerates slow full-text XML.
REQUEST_TIMEOUT = 40
# Europe PMC 503s often clear within a few seconds; 4 tries with linear backoff
# (1.5 s, 3 s, 4.5 s) covered most transient failures in testing.
MAX_RETRIES, BACKOFF_STEP = 4, 1.5
# Remaining failures usually clear after a longer pause: two extra passes, 10 s then 20 s.
SECOND_PASSES, SECOND_PASS_WAIT = 2, 10
# Pause between papers to stay well under public rate limits.
PER_PAPER_PAUSE = 0.2
# Merge heuristics for --diagnose-id. A graduate student rarely spans > 12 years,
# > 8 institutions, or >= 4 research fields with no dominant one (< 40% of works).
MERGE_MAX_SPAN_YEARS, MERGE_MAX_INSTITUTIONS = 12, 8
MERGE_MIN_FIELDS, MERGE_MAX_TOP_FIELD_SHARE = 4, 0.4
# Titles sharing > 80% of their content words are treated as preprint/published versions.
DUPLICATE_TITLE_JACCARD = 0.8


class FetchError(Exception):
    """Transient failure after retries. Distinct from 'not found' / 'not open access'."""


def _request(url, payload=None, headers=None, retries=MAX_RETRIES, is_json=True):
    h = {"User-Agent": UA}
    h.update(headers or {})
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        h["Content-Type"] = "application/json"
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h), timeout=REQUEST_TIMEOUT) as r:
                txt = r.read().decode("utf-8", "ignore")
            return json.loads(txt) if is_json else txt
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 410):
                return None                       # definitive: does not exist
            last = e
        except Exception as e:                    # timeouts, 5xx, bad JSON
            last = e
        time.sleep(BACKOFF_STEP * (i + 1))
    raise FetchError(f"{url} -> {last}")


def _clean_doi(d):
    d = (d or "").strip().lower()
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d) or None


def _aid(authorship):
    return ((authorship.get("author") or {}).get("id") or "").rsplit("/", 1)[-1]


# Renamed / co-named institutions, so a rename is not mistaken for a move.
# Extend as needed: lowercase substring -> canonical name.
INSTITUTION_ALIASES = {
    "second military medical university": "Naval Medical University (formerly Second Military Medical University)",
    "naval medical university": "Naval Medical University (formerly Second Military Medical University)",
    "fourth military medical university": "Air Force Medical University (formerly Fourth Military Medical University)",
    "air force medical university": "Air Force Medical University (formerly Fourth Military Medical University)",
    "third military medical university": "Army Medical University (formerly Third Military Medical University)",
    "army medical university": "Army Medical University (formerly Third Military Medical University)",
}


def _short_aff(s):
    """Reduce an affiliation string to its institution-level segment, with rename aliases."""
    low = (s or "").lower()
    for k, v in INSTITUTION_ALIASES.items():
        if k in low:
            return v
    parts = [p.strip() for p in (s or "").split(",") if p.strip()]
    for kw in ("University", "Academy", "Institute", "Hospital", "College"):
        for p in parts:
            if kw.lower() in p.lower() and not re.search(r"\d{3,}", p):
                return p
    return parts[0] if parts else ""


# ------------------------------------------------------------------ names
def _tok(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]", " ", s).split()


class Target:
    """Name matcher. 'full' = given and family match; 'initial' = family + initial only."""

    def __init__(self, full_name=None, surname=None):
        toks = _tok(full_name) if full_name else []
        self.raw = full_name or surname or ""
        self.family = toks[-1] if toks else "".join(_tok(surname))
        self.given = "".join(toks[:-1]) if toks else ""
        self.surname_only = not self.given

    def match_parts(self, given, family):
        g, f = "".join(_tok(given)), "".join(_tok(family))
        if self.surname_only:
            return "surname" if f == self.family else None
        if f == self.family and g:
            if g == self.given:
                return "full"
            if len(g) <= 3 and g[0] == self.given[0]:
                return "initial"
        if f == self.given and g == self.family:  # given/family swapped in metadata
            return "full"
        return None

    def match_raw(self, raw):
        t = _tok(raw)
        if len(t) < 2:
            return None
        return self.match_parts(" ".join(t[:-1]), t[-1]) or self.match_parts(" ".join(t[1:]), t[0])


def _best(matches):
    """Pick the strongest match from [(index, kind)]; full beats initial beats surname."""
    rank = {"full": 0, "initial": 1, "surname": 2}
    return sorted(matches, key=lambda m: rank[m[1]])[0] if matches else None


# --------------------------------------------------------------- OpenAlex
def oa_paged(path, params):
    cursor = "*"
    while cursor:
        q = dict(params, **{"per-page": 200, "cursor": cursor})
        d = _request(OA + path + "?" + urllib.parse.urlencode(q, safe=":,|"))
        if not d:
            return
        yield from d.get("results", [])
        cursor = (d.get("meta") or {}).get("next_cursor")


def find_author(name):
    d = _request(OA + "/authors?" + urllib.parse.urlencode({"search": name, "per-page": 15})) or {}
    out = []
    for a in d.get("results", []):
        insts = a.get("last_known_institutions") or []
        out.append({"id": a.get("id", "").rsplit("/", 1)[-1], "name": a.get("display_name"),
                    "works": a.get("works_count"), "cited_by": a.get("cited_by_count"),
                    "orcid": a.get("orcid"), "institutions": [i.get("display_name") for i in insts if i]})
    return out


def diagnose_id(aid):
    """Heuristics for 'several people merged into one ID'."""
    years, insts, fields = [], Counter(), Counter()
    n = 0
    for w in oa_paged("/works", {"filter": f"author.id:{aid}"}):
        n += 1
        if w.get("publication_year"):
            years.append(w["publication_year"])
        for a in w.get("authorships", []):
            if _aid(a) == aid:
                for i in a.get("institutions", []):
                    insts[i.get("display_name")] += 1
        fld = (((w.get("primary_topic") or {}).get("field")) or {}).get("display_name")
        if fld:
            fields[fld] += 1
    span = (max(years) - min(years)) if years else 0
    top_share = (fields.most_common(1)[0][1] / sum(fields.values())) if fields else 1.0
    flags = []
    if span > MERGE_MAX_SPAN_YEARS:
        flags.append(f"publication span {span} years")
    if len(insts) > MERGE_MAX_INSTITUTIONS:
        flags.append(f"{len(insts)} distinct institutions")
    if len(fields) >= MERGE_MIN_FIELDS and top_share < MERGE_MAX_TOP_FIELD_SHARE:
        flags.append(f"{len(fields)} research fields, top field only {top_share:.0%}")
    verdict = ("LIKELY MERGED: several people under one ID" if len(flags) >= 2
               else "SUSPICIOUS" if flags else "no obvious merge signal")
    return {"id": aid, "works": n, "year_range": [min(years), max(years)] if years else None,
            "institutions": insts.most_common(10), "fields": fields.most_common(8),
            "flags": flags, "verdict": verdict}


def anchor_search(anchor_id, target):
    """Anchor author's works that include a co-author matching the target name."""
    hits = []
    for w in oa_paged("/works", {"filter": f"author.id:{anchor_id}"}):
        for a in w.get("authorships", []):
            m = target.match_raw(a.get("raw_author_name") or "") or \
                target.match_raw((a.get("author") or {}).get("display_name") or "")
            if m and _aid(a) != anchor_id:
                hits.append({"doi": _clean_doi(w.get("doi")), "openalex_author_id": _aid(a),
                             "match": m, "title": w.get("title")})
                break
    return hits


def author_id_works(aid, keyword=None):
    kws = [k.strip().lower() for k in keyword.split(",")] if keyword else None
    out = []
    for w in oa_paged("/works", {"filter": f"author.id:{aid}"}):
        t = (w.get("title") or "").lower()
        if kws and not any(k in t for k in kws):
            continue
        if w.get("doi"):
            out.append(_clean_doi(w["doi"]))
    return out


def oa_work(doi, target):
    try:
        w = _request(OA + "/works/doi:" + urllib.parse.quote(doi, safe="/:"))
    except FetchError:
        return {"status": "fetch_error"}
    if not w:
        return {"status": "not_found"}
    info = {"status": "ok", "type": w.get("type"),
            "field": (((w.get("primary_topic") or {}).get("field")) or {}).get("display_name")}
    if target:
        for a in w.get("authorships", []):
            if target.match_raw(a.get("raw_author_name") or "") or \
               target.match_raw((a.get("author") or {}).get("display_name") or ""):
                info.update(target_openalex_id=_aid(a),
                            target_affiliations=a.get("raw_affiliation_strings", []),
                            target_is_corresponding_openalex=a.get("is_corresponding"))
                break
    return info


# ------------------------------------------------------------------ ORCID
def orcid_works(oid):
    d = _request(f"{ORCID}/{oid}/works", headers={"Accept": "application/json"}) or {}
    out = []
    for g in d.get("group", []):
        s = (g.get("work-summary") or [{}])[0]
        doi = None
        for e in ((s.get("external-ids") or {}).get("external-id") or []):
            if (e.get("external-id-type") or "").lower() == "doi":
                doi = _clean_doi(e.get("external-id-value"))
        out.append({"doi": doi, "type": s.get("type"),
                    "title": (((s.get("title") or {}).get("title")) or {}).get("value"),
                    "year": (((s.get("publication-date") or {}).get("year")) or {}).get("value")})
    return out


def orcid_search(full_name, affiliation=None):
    toks = full_name.split()
    q = f"given-names:{' '.join(toks[:-1])} AND family-name:{toks[-1]}"
    if affiliation:
        q += f' AND affiliation-org-name:"{affiliation}"'
    d = _request(f"{ORCID}/expanded-search/?q={urllib.parse.quote(q)}&rows=50",
                 headers={"Accept": "application/json"}) or {}
    return [{"orcid": r.get("orcid-id"), "name": f"{r.get('given-names')} {r.get('family-names')}",
             "institutions": r.get("institution-name") or []} for r in d.get("expanded-result") or []]


# --------------------------------------------------------------- Crossref
def crossref(doi):
    try:
        d = _request(CR + "/works/" + urllib.parse.quote(doi, safe="/"))
    except FetchError:
        return {"status": "fetch_error"}
    if not d:
        return {"status": "not_found"}
    m = d["message"]
    dates = [tuple((m.get(k) or {}).get("date-parts", [[None]])[0]) for k in
             ("published-online", "published-print", "issued")]
    dates = [dp for dp in dates if dp and dp[0]]
    parts = min(dates, key=lambda dp: tuple(x or 99 for x in dp) + (99,) * (3 - len(dp))) if dates else ()
    un = html.unescape
    return {"status": "ok", "type": m.get("type"), "title": un((m.get("title") or [""])[0]),
            "venue": un((m.get("container-title") or [""])[0] or (m.get("institution") or [{}])[0].get("name", "")),
            "date": "-".join(f"{p:02d}" if i else str(p) for i, p in enumerate(parts) if p) or None,
            "authors": [{"given": a.get("given", ""), "family": a.get("family", ""), "orcid": a.get("ORCID")}
                        for a in m.get("author", [])],
            "retraction_or_correction": bool(m.get("update-to") or m.get("updated-by"))}


# ---------------------------------------------------------------- PubPeer
def pubpeer(doi):
    """Comment count (0 = none), or None if the lookup failed."""
    try:
        r = _request(PUBPEER, payload={"dois": [doi]})
    except FetchError:
        return None
    fb = (r or {}).get("feedbacks") or []
    return sum(int(f.get("total_comments") or 1) for f in fb)


# -------------------------------------------------------- Europe PMC text
EQ_RE = re.compile(r"contribut\w*\s+(?:\w+\s+){0,4}equal|equal(?:ly)?\s+(?:\w+\s+){0,3}contribut"
                   r"|co-?first|joint(?:ly)?\s+first|share[sd]?\s+(?:the\s+)?first", re.I)
CORR_RE = re.compile(r"correspond", re.I)
EQ_MARKS, CORR_MARKS = {"†", "‡", "#"}, {"*", "⁎", "∗", "✉"}


def parse_front(xml):
    """Authors with equal-contribution / corresponding flags, from the JATS front matter only."""
    front = xml.split("<body", 1)[0]
    eq_ids, corr_ids = set(), set()
    for tag, attrs, body in re.findall(r"<(fn|corresp)\b([^>]*)>(.*?)</\1>", front, re.S):
        idm = re.search(r'id="([^"]+)"', attrs)
        txt = re.sub(r"<[^>]+>", " ", body)
        if idm and (tag == "corresp" or CORR_RE.search(txt)):
            corr_ids.add(idm.group(1))
        if idm and EQ_RE.search(txt):
            eq_ids.add(idm.group(1))
    notes = re.sub(r"<[^>]+>", " ", " ".join(re.findall(r"<author-notes>(.*?)</author-notes>", front, re.S)))
    has_eq_note = bool(eq_ids) or bool(EQ_RE.search(notes))
    authors = []
    for attrs, body in re.findall(r"<contrib(?=[\s>])([^>]*)>(.*?)</contrib>", front, re.S):
        if re.search(r'contrib-type="(?:editor|reviewer|translator)"', attrs):
            continue
        sn = re.search(r"<surname>([^<]*)</surname>", body)
        gn = re.search(r"<given-names[^>]*>([^<]*)</given-names>", body)
        if not (sn or gn):
            continue                              # group/collab authors
        rids = set(re.findall(r'rid="([^"]+)"', body))
        marks = {re.sub(r"<[^>]+>", "", t).strip() for t in re.findall(r"<xref[^>]*>(.*?)</xref>", body, re.S)}
        authors.append({
            "given": gn.group(1) if gn else "", "family": sn.group(1) if sn else "",
            "equal": ('equal-contrib="yes"' in attrs) or bool(rids & eq_ids) or (has_eq_note and bool(marks & EQ_MARKS)),
            "corresponding": ('corresp="yes"' in attrs) or bool(rids & corr_ids)
                             or 'ref-type="corresp"' in body or bool(marks & CORR_MARKS)})
    return {"has_equal_note": has_eq_note, "authors": authors}


def fulltext(doi):
    """status: ok | not_oa | not_indexed | fetch_error"""
    try:
        d = _request(EPMC + "/search?" + urllib.parse.urlencode(
            {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"}))
        res = ((d or {}).get("resultList") or {}).get("result") or []
        if not res:
            return {"status": "not_indexed"}
        pmcid = res[0].get("pmcid")
        if not pmcid:
            return {"status": "not_oa"}
        xml = _request(f"{EPMC}/{pmcid}/fullTextXML", is_json=False)
    except FetchError:
        return {"status": "fetch_error"}
    if not xml:
        return {"status": "not_oa", "pmcid": pmcid}
    return dict(parse_front(xml), status="ok", pmcid=pmcid)


# ------------------------------------------------------------ per-paper
def analyze_paper(doi, target):
    cr, oa, ft = crossref(doi), oa_work(doi, target), fulltext(doi)
    p = {"doi": doi, "crossref": cr.get("status"), "fulltext": ft.get("status"),
         "title": cr.get("title"), "venue": cr.get("venue"), "date": cr.get("date"),
         "crossref_type": cr.get("type"), "openalex_type": oa.get("type"), "field": oa.get("field"),
         "retraction_or_correction": cr.get("retraction_or_correction"), "pubpeer_comments": pubpeer(doi),
         "authors": [f"{a['given']} {a['family']}".strip() for a in cr.get("authors", [])],
         "target_affiliations": oa.get("target_affiliations", []),
         "target_is_corresponding_openalex": oa.get("target_is_corresponding_openalex"),
         "cofirst_authors": None, "corresponding_authors": None}
    if ft.get("status") == "ok":
        p["cofirst_authors"] = [f"{a['given']} {a['family']}".strip() for a in ft["authors"] if a["equal"]]
        p["corresponding_authors"] = [f"{a['given']} {a['family']}".strip() for a in ft["authors"] if a["corresponding"]]
    if not target:
        return p
    n = len(cr.get("authors", []))
    m = _best([(i, k) for i, a in enumerate(cr.get("authors", []))
               if (k := target.match_parts(a["given"], a["family"]))])
    p["n_authors"] = n
    p["target_position"] = (m[0] + 1) if m else None
    p["target_match"] = m[1] if m else None
    eq = corr = None
    if ft.get("status") == "ok":
        fm = _best([(i, k) for i, a in enumerate(ft["authors"]) if (k := target.match_parts(a["given"], a["family"]))])
        if fm:
            eq, corr = ft["authors"][fm[0]]["equal"], ft["authors"][fm[0]]["corresponding"]
    p["target_cofirst"], p["target_corresponding"] = eq, corr
    pos = p["target_position"]
    if pos is None:
        role = "not found in byline"
    elif n == 1:
        role = "sole author"
    elif eq:
        role = "co-first"
    elif pos == 1:
        role = "first" if eq is False else "first (shared? unknown)"
    elif pos == n:
        role = "last"
    else:
        role = "middle" if eq is False else "middle (co-first? unknown)"
    if corr:
        role += " + corresponding"
    p["target_role"] = role
    return p


def _title_key(t):
    return set(w for w in _tok(t) if len(w) > 3)


def summarize(papers, target):
    ok = [p for p in papers if p.get("target_position")]
    s = {"papers": len(papers), "target_in_byline": len(ok),
         "first_author": sum(1 for p in ok if p["target_position"] == 1),
         "cofirst": sum(1 for p in ok if p.get("target_cofirst")),
         "corresponding": sum(1 for p in ok if p.get("target_corresponding")),
         "cofirst_unknown": sum(1 for p in ok if p.get("target_cofirst") is None),
         "preprints": sum(1 for p in papers if p.get("crossref_type") == "posted-content"
                          or p.get("openalex_type") == "preprint"),
         "integrity_flags": [p["doi"] for p in papers if p.get("retraction_or_correction")
                             or (p.get("pubpeer_comments") or 0) > 0]}
    # collaborators: frequency, last-author count, corresponding count (full text only)
    freq, last, corr = Counter(), Counter(), Counter()
    for p in ok:
        for i, name in enumerate(p["authors"]):
            if i + 1 == p["target_position"] or not name:
                continue
            freq[name] += 1
            if i + 1 == p["n_authors"]:
                last[name] += 1
        for name in p.get("corresponding_authors") or []:
            if not target.match_raw(name):
                corr[name] += 1
    collab = sorted(freq, key=lambda k: (-corr[k], -last[k], -freq[k]))
    s["top_collaborators"] = [{"name": k, "papers": freq[k], "last_author": last[k], "corresponding_fulltext": corr[k]}
                              for k in collab[:6]]
    pi = next((c for c in s["top_collaborators"] if c["papers"] >= 2 and (c["last_author"] or c["corresponding_fulltext"])), None)
    s["likely_pi_inference"] = pi["name"] if pi else None
    if pi:
        s["papers_without_likely_pi"] = [p["doi"] for p in ok if pi["name"] not in p["authors"]]
    # timeline of the target's institution
    tl = []
    for p in sorted(ok, key=lambda p: p.get("date") or ""):
        inst = _short_aff((p.get("target_affiliations") or [""])[0]) or "(no affiliation string)"
        if tl and tl[-1]["institution"] == inst:
            tl[-1]["to"] = p.get("date")
            tl[-1]["papers"] += 1
        else:
            tl.append({"institution": inst, "from": p.get("date"), "to": p.get("date"), "papers": 1})
    s["affiliation_timeline"] = tl
    # preprint/published duplicates
    dups = []
    for i, a in enumerate(papers):
        for b in papers[i + 1:]:
            ka, kb = _title_key(a.get("title")), _title_key(b.get("title"))
            if ka and kb and len(ka & kb) / len(ka | kb) > DUPLICATE_TITLE_JACCARD:
                dups.append([a["doi"], b["doi"]])
    s["possible_duplicate_versions"] = dups
    return s


# ------------------------------------------------------------------ output
def _yn(v):
    return "unknown" if v is None else ("yes" if v else "no")


def print_report(target, sources, warnings, papers, summ):
    print(f"=== Audit: {target.raw or '(no target name: integrity checks only)'} ===")
    for k, v in sources.items():
        print(f"  source {k}: {v}")
    for w in warnings:
        print(f"  ! {w}")
    print("\n=== Papers ===")
    for p in sorted(papers, key=lambda p: p.get("date") or ""):
        pp = p["pubpeer_comments"]
        print(f"{p.get('date') or '????'} | {p.get('venue') or '?'} | {p.get('openalex_type') or p.get('crossref_type')}")
        print(f"   {(p.get('title') or '')[:90]}")
        print(f"   doi={p['doi']}  [found via: {', '.join(p.get('found_via', []))}]")
        if target.raw:
            print(f"   role={p.get('target_role')}  pos={p.get('target_position')}/{p.get('n_authors')}"
                  f"  match={p.get('target_match')}  co-first={_yn(p.get('target_cofirst'))}"
                  f"  corresponding={_yn(p.get('target_corresponding'))}  fulltext={p['fulltext']}")
            if p.get("target_affiliations"):
                print(f"   affiliation: {_short_aff(p['target_affiliations'][0])}")
        print(f"   integrity: pubpeer={'n/a (lookup failed)' if pp is None else ('CLEAN' if pp == 0 else f'{pp} COMMENT(S)')}"
              f"  retraction/correction={_yn(p.get('retraction_or_correction'))}")
        if p.get("cofirst_authors"):
            print(f"   co-first authors in full text: {', '.join(p['cofirst_authors'])}")
    if not target.raw:
        return
    s = summ
    print("\n=== Summary ===")
    print(f"  papers={s['papers']} (target in byline: {s['target_in_byline']}) preprints={s['preprints']}"
          "  (reviews vs. original research: judge from titles/abstracts; databases don't label them reliably)")
    print(f"  first-author={s['first_author']}  co-first(verified)={s['cofirst']}  corresponding(verified)={s['corresponding']}"
          f"  co-first unknown (no OA text)={s['cofirst_unknown']}")
    print(f"  integrity flags: {s['integrity_flags'] or 'none'}")
    print("  top collaborators (papers / last-author / corresponding in OA full text):")
    for c in s["top_collaborators"]:
        print(f"    {c['name']:28} {c['papers']:>2} / {c['last_author']:>2} / {c['corresponding_fulltext']:>2}")
    if s["likely_pi_inference"]:
        print(f"  likely PI [INFERENCE]: {s['likely_pi_inference']}; papers without this PI: "
              f"{len(s['papers_without_likely_pi'])} {s['papers_without_likely_pi'] or ''}")
    print("  affiliation timeline:")
    for t in s["affiliation_timeline"]:
        print(f"    {t['from']} -> {t['to']}  {t['institution']}  ({t['papers']} papers)")
    if s["possible_duplicate_versions"]:
        print(f"  possible preprint/published duplicates: {s['possible_duplicate_versions']}")


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Audit an early-career researcher's public record.")
    ap.add_argument("--find-author", metavar="NAME")
    ap.add_argument("--find-orcid", metavar="NAME")
    ap.add_argument("--affiliation", help="organisation name for --find-orcid")
    ap.add_argument("--match-dois", nargs="*", default=[], help="known DOIs to rank --find-orcid candidates")
    ap.add_argument("--diagnose-id", metavar="OPENALEX_ID")
    ap.add_argument("--name", help='target full name, e.g. "First Last"')
    ap.add_argument("--surname", help="legacy: surname-only matching (unsafe for common surnames)")
    ap.add_argument("--orcid")
    ap.add_argument("--anchor-author", metavar="OPENALEX_ID", help="collaborator (usually the PI) to anchor on")
    ap.add_argument("--author-id", metavar="OPENALEX_ID")
    ap.add_argument("--keyword", help="comma-separated title keywords to filter --author-id works")
    ap.add_argument("--dois", nargs="*", default=[])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = lambda obj: print(json.dumps(obj, ensure_ascii=False, indent=2))

    try:
        if a.find_author:
            r = find_author(a.find_author)
            if a.json: return out(r)
            print("Candidate OpenAlex authors (IDs may merge or split people; run --diagnose-id):")
            for c in r:
                print(f"  {c['id']:14} {c['name']} | works={c['works']} cites={c['cited_by']} orcid={c['orcid']}")
                print(f"                 {', '.join(filter(None, c['institutions']))}")
            return
        if a.find_orcid:
            known = {_clean_doi(d) for d in a.match_dois}
            cands = orcid_search(a.find_orcid, a.affiliation)
            for c in cands:
                works = orcid_works(c["orcid"])
                c["works"] = len(works)
                c["overlap"] = sorted(known & {w["doi"] for w in works if w["doi"]})
            cands.sort(key=lambda c: (-len(c["overlap"]), -c["works"]))
            if a.json: return out(cands)
            print(f"ORCID candidates for {a.find_orcid!r}" + (f" @ {a.affiliation!r}" if a.affiliation else "") + ":")
            for c in cands:
                tag = f"  <== matches {len(c['overlap'])}/{len(known)} known DOIs" if known else ""
                print(f"  {c['orcid']}  {c['name']} | works={c['works']} | {', '.join(c['institutions'][:2])}{tag}")
            if not known:
                print("  (pass --match-dois with DOIs you already trust to disambiguate)")
            return
        if a.diagnose_id:
            r = diagnose_id(a.diagnose_id)
            if a.json: return out(r)
            print(f"OpenAlex ID {r['id']}: {r['works']} works, years {r['year_range']}")
            print(f"  VERDICT: {r['verdict']}")
            for f in r["flags"]:
                print(f"   - {f}")
            print("  top institutions: " + "; ".join(f"{k} ({v})" for k, v in r["institutions"][:6]))
            print("  fields: " + "; ".join(f"{k} ({v})" for k, v in r["fields"]))
            return
    except FetchError as e:
        sys.exit(f"network error after retries: {e}")

    target = Target(a.name, a.surname) if (a.name or a.surname) else Target()
    if a.anchor_author and not a.name:
        sys.exit("--anchor-author needs --name")
    found, sources, warnings = {}, {}, []

    def add(doi, via):
        doi = _clean_doi(doi)
        if doi:
            found.setdefault(doi, []).append(via)

    for d in a.dois:
        add(d, "manual")
    if a.dois:
        sources["manual"] = f"{len(a.dois)} DOI(s)"
    try:
        if a.orcid:
            works = orcid_works(a.orcid)
            for w in works:
                add(w["doi"], "orcid")
            no_doi = [w["title"] for w in works if not w["doi"]]
            sources["orcid"] = f"{a.orcid}: {len(works)} works" + (f", {len(no_doi)} without DOI" if no_doi else "")
            if no_doi:
                warnings.append(f"ORCID works without DOI (not audited): {no_doi}")
        if a.anchor_author:
            hits = anchor_search(a.anchor_author, target)
            for h in hits:
                add(h["doi"], "anchor")
            ids = Counter(h["openalex_author_id"] for h in hits)
            sources["anchor"] = f"{a.anchor_author}: {len(hits)} co-authored works with {target.raw!r}"
            if len(ids) > 1:
                warnings.append(f"target is SPLIT across {len(ids)} OpenAlex IDs in anchor papers: {dict(ids)}")
            weak = [h["doi"] for h in hits if h["match"] != "full"]
            if weak:
                warnings.append(f"anchor matches on initials only (verify manually): {weak}")
        if a.author_id:
            dois = author_id_works(a.author_id, a.keyword)
            for d in dois:
                add(d, "author-id")
            sources["author-id"] = f"{a.author_id}: {len(dois)} works" + (f" matching {a.keyword!r}" if a.keyword else "")
            diag = diagnose_id(a.author_id)
            if diag["flags"]:
                warnings.append(f"{a.author_id} {diag['verdict']}: {'; '.join(diag['flags'])}")
    except FetchError as e:
        warnings.append(f"source lookup failed, results incomplete: {e}")
    if target.surname_only and target.raw:
        warnings.append("surname-only matching: a different author with the same surname can be mismatched; prefer --name")
    if a.orcid and (a.anchor_author or a.dois):
        orc = {d for d, v in found.items() if "orcid" in v}
        extra = [d for d in found if d not in orc]
        if extra:
            warnings.append(f"{len(extra)} DOI(s) not claimed on ORCID; attribution rests on other evidence: {extra}")
    if not found:
        sys.exit("No DOIs to audit. Provide --dois, --orcid, --anchor-author or --author-id.")

    papers = []
    for doi, via in found.items():
        p = analyze_paper(doi, target if target.raw else None)
        p["found_via"] = via
        papers.append(p)
        time.sleep(PER_PAPER_PAUSE)
    for attempt in range(SECOND_PASSES):          # slow extra passes for transient failures
        errs = [i for i, p in enumerate(papers) if "fetch_error" in (p["crossref"], p["fulltext"])]
        if not errs:
            break
        time.sleep(SECOND_PASS_WAIT * (attempt + 1))
        for i in errs:
            via = papers[i]["found_via"]
            papers[i] = analyze_paper(papers[i]["doi"], target if target.raw else None)
            papers[i]["found_via"] = via
            time.sleep(2)
    errs = [p["doi"] for p in papers if "fetch_error" in (p["crossref"], p["fulltext"])]
    if errs:
        warnings.append(f"transient fetch errors (re-run later; NOT the same as 'not OA'): {errs}")
    missing = [p["doi"] for p in papers if target.raw and p.get("target_position") is None]
    if missing:
        warnings.append(f"target name not found in byline (check name form, or drop the paper): {missing}")
    summ = summarize(papers, target) if target.raw else None
    if a.json:
        return out({"target": target.raw, "sources": sources, "warnings": warnings, "papers": papers, "summary": summ})
    print_report(target, sources, warnings, papers, summ)


if __name__ == "__main__":
    main()
