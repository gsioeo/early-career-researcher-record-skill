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
researcher-audit-skill/
├── SKILL.md                 # agent-facing workflow (Claude / Codex / Kiro-style skill)
└── scripts/
    └── researcher_audit.py  # CLI: OpenAlex → Crossref → PubPeer → Europe PMC
```

## Quick start

Requires Python 3.8+. Uses only the standard library; no API keys needed.

```bash
# 1. Find candidate author IDs. Check them for name collisions.
python researcher-audit-skill/scripts/researcher_audit.py --find-author "Firstname Lastname"

# 2. Audit one author, keeping only on-topic works
python researcher-audit-skill/scripts/researcher_audit.py \
  --author-id A0000000000 --keyword topic1,topic2 --surname Lastname

# 3. Or audit an explicit DOI list
python researcher-audit-skill/scripts/researcher_audit.py \
  --dois 10.xxxx/aaaa 10.yyyy/bbbb --surname Lastname

# Optional: JSON output, plus a contact address for the API "polite pool"
export RESEARCHER_AUDIT_MAILTO="you@example.org"
python researcher-audit-skill/scripts/researcher_audit.py --dois 10.xxxx/aaaa --json
```

Example output:

```
10.xxxx/aaaa
   pubpeer=CLEAN  retraction/correction=no  co-first: A, B, Lastname  | target_is_cofirst=True
```

The CLI covers identity resolution, authorship, and integrity checks. The agent carries out the collaboration and PI analysis, the trajectory narrative, and the capability rating from the script's output, following `SKILL.md`.

## Install as an agent skill

Copy `researcher-audit-skill/` into your agent's skills directory, for example:
- `~/.codex/skills/researcher-audit/`
- `~/.claude/skills/researcher-audit/`

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
