"""The agent loop. ~40 lines of logic — this IS the agent.

Send messages + tool defs -> if the model calls tools, dispatch and feed
results back -> repeat until it produces text. Everything else in the
agentic-AI ecosystem is an elaboration of this loop."""

import json
import os
import time

import anthropic

from . import ledger
from .tools import TOOLS, dispatch, audit

MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-4-5")
MAX_TURNS = 10

SYSTEM = """You are a financial analyst agent for a household cashflow ledger.
Your job is to study income, expenses, and trends on a continuous basis across
all categories — rental properties are one dimension among several (categories
prefixed 'Rental - ' roll up into rental subtotals).

Rules:
- Use tools for ALL data and ALL arithmetic. Never estimate or compute figures
  yourself; get_cashflow_summary and get_trends return computed numbers.
- Cite specifics in answers: dates, amounts, transaction descriptions.
- If a question is ambiguous (which period? which account?), ask a brief
  clarifying question instead of guessing.
- Action tools (draft_email, recategorize_transaction) only create proposals
  that the user approves separately. Because that approval gate exists, do NOT
  ask for permission in chat before using them — when the user explicitly asks
  you to recategorize or draft something, call the tool directly and note that
  a proposal is awaiting their approval. Asking twice is redundant.
- Merchant identification: infer cautiously. Ambiguous strings (e.g. 'WM.COM'
  could be Walmart or Waste Management) should be flagged as ambiguous, not
  asserted confidently. Check transaction context (amount, recurrence) first.
- Category discipline: when recategorizing, use ONLY exact existing category
  names from list_categories. Never invent new categories (e.g. do not propose
  'Auto - Fuel' when the ledger uses 'Transportation') unless the user
  explicitly asks to create one. Call list_categories first to get the
  canonical names.
- Bulk recategorization: for more than ~3 changes, use recategorize_batch with
  ONE tool call containing all items. Do not enumerate every transaction in
  prose first — a one-line summary of the batch is enough; the user reviews
  each item in the approval gate anyway.
- If the data cannot answer the question, say exactly what is missing.
- Be concise. Lead with the answer, then the supporting numbers.

{context}"""


def _dynamic_context():
    """Ground the model in dates it cannot otherwise know."""
    from datetime import date
    txns = ledger.load_transactions()
    dates = sorted(t["Date"] for t in txns)
    return (f"Context: today's date is {date.today().isoformat()}. "
            f"The ledger contains {len(txns)} transactions covering "
            f"{dates[0]} to {dates[-1]}. Periods outside this range have no "
            f"data — say so plainly; do not speculate about why.")


class Agent:
    def __init__(self, verbose=False):
        self.client = anthropic.Anthropic()  # needs ANTHROPIC_API_KEY
        self.history = []
        self.verbose = verbose
        self.system = SYSTEM.format(context=_dynamic_context())
        self.stats = {"turns": 0, "tool_calls": 0,
                      "input_tokens": 0, "output_tokens": 0}

    def ask(self, user_message):
        self.history.append({"role": "user", "content": user_message})
        for _ in range(MAX_TURNS):
            t0 = time.time()
            resp = self.client.messages.create(
                model=MODEL, max_tokens=8000, system=self.system,
                tools=TOOLS, messages=self.history)
            self.stats["turns"] += 1
            self.stats["input_tokens"] += resp.usage.input_tokens
            self.stats["output_tokens"] += resp.usage.output_tokens
            self.history.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content if b.type == "text")

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                self.stats["tool_calls"] += 1
                if self.verbose:
                    print(f"  ⚙ {block.name}({json.dumps(block.input)[:120]})")
                out = dispatch(block.name, block.input)
                if self.verbose:
                    print(f"    → {json.dumps(out)[:150]} ({time.time()-t0:.1f}s)")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, default=str)})
            self.history.append({"role": "user", "content": results})

        audit("max_turns_exceeded", {"turns": MAX_TURNS})
        return "Stopped: exceeded max reasoning turns. Try a narrower question."
