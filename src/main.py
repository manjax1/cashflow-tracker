import os
import sys
import base64
import argparse
from datetime import date, timedelta, datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv()

from utils import clean_env, resolve_ledger_path
from plaid_client import PlaidClient
from filters import load_rules, categorize_batch
from openpyxl import load_workbook
from ledger_writer import write_spending_ledger, get_last_snapshot_month, set_last_snapshot_month, set_meta_flag
from email_notifier import send_sync_summary
from drive_sync import download_ledger, upload_ledger, get_drive_service

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "spending_rules.json")

# Records where the daily sync loaded its categorization rules from — surfaced in
# the sync email so a silent fallback to a stale RULES_JSON is immediately visible.
_RULES_SOURCE = {"source": "unknown", "count": 0, "ok": False}


def _resolve_ledger_path() -> tuple[str, bool]:
    return resolve_ledger_path()


def _load_rules_with_fallback() -> list:
    """Load categorization rules. Priority:
    1. Google Drive (RULES_DRIVE_FILE_ID) — updated via scripts/push_rules_to_drive.py,
       no redeploy, single source of truth alongside the ledger.
    2. RULES_JSON env var (legacy Railway).
    3. Local spending_rules.json file.
    """
    import json
    from filters import load_rules

    rules_drive_id = clean_env(os.getenv("RULES_DRIVE_FILE_ID"), "RULES_DRIVE_FILE_ID")
    if rules_drive_id:
        try:
            download_ledger(rules_drive_id, RULES_PATH)   # generic Drive file download
            rules = load_rules(RULES_PATH)
            print(f"✅ Loaded {len(rules)} rules from Drive")
            _RULES_SOURCE.update(source="Drive", count=len(rules), ok=True)
            return rules
        except Exception as e:
            print(f"⚠️  Drive rules load failed: {e} — falling back")
            _RULES_SOURCE["drive_error"] = str(e)

    rules_json_env = clean_env(os.getenv("RULES_JSON"), "RULES_JSON")
    if rules_json_env:
        try:
            rules = json.loads(rules_json_env)
            print(f"✅ Loaded {len(rules)} rules from RULES_JSON env var")
            _RULES_SOURCE.update(source="RULES_JSON env var (STALE fallback)", count=len(rules), ok=False)
            from filters import load_rules as _sort_rules
            return sorted(rules, key=lambda r: len(r["keyword"]), reverse=True)
        except Exception as e:
            print(f"⚠️  RULES_JSON parse failed: {e} — falling back to file")

    if os.path.exists(RULES_PATH):
        from filters import load_rules
        rules = load_rules(RULES_PATH)
        print(f"✅ Loaded {len(rules)} rules from {RULES_PATH}")
        _RULES_SOURCE.update(source="local file", count=len(rules), ok=not bool(os.getenv("RAILWAY_ENVIRONMENT")))
        return rules

    print("⚠️  No spending_rules.json and no RULES_JSON env var — using Plaid categories only.")
    _RULES_SOURCE.update(source="none", count=0, ok=False)
    return []


