#!/bin/bash
# Claude Code WorktreeCreate hook.
#
# Branches new sub-agent worktrees off the parent worktree's CURRENT branch
# instead of the runtime default (which seeds from the repo's main branch).
# Without this hook, every `Agent({isolation: "worktree"})` invocation lands
# on stale code one fork-point behind the active branch — see awsk-noj.
#
# Hook input (stdin, JSON): { session_id, transcript_path, cwd, hook_event_name, name }
# Hook output (stdout): the absolute path of the newly created worktree.
# Non-zero exit aborts worktree creation.

set -euo pipefail

input="$(cat)"
worktree_name="$(echo "$input" | jq -r '.name')"
parent_path="$(echo "$input" | jq -r '.cwd')"

new_path="${parent_path}/.claude/worktrees/${worktree_name}"
current_branch="$(git -C "$parent_path" symbolic-ref --short HEAD)"

git -C "$parent_path" worktree add -b "$worktree_name" "$new_path" "$current_branch" >&2

echo "$new_path"
