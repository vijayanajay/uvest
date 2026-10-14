# Trail Learnings (`.trail/learnings/`)

This directory stores cross-session learnings, architectural decisions, ruled-out alternatives, and warnings left by coding agents (such as Freebuff, Claude Code, Cursor, Codex, and Gemini).

## Purpose
- **Prevent exploration amnesia:** Before modifying code, agents inspect past learnings to avoid re-exploring dead ends or breaking prior architectural decisions.
- **Team & Agent Alignment:** These learnings are committed to git alongside code changes so that every teammate and every subsequent agent run benefits from them.

## Learning File Format
When an agent or developer concludes a significant task or resolves a tricky bug, save a markdown note (`YYYY-MM-DD-topic.md`):

```markdown
# Learning: [Descriptive Title]
- **Date:** YYYY-MM-DD
- **Scope / Files:** [e.g. scripts/6_fundamental_audit.py]
- **Decided:** [What approach was taken and why]
- **Ruled Out:** [What was tried/considered and why it was rejected]
- **Watch Out For:** [Edge cases, circuit breaker interactions, or invariants]
```
