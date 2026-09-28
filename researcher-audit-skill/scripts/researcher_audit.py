#!/usr/bin/env python3
"""
researcher_audit.py — Audit a (typically advanced master's and early-stage PhD's level) researcher's public record.

Pipeline (all read-only, no auth required):
  1. OpenAlex  : resolve author -> works, affiliations, dates, author position
  2. Crossref  : per-DOI author order + retraction/correction (update-to) flags
  3. PubPeer   : per-DOI post-publication comments / integrity concerns
  4. EuropePMC : open-access full-text -> detect co-first ("contributed equally") footnotes

USAGE
  # By OpenAlex author id (most reliable; find it first, see --find-author)
  python researcher_audit.py --author-id A0000000000

  # Discover candidate author ids by name (WATCH FOR NAME COLLISIONS)
  python researcher_audit.py --find-author "Firstname Lastname"

  # Audit an explicit DOI list (skips OpenAlex author resolution)
  python researcher_audit.py --dois 10.xxxx/aaaa 10.yyyy/bbbb --surname Lastname

  # Restrict an author's works to those matching a keyword (to fight ID collisions)
  python researcher_audit.py --author-id A0000000000 --keyword topic1,topic2,topic3

  # Emit machine-readable JSON
  python researcher_audit.py --author-id A0000000000 --json

  # Optional: identify yourself to OpenAlex/Crossref "polite pool"
  export RESEARCHER_AUDIT_MAILTO="you@example.org"

CAVEATS (print-and-remember):
  * OpenAlex frequently MERGES distinct same-name people under one author id.
    Always eyeball affiliations/topics and use --keyword to filter.
  * author_position from OpenAlex/Crossref marks only the FIRST author; it CANNOT
    tell you co-first (共同一作). Co-first is a "†/#/*: contributed equally"
    footnote read from the full text (EuropePMC step). Non-OA papers can't be
    checked this way -> reported as "unknown".
  * "无 PubPeer 记录" is a point-in-time state, and PubPeer mostly catches image/data
    duplication, not methodological disputes. Absence != guarantee of integrity.
"""
import argparse, json, os, re, sys, time, urllib.parse, urllib.request

# Contact address is read from the environment; nothing personal is hardcoded.
_MAILTO = os.environ.get("RESEARCHER_AUDIT_MAILTO", "").strip()
UA = "researcher-audit/1.0" + (f" (mailto:{_MAILTO})" if _MAILTO else "")

