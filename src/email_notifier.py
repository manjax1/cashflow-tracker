import os
import sys
import smtplib
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from utils import clean_env


def _ym_label(ym: str) -> str:
    try:
        from datetime import datetime as _dt
        return _dt.strptime(ym + "-01", "%Y-%m-%d").strftime("%B %Y")
    except Exception:
        return ym


def _adriana_section(report: dict, force: bool = False) -> str:
    """Prominent Adriana rental-import block. Red when anything needs attention
    (errored/unmatched files, or a missing month); otherwise a compact green
    confirmation of what imported. Returns '' when there's nothing to say and
    not forced."""
    report = report or {}
    errors = report.get("errors", [])
    unmatched = report.get("unmatched", [])
    missing = report.get("missing_months", [])
    imported = report.get("imported", [])
    sanity = report.get("sanity", [])
    has_problem = bool(errors or unmatched or missing)
    if not (has_problem or imported or force):
        return ""

    def _li(items):
        return "".join(f"<li style='margin:3px 0'>{x}</li>" for x in items)

    def _amounts(i):
        # gross rent − management fee (− maintenance) = net deposit (Adriana's check)
        if i.get("net") is None:
            return f"${i.get('total', 0):,.2f}"
        parts = [f"gross rent ${i.get('gross', 0):,.2f}"]
        if i.get("mgmt_fee"):
            parts.append(f"mgmt fee ${i['mgmt_fee']:,.2f}")
        if i.get("maintenance"):
            parts.append(f"maintenance ${i['maintenance']:,.2f}")
        if i.get("other_deduction"):
            parts.append(f"other ${i['other_deduction']:,.2f}")
        return " − ".join(parts) + f" = <b>net deposit ${i['net']:,.2f}</b>"

    if has_problem:
        blocks = []
        if errors:
            blocks.append("<div style='margin-top:8px'><b>Files that errored:</b><ul style='margin:4px 0 0 0;padding-left:20px'>"
                          + _li(f"<code>{e['name']}</code> — {e['error']}" for e in errors) + "</ul></div>")
        if unmatched:
            blocks.append("<div style='margin-top:8px'><b>Files in the folder that were skipped:</b><ul style='margin:4px 0 0 0;padding-left:20px'>"
                          + _li(f"<code>{u['name']}</code> — {u['reason']}" for u in unmatched) + "</ul></div>")
        if missing:
            blocks.append("<div style='margin-top:8px'><b>Missing month(s):</b><ul style='margin:4px 0 0 0;padding-left:20px'>"
                          + _li(f"No file found for <b>{_ym_label(m)}</b> (expected last month's ledger)." for m in missing) + "</ul></div>")
        if imported:
            blocks.append("<div style='margin-top:8px;color:#2c5c3f'><b>Imported this run:</b><ul style='margin:4px 0 0 0;padding-left:20px'>"
                          + _li(f"{i['label']}: {i['added']} new rows — {_amounts(i)}" for i in imported) + "</ul></div>")
        return ("<div style='margin:16px 0;padding:14px 16px;background:#fdecea;"
                "border:2px solid #a2472e;border-radius:8px;color:#7a2e1a;font-size:13.5px'>"
                "<div style='font-size:15px;font-weight:700;color:#a2472e'>⚠️ Adriana rental import needs attention</div>"
                + "".join(blocks) + "</div>")

    # Success-only summary
    rows = []
    for i in imported:
        rows.append(f"<li style='margin:3px 0'>{i['label']}: <b>{i['added']}</b> new rows "
                    f"(skipped {i['skipped']}) — {_amounts(i)}</li>")
    warn = [f"<div style='margin-top:6px;color:#8a6d00'>⚠️ {_ym_label(s['ym'])} net deposit "
            f"${s['total']:,.2f} is {s['pct']:+.0f}% vs {_ym_label(s['prior_ym'])} "
            f"${s['prior_total']:,.2f} — worth a look.</div>"
            for s in sanity if s.get("flag")]
    return ("<div style='margin:16px 0;padding:12px 14px;background:#eef6ee;"
            "border-left:4px solid #2E7D32;border-radius:4px;font-size:13px;color:#2c5c3f'>"
            "<b>📋 Adriana rental import</b>"
            "<ul style='margin:6px 0 0 0;padding-left:20px'>" + "".join(rows) + "</ul>"
            + "".join(warn) + "</div>")


