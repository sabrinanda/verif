# Domain Docs

This project uses a single-context layout.

## Before exploring, read these

- Root `CONTEXT.md`, if present.
- Relevant decisions in `docs/adr/`, if present.

If these files do not exist, proceed silently. Do not flag their
absence or suggest creating them upfront. The domain-modeling
skill creates them lazily as terms and decisions are resolved.

## File structure

- `CONTEXT.md`: domain glossary and context at the project root.
- `docs/adr/`: architecture decision records.

## Use the glossary's vocabulary

Use terms as defined in `CONTEXT.md` in issue titles, proposals,
hypotheses, and tests. Avoid synonyms the glossary explicitly rejects.

If a needed concept is absent, reconsider invented terminology
or note the gap for domain-modeling.

## Flag ADR conflicts

If a proposal contradicts an existing ADR, identify the conflict
and explain why the decision should be reopened instead of
silently overriding it.
