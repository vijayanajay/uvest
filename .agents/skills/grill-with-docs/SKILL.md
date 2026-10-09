---
name: grill-with-docs
description: "A relentless interview to sharpen a plan, architecture, or quantitative trade hypothesis before code is written, creating ADRs and updating domain glossary as we go."
---

# Grill With Docs

Conduct a relentless Socratic interview to stress-test ideas, designs, or trading rules against real market physics before any implementation begins.

## Process

1. **Interrogate Assumptions:**
   - Challenge why this feature or rule needs to exist.
   - Probe failure modes: what happens during market circuit halts, high volatility, illiquidity, data API timeouts?
   - Apply the Kailash Nadh test: "Is this code strictly necessary, or can a simpler metric / standard library cover it?"

2. **Establish Seams & Contracts:**
   - Define exact inputs, outputs, schemas, and test seams.
   - Ensure the proposal adheres to the strict constraints: No Options, Free-First data, SQLite local storage.

3. **Capture Decisions in Real-Time:**
   - Update `docs/agents/domain.md` with any new domain terms.
   - Record architectural choices as Architecture Decision Records (ADRs).