def _build_html(summary: dict) -> str:
    run_date = summary.get("date", str(date.today()))
    total_spend = summary.get("total_spend", 0.0)
    tx_count = summary.get("tx_count", 0)
    added = summary.get("added", 0)
    top_category = summary.get("top_category", "N/A")
    categories = summary.get("top_categories", [])
    all_transactions = summary.get("transactions", [])
    new_txs = summary.get("new_transactions", [])
    excluded_rental = summary.get("excluded_rental_count", 0)
    ledger_path = summary.get("ledger_path", "")
    plaid_env = summary.get("plaid_env", "production")

    rows_html = "".join(
        f"<tr><td style='padding:4px 8px'>{c['category']}</td>"
        f"<td style='padding:4px 8px;text-align:right'>${c['amount']:,.2f}</td></tr>"
        for c in categories[:5]
    )

    tx_rows_html = "".join(
        f"<tr><td style='padding:3px 6px'>{t.get('date','')}</td>"
        f"<td style='padding:3px 6px'>{t.get('name','')}</td>"
        f"<td style='padding:3px 6px'>{t.get('account_label','')}</td>"
        f"<td style='padding:3px 6px'>{t.get('category','')}</td>"
        f"<td style='padding:3px 6px;text-align:right'>${t.get('amount',0):,.2f}</td></tr>"
        for t in all_transactions
    )

    new_tx_rows_html = "".join(
        f"<tr><td style='padding:3px 6px'>{t.get('date','')}</td>"
        f"<td style='padding:3px 6px'>{t.get('name','')}</td>"
        f"<td style='padding:3px 6px'>{t.get('account_label','')}</td>"
        f"<td style='padding:3px 6px'>{t.get('category','')}</td>"
        f"<td style='padding:3px 6px;text-align:right'>${t.get('amount',0):,.2f}</td></tr>"
        for t in new_txs
    )

    new_tx_section = (
        f"<h3 style='color:#2E7D32;margin-top:24px'>New Transactions ({added})</h3>"
        f"<table style='border-collapse:collapse;width:100%;font-size:12px'>"
        f"<tr style='background:#2E7D32;color:white'>"
        f"<th style='padding:4px 6px;text-align:left'>Date</th>"
        f"<th style='padding:4px 6px;text-align:left'>Description</th>"
        f"<th style='padding:4px 6px;text-align:left'>Account</th>"
        f"<th style='padding:4px 6px;text-align:left'>Category</th>"
        f"<th style='padding:4px 6px;text-align:right'>Amount</th>"
        f"</tr>{new_tx_rows_html}</table>"
    ) if added > 0 else ""

    excluded_note = (
        f"<p style='color:#cc0000;font-size:13px'>⚠️ {excluded_rental} rental-related "
        f"transactions excluded — verify they are correct.</p>"
        if excluded_rental > 0 else ""
    )

    # Costco pending-receipt reconciliation note — a reminder that a receipt you
    # uploaded earlier just matched a charge that posted, and was auto-split.
    costco_pending = summary.get("costco_pending") or {}
    costco_splits = [d for d in costco_pending.get("details", []) if d.get("status") == "split"]
    costco_note = ""
    if costco_splits:
        def _line(d):
            bd = ", ".join(f"{k} ${v:,.2f}" for k, v in (d.get("breakdown") or {}).items())
            return (f"<li style='margin:2px 0'>{d.get('date','')} · "
                    f"${abs(d.get('charge') or 0):,.2f} · {d.get('items', 0)} items"
                    + (f" — {bd}" if bd else "") + "</li>")
        still = costco_pending.get("still_pending", 0)
        still_txt = (f" &nbsp;·&nbsp; {still} receipt(s) still awaiting a charge"
                     if still else "")
        n = len(costco_splits)
        costco_note = (
            f"<div style='margin-top:18px;padding:10px 14px;background:#eef6ee;"
            f"border-left:4px solid #2E7D32;border-radius:4px;font-size:13px'>"
            f"<strong style='color:#2E7D32'>🧾 {n} Costco receipt{'s' if n != 1 else ''} "
            f"reconciled</strong>{still_txt}"
            f"<div style='color:#33503f;margin-top:4px'>A receipt you uploaded earlier matched "
            f"a charge that posted today, and was auto-split into item categories:</div>"
            f"<ul style='margin:6px 0 0 0;padding-left:18px;color:#33503f'>"
            + "".join(_line(d) for d in costco_splits) + "</ul></div>"
        )

    rs = summary.get("rules_source") or {}
    rules_note = ""
    if rs:
        ok = rs.get("ok")
        bg, fg, icon = (("#eef6ee", "#2c5c3f", "✓") if ok else ("#fdecea", "#a2472e", "⚠️"))
        extra = f" — Drive load failed: {rs['drive_error']}" if rs.get("drive_error") else ""
        rules_note = (
            f"<div style='margin-top:14px;padding:8px 12px;background:{bg};border-radius:4px;"
            f"font-size:12.5px;color:{fg}'>{icon} Categorization rules: "
            f"<b>{rs.get('count', 0)}</b> loaded from <b>{rs.get('source', '?')}</b>{extra}."
            + ("" if ok else " New/updated rules may not be applied — push rules to Drive and "
               "verify RULES_DRIVE_FILE_ID on Railway.") + "</div>"
        )

    from datetime import timedelta
    next_sync = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    return f"""
<html><body style='font-family:Arial,sans-serif;max-width:700px;margin:auto'>
<h2 style='color:#1F3864'>Cashflow Tracker — {run_date}</h2>
{_adriana_section(summary.get("adriana"))}
<table style='border-collapse:collapse;margin-bottom:16px'>
  <tr>
    <td style='padding:10px 20px;background:#f0f4fa;border-radius:6px;text-align:center'>
      <div style='font-size:22px;font-weight:bold;color:#1F3864'>${total_spend:,.2f}</div>
      <div style='font-size:11px;color:#666'>Total Spend This Run</div>
    </td>
    <td style='padding:10px 20px;background:#f0f4fa;border-radius:6px;text-align:center;margin-left:8px'>
      <div style='font-size:22px;font-weight:bold;color:#1F3864'>{tx_count}</div>
      <div style='font-size:11px;color:#666'>Transactions</div>
    </td>
    <td style='padding:10px 20px;background:#f0f4fa;border-radius:6px;text-align:center;margin-left:8px'>
      <div style='font-size:16px;font-weight:bold;color:#1F3864'>{top_category}</div>
      <div style='font-size:11px;color:#666'>Top Category</div>
    </td>
  </tr>
</table>
<h3>Top 5 Categories</h3>
<table style='border-collapse:collapse;width:100%;font-size:13px'>
  <tr style='background:#1F3864;color:white'>
    <th style='padding:6px 8px;text-align:left'>Category</th>
    <th style='padding:6px 8px;text-align:right'>Amount</th>
  </tr>
  {rows_html}
</table>
{new_tx_section}
{excluded_note}
{costco_note}
{rules_note}
<details style='margin-top:20px'>
  <summary style='cursor:pointer;font-weight:bold;color:#1F3864'>All Transactions Fetched This Run ({len(all_transactions)})</summary>
  <table style='border-collapse:collapse;width:100%;font-size:12px;margin-top:8px'>
    <tr style='background:#1F3864;color:white'>
      <th style='padding:4px 6px'>Date</th><th style='padding:4px 6px'>Description</th>
      <th style='padding:4px 6px'>Account</th><th style='padding:4px 6px'>Category</th>
      <th style='padding:4px 6px;text-align:right'>Amount</th>
    </tr>
    {tx_rows_html}
  </table>
</details>
<p style='font-size:11px;color:#888;margin-top:24px'>
  Ledger: {ledger_path} &nbsp;|&nbsp; Plaid: {plaid_env} &nbsp;|&nbsp; Next sync: {next_sync}
</p>
</body></html>
"""


