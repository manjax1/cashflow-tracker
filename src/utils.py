import os


def clean_env(value, name=""):
    """Strip surrounding quotes that Railway sometimes auto-adds to env vars."""
    if value:
        value = value.strip()
        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]
    return value


def resolve_ledger_path() -> tuple[str, bool]:
    """Return (ledger_path, is_cloud) using the same logic as the sync pipeline.

    On Railway (RAILWAY_ENVIRONMENT set) the ledger lives at /tmp/cashflow-tracker.xlsx
    regardless of what SPENDING_LEDGER_FILE_PATH says — the env var there is a stale
    local Mac path that was never updated for the container.  Locally the env var wins.
    """
    is_cloud = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    if is_cloud:
        return "/tmp/cashflow-tracker.xlsx", True
    path = clean_env(os.getenv("SPENDING_LEDGER_FILE_PATH"), "SPENDING_LEDGER_FILE_PATH")
    return path, False
