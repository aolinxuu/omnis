# omnis — working agreement for Claude sessions

Hackathon repo (NVIDIA Spark Hack Seattle, Aug 14–16 2026). Three people, hard ownership boundaries.
These rules apply to every Claude session in this repo, regardless of who is driving.

## Ownership — do not cross without an explicit ask

| Area | Owner | Rule for Claude |
|---|---|---|
| `frontend/` (HUD, map, `data/sightings.json`, `CONTRACT.md`) | Emily | Only modify when the **current prompt explicitly asks** for a frontend change. Never touch it as a side effect of other work. |
| Everything else — pipeline/backend: capture, YOLO/tracking, VSS, routing, WebSocket server, Spark scripts | Aolin + third teammate | Only modify when the **current prompt explicitly asks** for backend work. Do not "helpfully" fix, refactor, or format backend files. |
| `CLAUDE.md`, `.gitignore`, `scripts/scope_check.sh`, `README.md` | shared | Small edits OK when the prompt is about repo tooling. |

"Explicitly asks" means the request names the area or a file in it. "Make the demo work" is not permission to edit both sides.
If a task seems to need a change on the other side of the boundary, stop and say so — do not make the change.

## The contract is frozen
`frontend/data/sightings.json` and `frontend/CONTRACT.md` define the message shapes between pipeline and frontend.
Do not change existing field names/types. Additive optional fields only, and only after all three people agree — say so in the commit message.

## Before every `git push` — scope scan (mandatory)
Run `scripts/scope_check.sh <frontend|backend|shared> [<second area>]` with the area(s) the prompt authorised.
It lists every file changed vs `origin/main` (committed, staged, and unstaged) grouped by area and exits non-zero if anything is outside the authorised area(s).
If it fails: do not push. Show the offending files and ask.

Also: never commit `data/`, `.venv/`, model weights, or credentials (see `.gitignore`).

## Etiquette
- Commit only when asked; push only when asked. One logical change per commit.
- Don't rewrite history on `main`. Don't force-push.
- Don't start long-running servers on the Spark without saying which tmux pane they live in.