def run_sync(from_date: date = None, to_date: date = None) -> dict:
    is_cloud = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    plaid_env = clean_env(os.getenv("PLAID_ENV", "production"), "PLAID_ENV")
    resend_set = bool(clean_env(os.getenv("RESEND_API_KEY"), "RESEND_API_KEY"))
    token_set = bool(clean_env(os.getenv("PLAID_ACCESS_TOKEN"), "PLAID_ACCESS_TOKEN"))
    drive_set = bool(clean_env(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"), "GOOGLE_SERVICE_ACCOUNT_JSON"))
    print(f"[spending-tracker] env={plaid_env} cloud={is_cloud} resend={resend_set} token={token_set} drive={drive_set}")

    ledger_path, is_cloud = _resolve_ledger_path()
    file_id = clean_env(os.getenv("GOOGLE_DRIVE_FILE_ID"), "GOOGLE_DRIVE_FILE_ID")

    if is_cloud and file_id:
        try:
            download_ledger(file_id, ledger_path)
        except Exception:
            print("⚠️  Could not download existing ledger — will create fresh file.")

    client = PlaidClient()

    end = to_date if to_date else date.today()
    start = from_date if from_date else end - timedelta(days=7)
    print(f"Fetching transactions {start} → {end}")

    # ── Plaid items ──────────────────────────────────────────────────────
    # Each item is a separate Plaid login.
    #   • primary — your BofA login. Many accounts; filtered by an EXCLUDE
    #     list (everything is kept except the masks below).
    #   • additional items (e.g. Shalini's BofA login) use an INCLUDE list so
    #     we pull ONLY the named account(s). This is what prevents double-
    #     counting anything the primary item already covers — notably the
    #     joint checking x5799, which lives under both logins.
    #
    # 6450 (Tanusha credit card) is intentionally NOT excluded — tracked.
    PRIMARY_EXCLUDED_MASKS = {
        "4719": "Prateek checking (son)",
        "0043": "Tanusha checking (daughter)",
        "5663": "Adv Relationship Banking (2nd checking, no household activity)",
        "8305": "Primary mortgage loan account (payment already captured via checking debit)",
    }

    items = [{
        "name": "primary",
        "token": clean_env(os.getenv("PLAID_ACCESS_TOKEN"), "PLAID_ACCESS_TOKEN"),
        "mode": "exclude",
        "masks": set(PRIMARY_EXCLUDED_MASKS),
        "label_override": None,
        "required": True,
    }]

    # Shalini's BofA login — sync ONLY her Premium Rewards Visa (x3070).
    # Her joint checking (x5799) is already captured via the primary item,
    # so the include-list deliberately omits it to avoid double-counting.
    shalini_token = clean_env(os.getenv("PLAID_ACCESS_TOKEN_SHALINI"), "PLAID_ACCESS_TOKEN_SHALINI")
    if shalini_token:
        items.append({
            "name": "shalini",
            "token": shalini_token,
            "mode": "include",
            "masks": {"3070"},
            "label_override": "Shalini BoA VISA",
            "required": False,
        })

    raw_transactions: list = []
    account_map: dict = {}
    excluded_by_account: dict[str, list] = {mask: [] for mask in PRIMARY_EXCLUDED_MASKS}

    for item in items:
        token = item["token"]
        # Verify the token. The primary item is mandatory (locally we fall
        # back to the interactive link flow); additional items are best-effort
        # so an expired secondary login never blocks the household sync.
        if not token or not client.verify_access_token(token):
            if item["required"]:
                if is_cloud:
                    raise RuntimeError("Plaid access token invalid or missing. Run link flow locally first.")
                from link_flow import run_link_flow
                run_link_flow(client)
                token = clean_env(os.getenv("PLAID_ACCESS_TOKEN"), "PLAID_ACCESS_TOKEN")
            else:
                print(f"⚠️  Plaid item '{item['name']}' token missing/invalid — skipping "
                      f"(re-link with: python src/link_item.py {item['name']}).")
                continue

        item_txns = client.get_transactions(token, start, end)
        item_accounts = client.get_accounts(token)

        mode, masks, override = item["mode"], item["masks"], item.get("label_override")
        mask_by_id = {a["account_id"]: a.get("mask", "") for a in item_accounts}

        kept_ids = set()
        for a in item_accounts:
            mask = a.get("mask", "")
            keep = (mask in masks) if mode == "include" else (mask not in masks)
            if keep:
                account_map[a["account_id"]] = override or _account_label(a)
                kept_ids.add(a["account_id"])

        kept = dropped = 0
        for tx in item_txns:
            aid = tx.get("account_id", "")
            if aid in kept_ids:
                raw_transactions.append(tx)
                kept += 1
            else:
                dropped += 1
                # Primary-item exclusions are reported in the sync summary.
                mask = mask_by_id.get(aid, "")
                if item["name"] == "primary" and mask in excluded_by_account:
                    excluded_by_account[mask].append(tx)

        if item["name"] != "primary":
            print(f"[item:{item['name']}] {kept} txns kept "
                  f"(masks {sorted(masks)}), {dropped} from other accounts skipped")

    rules = _load_rules_with_fallback()

    included, excluded = categorize_batch(raw_transactions, rules, account_map)

    # Warn if any excluded transaction's name contains rental-related signals —
    # guards against broad exclusion rules accidentally suppressing rental income
    # or expenses (the same class of bug as the Hari Vasantapu keyword-shadowing
    # fix). CC payments are always intentional and excluded from this count.
    _RENTAL_NAME_SIGNALS = ("rent", "tenant", "lease")
    excluded_rental_count = sum(
        1 for tx in excluded
        if tx.get("category") != "Credit Card Payment"
        and any(sig in (tx.get("name") or "").lower() for sig in _RENTAL_NAME_SIGNALS)
    )

    result = write_spending_ledger(ledger_path, included)
    added, skipped = result["added"], result["skipped"]
    new_transactions = result.get("new_transactions", [])

    # Build summary
    category_totals: dict[str, float] = defaultdict(float)
    for tx in included:
        if tx.get("type") == "Expense":
            category_totals[tx["category"]] += tx.get("amount", 0.0)

    sorted_cats = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)
    total_spend = sum(v for _, v in sorted_cats)
    top_category = sorted_cats[0][0] if sorted_cats else "N/A"

    summary = {
        "date": str(end),
        "added": added,
        "skipped": skipped,
        "tx_count": added,
        "new_transactions": new_transactions,
        "total_spend": total_spend,
        "top_category": top_category,
        "top_categories": [{"category": c, "amount": a} for c, a in sorted_cats[:5]],
        "transactions": [
            {
                "date": tx.get("date"),
                "name": tx.get("name"),
                "account_label": tx.get("account_label"),
                "category": tx.get("category"),
                "amount": tx.get("amount"),
            }
            for tx in sorted(included, key=lambda t: t.get("date", ""), reverse=True)
        ],
        "excluded_rental_count": excluded_rental_count,
        "ledger_path": ledger_path,
        "plaid_env": plaid_env,
        "rules_source": dict(_RULES_SOURCE),
    }

    # ── Adriana rental file processing ───────────────────────────────────
    # Robust + loud: every file in the folder is classified (imported / unmatched
    # / errored), a missing expected month is detected, and each import is
    # sanity-checked against the prior month. Any problem is reported in a
    # prominent email section AND triggers an immediate standalone alert.
    adriana_report = {"imported": [], "unmatched": [], "errors": [],
                      "missing_months": [], "sanity": []}
    if drive_set:
        try:
            from adriana_parser import discover_adriana_files, parse_adriana_file
            drive_svc = get_drive_service()
            wb_check = load_workbook(ledger_path)
            disc = discover_adriana_files(drive_svc, wb_check)
            wb_check.close()
            adriana_report["unmatched"] = disc["unmatched"]

            for fm in disc["to_process"]:
                ym_key = f"{fm['year']}-{fm['month']:02d}"
                month_label = datetime(fm["year"], fm["month"], 1).strftime("%B %Y")
                try:
                    a_txns = parse_adriana_file(drive_svc, fm)
                    a_result = write_spending_ledger(ledger_path, a_txns)
                    set_meta_flag(ledger_path, f"adriana_processed:{ym_key}")
                    # Split into gross rent, deductions, and the NET deposit Adriana
                    # actually sends (gross − management fee − maintenance).
                    def _sum(pred):
                        return round(sum(t["amount"] for t in a_txns if pred(t)), 2)
                    gross = _sum(lambda t: t.get("type") == "Income")
                    mgmt_fee = _sum(lambda t: "Management Fee" in t.get("category", ""))
                    maintenance = _sum(lambda t: "Maintenance" in t.get("category", ""))
                    other_ded = _sum(lambda t: t.get("type") == "Expense"
                                     and "Management Fee" not in t.get("category", "")
                                     and "Maintenance" not in t.get("category", ""))
                    deductions = round(mgmt_fee + maintenance + other_ded, 2)
                    net_deposit = round(gross - deductions, 2)
                    prior_ym = _prev_ym(ym_key)
                    prior_net = _adriana_net(ledger_path, prior_ym)
                    entry = {"name": fm["name"], "ym": ym_key, "label": month_label,
                             "added": a_result["added"], "skipped": a_result["skipped"],
                             "rows": len(a_txns), "gross": gross, "mgmt_fee": mgmt_fee,
                             "maintenance": maintenance, "other_deduction": other_ded,
                             "net": net_deposit, "total": gross}
                    adriana_report["imported"].append(entry)
                    if len(a_txns) == 0:
                        adriana_report["errors"].append(
                            {"name": fm["name"], "error": "0 transactions parsed — header row "
                             "or Property/column names may not match the expected layout"})
                    elif prior_net > 0:
                        pct = (net_deposit - prior_net) / prior_net * 100
                        s = {"ym": ym_key, "total": net_deposit, "prior_ym": prior_ym,
                             "prior_total": prior_net, "pct": round(pct, 1),
                             "flag": abs(pct) >= 25, "basis": "net deposit"}
                        adriana_report["sanity"].append(s)
                    print(f"📋 Adriana {month_label}: {a_result['added']} added, "
                          f"{a_result['skipped']} skipped — gross ${gross:,.2f}, "
                          f"fees ${mgmt_fee:,.2f}, net ${net_deposit:,.2f}")
                except Exception as e_fm:
                    adriana_report["errors"].append({"name": fm["name"], "error": str(e_fm)})
                    print(f"⚠️  Adriana '{fm['name']}': {e_fm} — skipping")

            # Reconcile statement totals vs actual bank deposits (whenever a file
            # was imported this run).
            if adriana_report["imported"]:
                last = adriana_report["imported"][-1]
                adriana_report["reconciliation"] = _adriana_reconcile(
                    ledger_path, last["ym"], last.get("net", 0.0))
                rec = adriana_report["reconciliation"]
                print(f"🏦 Adriana reconcile: statements ${rec['cum_net']:,.2f} vs "
                      f"deposits ${rec['cum_deposits']:,.2f} (diff ${rec['diff']:,.2f}) — {rec['kind']}")

            # Missing expected month: previous calendar month with no file present
            # and not already processed.
            prev = _prev_ym(date.today().strftime("%Y-%m"))
            covered = disc["present_months"] | disc["processed_months"]
            if prev not in covered:
                adriana_report["missing_months"].append(prev)
                print(f"⚠️  Adriana: no file found for {prev} (expected last month's ledger)")

            # Immediate standalone alert the moment a problem is detected.
            if (adriana_report["errors"] or adriana_report["unmatched"]
                    or adriana_report["missing_months"]
                    or adriana_report.get("reconciliation", {}).get("flag")):
                try:
                    from email_notifier import send_adriana_alert
                    send_adriana_alert(adriana_report, str(end))
                except Exception as e_alert:
                    print(f"⚠️  Adriana alert email failed (non-fatal): {e_alert}")
        except Exception as e_adriana:
            adriana_report["errors"].append({"name": "(discovery)", "error": str(e_adriana)})
            print(f"⚠️  Adriana sync failed (non-fatal): {e_adriana}")
    summary["adriana"] = adriana_report

    # Keep Adriana per-property rows excluded from net in the stored file so the
    # spreadsheet Monthly Summary never double-counts them against the bank deposit.
    _adr_healed = _exclude_adriana_from_net(ledger_path)
    if _adr_healed:
        print(f"🩹 Re-excluded {_adr_healed} Adriana row(s) from net (had been re-included)")
        summary["adriana"]["net_exclusion_healed"] = _adr_healed

    # ── Costco pending-receipt reconciliation ────────────────────────────
    # Receipts uploaded before their charge posted are queued in the ledger;
    # now that fresh charges are in, try to auto-split them.
    try:
        from agent.costco import reconcile_pending
        pend = reconcile_pending(ledger_path)
        summary["costco_pending"] = pend
        if pend.get("split") or pend.get("expired") or pend.get("still_pending"):
            print(f"🧾 Costco pending: split {pend['split']}, expired {pend.get('expired', 0)}, "
                  f"{pend['still_pending']} still awaiting a charge")
    except Exception as e_costco:
        print(f"⚠️  Costco pending reconcile failed (non-fatal): {e_costco}")

    # ── Rent status sheets (pending rent + arrears per property) ─────────
    try:
        from ledger_writer import write_rent_sheets
        rent_totals = write_rent_sheets(ledger_path)
        summary["rent"] = rent_totals
        print(f"🏠 Rent status: pending ${rent_totals['pending_this_month']:,.2f} this month, "
              f"arrears ${rent_totals['total_arrears']:,.2f} across {rent_totals['properties']} properties")
    except FileNotFoundError:
        print("🏠 Rent status skipped — rent_roll.json not found")
    except Exception as e_rent:
        print(f"⚠️  Rent status build failed (non-fatal): {e_rent}")

    need_snapshot    = False
    snapshot_attachment: list[dict] | None = None
    year_month = date.today().strftime("%Y-%m")

    if is_cloud and file_id:
        upload_ok = False
        try:
            upload_ledger(file_id, ledger_path)
            upload_ok = True
        except Exception as e:
            print(f"⚠️  Drive upload failed: {e}")

        if upload_ok:
            try:
                wb_meta = load_workbook(ledger_path, read_only=True)
                last = get_last_snapshot_month(wb_meta)
                wb_meta.close()
                if last != year_month:
                    need_snapshot = True
                    with open(ledger_path, "rb") as _f:
                        encoded = base64.b64encode(_f.read()).decode()
                    snapshot_attachment = [{
                        "filename": f"cashflow-tracker-snapshot-{year_month}.xlsx",
                        "content": encoded,
                    }]
                    print(f"📸 New month ({year_month}) — ledger will be attached to sync email.")
                else:
                    print(f"📸 Snapshot already sent for {year_month} — no attachment this run.")
            except Exception as e:
                print(f"⚠️  _Meta read failed (non-fatal): {e}")

    email_ok = False
    try:
        send_sync_summary(summary, attachments=snapshot_attachment)
        email_ok = True
    except Exception as e:
        print(f"⚠️  Email failed: {e}")

    if need_snapshot and email_ok and is_cloud and file_id:
        try:
            wb_meta = load_workbook(ledger_path)
            set_last_snapshot_month(wb_meta, year_month)
            wb_meta.save(ledger_path)
            upload_ledger(file_id, ledger_path)
            print(f"📸 _Meta updated and re-uploaded for {year_month}")
        except Exception as e:
            print(f"⚠️  _Meta update/re-upload failed (non-fatal): {e}")

    excl_parts = [
        f"{len(txns)} from {mask} ({PRIMARY_EXCLUDED_MASKS[mask]})"
        for mask, txns in excluded_by_account.items() if txns
    ]
    if excl_parts:
        print(f"Excluded — {', '.join(excl_parts)}")
    print(f"✅ Sync complete — {added} added, {skipped} skipped, ${total_spend:,.2f} total spend")
    return summary


def _prev_ym(ym: str) -> str:
    """'2026-09' -> '2026-08' (calendar previous month)."""
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y-1}-12" if m == 1 else f"{y}-{m-1:02d}"


