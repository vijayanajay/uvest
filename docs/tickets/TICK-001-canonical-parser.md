# TICK-001: Canonical Ingestion Parser & Gatekeeper

**Status:** FROZEN  
**Priority:** P0  
**Test Seam:** `tests/test_0_ingest_reco.py`  
**Target Script:** `scripts/0_ingest_reco.py`  
**Output Artifact:** `current_trade.json`  
**Pipeline Contract:** `BRD.md` §9.1 (stage 0)  

### 1. Goal & Context
Parse raw user recommendation (unformatted copy-pasted text or mobile screenshot) into canonical `current_trade.json`. Enforce strict pre-flight gatekeeper checks: reject options immediately, check NSE MWPL F&O ban list, and check SEBI ASM/GSM surveillance lists.

### 2. Frozen Specification Reference
- `BRD.md Section 3.1, 3.2, 3.3`
- Input: String or Image file path via `--input`
- Output: `current_trade.json` (canonical schema)

### 3. TDD Acceptance Criteria
- [ ] Accepts unformatted text with Symbol, Type, Entry Range, Live Price, SL, Target.
- [ ] Strict Options Gate: If instrument is `CE`, `PE`, or `OPT`, exits with error `ERR_OPTIONS_PROHIBITED` and exit code 2.
- [ ] Checks mock or cached MWPL ban list for Futures: flags `ban_status: true` if banned.
- [ ] Validates lot sizes and calculates total initial margin exposure.
