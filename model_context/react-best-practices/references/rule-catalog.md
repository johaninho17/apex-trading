# Rule Catalog

Use this file as the quick lookup for rule ids and priorities. Start with the highest-priority categories that match the task, then work downward only if the higher-impact checks are already addressed.

## Table of Contents

- [Eliminating Waterfalls](#eliminating-waterfalls)
- [Bundle Size Optimization](#bundle-size-optimization)
- [Server-Side Performance](#server-side-performance)
- [Client-Side Data Fetching](#client-side-data-fetching)
- [Re-render Optimization](#re-render-optimization)
- [Rendering Performance](#rendering-performance)
- [JavaScript Performance](#javascript-performance)
- [Advanced Patterns](#advanced-patterns)

## Eliminating Waterfalls

Priority: `CRITICAL`
Prefix: `async-`

- `async-defer-await` - Move `await` into the branch that actually needs the result.
- `async-parallel` - Use `Promise.all()` for independent work.
- `async-dependencies` - Use partial-dependency orchestration instead of serializing unrelated work.
- `async-api-routes` - Start promises early and await late in API routes.
- `async-suspense-boundaries` - Use Suspense boundaries to stream content instead of blocking the whole tree.

## Bundle Size Optimization

Priority: `CRITICAL`
Prefix: `bundle-`

- `bundle-barrel-imports` - Import directly from source modules instead of barrel files.
- `bundle-dynamic-imports` - Use `next/dynamic` for heavy client components.
- `bundle-defer-third-party` - Load analytics and logging after hydration when possible.
- `bundle-conditional` - Load modules only when the feature is active.
- `bundle-preload` - Preload likely-next code paths on hover or focus when it improves perceived speed.

## Server-Side Performance

Priority: `HIGH`
Prefix: `server-`

- `server-auth-actions` - Authenticate server actions with the same rigor as API routes.
- `server-cache-react` - Use `React.cache()` for per-request deduplication.
- `server-cache-lru` - Use an LRU cache for cross-request reuse when appropriate.
- `server-dedup-props` - Avoid serializing the same data multiple times into client props.
- `server-hoist-static-io` - Hoist static I/O like fonts and logos to module scope.
- `server-serialization` - Minimize data passed from server components to client components.
- `server-parallel-fetching` - Restructure server components to fetch in parallel.
- `server-after-nonblocking` - Use `after()` for non-blocking post-response work.

## Client-Side Data Fetching

Priority: `MEDIUM-HIGH`
Prefix: `client-`

- `client-swr-dedup` - Use SWR when automatic request deduplication fits the data flow.
- `client-event-listeners` - Deduplicate global event listeners.
- `client-passive-event-listeners` - Mark scroll and similar listeners as passive when safe.
- `client-localstorage-schema` - Version and minimize localStorage payloads.

## Re-render Optimization

Priority: `MEDIUM`
Prefix: `rerender-`

- `rerender-defer-reads` - Avoid subscribing to state that is only needed inside callbacks.
- `rerender-memo` - Extract expensive work into memoized components when the cost is real.
- `rerender-memo-with-default-value` - Hoist default non-primitive props.
- `rerender-dependencies` - Depend on primitive values in effects when possible.
- `rerender-derived-state` - Subscribe to derived booleans instead of raw values when only the boolean matters.
- `rerender-derived-state-no-effect` - Derive render-time state during render instead of in effects.
- `rerender-functional-setstate` - Use functional `setState` to keep callbacks stable.
- `rerender-lazy-state-init` - Pass an initializer function to `useState` for expensive setup.
- `rerender-simple-expression-in-memo` - Skip memoization for cheap primitive expressions.
- `rerender-move-effect-to-event` - Keep interaction logic in event handlers rather than effects.
- `rerender-transitions` - Use `startTransition` for non-urgent updates.
- `rerender-use-ref-transient-values` - Store transient, high-frequency values in refs.
- `rerender-no-inline-components` - Do not define components inside components.

## Rendering Performance

Priority: `MEDIUM`
Prefix: `rendering-`

- `rendering-animate-svg-wrapper` - Animate a wrapper element instead of the SVG node directly.
- `rendering-content-visibility` - Use `content-visibility` for long lists or below-the-fold sections.
- `rendering-hoist-jsx` - Hoist static JSX outside the component body.
- `rendering-svg-precision` - Reduce unnecessary SVG coordinate precision.
- `rendering-hydration-no-flicker` - Use an inline script or equivalent pattern to avoid client-only hydration flicker.
- `rendering-hydration-suppress-warning` - Suppress hydration warnings only for expected mismatches.
- `rendering-activity` - Use `Activity` for show and hide patterns when supported.
- `rendering-conditional-render` - Prefer a ternary over `&&` when falsey rendering can be ambiguous.
- `rendering-usetransition-loading` - Prefer `useTransition` for pending UI instead of manual loading flags when appropriate.
- `rendering-resource-hints` - Add React DOM resource hints for preload and preconnect cases.
- `rendering-script-defer-async` - Use `defer` or `async` on script tags.

## JavaScript Performance

Priority: `LOW-MEDIUM`
Prefix: `js-`

- `js-batch-dom-css` - Batch DOM and CSS changes instead of interleaving reads and writes.
- `js-index-maps` - Build a `Map` for repeated keyed lookups.
- `js-cache-property-access` - Cache repeated property access inside hot loops.
- `js-cache-function-results` - Cache repeated pure function results at module scope when reuse is meaningful.
- `js-cache-storage` - Cache repeated `localStorage` or `sessionStorage` reads.
- `js-combine-iterations` - Combine chained `filter` and `map` passes when the array is large or hot.
- `js-length-check-first` - Check length before expensive comparisons.
- `js-early-exit` - Return early to skip unnecessary work.
- `js-hoist-regexp` - Hoist `RegExp` construction out of loops.
- `js-min-max-loop` - Use a loop for min and max instead of sorting.
- `js-set-map-lookups` - Use `Set` or `Map` for repeated membership checks.
- `js-tosorted-immutable` - Use `toSorted()` when immutable sorting is required.
- `js-flatmap-filter` - Use `flatMap()` when mapping and filtering can be done in one pass.

## Advanced Patterns

Priority: `LOW`
Prefix: `advanced-`

- `advanced-event-handler-refs` - Store event handlers in refs when stable handler identity matters.
- `advanced-init-once` - Initialize app-level work once per app load.
- `advanced-use-latest` - Use a latest-value ref pattern for stable callback access to changing values.
