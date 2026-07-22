"""MCP server — exposes the Cashflow ledger's deterministic tools via the Model
Context Protocol, so Claude Desktop (or any MCP client) can query your finances
directly. Read-only by design; mutations stay behind the web/CLI approval gate.

The SAME ledger.py functions power the CLI agent, the web agent, and this server
— MCP just wraps them in a standard protocol. Splits apply automatically because
these functions read effective_rows().

Run (Claude Desktop launches this for you via config):
    python -m src.agent.mcp_server

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"cashflow": {
        "command": "python", "args": ["-m", "src.agent.mcp_server"],
        "cwd": "/path/to/cashflow-tracker",
        "env": {"SPENDING_LEDGER_FILE_PATH": "/path/to/cashflow-tracker.xlsx"}}}}
"""

from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP   # noqa: E402

from . import ledger                     # noqa: E402

mcp = FastMCP("cashflow")


@mcp.tool()
def ledger_coverage() -> dict:
    """Return the date range and transaction count the ledger covers. ALWAYS
    call this first so you don't ask about periods with no data."""
    txns = ledger.load_transactions()
    dates = sorted(t["Date"] for t in txns)
    return {"start": dates[0], "end": dates[-1], "transactions": len(txns),
            "note": "Only dates within [start, end] have data."}


@mcp.tool()
def query_transactions(start_date: str, end_date: str, category: Optional[str] = None,
                       tx_type: Optional[str] = None, search: Optional[str] = None,
                       limit: int = 50) -> dict:
    """Look up ledger transactions in a date range with optional filters.
    Dates are ISO YYYY-MM-DD. `category` is a case-insensitive substring
    ('Rental' matches all Rental - * categories). `tx_type` is 'Income' or
    'Expense'. `search` matches the description. Returns rows + totals."""
    return ledger.query_transactions(start_date, end_date, category=category,
                                     tx_type=tx_type, search=search, limit=limit)


@mcp.tool()
def cashflow_summary(start_date: str, end_date: str, group_by: str = "category") -> dict:
    """Computed income, expense, and net for a period, grouped by 'category',
    'month', 'account', or 'type'. Use this for totals — it does the arithmetic;
    never sum transactions yourself. Includes a Rental - * rollup."""
    return ledger.get_cashflow_summary(start_date, end_date, group_by=group_by)


@mcp.tool()
def trends(metric: str = "net", granularity: str = "month",
           lookback_periods: int = 6, category: Optional[str] = None) -> dict:
    """Time series for a metric ('income', 'expenses', or 'net') with
    period-over-period deltas and a trailing average. granularity is 'month' or
    'quarter'. Optional category filter. Use for any 'trend'/'over time'
    question. The current period may be flagged partial."""
    return ledger.get_trends(metric=metric, granularity=granularity,
                             lookback_periods=lookback_periods, category=category)


@mcp.tool()
def list_categories() -> dict:
    """List all ledger categories with counts, totals, and date ranges. Call
    this when a category is named colloquially to find the exact name."""
    return ledger.list_categories()


@mcp.tool()
def find_anomalies(lookback_days: int = 30) -> dict:
    """Deterministic anomaly checks over the recent window: possible duplicates,
    unusually large transactions, missing expected rental income, category spend
    spikes, and uncategorized backlog. Explain and prioritize the findings."""
    return ledger.find_anomalies(lookback_days=lookback_days)


def main():
    mcp.run()   # stdio transport by default


if __name__ == "__main__":
    main()
