# Prompt Templates

This directory stores all runtime prompts as markdown templates rendered by `PromptManager`.

## Format

- Template engine: Jinja2
- Variable syntax: `{{ variable_name }}`
- Missing variables: fail fast (`StrictUndefined`)

## Template Map

- `agent/reverse_expert_system.md`: Main policy-loop system prompt
- `agent/subagent_user.md`: Subagent user prompt body
- `agent/precompression_notice.md`: Soft-threshold precompression guidance
- `agent/policy_compress_snapshot.md`: Fallback 8-block compressed snapshot
- `distiller/system.md`: Context distiller system prompt
- `distiller/user.md`: Context distiller user prompt
- `subagents/*.md`: Subagent profile system prompts
  - includes `subagents/idapython_executor.md` and `subagents/idapython_executor_no_kb.md`

## Variables

- `agent/subagent_user.md`
  - `user_request`, `task`, `context`
- `agent/precompression_notice.md`
  - `iteration`, `reason`, `policy_messages`, `policy_tokens_est`, `prunable_rows_md`
- `agent/policy_compress_snapshot.md`
  - `user_request`, `iteration`, `key_technical_concepts`, `pending_tasks`, `current_work`, `optional_next_step`, `handoff_anchors`
- `distiller/user.md`
  - `iteration`, `user_request`, `task_board_md`, `knowledge_md`, `context_md`, `history_md`
- `subagents/*.md`
  - `profile_name`

## Conventions

- Keep output concise, executable, and evidence-driven.
- Keep markdown structure stable to reduce behavior drift.
- Tool semantics come from each tool's docstring bound via `bind_tools`; prompts should focus on call strategy instead of duplicating parameter schema.
- Add new profile prompts under `subagents/` with filename as profile name.