def _adriana_month_total(ledger_path: str, ym: str) -> float:
    """Sum of Adriana transaction amounts already in the ledger for a month,
    identified by the SourceRef prefix 'adriana:<ym>:'. Used to sanity-check a
    freshly imported month against the prior one. Returns 0.0 if none/unreadable."""
    try:
        wb = load_workbook(ledger_path, read_only=True)
        ws = wb["Transactions"]
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        ref_i = header.index("SourceRef")
        amt_i = header.index("Amount")
        total = 0.0
        prefix = f"adriana:{ym}:"
        for row in ws.iter_rows(min_row=2, values_only=True):
            ref = row[ref_i] if ref_i < len(row) else None
            if ref and str(ref).startswith(prefix):
                try:
                    total += abs(float(row[amt_i]))
                except (TypeError, ValueError):
                    pass
        wb.close()
        return round(total, 2)
    except Exception:
        return 0.0


def _exclude_adriana_from_net(ledger_path: str) -> int:
    """Self-heal: ensure Adriana per-property rows are stored IncludeInNet=False,
    so the spreadsheet Monthly Summary never double-counts (the app already
    excludes them at load, but the sheet reads the stored flag). Idempotent;
    returns how many rows it had to fix (>0 means something re-included them)."""
    try:
        wb = load_workbook(ledger_path)
        ws = wb["Transactions"]
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        ref_i, inc_i = header.index("SourceRef"), header.index("IncludeInNet")
        fixed = 0
        for row in ws.iter_rows(min_row=2):
            ref = row[ref_i].value
            if ref and str(ref).lower().startswith("adriana:") \
                    and row[inc_i].value not in (False, "FALSE", 0, "0"):
                row[inc_i].value = False
                fixed += 1
        if fixed:
            wb.save(ledger_path)
        wb.close()
        return fixed
    except Exception as e:
        print(f"⚠️  Adriana net-exclusion self-heal failed (non-fatal): {e}")
        return 0


