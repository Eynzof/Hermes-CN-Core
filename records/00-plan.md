# Plan: Sync local fork onto upstream NousResearch/hermes-agent

Date: (fill in)
Working dir: C:/dev/Hermes-CN-Core
Current branch: dev-fix
Remotes: origin=Eynzof/Hermes-CN-Core, upstream=NousResearch/hermes-agent

## Situation analysis (facts)
- merge-base(dev-fix, upstream/main) = 3ef6bbd20 "chore: release v0.19.0 (2026.7.20) (#68175)"
- dev-fix is 348 ahead / 4194 behind upstream/main
- main is 358 ahead / 4194 behind upstream/main
- Of our 348 unique commits: 113 merge commits, 235 non-merge
- Straight three-way merge of upstream/main into dev-fix conflicts in ~629 files
  (workflows, docs, agent/*, tools/*, hermes_cli/*, tests/*, ...)

## Strategy decision
The fork's own documented sync convention (history shows "chore: 合并官方 vX.Y 更新"
merge commits) is a merge-based sync, and with a 4194-commit gap a literal
`git rebase` would replay 235 commits with hundreds of per-commit conflict
sessions. We therefore perform the sync as a merge of upstream/main into dev-fix
(= functionally "re-basing" our development onto the latest official code),
resolving all conflicts guided by FORK_NOTES.md. All decisions recorded in this dir.

## Steps
1. Backup branch + records dir        [done]
2. git merge upstream/main into dev-fix (no-commit first)
3. Categorize & resolve conflicts per FORK_NOTES.md (sub-agents in parallel batches)
4. Commit merge
5. Run official tests; fix errors
6. Write MERGE-RECORD.md