def send_via_resend(recipient: str, subject: str, html_body: str, api_key: str, sender: str,
                    attachments: list[dict] | None = None) -> bool:
    payload: dict = {"from": sender, "to": [recipient], "subject": subject, "html": html_body}
    if attachments:
        payload["attachments"] = attachments
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return True


def send_via_gmail(recipient: str, subject: str, html_body: str, sender: str, password: str) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())
    return True


def send_via_sendgrid(recipient: str, subject: str, html_body: str, api_key: str, sender: str) -> bool:
    resp = requests.post(
        "https://api.sendgrid.com/v3/mail/send",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "personalizations": [{"to": [{"email": recipient}]}],
            "from": {"email": sender},
            "subject": subject,
            "content": [{"type": "text/html", "value": html_body}],
        },
        timeout=15,
    )
    resp.raise_for_status()
    return True


def _send_email(subject: str, html_body: str, attachments: list[dict] | None = None) -> bool:
    """Send one email through the provider fallback chain (Resend → Gmail →
    SendGrid). Returns True on the first success. Shared by the daily summary
    and the standalone Adriana alert."""
    recipient = clean_env(os.getenv("EMAIL_RECIPIENT"), "EMAIL_RECIPIENT")
    sender = clean_env(os.getenv("EMAIL_SENDER", "onboarding@resend.dev"), "EMAIL_SENDER")
    resend_key = clean_env(os.getenv("RESEND_API_KEY"), "RESEND_API_KEY")
    sendgrid_key = clean_env(os.getenv("SENDGRID_API_KEY"), "SENDGRID_API_KEY")
    gmail_pass = clean_env(os.getenv("EMAIL_PASS"), "EMAIL_PASS")

    try:
        if resend_key:
            send_via_resend(recipient, subject, html_body, resend_key,
                            "Cashflow Tracker <onboarding@resend.dev>", attachments)
            print("✅ Email sent via Resend")
            return True
    except Exception as e:
        print(f"⚠️  Resend failed: {e}")
    try:
        if gmail_pass:
            send_via_gmail(recipient, subject, html_body, sender, gmail_pass)
            print("✅ Email sent via Gmail SMTP")
            return True
    except Exception as e:
        print(f"⚠️  Gmail SMTP failed (expected on Railway): {e}")
    try:
        if sendgrid_key:
            send_via_sendgrid(recipient, subject, html_body, sendgrid_key, sender)
            print("✅ Email sent via SendGrid")
            return True
    except Exception as e:
        print(f"⚠️  SendGrid failed: {e}")
    print("⚠️  All email providers failed — no notification sent.")
    return False


