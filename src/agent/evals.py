"""Project #2 — Eval harness for the cashflow agent.

Because the deterministic layer is a trusted source of truth, most cases carry a
DETERMINISTIC ORACLE: the harness computes the correct answer from the ledger and
checks the agent reported it. Stronger than pure LLM-as-judge.

Usage:
    python -m src.agent.evals run                 # all cases
    python -m src.agent.evals run --tag rental    # subset by tag
    python -m src.agent.evals run --baseline last # regression vs previous run
    python -m src.agent.evals list                # show cases
"""

import glob
import json
import os
import re
import sys
import time
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from . import ledger                      # noqa: E402

ROOT = ledger.REPO_ROOT
CASES = os.path.join(ROOT, "evals", "cases.jsonl")
RUNS_DIR = os.path.join(ROOT, "evals", "runs")


# --------------------------- deterministic oracles ---------------------------

def _rows(category, kind, start, end):
    rows = [t for t in ledger.effective_rows()
            if t["IncludeInNet"] and start <= t["Date"] <= end
            and (kind is None or t["Type"].lower() == kind.lower())
            and (category is None or category.lower() in t["Category"].lower())]
    return rows


def category_sum(category=None, kind=None, start=None, end=None):
    return round(sum(t["Amount"] for t in _rows(category, kind, start, end)), 2)


def category_monthly_avg(category=None, kind=None, start=None, end=None, months=1):
    return round(category_sum(category, kind, start, end) / months, 2)


def count(category=None, kind=None, start=None, end=None):
    return len(_rows(category, kind, start, end))


def net(start=None, end=None):
    inc = category_sum(None, "Income", start, end)
    exp = category_sum(None, "Expense", start, end)
    return round(inc - exp, 2)


ORACLES = {"category_sum": category_sum, "category_monthly_avg": category_monthly_avg,
           "count": count, "net": net}


# ------------------------------- checks --------------------------------

def _numbers_in(text):
    """Extract numeric values from answer text (handles $, commas, decimals)."""
    return [float(n.replace(",", "")) for n in re.findall(r"-?\$?([\d,]+(?:\.\d+)?)", text)
            if n.replace(",", "").replace(".", "").isdigit()]


def check_tools_include(agent_tools, tools, **_):
    missing = [t for t in tools if t not in agent_tools]
    return (not missing, f"missing tool(s): {missing}" if missing else "")


def check_tools_exclude(agent_tools, tools, **_):
    hit = [t for t in tools if t in agent_tools]
    return (not hit, f"unexpected tool(s): {hit}" if hit else "")


def check_number(answer, oracle=None, args=None, tolerance=1.0, **_):
    expected = ORACLES[oracle](**(args or {}))
    for n in _numbers_in(answer):
        if abs(n - abs(expected)) <= tolerance:
            return True, f"found {expected}"
    return False, f"expected ~{expected} (±{tolerance}); not found in answer"


def check_contains(answer, text=None, any_of=None, **_):
    opts = any_of or [text]
    ok = any(o.lower() in answer.lower() for o in opts)
    return ok, "" if ok else f"answer missing any of: {opts}"


def check_not_contains(answer, text=None, **_):
    ok = text.lower() not in answer.lower()
    return ok, "" if ok else f"answer should not contain: {text!r}"


CHECKS = {"tools_include": check_tools_include, "tools_exclude": check_tools_exclude,
          "number": check_number, "contains": check_contains,
          "not_contains": check_not_contains}


def run_check(spec, answer, agent_tools):
    fn = CHECKS[spec["type"]]
    kw = {k: v for k, v in spec.items() if k != "type"}
    if spec["type"] in ("tools_include", "tools_exclude"):
        return fn(agent_tools, **kw)
    return fn(answer, **kw)


# ------------------------------- runner --------------------------------

def load_cases():
    if not os.path.exists(CASES):
        sys.exit(f"No cases file at {CASES}")
    out = []
    with open(CASES) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                out.append(json.loads(line))
    return out


def run(tag=None, baseline=None):
    from .agent import Agent
    cases = [c for c in load_cases() if not tag or tag in c.get("tags", [])]
    print(f"Running {len(cases)} eval case(s)"+(f" [tag={tag}]" if tag else "")+"\n")
    results, tot_in, tot_out, t0 = [], 0, 0, time.time()
    for c in cases:
        agent = Agent()                              # fresh agent per case
        try:
            answer = agent.ask(c["question"])
        except Exception as e:
            results.append({"id": c["id"], "passed": False,
                            "fails": [f"agent error: {e}"]}); continue
        atools = [tc["name"] for tc in agent.last_tool_calls]
        tot_in += agent.stats["input_tokens"]; tot_out += agent.stats["output_tokens"]
        fails = []
        for spec in c.get("checks", []):
            ok, why = run_check(spec, answer, atools)
            if not ok:
                fails.append(f"[{spec['type']}] {why}")
        passed = not fails
        results.append({"id": c["id"], "passed": passed, "fails": fails})
        mark = "✓" if passed else "✗"
        print(f"  {mark}  {c['id']}")
        for fl in fails:
            print(f"        {fl}")

    n_pass = sum(r["passed"] for r in results)
    dt = time.time() - t0
    print(f"\n{n_pass}/{len(results)} passed  ·  {tot_in}+{tot_out} tokens  ·  {dt:.1f}s")

    os.makedirs(RUNS_DIR, exist_ok=True)
    run_path = os.path.join(RUNS_DIR, f"run_{datetime.now():%Y%m%d_%H%M%S}.json")
    with open(run_path, "w") as f:
        json.dump({"ts": datetime.now().isoformat(), "pass": n_pass,
                   "total": len(results), "results": results}, f, indent=2)

    if baseline:
        _regress(results)
    return results


def _regress(results):
    runs = sorted(glob.glob(os.path.join(RUNS_DIR, "run_*.json")))
    if len(runs) < 2:
        print("\n(no prior run to compare)"); return
    prev = json.load(open(runs[-2]))
    prev_pass = {r["id"]: r["passed"] for r in prev["results"]}
    regressed = [r["id"] for r in results if prev_pass.get(r["id"]) and not r["passed"]]
    fixed = [r["id"] for r in results if prev_pass.get(r["id"]) is False and r["passed"]]
    print(f"\nRegression vs {os.path.basename(runs[-2])}:")
    print(f"  regressed (pass→fail): {regressed or 'none'}")
    print(f"  fixed (fail→pass):     {fixed or 'none'}")


def list_cases():
    for c in load_cases():
        print(f"  {c['id']:<34} {c.get('tags', [])}  — {c['question'][:60]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "run":
        tag = args[args.index("--tag") + 1] if "--tag" in args else None
        base = "last" if "--baseline" in args else None
        run(tag=tag, baseline=base)
    elif args and args[0] == "list":
        list_cases()
    else:
        print(__doc__)
