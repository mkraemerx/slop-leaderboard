---
ID: ASSUMPTION-012
Category: Technical
Status: open
Created: 2026-05-08
Context: FR-03 Commit Metrics — file category classification
Level: 1
---

# File category is determined by path and extension heuristics, not content

## Assumption

Each changed file is assigned to exactly one category (Code, Tests, Docs, Config/infra) using a priority-ordered set of path and extension rules. No file content is read for classification.

Priority order (first match wins):

1. **Tests** — filename matches `test_*`, `*_test.*`, `*_spec.*`, `*.test.*`, `*.spec.*`; or path contains a segment matching `test`, `tests`, `__tests__`, `spec`
2. **Docs** — extension in `{.md, .rst, .adoc, .txt}` or path starts with `docs/`
3. **Config/infra** — filename matches `Dockerfile*`, `*.yml`, `*.yaml`, `*.toml`, `*.ini`, `*.cfg`, `*.env*`; or path contains `.github/`, `ci/`, `deploy/`, `infra/`
4. **Code** — any recognised programming language extension (`.py`, `.js`, `.ts`, `.jsx`, `.tsx`, `.java`, `.go`, `.rb`, `.rs`, `.cpp`, `.c`, `.cs`, `.php`, `.swift`, `.kt`, etc.)
5. **Other** — everything else (images, binaries, lock files, etc.)

Lines changed in **Other** files are counted in totals but not shown in the category breakdown.

## Rationale

Content-based classification (e.g. detecting comment lines or reading AST) is expensive, language-specific, and fragile across diff formats. Path/extension heuristics are fast, language-agnostic, and correct for the vast majority of real-world repositories.

## Known limitations

- A file named `utils.py` inside a `tests/` directory is correctly classified as Tests (path rule wins over extension).
- A YAML file that is application config (e.g. `config/settings.yml`) and a GitHub Actions workflow (`.github/workflows/ci.yml`) both land in Config/infra — the distinction is not made.
- Generated files (e.g. `*.min.js`, `package-lock.json`) are classified as Code or Config but represent no human authorship; this is a known inaccuracy accepted for v1.

## Notes

- The classification rules should be defined as a single configurable mapping so they can be adjusted without code changes if edge cases arise.
