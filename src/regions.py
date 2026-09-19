"""Deterministic geographic tagging of transactions from merchant/location
signals in the description. This replaces the agent 'eyeballing' which merchants
look Singaporean/Malaysian — that was non-deterministic and inconsistent between
runs (one run found the Johor charges, the next declared there were none). With a
maintained keyword map, "charges from Singapore and nearby" becomes an exact,
repeatable query.

classify_region(description) -> 'Singapore' | 'Malaysia' | 'Indonesia'
                                | 'SEA (ambiguous)' | None

Design notes:
- Specific COUNTRY signals win. Order is Singapore, Malaysia, Indonesia.
- 'SEA (ambiguous)': a brand that is South-East-Asia-exclusive (e.g. Grab) but
  whose description carries no country — clearly in-region, country unknown.
- Globally-present brands (Starbucks, 7-Eleven, Subway, McDonald's) are NOT
  auto-tagged, because most instances are domestic (US) — they'd pollute the
  list. Instead query_by_region flags them ONLY when they fall inside a detected
  travel window (dates bracketed by confirmed in-region charges), listed in a
  clearly-separate 'ambiguous_in_window' bucket so nothing is silently dropped
  or over-counted.

The token lists are the maintenance surface: add merchants here as you see them.
Keep tokens specific enough to avoid false matches (they are matched as
case-insensitive substrings of the description).
"""

# (region_label, [lowercase substrings]). Specific country signals.
_COUNTRY_RULES = [
    ("Singapore", [
        "singapore", ".sg", " sg ", "pte ltd", "pte. ltd", "pte.ltd",
        "nus ", " nus", "nus-", "n.u.s", "ntuc", "fairprice", "sheng siong",
        "kopitiam", "cheers", "sentosa", "vivocity", "vivo city", "xtra vivo",
        "bugis", "lau pa sat", "shaw theatres", "shaw.sg", "breadtalk",
        "octobox", "marquee singapore", "kintsugi", "tripletsnus", "m1 shop",
        "stuff'd", "yochi asia", "smp_", "i love taimei", "ijooz",
        "supersnacks", "katong", "clementi", "changi",
    ]),
    ("Malaysia", [
        "malaysia", "johor", "pagoh", "paradigm mall", "bhpetrol", "bh petrol",
        " jb ", "kuala lumpur", "klcc", "mid valley", " myr", "ringgit",
        "empire sushi", "chucky cat", "f.o.s", "petronas", "touch n go",
        "touch 'n go", "mydin", "lotus's", "setia city",
    ]),
    ("Indonesia", [
        "indonesia", "batam", "bintan", "jakarta", " bali", " idr", "rupiah",
        "nagoya hill", "grand batam",
    ]),
    ("Vietnam", [
        "vietnam", "viet nam", "hanoi", "ha noi", "ho chi minh", "saigon",
        "sai gon", "da nang", "danang", "hoi an", "nha trang", "halong",
        "ha long", " vnd", "highlands coffee", "the coffee house", "vinmart",
        "winmart", "vietjet", "bunny.vn", ".vn ", "phu quoc",
    ]),
    ("Thailand", [
        "thailand", "bangkok", "phuket", "chiang mai", "chiangmai", "pattaya",
        "krabi", "koh samui", "koh phangan", " thb", "baht", "siam paragon",
        "central world", "bts skytrain", "7-eleven thailand", "grab th",
    ]),
    ("Philippines", [
        "philippines", "manila", "makati", "cebu", "quezon", "boracay",
        "palawan", "davao", "jollibee", "sm mall", "mercury drug", "gcash",
        "bgc taguig",
    ]),
    ("Cambodia", [
        "cambodia", "phnom penh", "siem reap", "angkor", " khr", "riel ",
        "sihanoukville",
    ]),
    ("Laos", [
        "laos", "vientiane", "luang prabang", "lao pdr", "vang vieng",
    ]),
    ("Myanmar", [
        "myanmar", "yangon", "mandalay", "naypyidaw", "bagan",
    ]),
    ("Brunei", [
        "brunei", "bandar seri", "seri begawan", " bnd",
    ]),
]

# SEA-exclusive brands with no country in the string → clearly in-region, unknown
# which country. Grab operates only across SEA.
_SEA_ONLY = ["grab*", "grab ", "grabpay", "gojek", "shopeepay", "foodpanda"]

# Globally-present brands: only meaningful inside a travel window (handled by
# query_by_region), never auto-tagged to a region on their own.
_GLOBAL_AMBIGUOUS = [
    "starbucks", "7-eleven", "7 eleven", "7eleven", "mcdonald", "kfc",
    "subway", "burger king", "uniqlo", "cotton on", "din tai fung",
]


def classify_region(description):
    d = (description or "").lower()
    for region, toks in _COUNTRY_RULES:
        if any(t in d for t in toks):
            return region
    if any(t in d for t in _SEA_ONLY):
        return "SEA (ambiguous)"
    return None


def is_global_ambiguous(description):
    """True for globally-present brands that are only regionally meaningful when
    they fall inside a confirmed travel window."""
    d = (description or "").lower()
    return any(t in d for t in _GLOBAL_AMBIGUOUS)


# Convenience: the region labels this module can emit, for callers/UI.
REGION_LABELS = ["Singapore", "Malaysia", "Indonesia", "Vietnam", "Thailand",
                 "Philippines", "Cambodia", "Laos", "Myanmar", "Brunei",
                 "SEA (ambiguous)"]
