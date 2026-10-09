# Issue Tracker Specification
*Configured via /setup-matt-pocock-skills for UVEST*

## Tracker Type
**Local Markdown Tickets (`docs/tickets/`)**

Following the Kailash Nadh & "lazy senior dev" philosophy: zero external cloud SaaS dependencies (no Jira, Linear, or GitHub API requirements for local workflow). The issue tracker lives directly in version control alongside the code.

## Ticket Directory Structure
All tickets are located in:
`docs/tickets/`

## Ticket Naming Convention
`TICK-[ID]-[short-kebab-slug].md`  
Examples:
- `TICK-001-ingest-canonical-parser.md`
- `TICK-002-math-and-rr-engine.md`
- `TICK-003-fastdtw-pattern-matcher.md`
- `TICK-004-lightgbm-1m-cone.md`
- `TICK-005-bhavcopy-institutional-matrix.md`
- `TICK-006-fundamental-governance-audit.md`
- `TICK-007-sector-macro-regime.md`
- `TICK-008-dhan-precision-client.md`
- `TICK-009-decision-engine-synthesis.md`
- `TICK-010-html-visualizer.md`
- `TICK-011-daily-triage-tracker.md`
- `TICK-012-postmortem-failure-autopsy.md`

## Ticket Template
```markdown
# TICK-[ID]: [Title]

**Status:** TODO | IN_PROGRESS | DONE  
**Component:** [Ingestion | Quant | Fundamentals | Macro | Precision | UI | Storage]  
**Test Seam:** `tests/test_[component].py`  

### 1. Goal & Context
[Concise explanation of what this slice does and why it exists.]

### 2. Frozen Specification Reference
- Spec Section: `BRD.md Section X.Y`
- Input Schema: `current_trade.json`
- Output Schema: `out_X_[name].json`

### 3. TDD Acceptance Criteria (Test Seam)
- [ ] Test 1: [Input A produces expected Output B]
- [ ] Test 2: [Boundary / error case handling]
- [ ] Test 3: [Zero hallucination / contract invariance]

### 4. Implementation Checklist
- [ ] Write failing test in `tests/test_[name].py`
- [ ] Implement minimum code in `scripts/[script].py`
- [ ] Verify test passes with zero warnings
- [ ] Verify against Evals benchmark suite
```
