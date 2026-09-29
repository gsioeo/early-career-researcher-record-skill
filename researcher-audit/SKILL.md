---
name: researcher-audit
description: "Audits an early-career researcher's public publication record (master's or PhD student, junior author). Builds a verified publication list; traces research direction and affiliation changes over time (研究流变); distinguishes sole-first, co-first and middle authorship (共同一作 vs 中间作者); infers the likely PI; checks PubPeer and retraction/correction status; and rates capability against a stated baseline such as PhD year 1. Use when asked what a person has published, how strong they are, or whether they suit a PhD place, job or collaboration, or when given their name, a DOI, or an ORCID, OpenAlex, Google Scholar or ResearchGate profile. Handles merged and split author IDs for common names. Not for judging the scientific validity of a single paper."
license: MIT
metadata:
  short-description: Audit an early-career researcher's public record
---

# Researcher audit

Builds an evidence-graded profile of one researcher from public bibliographic data. The bundled script does the deterministic lookups. You make the identity decisions, write the trajectory narrative, and do the evaluation.

**Requirements:**
- Python 3.8+, standard library only.
- Outbound HTTPS to ORCID, OpenAlex, Crossref, PubPeer and Europe PMC.
- Time: a full audit takes several minutes because of paging and retries.

Run `python scripts/researcher_audit.py --help` for every flag.

## Rules that unassisted audits break

1. **Never build the record from one OpenAlex author ID.** IDs merge different people into one, and split one person across several. Both failures are common for common Chinese names.
2. **Never read co-first or corresponding authorship from `author_position` or OpenAlex `is_corresponding`.** Only full-text footnotes count. If the full text is unreadable, the answer is `unknown`, not "no".
3. **`fetch_error` means the request failed**, not that the paper is paywalled. Re-run, and report any that remain as unchecked.
4. **Match the target by full name (`--name`), never by surname alone.** Common surnames recur within one byline.
5. **Label every conclusion [verified] or [inferred].** These are always inferences: degree stage, graduation, PI identity, and attribution of papers the person has not claimed on ORCID.

## Workflow

Copy this checklist and track progress:

```
Audit progress:
- [ ] 1. Collect seed DOIs and the name as printed in bylines
- [ ] 2. Anchor identity (ORCID and/or PI) and confirm the paper set
- [ ] 3. Run the full audit; re-run until no fetch_error remains
- [ ] 4. Write findings: record, trajectory, collaborators, timeline, integrity
- [ ] 5. Evaluate against the baseline (references/evaluation.md)
- [ ] 6. List what could not be verified
```

### 1. Seed

Start from what the user gave: a DOI, a name and institution, or a profile page.
- **ResearchGate** blocks automated fetches. Ask the user to save the profile as HTML, then parse the JSON store embedded in the page for the publication list.
- **Byline spelling:** take the target's name as printed in bylines from a seed paper's Crossref record.

### 2. Anchor identity

Follow these steps in order.

1. **ORCID.** Run:
   ```bash
   python scripts/researcher_audit.py --find-orcid "First Last" --affiliation "Org" --match-dois SEED_DOI [SEED_DOI ...]
   ```
   Accept a candidate only if its claimed works include the seed DOIs. Empty or non-overlapping profiles belong to other people.
2. **PI anchor.** Take the likely PI's OpenAlex author ID from a seed paper's OpenAlex record; this is usually the last or corresponding author. Pass it as `--anchor-author`. This collects the target's papers with the PI, whichever split ID each one sits under.
3. **OpenAlex author ID, only as a last resort.** Run `--diagnose-id ID` first. If it reports `LIKELY MERGED`, do not use that ID as a source.

Do not filter by institution alone; a large university can hold dozens of researchers with the same name.

If the user says the paper count is wrong, look outside the anchors: preprints, and papers from an earlier institution or lab. Add each candidate with `--dois` and check it individually. Do not loosen filters.

### 3. Run

```bash
python scripts/researcher_audit.py --name "First Last" --orcid ORCID --anchor-author PI_ID [--dois EXTRA ...] --json > /tmp/audit.json
```

Write the output outside any git repository, because it describes an identifiable person. Then act on each warning:

| Warning | Action |
|---|---|
| `SPLIT across N OpenAlex IDs` | Expected. It confirms why the anchors are needed. |
| `not claimed on ORCID` | Weaker attribution. State this, or confirm with the user. |
| `initials only` / `name not found in byline` | Verify manually, or drop the paper. |
| `transient fetch errors` | Re-run the same command. Report remaining failures as unchecked. |

### 4. Findings

Work from the JSON. You have latitude in how to present these.

- **Record:** a table of date, venue, type, position (n/N), role and integrity status. Classify review vs original research from the title and abstract; database type fields are unreliable.
- **Trajectory:** read the abstracts in date order, and describe shifts in method or platform, in strategy, and in application. Give a one-sentence through-line, then the stages. Include conference posters the user provides; mark any that appear in no public index as unverified.
- **Collaborators and PI:** use `top_collaborators` and `likely_pi_inference`, and list the papers without that PI. The heuristic fails when authors are ordered alphabetically, or when a paper has co-corresponding authors.
- **Affiliation timeline:** use `affiliation_timeline`. If an institution was renamed, add the rename to `INSTITUTION_ALIASES` in the script so it isn't reported as a move.
- **Integrity:** PubPeer comment counts and Crossref retraction/correction flags. Having no PubPeer comments reflects only the date of the check, and PubPeer mostly catches image and data duplication. Describe any flag neutrally.

### 5. Evaluate

Read [references/evaluation.md](references/evaluation.md) for the rubric and the report template.

### 6. Limits

End the report with what could not be checked, for example: co-first status left `unknown` because the full text is paywalled, meeting abstracts that no public database indexes, papers attributed without ORCID, and the person's degree stage.

## References

- [references/evaluation.md](references/evaluation.md): capability rubric, authorship weighting, report template. Read at step 5.
- [references/data-sources.md](references/data-sources.md): endpoints, the footnote markup variants the parser handles, and known source quirks. Read it when script output looks wrong, or before querying an API directly.
