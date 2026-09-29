# Data sources and known quirks

## Sources

| Source | Endpoint | Used for |
|---|---|---|
| ORCID | `pub.orcid.org/v3.0/{orcid}/works`, `/expanded-search/?q=...` | Works claimed by the person; name + affiliation search |
| OpenAlex | `api.openalex.org/works`, `/authors` | Author IDs, affiliations, PI-anchored discovery |
| Crossref | `api.crossref.org/works/{doi}` | Byline order; `update-to` flags retractions and corrections |
| PubPeer | `POST pubpeer.com/v3/publications?devkey=PubMed`, body `{"dois": [...]}` | Comment counts |
| Europe PMC | `ebi.ac.uk/europepmc/webservices/rest/search`, `/{pmcid}/fullTextXML` | Open-access full text: equal-contribution and corresponding-author footnotes |

- **Keys:** none are required.
- **Polite pool:** set `RESEARCHER_AUDIT_MAILTO` to be identified in OpenAlex and Crossref's polite pool.
- **PubPeer:** the endpoint is the one PubPeer's browser extension calls. It is undocumented and may change.
- **Retraction Watch:** not queried by the script. Search the web for the person's name together with "retraction" if needed.

## Quirks the script handles

- **Crossref author metadata:**
  - The `sequence` field has only two values, `first` and `additional`, so equal contribution cannot be expressed.
  - Author ORCIDs are often missing, so the DOI → ORCID link is frequently broken. Disambiguate ORCID candidates by overlap with known DOIs instead.
- **OpenAlex `is_corresponding`** is unreliable. It has marked equal-contribution authors as corresponding authors, and has left a paper's real corresponding authors unmarked.
- **JATS markup for equal contribution varies:**
  - an `equal-contrib="yes"` attribute on `<contrib>`;
  - an `<fn>` in `<author-notes>`, referenced by `rid`. Its label may be †, #, or a digit that looks like an affiliation number;
  - a bare symbol `<xref>` (#, †, ‡) on a `<contrib>` that has no `contrib-type` attribute.
- **Equal-contribution wording varies:** "contributed equally", "contribute this work equally", "co-first", "joint first".
- **Parser scope:** the parser reads only the front matter, because the same phrases can also appear in the reference list.
- **Corresponding-author markers:** `<corresp>` referenced by `rid`, `corresp="yes"`, or the symbols *, ⁎ or ✉.
- **Europe PMC returns 503 often.** The script retries each request, then makes two slower passes. Any failures left after that are reported as `fetch_error`.
- **Crossref dates:** `issued` can be the print date, months after the online date. The script uses the earliest of `published-online`, `published-print` and `issued`.
- **Renamed institutions** are normalized through `INSTITUTION_ALIASES` in the script, so a rename is not reported as a move. It ships with a few known renames; add others there.

## Limits

- Co-first status can be verified only for papers whose full text Europe PMC holds as open access. For all other papers it stays `unknown` and needs a manual check on the publisher's page.
- Preprint servers and theses are often absent from Europe PMC and poorly linked in OpenAlex. ORCID is the most reliable place to find them.
- Degree stage, graduation and relocation are not recorded in any of these sources.
