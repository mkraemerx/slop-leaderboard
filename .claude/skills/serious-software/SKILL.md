---
name: serious-software
description: Documents assumptions made by the agent during its work as Markdown files in docs/assumptions/. Runs non-blocking after every substantial action. Level controls the filter (configurable in CLAUDE.md, default: 1).
---

# Serious Software – Making Assumptions Visible

During every substantial piece of work (writing code, making decisions, proposing architecture, creating tickets), consider which assumptions you made and document them according to the configured level.

## Level Definition

Read the current level from CLAUDE.md (key `serious-software-level`). If no level is specified, use **Level 1**.

| Level | What gets documented                                                                          |
| ----- | --------------------------------------------------------------------------------------------- |
| 0     | Only truly surprising or risky assumptions — things an experienced developer would not expect |
| 1     | Non-obvious assumptions and important design decisions that had real alternatives             |
| 2     | Additionally: consciously chosen patterns and conventions, even common ones                   |
| 3     | Everything, including established defaults (not recommended)                                  |

## Categories of Assumptions

Assign a category to each assumption for better organization out of the following list:

- Security (for all assumptions related to security, data privacy and access control)
- Operations (for all assumptions related to deployment, configuration, hosting, infrastructure and maintenance)
- UI (for all assumptions related to the user interface, user experience and interaction design))
- Features (for all assumptions related to the functionality, behavior and requirements of the software)
- Architectural (for all assumptions that is not specific to a more detailed topic and related to the overall system design, structure and the quality attributes of the software)
- Technical (for non-architectural assumptions related to the implementation, coding, technical choices and tools)

## What is an Assumption?

An assumption is a decision or precondition you took silently, without explicitly aligning with the user. Often, this comes from the combination of sparse user input and the world knowledge of the model used to fill these gaps. Examples:

- Technical choice without explicit requirement ("I used React Router state because...")
- Interpretation of a requirement ("I understood 'confirmation' as a separate page, not a modal")
- Presupposed constraints ("I assumed no backend exists")
- Omitted features ("I skipped error handling because this is a prototype")
- Simplifications ("I used in-memory storage for simplicity accecpting data loss on restart")

## Process

1. **Do not block** — complete the actual task fully first
2. **Collect assumptions** — identify assumptions made in hindsight
3. **Apply level filter** — keep only those matching the configured level
4. **Document** — create a file in `docs/assumptions/` for each relevant assumption

## File Format

Filename: `ASSUMPTION-XXX-short-slug.md` (XXX = next available number)

```markdown
---
ID: ASSUMPTION-XXX
Category: <one of the categories from above>
Status: open
Created: YYYY-MM-DD
Context: <ticket ID or description of the work package>
Level: <0-3, minimum level at which this assumption is documented>
---

# <Short name of the assumption>

## Assumption

<What was assumed?>

## Rationale

<Why did the agent make this assumption?>

## Status Values

- `open` — documented, not yet reviewed
- `accepted` — explicitly confirmed by the user
- `rejected` — assumption was wrong, has been corrected
- `needs-validation` — must be clarified urgently

## Notes

- Do not create a file for things the user explicitly specified
- Do not create a duplicate — update the status of an existing file instead
- After documenting: brief summary in chat ("I documented X assumptions: ASSUMPTION-001, ASSUMPTION-002")
- Group multiple small assumptions from the same context into one file
```
