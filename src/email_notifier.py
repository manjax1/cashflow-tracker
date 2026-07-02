import os
import sys
import smtplib
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from utils import clean_env


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

    from datetime import timedelta
    next_sync = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")

    return f"""
<html><body style='font-family:Arial,sans-serif;max-width:700px;margin:auto'>
<h2 style='color:#1F3864'>Cashflow Tracker — {run_date}</h2>
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


def send_sync_summary(summary: dict, attachments: list[dict] | None = None):
    recipient = clean_env(os.getenv("EMAIL_RECIPIENT"), "EMAIL_RECIPIENT")
    sender = clean_env(os.getenv("EMAIL_SENDER", "onboarding@resend.dev"), "EMAIL_SENDER")
    resend_key = clean_env(os.getenv("RESEND_API_KEY"), "RESEND_API_KEY")
    sendgrid_key = clean_env(os.getenv("SENDGRID_API_KEY"), "SENDGRID_API_KEY")
    gmail_pass = clean_env(os.getenv("EMAIL_PASS"), "EMAIL_PASS")

    run_date = summary.get("date", str(date.today()))
    added = summary.get("added", 0)
    tx_word = "transaction" if added == 1 else "transactions"
    subject = f"Cashflow Tracker — {run_date} | {added} new {tx_word}"
    html_body = _build_html(summary)

    try:
        if resend_key:
            send_via_resend(recipient, subject, html_body, resend_key,
                            "Cashflow Tracker <onboarding@resend.dev>", attachments)
            print("✅ Email sent via Resend")
            return
    except Exception as e:
        print(f"⚠️  Resend failed: {e}")

    try:
        if gmail_pass:
            send_via_gmail(recipient, subject, html_body, sender, gmail_pass)
            print("✅ Email sent via Gmail SMTP")
            return
    except Exception as e:
        print(f"⚠️  Gmail SMTP failed (expected on Railway): {e}")

    try:
        if sendgrid_key:
            send_via_sendgrid(recipient, subject, html_body, sendgrid_key, sender)
            print("✅ Email sent via SendGrid")
            return
    except Exception as e:
        print(f"⚠️  SendGrid failed: {e}")

    print("⚠️  All email providers failed — sync completed but no notification sent.")
