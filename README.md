# early-career-researcher-record-skill

An AI-agent skill plus a zero-dependency Python CLI for auditing an early-career researcher's **public publication record**, such as that of a master's student, PhD student, or junior author.

## What it checks

| Axis | What you get |
|------|--------------|
| **Publications and trajectory** | Works, venues, citations, affiliations, and how the research direction evolves over time |
| **Authorship** | **Co-first ("contributed equally") authors** read from open-access full-text footnotes. Database `author_position` fields cannot show this. |
| **Collaborators and PI** | The most frequent collaborator, especially one repeatedly in the **corresponding-author** or last-author position, who is usually the **PI or advisor** |
| **Integrity signals** | PubPeer comment counts and Crossref retraction/correction status for each DOI |
| **Capability** | A rubric for rating output and independence against a stated baseline, such as "PhD year 1" |

It also handles a common failure in bibliographic data: **OpenAlex merging different people who share a name into one author ID**.

## Why the PI matters

An early-career researcher's papers usually come from one lab. Identifying the PI, i.e. the collaborator who keeps appearing as corresponding author, separates *what the lab platform produced* from *what the person contributed*. It also frames the key question about independence: **has this person ever published without that PI as corresponding author?**

This is a heuristic, and it can be wrong in these cases:
- Some papers list co-corresponding authors.
- A postdoc may serve as corresponding author.
- In fields that order authors alphabetically, the last author is not necessarily the senior author.

Confirm it against the lab page or the thesis acknowledgements where possible.

## Layout

```
researcher-audit/                 # the skill; copy this folder into your agent's skills directory
├── SKILL.md                      # workflow and rules (loaded when the skill triggers)
├── agents/openai.yaml            # Codex UI metadata
├── references/
│   ├── evaluation.md             # capability rubric and report template (read at the evaluation step)
│   └── data-sources.md           # endpoints, JATS footnote variants, API quirks
└── scripts/
    └── researcher_audit.py       # CLI: ORCID + OpenAlex + Crossref + PubPeer + Europe PMC
evals/evals.json                  # three behavioural test scenarios (not part of the installed skill)
```

## Quick start

Requires Python 3.8+. Uses only the standard library; no API keys needed.

```bash
S=researcher-audit/scripts/researcher_audit.py

# 1. Resolve identity. OpenAlex IDs can merge several people or split one person.
python $S --find-author "Firstname Lastname"
python $S --diagnose-id A0000000000                     # is this ID several people merged?
python $S --find-orcid "Firstname Lastname" --affiliation "University" \
  --match-dois 10.xxxx/known1 10.xxxx/known2            # rank ORCIDs by overlap with DOIs you trust

# 2. Audit: combine ORCID, a PI anchor, and any extra DOIs
python $S --name "Firstname Lastname" --orcid 0000-0000-0000-0000 \
  --anchor-author A_PI_ID --dois 10.xxxx/extra

# Optional: JSON output, plus a contact address for the API "polite pool"
export RESEARCHER_AUDIT_MAILTO="you@example.org"
python $S --name "Firstname Lastname" --orcid 0000-0000-0000-0000 --json
```

Example output (abridged):

```
  ! target is SPLIT across 5 OpenAlex IDs in anchor papers
2025-12 | Journal X | article
   role=first  pos=1/2  match=full  co-first=no  corresponding=no  fulltext=ok
   integrity: pubpeer=CLEAN  retraction/correction=no
=== Summary ===
  first-author=1  co-first(verified)=0  corresponding(verified)=0  co-first unknown (no OA text)=5
  likely PI [INFERENCE]: A. Collaborator; papers without this PI: 1
  affiliation timeline:
    2024-02 -> 2025-05  University A  (2 papers)
    2025-10 -> 2026-06  University B  (6 papers)
```

The CLI covers identity resolution, byline roles, the collaborator and PI summary, the affiliation timeline, and integrity checks. The agent writes the trajectory narrative and the capability rating from this output, following `SKILL.md`.

## Install as an agent skill

The installable skill is the `researcher-audit/` folder inside this repo. Copy it into your agent's skills directory so the folder name stays `researcher-audit`:

```bash
git clone https://github.com/gsioeo/early-career-researcher-record-skill.git
cd early-career-researcher-record-skill

# Codex
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -r researcher-audit "${CODEX_HOME:-$HOME/.codex}/skills/"

# Claude Code (personal skills; use .claude/skills/ inside a project for project-only)
mkdir -p ~/.claude/skills
cp -r researcher-audit ~/.claude/skills/
```

To update, pull the repo and copy the folder again. `evals/` and this README are for development and are not part of the installed skill.

## Limitations

- Co-first status can only be read from **open-access** full text in Europe PMC. Paywalled papers are reported as `unknown`; the tool never guesses.
- "No PubPeer comments" reflects a single point in time. PubPeer mostly catches image or data duplication, not methodological disputes.
- Identifying the PI is an inference from byline patterns, not a verified fact.
- Degree status, graduation, and relocation are **not** recorded in bibliographic databases. Treat any such claim as inference.
- The PubPeer endpoint is the public one used by its browser extension. It is undocumented and may change.

## Responsible use

This tool compiles information about **identifiable individuals**. Use it only for legitimate purposes, such as admissions, hiring, collaboration due diligence, or auditing your own record.

- Keep generated reports private. Report files are git-ignored by default.
- Describe integrity findings in neutral, evidence-based language.
- Never infer misconduct from the presence or absence of a signal.

## Related projects

- **OpenAlex MCP servers and skills:** `oksure/openalex-research-mcp`, `drAbreu/alex-mcp` (author disambiguation)
- **Integrity:** Retraction Watch Database, PubPeer, `HelenoPaiva/Retraction-Radar`, `ammawla/encode-toolkit` (publication-trust)

## License

MIT