def send_adriana_alert(report: dict, run_date: str) -> bool:
    """Standalone, immediate alert sent the moment Adriana processing hits a
    problem (errored file, unmatched file in the folder, or a missing month) —
    so it isn't buried in or dependent on the full daily summary."""
    n = (len(report.get("errors", [])) + len(report.get("unmatched", []))
         + len(report.get("missing_months", [])))
    subject = (f"⚠️ Adriana rental import needs attention — {run_date} "
               f"({n} issue{'s' if n != 1 else ''})")
    html = (f"<html><body style='font-family:Arial,sans-serif;max-width:640px;margin:auto'>"
            f"<h2 style='color:#a2472e'>Adriana rental import — action needed</h2>"
            f"<p style='font-size:13px;color:#555'>Detected during the {run_date} sync. "
            f"The daily summary email will also include this.</p>"
            f"{_adriana_section(report, force=True)}</body></html>")
    return _send_email(subject, html)


def send_sync_summary(summary: dict, attachments: list[dict] | None = None):
    run_date = summary.get("date", str(date.today()))
    added = summary.get("added", 0)
    tx_word = "transaction" if added == 1 else "transactions"
    ad = summary.get("adriana") or {}
    flag = ("  ⚠️ Adriana" if (ad.get("errors") or ad.get("unmatched")
                               or ad.get("missing_months")) else "")
    subject = f"Cashflow Tracker — {run_date} | {added} new {tx_word}{flag}"
    _send_email(subject, _build_html(summary), attachments)
