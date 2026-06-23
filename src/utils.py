def clean_env(value, name=""):
    """Strip surrounding quotes that Railway sometimes auto-adds to env vars."""
    if value:
        value = value.strip()
        if (value.startswith("'") and value.endswith("'")) or \
           (value.startswith('"') and value.endswith('"')):
            value = value[1:-1]
    return value
