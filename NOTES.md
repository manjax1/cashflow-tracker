# Operational Notes

## Data Coverage Boundary: CSV Import vs. Live Plaid Sync

**Established 2026-06-26.**

The ledger has two distinct data sources that meet at a deliberate boundary:

| Source | Period |
|--------|--------|
| 18-file historical CSV import (BofA statements) | July 1, 2025 – March 26, 2026 |
| Live Plaid sync | March 27, 2026 – present |

### Why March 27, not April 1

BofA statement coverage ended March 26, 2026 (last day in the final statement file).
The live Plaid sync had only been triggered starting April 1, 2026, leaving a
**5-day gap (March 27–31, 2026)** with no data in either source.

That gap was closed via a one-time Railway sync call on 2026-06-26, pulling
March 27–31 transactions from Plaid retroactively.

### Rule for future full rebuilds

Any rebuild that wants complete, gap-free coverage from July 2025 to present
**must combine both steps in order**:

1. Run `src/run_full_import_with_report.py` — imports all 18 CSV files
   covering July 1, 2025 through March 26, 2026.

2. Run a live Plaid sync starting **March 27, 2026** (not April 1) through
   the present date. Use `src/main.py --from-date 2026-03-27` or the
   equivalent Railway/API trigger.

**Do not use April 1, 2026 as the Plaid sync start date for rebuilds.**
Always use March 27, 2026 as the live-sync boundary.

### Files involved in a full rebuild

- `src/run_full_import_with_report.py` — orchestrates the 18-file CSV import
- `src/aggregate_dryrun.py` — dry-run preview of the same 18 files (no writes)
- `SPENDING_LEDGER_FILE_PATH` in `.env` — canonical ledger location
  (currently `cashflow-tracker.xlsx` in the project root)
