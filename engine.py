import json, re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

BASE = Path(__file__).parent
IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_YEAR = 2026

REGIONS = ["North", "South", "East", "West", "Central"]
PRODUCTS = ["Air Fryer", "Blender", "Grinder", "Juicer", "Kettle", "Mixer", "Rice Cooker", "Toaster"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
MONTH_ABBR = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}
MONTH_ABBR["sept"] = 9
NUMWORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10}


# ---------------------------------------------------------------- data
def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load():
    rates = {k: Decimal(v) for k, v in json.loads((BASE / "rates.json").read_text()).items()}
    latest = {}
    for idx, line in enumerate((BASE / "orders.ndjson").read_text().splitlines()):
        if not line.strip():
            continue
        r = json.loads(line)
        key = (_parse(r["updated_at"]), idx)
        if r["id"] not in latest or key > latest[r["id"]][0]:
            latest[r["id"]] = (key, r)
    orders = []
    for _, r in latest.values():
        orders.append({
            "id": r["id"], "customer": r["customer"], "region": r["region"],
            "product": r["product"], "qty": int(r["qty"]), "status": r["status"],
            "usd": Decimal(str(r["amount"])) * rates[r["currency"]],
            "date": _parse(r["created_at"]).astimezone(IST).date(),
        })
    return orders


ORDERS = load()
CUSTOMERS = {o["customer"] for o in ORDERS}


