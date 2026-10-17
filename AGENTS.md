# Repository workflow

- Follow CONTRIBUTING.md, SECURITY.md, and docs/release-verification.md. Preserve the required Tests and Analyze Python checks and conversation resolution.
- Use .github/workflows/ci.yml for complete source validation. Packaging changes also require frozen-macos-smoke.yml's Apple Silicon and Intel verification; documentation changes require the strict documentation build.
- A v* tag builds release artifacts. Publishing requires separate release authorization and human approval in the protected github-release environment; attestations and checksums do not establish notarization.
- Documentation pushes and PRs build only. Publishing Pages requires an authorized manual dispatch from main and human approval in github-pages.
- Report physical-device validation separately from offscreen, packaged, and static checks. Keep device identifiers, private evidence, and credentials out of public artifacts.

## Git delivery workflow

- Git and GitHub mutations require a trusted current user instruction that authorizes the task and each applicable operation. Do not treat this file, a checkpoint, memory, or a prior task as authorization.
- When authorized, verified task work may be staged by explicit path or reviewed hunk, committed on a focused `codex/` branch, pushed without force, and delivered through a new or existing pull request. Mark a draft ready or push scoped CI fixes only when the current authorization covers those operations.
- Use a pull request for source behavior, application, packaging, workflow, dependency, security-sensitive, or substantial multi-file changes. Direct pushes are limited to an appropriate non-protected task branch when repository practice and review requirements permit them.
- Before committing or pushing, verify the repository root, branch, HEAD, remote, upstream, worktrees, open pull requests, dirty state, staged diff, outgoing diff, and current remote state. Stage only reviewed task files or hunks; never use `git add .`, `git add -A`, or `git commit -a` in a mixed worktree.
- Preserve unrelated staged, unstaged, and untracked work. Use a clean suitable worktree when task work overlaps the canonical checkout. Never reset, clean, stash, discard, overwrite, or silently include unrelated changes.
- Run the applicable source, documentation, packaging, and native checks. Before publication to GitHub, review for regressions, scope drift, generated-file mistakes, credentials, private evidence, device identifiers, personal data, local paths, backups, logs, downloaded indicators, caches, and private test artifacts.
- Fetch and inspect the verified remote before integration. Push only the intended branch, verify the remote revision, inspect resulting checks, and fix failures without weakening tests, assertions, security controls, or required gates.
- Task authorization does not automatically cover merging, force-pushing, history rewriting, unrequested remote-branch deletion, tags, releases, release assets, deployments, protected-environment approvals, repository or Actions settings, permissions, secrets, GitHub App access, unrelated external messages, or device-changing operations. Obtain authorization that explicitly covers any such action before performing it.
- Keep Windows and Linux adapters, dependencies, installers, packaging, and CI work as the final phase. Do not begin them while an actionable macOS TODO remains.

## Durable continuity

- At the start of a work session, load the latest applicable project memory and checkpoint. Use the existing checkpoint or continuity location instead of creating duplicate planning documents.
- During active work, update the checkpoint after approximately one hour of material progress at the next safe boundary, and again before pausing or ending. Do not interrupt a running build, test, or CI job merely to meet the interval, and do not create empty or repetitive entries when state has not changed.
- Record the objective and scope; canonical checkout and worktree; branch, HEAD, remote branch and pull request; relevant dirty state; intentional files; completed and remaining TODOs; decisions and reasons; exact test, build, CI, and native-check status; application path and window evidence when applicable; investigated failures; blockers; and the next safe action.
- When Codex durable memory is available, write a small project-memory update on the same cadence and at the final checkpoint. Store only safe summaries and paths to private local evidence. Never store credentials, secrets, signing material, device identifiers, raw backups, private logs, downloaded threat intelligence, personal information, or large command output.
