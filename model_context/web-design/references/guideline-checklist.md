# Guideline Checklist

Use this checklist when reviewing frontend code. Start with the categories most likely to affect accessibility, correctness, user trust, or perceived performance.

## Table of Contents

- [Accessibility](#accessibility)
- [Focus States](#focus-states)
- [Forms](#forms)
- [Animation](#animation)
- [Typography](#typography)
- [Content Handling](#content-handling)
- [Images](#images)
- [Performance](#performance)
- [Navigation and State](#navigation-and-state)
- [Touch and Interaction](#touch-and-interaction)
- [Safe Areas and Layout](#safe-areas-and-layout)
- [Dark Mode and Theming](#dark-mode-and-theming)
- [Locale and i18n](#locale-and-i18n)
- [Hydration Safety](#hydration-safety)
- [Hover and Interactive States](#hover-and-interactive-states)
- [Content and Copy](#content-and-copy)
- [Anti-patterns](#anti-patterns)

## Accessibility

- Icon-only buttons need `aria-label`.
- Form controls need a `<label>` or `aria-label`.
- Interactive elements need keyboard handlers when native semantics are missing.
- Use `<button>` for actions and `<a>` or `<Link>` for navigation.
- Images need `alt`, or `alt=""` when decorative.
- Decorative icons need `aria-hidden="true"`.
- Async updates such as toasts or validation need `aria-live="polite"`.
- Prefer semantic HTML such as `<button>`, `<a>`, `<label>`, and `<table>` before extra ARIA.
- Keep headings hierarchical from `<h1>` through `<h6>` and include a skip link for main content.
- Add `scroll-margin-top` to heading anchors when sticky headers can obscure them.

## Focus States

- Interactive elements need a visible focus treatment such as `:focus-visible`.
- Never remove outlines without a clear focus replacement.
- Prefer `:focus-visible` over `:focus` for mouse interactions.
- Use `:focus-within` for compound controls when it improves clarity.

## Forms

- Inputs need `autocomplete` and meaningful `name` attributes.
- Use the correct `type` and `inputmode` for the data being entered.
- Never block paste with `preventDefault()`.
- Make labels clickable with `htmlFor` or by wrapping the control.
- Disable spellcheck on emails, codes, and usernames when appropriate.
- Ensure checkboxes and radios share a single hit target with their labels.
- Keep the submit button enabled until the request actually starts, then show progress.
- Show inline errors near fields and focus the first invalid field on submit.
- Use the ellipsis character in placeholders when showing an example pattern.
- Use `autocomplete="off"` on non-auth fields only when it avoids incorrect password-manager behavior.
- Warn before navigation if the user has unsaved changes.

## Animation

- Respect `prefers-reduced-motion`.
- Animate `transform` and `opacity` when possible.
- Never use `transition: all`; list explicit properties.
- Set the correct `transform-origin`.
- For SVG transforms, prefer a wrapper element such as `<g>` with the right origin settings.
- Ensure animations are interruptible when user input changes state mid-motion.

## Typography

- Use the ellipsis character instead of three periods where style matters.
- Use typographic quotes instead of straight quotes in polished UI copy.
- Use non-breaking spaces for compact units, shortcuts, and brand fragments when needed.
- End loading states with an ellipsis character.
- Use `font-variant-numeric: tabular-nums` for number columns and comparisons.
- Use `text-wrap: balance` or `text-wrap: pretty` on headings when appropriate.

## Content Handling

- Handle long text with truncation, clamping, or wrapping.
- Give flex children `min-w-0` when truncation should work inside flex layouts.
- Handle empty strings and arrays with a real empty state.
- Anticipate short, average, and very long user-generated content.

## Images

- Give `<img>` elements explicit `width` and `height`.
- Mark below-the-fold images as lazy-loaded when appropriate.
- Mark above-the-fold critical images with `priority` or `fetchpriority="high"` when supported.

## Performance

- Virtualize large lists or use `content-visibility` where it meaningfully reduces work.
- Avoid layout reads during render, including `getBoundingClientRect`, `offsetHeight`, `offsetWidth`, and `scrollTop`.
- Batch DOM reads and writes instead of interleaving them.
- Prefer uncontrolled inputs unless controlled inputs stay cheap on each keystroke.
- Add preconnect hints for CDN or asset domains when startup latency matters.
- Preload critical fonts and use `font-display: swap`.

## Navigation and State

- Reflect filters, tabs, pagination, and expanded state in the URL when deep-linking matters.
- Use real links for navigation so modified clicks work.
- Consider URL synchronization for stateful UI when users need durable sharing or navigation.
- Require confirmation or an undo path for destructive actions.

## Touch and Interaction

- Use `touch-action: manipulation` where it reduces double-tap delays.
- Set `-webkit-tap-highlight-color` intentionally.
- Use `overscroll-behavior: contain` in modals, drawers, and sheets when background scroll should not leak through.
- Disable text selection during drag interactions when appropriate.
- Use `autoFocus` sparingly and avoid it on mobile unless clearly justified.

## Safe Areas and Layout

- Account for `env(safe-area-inset-*)` in full-bleed layouts.
- Prevent unwanted horizontal scrollbars and fix overflowing content.
- Prefer flexbox or grid over JavaScript measurement for layout.

## Dark Mode and Theming

- Set `color-scheme: dark` on `<html>` when the page is actually dark themed.
- Match `<meta name="theme-color">` to the page background.
- Style native `<select>` controls explicitly in dark themes, especially on Windows.

## Locale and i18n

- Use `Intl.DateTimeFormat` for dates and times.
- Use `Intl.NumberFormat` for numbers and currency.
- Detect language from browser preferences or request headers, not IP.

## Hydration Safety

- Inputs with a controlled `value` need `onChange`, or should use `defaultValue`.
- Guard date and time rendering against server-client mismatches.
- Use `suppressHydrationWarning` only for truly expected mismatches.

## Hover and Interactive States

- Buttons and links need visible hover feedback when hover is available.
- Make hover, active, and focus states more prominent than the resting state.

## Content and Copy

- Prefer active voice.
- Use Title Case for headings and buttons when that matches the product style.
- Use numerals for counts.
- Prefer specific button labels over vague ones.
- Error messages should include the next step or likely fix.
- Prefer second person over first person.
- Use `&` instead of `and` only when space is tight and the product style allows it.

## Anti-patterns

- `user-scalable=no` or `maximum-scale=1`
- blocking paste with `onPaste` plus `preventDefault()`
- `transition: all`
- removing outlines without a focus replacement
- inline click navigation without a real link
- clickable `<div>` or `<span>` in place of `<button>`
- images without dimensions
- large `.map()` lists without virtualization or an equivalent mitigation
- form inputs without labels
- icon buttons without `aria-label`
- hardcoded date or number formatting instead of `Intl`
- `autoFocus` without a clear reason
