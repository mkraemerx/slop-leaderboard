# Git Repo Leaderboard Dashboard — Requirements

## Context

The primary use case is a training or course setting: an instructor provides a template repository, authors work in their own clone of it (all bundled in one organisation) and complete exercises there. The dashboard tracks contribution activity across all participant repositories and enables comparison of exercise solutions between authors.

---

## Functional Requirements

| ID | Title |
|----|-------|
| [FR-01](requirements/FR-01.md) | Template & Participant Repository Tracking |
| [FR-02](requirements/FR-02.md) | Git Analysis |
| [FR-03](requirements/FR-03.md) | Commit Metrics |
| [FR-04](requirements/FR-04.md) | Exercises |
| [FR-05](requirements/FR-05.md) | Leaderboard |
| [FR-06](requirements/FR-06.md) | Solution Comparison |
| [FR-07](requirements/FR-07.md) | Authentication |
| [FR-08](requirements/FR-08.md) | Webhook |
| [FR-09](requirements/FR-09.md) | UI |

## Quality Requirements

| ID | Title |
|----|-------|
| [QR-01](requirements/QR-01.md) | Performance |
| [QR-02](requirements/QR-02.md) | Reliability |
| [QR-03](requirements/QR-03.md) | Portability |
| [QR-04](requirements/QR-04.md) | Maintainability |
| [QR-05](requirements/QR-05.md) | Security |

---

## Out of Scope (v1)

- Tracking more than one template repository simultaneously
- Semantic code analysis (detecting algorithmically equivalent solutions)
- Notifications (email, Slack, etc.)
- Configurable scoring formula
- Historical trend charts
- Multi-user / multi-tenant support