def _get(url, is_json=True, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read().decode("utf-8", "ignore")
            return json.loads(data) if is_json else data
        except Exception as e:
            if i == retries - 1:
                return None
            time.sleep(1.5 * (i + 1))
    return None

def _post_json(url, payload, retries=3):
    body = json.dumps(payload).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=body,
                headers={"User-Agent": UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception:
            if i == retries - 1:
                return None
            time.sleep(1.5 * (i + 1))
    return None

# ---------- OpenAlex ----------
def find_author(name):
    url = "https://api.openalex.org/authors?search=" + urllib.parse.quote(name) + "&per-page=10"
    d = _get(url)
    out = []
    if not d:
        return out
    for a in d.get("results", []):
        insts = a.get("last_known_institutions") or ([a.get("last_known_institution")] if a.get("last_known_institution") else [])
        inst_names = ", ".join(i.get("display_name","") for i in insts if i)
        out.append({
            "id": a.get("id","").replace("https://openalex.org/",""),
            "name": a.get("display_name"),
            "works_count": a.get("works_count"),
            "cited_by": a.get("cited_by_count"),
            "orcid": a.get("orcid"),
            "institutions": inst_names,
        })
    return out

def author_works(author_id, keyword=None):
    aid = author_id if author_id.startswith("http") else "https://openalex.org/" + author_id
    url = ("https://api.openalex.org/works?filter=author.id:" + author_id.replace("https://openalex.org/","")
           + "&per-page=100&sort=publication_date:asc")
    d = _get(url)
    works = []
    if not d:
        return works
    kws = [k.strip().lower() for k in keyword.split(",")] if keyword else None
    for w in d.get("results", []):
        title = w.get("title") or ""
        if kws and not any(k in title.lower() for k in kws):
            continue
        pos = None; aff = ""
        for a in w.get("authorships", []):
            if a["author"]["id"] == aid:
                pos = a.get("author_position")
                aff = " | ".join(a.get("raw_affiliation_strings", [])) or \
                      " | ".join(i["display_name"] for i in a.get("institutions", []))
        works.append({
            "title": title,
            "date": w.get("publication_date"),
            "doi": (w.get("doi") or "").replace("https://doi.org/",""),
            "type": w.get("type"),
            "cited_by": w.get("cited_by_count"),
            "venue": (w.get("primary_location") or {}).get("source", {} ).get("display_name") if w.get("primary_location") else None,
            "openalex_position": pos,
            "affiliation": aff,
        })
    return works

# ---------- Crossref ----------
def crossref_info(doi):
    d = _get("https://api.crossref.org/works/" + urllib.parse.quote(doi))
    if not d:
        return None
    m = d.get("message", {})
    authors = [ (a.get("given","")+" "+a.get("family","")).strip() for a in m.get("author", []) ]
    return {
        "type": m.get("type"),
        "authors": authors,
        "has_update": bool(m.get("update-to") or m.get("updated-by")),
        "update_to": m.get("update-to"),
    }

# ---------- PubPeer ----------
def pubpeer_check(dois):
    """Returns {doi: n_comments or None}. Uses public v3 endpoint used by the browser extension."""
    res = {}
    r = _post_json("https://pubpeer.com/v3/publications?devkey=PubMed", {"dois": dois})
    if r is None:
        return {d: None for d in dois}
    # response: {"feedbacks":[{"id":..,"total_comments":..,"link":..,"doi":..}, ...]}
    fb = {f.get("doi","").lower(): f for f in r.get("feedbacks", [])}
    for d in dois:
        f = fb.get(d.lower())
        res[d] = f.get("total_comments", 0) if f else 0
    return res

# ---------- EuropePMC co-first detection ----------
def epmc_pmcid(doi):
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:%22"
           + urllib.parse.quote(doi) + "%22&format=json&resultType=core")
    d = _get(url)
    try:
        r = d["resultList"]["result"]
        if r and r[0].get("isOpenAccess") == "Y":
            return r[0].get("pmcid")
    except Exception:
        pass
    return None

def cofirst_authors(pmcid):
    """Return list of surnames flagged 'contributed equally', or None if not determinable."""
    if not pmcid:
        return None
    xml = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/%s/fullTextXML" % pmcid, is_json=False)
    if not xml:
        return None
    # footnote ids whose text says 'contributed equally'
    eq_ids = set()
    for m in re.finditer(r'<fn[^>]*id="([^"]+)"[^>]*>(.*?)</fn>', xml, re.S):
        if re.search(r'contributed equally|equal(?:ly)? to this|co-first|joint first', m.group(2), re.I):
            eq_ids.add(m.group(1))
    inline_symbols = {"†", "#", "‡"}
    flagged = []
    for c in re.findall(r'<contrib [^>]*contrib-type="author"[^>]*>(.*?)</contrib>', xml, re.S) or \
             re.findall(r'<contrib\b(?![^>]*contrib-type="(?:editor|reviewer)")[^>]*>(.*?)</contrib>', xml, re.S):
        surn = re.search(r'<surname>([^<]+)</surname>', c)
        name = surn.group(1) if surn else "?"
        rids = re.findall(r'rid="([^"]+)"', c)
        xtxt = [re.sub(r'<[^>]+>', '', t).strip() for t in re.findall(r'<xref[^>]*>(.*?)</xref>', c, re.S)]
        eq = ('equal-contrib="yes"' in c) or any(r in eq_ids for r in rids) or any(s in inline_symbols for s in xtxt)
        if eq:
            flagged.append(name)
    return flagged  # [] means "checked, none flagged"; None means "couldn't check"

# ---------- Orchestration ----------
def audit(dois, target_surname=None):
    report = []
    pp = pubpeer_check(dois)
    for doi in dois:
        cr = crossref_info(doi)
        pmcid = epmc_pmcid(doi)
        cof = cofirst_authors(pmcid)
        entry = {
            "doi": doi,
            "type": (cr or {}).get("type"),
            "authors": (cr or {}).get("authors"),
            "retraction_or_correction": (cr or {}).get("has_update"),
            "pubpeer_comments": pp.get(doi),
            "cofirst_flagged": cof,  # list | [] | None
            "openaccess_pmcid": pmcid,
        }
        if target_surname is not None and cof is not None:
            entry["target_is_cofirst"] = target_surname.lower() in [c.lower() for c in cof]
        report.append(entry)
        time.sleep(0.3)
    return report

def main():
    ap = argparse.ArgumentParser(description="Audit a researcher's public publication record.")
    ap.add_argument("--find-author", metavar="NAME", help="List candidate OpenAlex author ids for a name")
    ap.add_argument("--author-id", help="OpenAlex author id, e.g. A0000000000")
    ap.add_argument("--dois", nargs="*", help="Explicit DOI list to audit")
    ap.add_argument("--keyword", help="Comma-separated keywords to filter an author's works (fights ID collisions)")
    ap.add_argument("--surname", help="Target surname to test co-first status against (e.g. Smith)")
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()

    if args.find_author:
        cands = find_author(args.find_author)
        if args.json:
            print(json.dumps(cands, ensure_ascii=False, indent=2)); return
        print("Candidate OpenAlex authors (BEWARE same-name merges):\n")
        for c in cands:
            print(f"  {c['id']:14} {c['name']}  | works={c['works_count']} cites={c['cited_by']}")
            print(f"                 orcid={c['orcid']}  inst={c['institutions']}")
        return

    dois = args.dois
    works = []
    if args.author_id:
        works = author_works(args.author_id, args.keyword)
        dois = [w["doi"] for w in works if w["doi"]]

    if not dois:
        print("No DOIs to audit. Provide --author-id or --dois.", file=sys.stderr); sys.exit(1)

    rep = audit(dois, target_surname=args.surname)

    if args.json:
        print(json.dumps({"works": works, "audit": rep}, ensure_ascii=False, indent=2)); return

    if works:
        print("=== OpenAlex works (filtered) ===")
        for w in works:
            print(f"  {w['date']} | pos={w['openalex_position']} | cites={w['cited_by']} | {w['title'][:65]}")
            print(f"            aff: {w['affiliation'][:100]}")
        print()
    print("=== Per-DOI integrity + co-first audit ===")
    for e in rep:
        pp = e["pubpeer_comments"]
        pp_s = "n/a" if pp is None else ("CLEAN" if pp == 0 else f"{pp} COMMENTS!")
        upd = "YES!" if e["retraction_or_correction"] else "no"
        cof = e["cofirst_flagged"]
        if cof is None:
            cof_s = "unknown (not OA)"
        elif not cof:
            cof_s = "no equal-contrib note"
        else:
            cof_s = "co-first: " + ", ".join(cof)
        line = f"  {e['doi']}\n     pubpeer={pp_s}  retraction/correction={upd}  {cof_s}"
        if "target_is_cofirst" in e:
            line += f"  | target_is_cofirst={e['target_is_cofirst']}"
        print(line)

if __name__ == "__main__":
    main()
