---
name: vercel-web-interface-guidelines
description: Review web UI code for Vercel-style interface guideline compliance. Use when auditing React, Next.js, HTML, CSS, or component-library files for accessibility, focus states, forms, motion, typography, content handling, images, performance, navigation, touch behavior, theming, hydration safety, or copy quality, especially when the task calls for terse file:line findings.
---

# Web Interface Guidelines

Use this skill to audit frontend code and return concise, high-signal findings.

## Follow This Workflow

1. Review the files named in `$ARGUMENTS` when the user provides a path or pattern.
2. If no explicit scope is provided, inspect the smallest relevant UI surface instead of the whole app.
3. Check high-impact categories first:
   - accessibility and semantics
   - forms and interaction safety
   - hydration and rendering safety
   - performance and layout stability
4. Read only the matching sections of [references/guideline-checklist.md](references/guideline-checklist.md).
5. Report only real issues, regressions, or notable gaps. Skip low-value commentary.

## Reporting Rules

- Group findings by file.
- Use `file:line` format for each finding.
- State the issue first and keep explanations brief unless the fix is non-obvious.
- Mention the violated guideline in plain language when it helps the user act quickly.
- If a file is clean, report `pass`.
- Do not add a preamble or a long summary before the findings.

## Output Shape

```text
## src/Button.tsx

src/Button.tsx:42 - icon button missing aria-label
src/Button.tsx:18 - input lacks label
src/Button.tsx:55 - animation missing reduced-motion handling
src/Button.tsx:67 - transition: all; list explicit properties

## src/Modal.tsx

src/Modal.tsx:12 - missing overscroll-behavior: contain
src/Modal.tsx:34 - use the ellipsis character instead of three periods

## src/Card.tsx

pass
```

## Use Good Judgment

- Prefer semantic, accessibility, or behavior bugs over stylistic nits.
- Do not demand every possible enhancement when the code path is low-risk or intentionally simple.
- Flag anti-patterns immediately when they affect accessibility, zooming, keyboard use, hydration, or large-list performance.
- Preserve the repository's existing design system unless a rule identifies a clear UX or correctness problem.

## Bundled Reference

- Read [references/guideline-checklist.md](references/guideline-checklist.md) for the full checklist by category.
- Keep future framework-specific notes or examples in `references/` instead of expanding this file.
