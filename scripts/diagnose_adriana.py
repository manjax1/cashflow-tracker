#!/usr/bin/env python3
"""Diagnose why an Adriana rental file wasn't picked up by the daily sync.

The daily job discovers Adriana files with a STRICT filter (see
adriana_parser.list_unprocessed_adriana_files):

  1. The file must live in the folder whose id is ADRIANA_FOLDER_ID (a specific
     folder id baked into the code — NOT just any folder named "Adriana Ledger").
  2. The file NAME must match:  Adriana Managed Properties Ledger - <Month> <Year>
     with a FULL month name (e.g. "August 2026", not "Aug 2026").
  3. The ledger's _Meta must not already have adriana_processed:<YYYY-MM> = true.
  4. The file must be a real .xlsx or .csv the service account can download —
     a NATIVE Google Sheet (mimeType application/vnd.google-apps.spreadsheet)
     is discovered but fails to parse (unsupported MIME), and is skipped.

This script checks all four against your live Drive + master ledger and prints a
verdict. Run it from the repo root in the environment your daily sync uses.

    python scripts/diagnose_adriana.py \
        --file 1xfiG93T32Wyzs1UbDMqr1tweff9Ncym6      # the Aug file you uploaded
    # optionally also inspect the folder you *think* you uploaded to:
    python scripts/diagnose_adriana.py --file <id> --folder <your_folder_id>
"""
import argparse
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

import openpyxl  # noqa: E402
from drive_sync import get_drive_service, download_ledger  # noqa: E402
import adriana_parser as AP  # noqa: E402


def check_name(name):
    pair = AP.parse_month_year(name)
    if not pair:
        return False, "no recognizable month+year in the filename"
    year, month = pair
    return True, f"matches → {year}-{month:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="1xfiG93T32Wyzs1UbDMqr1tweff9Ncym6",
                    help="Drive file id of the Adriana month file to inspect")
    ap.add_argument("--folder", default=None,
                    help="also list this folder id (e.g. the folder you uploaded to)")
    args = ap.parse_args()
    svc = get_drive_service()

    print(f"Code scans ADRIANA_FOLDER_ID = {AP.ADRIANA_FOLDER_ID}\n")

    # ── 1. The specific file ───────────────────────────────────────────────
    print("── FILE UNDER INVESTIGATION ─────────────────────────────")
    try:
        meta = svc.files().get(
            fileId=args.file,
            fields="id,name,mimeType,parents,trashed,owners(emailAddress)").execute()
    except Exception as e:
        print(f"  ✗ Cannot read file {args.file}: {e}")
        print("  → The service account can't see it — the file (or its folder) must be")
        print("    shared with the service-account email in your GOOGLE_SERVICE_ACCOUNT_JSON")
        print("    (rental-ledger-sync@…​.iam.gserviceaccount.com).")
        meta = None
    if meta:
        print(f"  name      : {meta['name']!r}")
        print(f"  mimeType  : {meta['mimeType']}")
        print(f"  parents   : {meta.get('parents')}")
        print(f"  trashed   : {meta.get('trashed')}")
        ok_name, why = check_name(meta["name"])
        print(f"  name check: {'✓' if ok_name else '✗'} {why}")
        in_folder = AP.ADRIANA_FOLDER_ID in (meta.get("parents") or [])
        print(f"  in scanned folder: {'✓ yes' if in_folder else '✗ NO — file is in a different folder'}")
        native = meta["mimeType"] == "application/vnd.google-apps.spreadsheet"
        parseable = ("spreadsheetml" in meta["mimeType"]) or ("csv" in meta["mimeType"]) or meta["mimeType"] == "text/plain"
        if native:
            print("  format    : ✗ NATIVE Google Sheet — the parser only handles uploaded .xlsx/.csv;")
            print("              re-upload as .xlsx (or File ▸ Download ▸ .xlsx and put that in the folder).")
        else:
            print(f"  format    : {'✓ downloadable (' + meta['mimeType'] + ')' if parseable else '✗ unsupported: ' + meta['mimeType']}")
        # dry-run parse if downloadable
        if parseable and ok_name:
            yr, mo = AP.parse_month_year(meta["name"])
            fm = {"file_id": args.file, "name": meta["name"], "mime_type": meta["mimeType"],
                  "year": yr, "month": mo}
            try:
                txns = AP.parse_adriana_file(svc, fm)
                total = sum(t["amount"] for t in txns)
                print(f"  dry-run parse: {len(txns)} transactions, total ${total:,.2f}")
                if not txns:
                    print("              → 0 rows parsed: header row not found, or Property/Payee "
                          "names don't match the mapping. Check column headers (Date/Property/Income/Expense).")
            except Exception as e:
                print(f"  dry-run parse ERROR: {e}")

    # ── 2. What the code's folder actually contains ────────────────────────
    for label, fid in [("CODE'S SCANNED FOLDER", AP.ADRIANA_FOLDER_ID)] + (
            [("YOUR --folder", args.folder)] if args.folder else []):
        print(f"\n── {label} ({fid}) ──────────────────")
        try:
            fs = svc.files().list(q=f"'{fid}' in parents and trashed=false",
                                  fields="files(id,name,mimeType)").execute().get("files", [])
            if not fs:
                print("  (empty or not visible to the service account)")
            for f in fs:
                ok, _ = check_name(f["name"])
                print(f"  [{'✓' if ok else '✗'}] {f['name']!r}  ({f['mimeType'].split('.')[-1]})")
        except Exception as e:
            print(f"  ✗ cannot list: {e}")

    # ── 3. Already-processed flag in the master ledger ─────────────────────
    print("\n── _Meta processed flags (master ledger) ────────────────")
    try:
        tmp = os.path.join(tempfile.gettempdir(), "adriana_diag_ledger.xlsx")
        download_ledger(os.environ["GOOGLE_DRIVE_FILE_ID"], tmp)
        wb = openpyxl.load_workbook(tmp)
        for ym in ("2026-08",):
            flag = AP._meta_flag_is_set(wb, f"adriana_processed:{ym}")
            print(f"  adriana_processed:{ym} = {flag}"
                  + ("   ← already marked done; sync will SKIP it (clear it to reprocess)" if flag else ""))
    except Exception as e:
        print(f"  (could not read _Meta: {e})")


if __name__ == "__main__":
    main()
