# Execution Playbook

Use this reference to apply the Apex working style during implementation.

## Planning

- For non-trivial tasks, write a plan with checkable items in `tasks/todo.md` before editing code.
- Update the plan as you progress; mark items complete when they are actually done.
- Add a short review or results section to `tasks/todo.md` before finishing substantial work.
- If the approach stops making sense, stop and rewrite the plan instead of forcing the current path.

## Exploration

- Gather context from the codebase before deciding on a fix.
- Keep the active context small by reading only the files needed for the current step.
- Use parallel exploration when it helps, but keep each thread of work narrow and purposeful.

## Verification

- Never mark work complete without proof.
- Run the most relevant targeted tests, scripts, or manual checks for the change.
- Review logs and compare before-and-after behavior when that matters to correctness.
- Ask whether a staff engineer would accept the evidence you have gathered.

## Design Quality

- Prefer simple solutions that touch the fewest files needed.
- Fix root causes rather than layering on temporary patches.
- Pause on non-trivial changes and ask whether there is a more elegant implementation.
- If the current fix feels hacky, step back and implement the cleaner approach.

## Bug Fixing

- Treat bug reports as end-to-end ownership tasks.
- Use logs, errors, and failing tests as the trail to the root cause.
- Resolve the issue without making the user hand-hold the investigation.

## Lessons

- After a user correction, add the durable pattern to `tasks/lessons.md`.
- Write lessons as reusable rules that help avoid repeating the same mistake.
- Review relevant lessons at the start of related work when the topic overlaps.

## Collaboration

- Keep the user informed with high-level progress updates during substantial work.
- Explain changes at a high level as you move through the task.
- Optimize for momentum without hiding important tradeoffs.
