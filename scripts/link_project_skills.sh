#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 系统知识类 skills（src/skills/ → .agents/skills/）──
SRC_SKILLS_DIR="$ROOT/src/skills"
AGENTS_DIR="$ROOT/.agents/skills"
mkdir -p "$AGENTS_DIR"

for skill_dir in "$SRC_SKILLS_DIR"/*/; do
  [ -f "$skill_dir/skill.yaml" ] || continue
  name="$(basename "$skill_dir")"
  dst="$AGENTS_DIR/$name"
  rm -rf "$dst"
  ln -s "../../src/skills/$name" "$dst"
  printf 'linked %s -> %s\n' "$dst" "$skill_dir"
done

# ── 开发调试类 skills（dev_skills/ → .claude/skills/ 与 .codex/skills/）──
DEV_SKILLS_DIR="$ROOT/dev_skills"
CLAUDE_SKILLS_DIR="$ROOT/.claude/skills"
CODEX_SKILLS_DIR="$ROOT/.codex/skills"
CLAUDE_CMD_DIR="$ROOT/.claude/commands"
mkdir -p "$CLAUDE_SKILLS_DIR" "$CODEX_SKILLS_DIR"

for skill_dir in "$DEV_SKILLS_DIR"/*/; do
  [ -f "$skill_dir/SKILL.md" ] || continue
  name="$(basename "$skill_dir")"

  for skills_root in "$CLAUDE_SKILLS_DIR" "$CODEX_SKILLS_DIR"; do
    dst="$skills_root/$name"
    rm -rf "$dst"
    ln -s "../../dev_skills/$name" "$dst"
    printf 'linked %s -> %s\n' "$dst" "$skill_dir"
  done

  old_cmd="$CLAUDE_CMD_DIR/$name.md"
  if [ -e "$old_cmd" ] || [ -L "$old_cmd" ]; then
    rm -f "$old_cmd"
    printf 'removed legacy %s\n' "$old_cmd"
  fi
done

printf 'done: system skills -> .agents/skills, dev skills -> .claude/skills + .codex/skills\n'
