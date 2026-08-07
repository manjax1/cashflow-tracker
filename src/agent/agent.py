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
- Recategorization scale:
  * A handful up to ~20 changes → use recategorize_batch with ONE tool call
    containing all items. Don't enumerate them in prose first; a one-line
    summary is enough (the user reviews each item in the approval gate).
  * A LARGE cleanup (dozens+ of transactions, many distinct merchants, or any
    "categorize all the uncategorized / build a baseline" request) → do NOT
    attempt a giant batch tool call (it overruns the response limit and can't
    classify future transactions anyway). Instead, recommend the durable
    rules-based baseline: `python scripts/suggest_rules.py` proposes
    keyword→category rules from the uncategorized merchants (dry-run with a
    coverage preview), then `--apply`, then `scripts/push_rules_to_drive.py`,
    then `src/recategorize_ledger.py --apply` re-classifies existing rows.
    Rules also auto-classify future syncs. Briefly explain this and offer to
    identify the top merchant clusters to seed it; don't call recategorize_batch
    for the whole set.
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
    def __init__(self, verbose=False, tools=None, read_only=False):
        self.client = anthropic.Anthropic()  # needs ANTHROPIC_API_KEY
        self.history = []
        self.verbose = verbose
        self.tools = tools if tools is not None else TOOLS
        self.system = SYSTEM.format(context=_dynamic_context())
        if read_only:
            self.system += ("\nThis is a READ-ONLY interface: action tools are "
                            "unavailable. For recategorizations or drafts, tell "
                            "the user to use the CLI (python -m src.agent.cli).")
        self.stats = {"turns": 0, "tool_calls": 0,
                      "input_tokens": 0, "output_tokens": 0}
        self.last_tool_calls = []  # (name, input) pairs from the latest ask()

    def ask(self, user_message):
        self.history.append({"role": "user", "content": user_message})
        self.last_tool_calls = []
        for _ in range(MAX_TURNS):
            t0 = time.time()
            resp = self.client.messages.create(
                model=MODEL, max_tokens=16000, system=self.system,
                tools=self.tools, messages=self.history)
            self.stats["turns"] += 1
            self.stats["input_tokens"] += resp.usage.input_tokens
            self.stats["output_tokens"] += resp.usage.output_tokens
            self.history.append({"role": "assistant", "content": resp.content})

            tool_uses = [b for b in resp.content if b.type == "tool_use"]

            # A tool call cut off at the output limit yields an incomplete
            # tool_use. Appending it with no tool_result corrupts every later
            # turn (the "tool_use without tool_result" 400). Drop it and guide.
            if resp.stop_reason == "max_tokens" and tool_uses:
                self.history.pop()
                audit("tool_use_truncated", {"tools": [b.name for b in tool_uses]})
                return ("That request was too large to complete in one step — the tool "
                        "call exceeded the response limit. For bulk recategorization, it's "
                        "better to create a few keyword rules (which also auto-classify "
                        "future transactions) or work in smaller batches. See "
                        "`scripts/suggest_rules.py` for the baseline workflow.")

            if not tool_uses:
                return "".join(b.text for b in resp.content if b.type == "text")

            results = []
            for block in tool_uses:
                self.stats["tool_calls"] += 1
                self.last_tool_calls.append({"name": block.name, "input": block.input})
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