def _adriana_reconcile(ledger_path: str, latest_ym: str, latest_net: float) -> dict:
    """Reconcile Adriana statement totals against the actual bank deposits.

    Compares CUMULATIVE statement net (all 'adriana:' rows: income − fees −
    maintenance) against CUMULATIVE branch deposits ('BOFA FIN CTR … DEPOSIT'
    categorized Rental-Income) — timing-agnostic, so a deposit landing in the
    next month doesn't create a false discrepancy. Flags when they diverge beyond
    tolerance, allowing for the most-recent month's deposit possibly not having
    posted yet. Positive diff = bank received MORE than statements show.
    """
    TOL = 25.0
    try:
        wb = load_workbook(ledger_path, read_only=True)
        ws = wb["Transactions"]
        h = [str(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1))]
        di, ni, ci, ai, ti, si = (h.index(x) for x in
                                  ["Date", "Description", "Category", "Amount", "Type", "SourceRef"])
        cum_net = cum_dep = 0.0
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r[di] is None:
                continue
            desc, cat, sref = str(r[ni] or ""), str(r[ci] or ""), str(r[si] or "")
            try:
                amt = abs(float(r[ai] or 0))
            except (TypeError, ValueError):
                continue
            if sref.lower().startswith("adriana:"):
                cum_net += amt if str(r[ti]) == "Income" else -amt
            elif "BOFA FIN CTR" in desc and "DEPOSIT" in desc and "rental" in cat.lower():
                cum_dep += amt
        wb.close()
        cum_net, cum_dep = round(cum_net, 2), round(cum_dep, 2)
        diff = round(cum_dep - cum_net, 2)                 # + = bank exceeds statements
        # The just-processed month's deposit may not have posted yet; allow that.
        if diff > TOL:
            flag, kind = True, "bank deposits EXCEED statement totals"
        elif diff < -(latest_net + TOL):
            flag, kind = True, "bank deposits fall SHORT of statement totals"
        else:
            flag, kind = False, "reconciled"
        return {"cum_net": cum_net, "cum_deposits": cum_dep, "diff": diff,
                "flag": flag, "kind": kind, "tol": TOL, "latest_ym": latest_ym}
    except Exception as e:
        return {"flag": False, "kind": f"reconcile skipped: {e}", "diff": 0.0,
                "cum_net": 0.0, "cum_deposits": 0.0, "tol": TOL, "latest_ym": latest_ym}