# ---------------------------------------------------------------- execution
def money(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def select(spec):
    out = []
    for o in ORDERS:
        if spec.get("region") and o["region"] != spec["region"]:
            continue
        if spec.get("product") and o["product"] != spec["product"]:
            continue
        if spec.get("customer") and o["customer"] != spec["customer"]:
            continue
        if spec.get("start") and o["date"] < spec["start"]:
            continue
        if spec.get("end") and o["date"] > spec["end"]:
            continue
        out.append(o)
    return out


STATUS_FOR = {"revenue": "paid", "orders": "paid", "units": "paid", "aov": "paid",
              "customers": "paid", "refund_amount": "refunded", "refund_count": "refunded",
              "void_count": "void"}


def value_of(metric, rows):
    st = STATUS_FOR[metric]
    rows = [o for o in rows if o["status"] == st]
    if metric in ("revenue", "refund_amount"):
        return sum((o["usd"] for o in rows), Decimal(0))
    if metric in ("orders", "refund_count", "void_count"):
        return Decimal(len(rows))
    if metric == "units":
        return Decimal(sum(o["qty"] for o in rows))
    if metric == "aov":
        return (sum((o["usd"] for o in rows), Decimal(0)) / len(rows)) if rows else Decimal(0)
    if metric == "customers":
        return Decimal(len({o["customer"] for o in rows}))
    raise ValueError(metric)


def fmt(metric, v):
    if metric in ("revenue", "refund_amount", "aov"):
        return money(v)
    return int(v)


def execute(spec):
    metric = spec["metric"]
    rows = select(spec)
    group = spec.get("group_by")
    if not group:
        return fmt(metric, value_of(metric, rows))
    groups = defaultdict(list)
    for o in rows:
        groups[o[group]].append(o)
    if group == "region":
        for k in REGIONS:
            groups.setdefault(k, [])
    if group == "product":
        for k in PRODUCTS:
            groups.setdefault(k, [])
    scored = [(k, value_of(metric, v)) for k, v in groups.items()]
    reverse = spec.get("order", "desc") == "desc"
    scored.sort(key=lambda kv: (-kv[1] if reverse else kv[1], kv[0]))
    n = spec.get("top_n") or 1
    names = [k for k, _ in scored[:n]]
    if spec.get("with_values"):
        return [{"name": k, "value": fmt(metric, v)} for k, v in scored[:n]]
    return names[0] if n == 1 else names


# ---------------------------------------------------------------- parsing
def _month_regex():
    names = "|".join(sorted(list(MONTHS) + list(MONTH_ABBR), key=len, reverse=True))
    return names


MON = _month_regex()


def _mnum(s):
    s = s.lower()
    return MONTHS.index(s) + 1 if s in MONTHS else MONTH_ABBR[s]


def _month_bounds(y, m):
    start = date(y, m, 1)
    end = (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return start, end


def parse_dates(t):
    """Return (start, end) or (None, None)."""
    # ISO dates
    iso = re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", t)
    if iso:
        ds = sorted(date(int(a), int(b), int(c)) for a, b, c in iso)
        return ds[0], ds[-1]
    ym = re.findall(r"\b(\d{4})-(\d{2})\b", t)
    if ym:
        ps = sorted(_month_bounds(int(a), int(b)) for a, b in ym)
        return ps[0][0], ps[-1][1]
    year_m = re.search(r"\b(20\d{2})\b", t)
    year = int(year_m.group(1)) if year_m else DEFAULT_YEAR

    # specific days: "march 5", "5th of march", "5 march 2026"
    days = []
    for m in re.finditer(rf"\b({MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s*(20\d{{2}}))?", t):
        d_ = int(m.group(2))
        if 1 <= d_ <= 31:
            days.append((m.start(), date(int(m.group(3) or year), _mnum(m.group(1)), d_)))
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MON})\b\.?(?:,?\s*(20\d{{2}}))?", t):
        d_ = int(m.group(1))
        if 1 <= d_ <= 31:
            days.append((m.start(), date(int(m.group(3) or year), _mnum(m.group(2)), d_)))
    if days:
        ds = sorted(d for _, d in days)
        return ds[0], ds[-1]

    # quarters / halves
    q = re.search(r"\bq([1-4])\b", t)
    qw = re.search(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\b", t)
    if q or qw:
        n = int(q.group(1)) if q else {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3,
                                      "3rd": 3, "fourth": 4, "4th": 4}[qw.group(1)]
        return date(year, 3 * n - 2, 1), _month_bounds(year, 3 * n)[1]
    h = re.search(r"\bh([12])\b", t) or re.search(r"\b(first|second) half\b", t)
    if h:
        g = h.group(1)
        n = 1 if g in ("1", "first") else 2
        return (date(year, 1, 1), date(year, 6, 30)) if n == 1 else (date(year, 7, 1), date(year, 12, 31))

    # months
    ms = []
    for m in re.finditer(rf"\b({MON})\b\.?(?:,?\s*(20\d{{2}}))?", t):
        word = m.group(1)
        if word == "may" and not re.search(r"(in|of|during|for|from|to|and|through|between|since|until|till|by|-)\s+may\b|\bmay\s+(20\d{2}|\d)|^may\b", t):
            continue
        ms.append((int(m.group(2) or year), _mnum(word)))
    if ms:
        bs = sorted(_month_bounds(y, mo) for y, mo in ms)
        return bs[0][0], bs[-1][1]
    if year_m:
        return date(year, 1, 1), date(year, 12, 31)
    return None, None


def find_region(t):
    for r in REGIONS:
        if re.search(rf"\b{r.lower()}(?:ern)?\b", t):
            return r
    return None


def find_product(t):
    squashed = re.sub(r"[^a-z]", "", t)
    for p in sorted(PRODUCTS, key=len, reverse=True):
        key = p.lower().replace(" ", "")
        if re.search(rf"\b{re.escape(p.lower())}(?:s|es)?\b", t) or key in squashed and len(key) > 5:
            return p
    return None


def parse_number(t, default=None):
    m = re.search(r"\b(?:top|bottom|best|worst|lowest|highest|first)\s+(\d+|" + "|".join(NUMWORDS) + r")\b", t)
    if m:
        v = m.group(1)
        return int(v) if v.isdigit() else NUMWORDS[v]
    m = re.search(r"\b(\d+|" + "|".join(NUMWORDS) + r")\s+(?:best|top|highest|lowest|worst|most|least|biggest|largest|products|customers|regions)\b", t)
    if m:
        v = m.group(1)
        return int(v) if v.isdigit() else NUMWORDS[v]
    return default


def parse_rules(question):
    t = question.lower().strip()
    t = re.sub(r"\s+", " ", t)
    spec = {}
    cm = re.search(r"\bc\d{4}\b", t)
    if cm and cm.group(0).upper() in CUSTOMERS:
        spec["customer"] = cm.group(0).upper()
    spec["region"] = find_region(t)
    spec["product"] = find_product(t)
    spec["start"], spec["end"] = parse_dates(t)

    # metric
    if "refund" in t:
        count_q = re.search(r"how many|number of|count of|\bcount\b|# of", t)
        spec["metric"] = "refund_count" if count_q and not re.search(r"amount|value|worth|total refund(?:ed)? (?:in|usd|\$)|how much", t) else "refund_amount"
    elif re.search(r"\bvoid(?:ed)?\b|cancel", t):
        spec["metric"] = "void_count"
    elif re.search(r"average order|avg order|\baov\b|average (?:sale|ticket|basket|revenue per order|value)|mean order", t):
        spec["metric"] = "aov"
    elif re.search(r"unique customers|distinct customers|how many customers|number of customers|customers (?:bought|purchased|ordered|placed)|active customers|different customers", t):
        spec["metric"] = "customers"
    elif re.search(r"units|quantity|quantities|items sold|pieces|how many .* (?:sold|were sold)", t) and not re.search(r"revenue|sales amount", t):
        spec["metric"] = "units"
    elif re.search(r"how many orders|number of orders|order count|count of orders|how many paid orders|orders (?:were|did)|total orders|# of orders", t):
        spec["metric"] = "orders"
    elif re.search(r"revenue|sales|sold|earn|income|turnover|spent|spend|bought|purchas|worth|\busd\b|dollar|\$", t):
        spec["metric"] = "revenue"
    else:
        spec["metric"] = None

    # ranking / grouping
    rank = re.search(r"\b(top|best|highest|most|largest|biggest|leading|lowest|least|worst|smallest|bottom|fewest)\b", t)
    group = None
    if rank:
        if re.search(r"\bcustomers?\b", t) and not spec.get("customer") and not re.search(r"how many customers", t):
            group = "customer"
        elif re.search(r"\bproducts?\b|\bitems?\b|\bappliances?\b", t) and not spec.get("product"):
            group = "product"
        elif re.search(r"\bregions?\b|\bareas?\b|\bterritor", t) and not spec.get("region"):
            group = "region"
    if not group and re.search(r"\bwhich (?:one )?(?:product|item)\b|\bwhat (?:product|item)\b", t) and rank and not spec.get("product"):
        group = "product"
    if group:
        spec["group_by"] = group
        low = re.search(r"\b(lowest|least|worst|smallest|bottom|fewest)\b", t)
        spec["order"] = "asc" if low else "desc"
        spec["top_n"] = parse_number(t, 1)
        if spec["metric"] is None:
            spec["metric"] = "revenue"
        if re.search(r"\bhow much\b|\band (?:their|its) (?:revenue|total)|with (?:its|their) (?:revenue|total)|along with", t):
            spec["with_values"] = True
    return spec


# ---------------------------------------------------------------- LLM fallback (optional)
def parse_llm(question):
    import os, urllib.request
    tok = os.environ.get("AIPIPE_TOKEN")
    if not tok:
        return None
    prompt = (
        "Convert the question about an order ledger into JSON with keys: "
        "metric (revenue|refund_amount|refund_count|orders|units|aov|customers|void_count), "
        "region (North|South|East|West|Central|null), product (Air Fryer|Blender|Grinder|Juicer|Kettle|Mixer|Rice Cooker|Toaster|null), "
        "customer (like C1200 or null), start (YYYY-MM-DD or null), end (YYYY-MM-DD inclusive or null), "
        "group_by (product|region|customer|null), order (desc|asc), top_n (int or null). "
        "Months without a year mean 2026. Reply with JSON only.\nQuestion: " + question)
    body = json.dumps({"model": "gpt-4.1-mini", "temperature": 0,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://aipipe.org/openai/v1/chat/completions", body,
                                 {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    spec = json.loads(data["choices"][0]["message"]["content"])
    for k in ("start", "end"):
        spec[k] = date.fromisoformat(spec[k]) if spec.get(k) else None
    spec["top_n"] = spec.get("top_n") or 1
    return spec


def answer(question):
    spec = parse_rules(question)
    if spec["metric"] is None:
        try:
            alt = parse_llm(question)
        except Exception:
            alt = None
        if alt:
            spec = alt
        else:
            spec["metric"] = "revenue"
    return execute(spec), spec
