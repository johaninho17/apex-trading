---
name: vercel-react-best-practices
description: Apply Vercel's React and Next.js performance guidance when writing, reviewing, or refactoring React or Next.js code. Use for tasks involving components, App Router pages, server or client data fetching, Suspense and streaming, hydration safety, bundle size, re-render behavior, rendering performance, or JavaScript hot paths.
---

# Vercel React Best Practices

Use this skill as a performance-first checklist for React and Next.js work.

## Follow This Workflow

1. Classify the task as implementation, refactor, review, or debugging.
2. Start with the highest-impact categories before micro-optimizations:
   - `async-*`
   - `bundle-*`
   - `server-*`
3. Read [references/rule-catalog.md](references/rule-catalog.md) and load only the sections that match the task.
4. Prefer changes that improve user-perceived latency, bundle size, correctness, or maintainability with minimal churn.
5. Keep the repository's established patterns unless a rule fixes a clear performance problem or removes unnecessary work.

## Apply Rules By Task Type

### When Writing or Refactoring

- Pull independent async work forward and await it as late as possible.
- Prefer server-side data loading and serialization minimization before adding client fetching.
- Import directly from source modules instead of barrel files when bundle size matters.
- Add dynamic imports only for clearly heavy or conditionally used client code.
- Remove unnecessary effects, derived state, and non-primitive dependencies before reaching for memoization.
- Use transitions, deferred values, refs, or hoisted JSX only when they reduce visible work or interaction cost.

### When Reviewing

- Prioritize findings that can cause waterfalls, redundant fetching, hydration bugs, excessive client bundle cost, or repeated re-renders.
- Cite the matching rule id in each finding so the feedback is easy to trace.
- Prefer small, concrete fixes over broad rewrites.
- Skip low-value nits when the code path is cold or the expression is already cheap.

### When Debugging

- Check for serialized async chains before profiling lower-level render issues.
- Confirm whether work belongs on the server before optimizing a client component.
- Inspect repeated subscriptions, unstable dependencies, and inline component definitions when a tree re-renders too often.
- Look for long lists, SVG animation, hydration mismatches, and script loading when the issue is visual or interaction-related.

## Use Good Judgment

- Do not force memoization around cheap primitive calculations.
- Do not add caching, Suspense boundaries, or dynamic imports unless the tradeoff is justified by the code path.
- Do not move logic from event handlers into effects unless synchronization is truly required.
- Treat the rule catalog as a prioritization aid, not a mandate to change every matching line.

## Bundled Reference

- Read [references/rule-catalog.md](references/rule-catalog.md) for the full list of rule ids, priorities, and one-line summaries.
- Keep future deep examples or framework-version notes in `references/` instead of expanding this file.