def _adriana_net(ledger_path: str, ym: str) -> float:
    """Net Adriana payout for a month = income − expenses among 'adriana:<ym>:'
    rows (what Adriana actually deposits). 0.0 if none/unreadable."""
    try:
        wb = load_workbook(ledger_path, read_only=True)
        ws = wb["Transactions"]
        header = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        ref_i, amt_i, typ_i = header.index("SourceRef"), header.index("Amount"), header.index("Type")
        net = 0.0
        prefix = f"adriana:{ym}:"
        for row in ws.iter_rows(min_row=2, values_only=True):
            ref = row[ref_i] if ref_i < len(row) else None
            if ref and str(ref).startswith(prefix):
                try:
                    a = abs(float(row[amt_i]))
                    net += a if str(row[typ_i]) == "Income" else -a
                except (TypeError, ValueError):
                    pass
        wb.close()
        return round(net, 2)
    except Exception:
        return 0.0


def _account_label(account: dict) -> str:
    subtype = account.get("subtype", "").lower()
    if "checking" in subtype:
        return "Checking"
    if "credit" in subtype:
        return "Credit Card"
    return account.get("name", "Unknown")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Spending Tracker sync")
    parser.add_argument(
        "--from-date",
        help="Start date for transaction fetch (YYYY-MM-DD). Defaults to 7 days ago.",
    )
    args = parser.parse_args()

    from_date = None
    if args.from_date:
        try:
            from_date = date.fromisoformat(args.from_date)
        except ValueError:
            parser.error("--from-date must be YYYY-MM-DD")
        if from_date > date.today():
            parser.error("--from-date cannot be in the future")
        if (date.today() - from_date).days > 730:
            print("⚠️  Warning: requesting more than 2 years of history — Plaid may limit results.")

    run_sync(from_date)
