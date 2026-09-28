---
name: researcher-audit
description: "Audit an early-career researcher (master's student, PhD student, or junior author) from their public publication record. Use when the user asks to: summarize someone's publications or research direction and how it has evolved (研究流变); tell co-first authorship apart from middle authorship (共同一作 vs 中间作者); identify the likely PI or advisor from collaboration patterns; check PubPeer, Retraction Watch, or retraction/correction status; trace affiliations and timeline; or assess research ability and potential against a stated baseline (e.g. 'PhD year 1'). Also use when given a ResearchGate, Google Scholar, OpenAlex, or ORCID link and asked what the person works on and how strong they are. Handles same-name author collisions in bibliographic databases."
license: MIT (see LICENSE)
---

# Early-Career Researcher Audit

Audit a researcher's public record on four axes:

1. publications and venues
2. research direction and trajectory (研究流变)
3. authorship, collaborators, and integrity signals
4. capability and potential, measured against a stated baseline

## Quick reference

| Task | Command |
|------|---------|
| Find candidate author IDs (check for collisions) | `python scripts/researcher_audit.py --find-author "First Last"` |
| Audit one author, on-topic works only | `python scripts/researcher_audit.py --author-id A0000000000 --keyword topic1,topic2 --surname Last` |
| Audit an explicit DOI list | `python scripts/researcher_audit.py --dois 10.x/aaa 10.y/bbb --surname Last` |
| Machine-readable output | add `--json` |

The script is read-only, needs no API keys, and chains **OpenAlex → Crossref → PubPeer → Europe PMC**.

---

## Workflow

### Step 0: Get the source record
- ResearchGate usually returns 403 to automated requests. Ask the user to save the profile page as `.html`. Its embedded Relay/GraphQL JSON store holds the full publication list, affiliations, co-authors, and citation counts, so parse that directly.
- Otherwise, resolve the person through OpenAlex, ORCID, or Google Scholar.

### Step 1: Resolve identity and rule out name collisions ⚠️
`--find-author "Name"` lists candidate OpenAlex IDs. **OpenAlex often merges different people who share a name into one ID**, for example researchers from unrelated fields and institutions. Always:
- inspect the listed institutions and topics;
- use `--keyword` to keep only on-topic works;
- cross-check against a ground-truth source, such as the saved profile page or ORCID.

### Step 2: Publications and venues
For each paper, list the title, date, venue, citation count, and the target's byline position. Note the journal tier (for example Science/Nature family, Q1 society or publisher journals) and the balance between reviews and original research.

### Step 3: Research direction and trajectory (流变)
Read the abstracts and group them chronologically. Describe how the work evolves along axes such as:
- **method or platform**: which tools, carriers, or models change;
- **strategy**: for example single agent → combination → multimodal;
- **application domain**: which problem, disease, or system is targeted.

Give the through-line in one sentence, then describe each stage.

Conference posters or abstracts can be included if the user provides them. If the meeting's abstract book is not publicly indexed, mark them as unverified.

### Step 4: Authorship, co-first vs middle (共同一作) ⚠️ critical
**Do not use `author_position` to judge co-first status.** OpenAlex and Crossref mark only the single first author; everyone else is "middle" or "additional". Co-first authorship is shown by a **footnote in the article** ("† These authors contributed equally").

The script reads that footnote from the **Europe PMC open-access full-text XML** and reports one of:
- `co-first: <names>`: equal-contribution authors were detected;
- `no equal-contrib note`: the full text was checked and has no such note;
- `unknown (not OA)`: the paper is paywalled, so the footnote cannot be read. Say so; do not guess.

Pass `--surname` to get a per-paper `target_is_cofirst` boolean.

### Step 5: Collaboration network and PI identification
Count how often each co-author appears across the target's papers, and record who holds the **corresponding-author** role (`*` / ✉ / `<corresp>` in the full text) and the **last-author** position.

- **Heuristic:** the target's most frequent collaborator, especially one who is repeatedly the corresponding and/or last author, is usually the **PI or advisor**. For a graduate student, a single dominant corresponding author across all papers strongly suggests the student's lab head.
- **Other frequent collaborators** who alternate first-author positions with the target are usually labmates, such as senior students or postdocs. Collaborators with a different institution in the affiliation strings are usually external partners.
- **Caveats:**
  - Some papers have co-corresponding authors. Sometimes a postdoc or associate PI is listed as corresponding author alongside the PI.
  - Author-order conventions differ by field. In some fields names are alphabetical, and the last author is not the senior author.
  - This is an inference. Confirm it with the lab or group web page, or the thesis acknowledgements, when possible.

Why it matters:
- It separates output that reflects the **lab's platform** from output that reflects the target's own contribution.
- It frames the independence question in Step 7: has the target ever published without this PI as corresponding author?

### Step 6: Integrity signals
For each DOI, the script reports:
- **PubPeer** comment count (`CLEAN` means no comments). This is a point-in-time state, and PubPeer mostly catches image or data duplication rather than methodological disputes. State both limits.
- **Crossref** retraction/correction status, from the `update-to` field.

Optionally, also search Retraction Watch or the web for the author's name together with "retraction".

### Step 7: Affiliation and timeline
Trace each paper's affiliation strings and dates. A stable lab together with a burst of papers within about two years is typical of a graduate student's output during the degree. A later change of affiliation, or no new papers, is consistent with graduation. Graduation and relocation themselves are **not** recorded in bibliographic databases, so report them as inference, not fact.

### Step 8: Capability and potential evaluation
Evaluate against the baseline the user specifies, for example "PhD year 1". Assess each of the following:
- **Output and venue level** relative to the baseline.
- **Completeness of the research pipeline**: can the person run an end-to-end workflow, from design to validation?
- **Field vision**: reviews written and breadth of topics.
- **Independence**, the main indicator of the ceiling:
  - Does the target hold any **sole first-author or corresponding-author** paper?
  - Has the target published without the PI identified in Step 5?
  - If every paper is co-first or middle authorship under one PI as corresponding author, independent project design is **unproven, not absent**.
- **Direction sense**: does the person open new angles, or mainly execute the PI's plan?

State potential as a range. Name what would raise or lower the ceiling, for example output after leaving the lab, or a sole-first paper whose project the target led.

---

## Evidence discipline (always)
- Keep **[verified]** findings, backed by tool output you can cite, separate from **[inferred / unverified]** ones.
- Explicitly list what could not be checked, such as paywalled footnotes, unindexed meeting abstracts, graduation status, or the confirmed PI identity. Do not present assumptions as facts.
- Use neutral language for integrity findings. The absence of a signal is not proof of integrity, and the presence of a comment is not proof of misconduct.

## Data sources
- **OpenAlex** `api.openalex.org`: authors, works, affiliations, and byline positions (JSON, no key needed).
- **Crossref** `api.crossref.org/works/{doi}`: author order and `update-to` retraction/correction status.
- **PubPeer** `POST pubpeer.com/v3/publications?devkey=PubMed` with body `{"dois":[...]}`: comment counts. This is the public endpoint used by PubPeer's browser extension; it is undocumented and may change.
- **Europe PMC** `.../{pmcid}/fullTextXML`: equal-contribution footnotes and corresponding-author markers.
- **Retraction Watch**: via web search. No open API for ad-hoc DOI checks is used here.
