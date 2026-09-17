#!/usr/bin/env python3
"""Fetch every indicator from free official feeds and write data/latest.json.

Runs in GitHub Actions on a schedule. Standard library only, plus xlrd for the
NY Fed ACM term premium spreadsheet.

Rules:
- Each indicator comes from one named source. No mixing, no estimating.
- If a fetch fails, the previous value is kept and marked stale with the error.
- Values in data/manual.json (hand or Claude-entered) are merged in as-is.

API keys come from environment variables (GitHub repository secrets):
  FRED_API_KEY   required  https://fred.stlouisfed.org/docs/api/api_key.html
  EIA_API_KEY    optional  https://www.eia.gov/opendata/register.php  (WTI futures)
  TWELVEDATA_API_KEY optional https://twelvedata.com/register     (USD/JPY, DXY)
"""
import csv, io, json, math, os, statistics, sys, time, traceback
import urllib.parse, urllib.request
from datetime import datetime, date, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = {"User-Agent": "macro-tension-monitor/1.0 (+https://github.com)"}


# ---------------------------------------------------------------- helpers
def http_get(url, params=None, timeout=40, retries=2):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed: {redact(url)}: {last}")


def redact(url):
    for k in ("api_key", "apikey"):
        if k + "=" in url:
            head, _, tail = url.partition(k + "=")
            url = head + k + "=***" + ("&" + tail.split("&", 1)[1] if "&" in tail else "")
    return url


def mdy(s):
    return datetime.strptime(s.strip(), "%m/%d/%Y").date().isoformat()


def r2(x):
    return round(x + 0.0, 2)


def reading(value, asof, source, **extra):
    d = {"value": value, "asof": asof, "source": source}
    d.update(extra)
    return d


def env(name, required=False):
    v = os.environ.get(name, "").strip()
    if required and not v:
        raise RuntimeError(f"missing secret {name}")
    return v


# ---------------------------------------------------------------- sources
_cache = {}


def treasury_curve():
    """Treasury daily par yield curve, current year CSV (newest row first)."""
    if "tsy" in _cache:
        return _cache["tsy"]
    y = datetime.now(timezone.utc).year
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           f"daily-treasury-rates.csv/{y}/all?type=daily_treasury_yield_curve"
           f"&field_tdr_date_value={y}&page&_format=csv")
    rows = list(csv.DictReader(io.StringIO(http_get(url).decode("utf-8-sig"))))
    rows = [r for r in rows if r.get("Date")]
    rows.sort(key=lambda r: mdy(r["Date"]), reverse=True)
    _cache["tsy"] = rows
    return rows


def tsy(col):
    def f():
        for r in treasury_curve():
            v = (r.get(col) or "").strip()
            if v:
                return reading(r2(float(v)), mdy(r["Date"]), "U.S. Treasury daily par yield curve")
        raise RuntimeError(f"no value for {col}")
    return f


def fred_obs(series, limit=60):
    key = env("FRED_API_KEY", required=True)
    raw = http_get("https://api.stlouisfed.org/fred/series/observations", {
        "series_id": series, "api_key": key, "file_type": "json",
        "sort_order": "desc", "limit": limit})
    out = []
    for o in json.loads(raw)["observations"]:
        try:
            out.append((o["date"], float(o["value"])))
        except ValueError:
            pass  # "." = no observation
    return out  # newest first


def fred_latest(series, label, nd=2):
    def f():
        d, v = fred_obs(series, 10)[0]
        return reading(round(v, nd), d, f"FRED {series} ({label})")
    return f


def fred_yoy(series, label):
    """12-month % change of a monthly index, rounded to 1 dp like BLS/BEA headlines."""
    def f():
        obs = dict(fred_obs(series, 30))
        latest = max(obs)
        y, m, _ = map(int, latest.split("-"))
        prior = f"{y-1:04d}-{m:02d}-01"
        if prior not in obs:
            raise RuntimeError("missing year-ago observation")
        return reading(round((obs[latest] / obs[prior] - 1) * 100, 1), latest, f"FRED {series} ({label}), 12-month change")
    return f


def corr():
    """-1 x Pearson(SPX daily % return, DGS10 daily change), last 21 changes on common dates."""
    s = dict(fred_obs("SP500", 40))
    y = dict(fred_obs("DGS10", 40))
    common = sorted(set(s) & set(y))[-22:]
    if len(common) < 22:
        raise RuntimeError("fewer than 22 common dates")
    rs = [s[common[i]] / s[common[i-1]] - 1 for i in range(1, 22)]
    dy = [y[common[i]] - y[common[i-1]] for i in range(1, 22)]
    return reading(r2(-statistics.correlation(rs, dy)), common[-1], "FRED SP500 and DGS10, 21-day correlation (sign flipped)")


def vix():
    rows = list(csv.DictReader(io.StringIO(http_get(
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv").decode("utf-8-sig"))))
    last = max(rows, key=lambda r: mdy(r["DATE"]))
    return reading(r2(float(last["CLOSE"])), mdy(last["DATE"]), "Cboe VIX daily history")


def srf():
    """Sum accepted amounts of all SRF operations on the latest business day that has
    both a morning and an afternoon operation (or any day before today)."""
    j = json.loads(http_get("https://markets.newyorkfed.org/api/rp/repo/all/results/lastTwoWeeks.json"))
    by_day = {}
    for op in j["repo"]["operations"]:
        if op.get("operationType") != "Repo" or op.get("auctionStatus") != "Results":
            continue
        by_day.setdefault(op["operationDate"], []).append(op)
    today = datetime.now(timezone.utc).date().isoformat()
    for d in sorted(by_day, reverse=True):
        if d < today or len(by_day[d]) >= 2:
            total = sum(float(o.get("totalAmtAccepted") or 0) for o in by_day[d])
            return reading(round(total / 1e9), d, "NY Fed repo operation results (SRF)", ops=len(by_day[d]))
    raise RuntimeError("no completed operations")


COT_CODES = {"042601": 200_000, "044601": 100_000, "043602": 100_000,
             "043607": 100_000, "020601": 100_000, "020604": 100_000}


def cot():
    codes = ",".join(f"'{c}'" for c in COT_CODES)
    raw = http_get("https://publicreporting.cftc.gov/resource/gpe5-46if.json", {
        "$select": "report_date_as_yyyy_mm_dd,cftc_contract_market_code,lev_money_positions_long,lev_money_positions_short",
        "$where": f"cftc_contract_market_code in({codes})",
        "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 24})
    rows = json.loads(raw)
    latest = max(r["report_date_as_yyyy_mm_dd"] for r in rows)
    cur = {r["cftc_contract_market_code"]: r for r in rows if r["report_date_as_yyyy_mm_dd"] == latest}
    if set(cur) != set(COT_CODES):
        raise RuntimeError(f"latest report missing contracts: {set(COT_CODES) - set(cur)}")
    face = sum((float(r["lev_money_positions_short"]) - float(r["lev_money_positions_long"])) * COT_CODES[c]
               for c, r in cur.items())
    return reading(round(face / 1e9), latest[:10], "CFTC Traders in Financial Futures, futures only")


def acm():
    import xlrd  # pip install xlrd==2.0.1
    book = xlrd.open_workbook(file_contents=http_get(
        "https://www.newyorkfed.org/medialibrary/media/research/data_indicators/ACMTermPremium.xls"))
    sh = next(s for s in book.sheets() if "daily" in s.name.lower())
    hdr = [str(c.value).strip().upper() for c in sh.row(0)]
    di, ti = hdr.index("DATE"), hdr.index("ACMTP10")
    for r in range(sh.nrows - 1, 0, -1):
        dv, tv = sh.cell_value(r, di), sh.cell_value(r, ti)
        if tv in ("", None):
            continue
        if isinstance(dv, float):
            d = xlrd.xldate.xldate_as_datetime(dv, book.datemode).date()
        else:
            d = None
            for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
                try:
                    d = datetime.strptime(str(dv).strip(), fmt).date(); break
                except ValueError:
                    pass
            if d is None:
                raise RuntimeError(f"unparsed date {dv!r}")
        return reading(round(float(tv) * 100), d.isoformat(), "NY Fed ACM term premia (ACMTP10)")
    raise RuntimeError("no ACM rows")


def jgb10():
    def parse(text):
        lines = text.splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("Date"))
        rows = [r for r in csv.DictReader(lines[start:]) if r.get("Date") and (r.get("10Y") or "").strip() not in ("", "-")]
        return rows
    base = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
    rows = parse(http_get(base + "jgbcme.csv").decode("utf-8", "replace"))
    if not rows:  # first days of a month
        rows = parse(http_get(base + "historical/jgbcme_all.csv").decode("utf-8", "replace"))
    last = rows[-1]
    y, m, d = map(int, last["Date"].split("/"))
    return reading(r2(float(last["10Y"])), date(y, m, d).isoformat(), "Japan Ministry of Finance JGB yields")


def eia_wti():
    key = env("EIA_API_KEY", required=True)
    raw = http_get("https://api.eia.gov/v2/petroleum/pri/fut/data/", {
        "api_key": key, "frequency": "daily", "data[0]": "value",
        "facets[series][]": "RCLC1", "sort[0][column]": "period",
        "sort[0][direction]": "desc", "length": 5})
    row = json.loads(raw)["response"]["data"][0]
    return reading(r2(float(row["value"])), row["period"], "EIA NYMEX WTI futures, contract 1 (RCLC1)")


FX = ["EUR/USD", "USD/JPY", "GBP/USD", "USD/CAD", "USD/SEK", "USD/CHF"]


def twelvedata_fx():
    if "fx" in _cache:
        return _cache["fx"]
    key = env("TWELVEDATA_API_KEY", required=True)
    raw = json.loads(http_get("https://api.twelvedata.com/time_series", {
        "symbol": ",".join(FX), "interval": "1day", "outputsize": 5, "apikey": key}))
    today = datetime.now(timezone.utc).date().isoformat()
    closes = {}
    for sym in FX:
        blk = raw.get(sym, {})
        if blk.get("status") != "ok":
            raise RuntimeError(f"Twelve Data {sym}: {blk.get('message', 'error')}")
        bars = [b for b in blk["values"] if b["datetime"][:10] < today]  # completed days only
        closes[sym] = {b["datetime"][:10]: float(b["close"]) for b in bars}
    _cache["fx"] = closes
    return closes


def usdjpy():
    c = twelvedata_fx()["USD/JPY"]
    d = max(c)
    return reading(r2(c[d]), d, "Twelve Data USD/JPY daily close")


def dxy():
    """ICE DXY formula computed from six daily closes on the same date."""
    c = twelvedata_fx()
    common = set.intersection(*(set(c[s]) for s in FX))
    if not common:
        raise RuntimeError("no common FX date")
    d = max(common)
    v = (50.14348112 * c["EUR/USD"][d] ** -0.576 * c["USD/JPY"][d] ** 0.136 * c["GBP/USD"][d] ** -0.119
         * c["USD/CAD"][d] ** 0.091 * c["USD/SEK"][d] ** 0.042 * c["USD/CHF"][d] ** 0.036)
    return reading(r2(v), d, "Computed from Twelve Data FX closes using the ICE DXY formula")


def fiscal(dataset, params):
    raw = http_get(f"https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/{dataset}", params)
    return json.loads(raw)["data"]


def auctions():
    """Recent results and announced upcoming nominal coupon auctions (no TIPS, no FRNs)."""
    since = date.fromordinal(date.today().toordinal() - 75).isoformat()
    rows = fiscal("auctions_query", {
        "fields": "auction_date,security_type,security_term,original_security_term,high_yield,cusip,reopening,inflation_index_security,floating_rate",
        "filter": f"auction_date:gte:{since},security_type:in:(Note,Bond),inflation_index_security:eq:No,floating_rate:eq:No",
        "sort": "-auction_date", "page[size]": 100})
    out = []
    for r in rows:
        term = r.get("original_security_term") or r["security_term"]
        hy = r.get("high_yield")
        out.append({"date": r["auction_date"], "term": term, "security_term": r["security_term"],
                    "reopening": r.get("reopening") == "Yes", "cusip": r["cusip"],
                    "high_yield": float(hy) if hy not in (None, "", "null") else None})
    return out


# ---------------------------------------------------------------- registry
FETCHERS = {
    "ust2": tsy("2 Yr"), "ust10": tsy("10 Yr"), "ust30": tsy("30 Yr"),
    "ff": fred_latest("DFEDTARU", "fed funds target, upper bound"),
    "tp10": acm,
    "vix": vix,
    "spx": fred_latest("SP500", "S&P 500 close"),
    "corr": corr,
    "brent": fred_latest("DCOILBRENTEU", "Brent spot, EIA"),
    "wti": eia_wti,
    "diesel": fred_latest("GASDESW", "US on-highway diesel, EIA weekly"),
    "cpi": fred_yoy("CPIAUCNS", "CPI-U, not seasonally adjusted"),
    "pce": fred_yoy("PCEPILFE", "core PCE price index"),
    "jgb10": jgb10,
    "usdjpy": usdjpy,
    "dxy": dxy,
    "srf": srf,
    "cot": cot,
}


def main():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "latest.json")
    prev = json.load(open(path)) if os.path.exists(path) else {"indicators": {}}
    manual = json.load(open(os.path.join(DATA, "manual.json"))) if os.path.exists(os.path.join(DATA, "manual.json")) else {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    only = set(sys.argv[1:])
    out, ok, failed = {}, [], []

    for id_, fn in FETCHERS.items():
        if only and id_ not in only:
            out[id_] = prev["indicators"].get(id_)
            continue
        try:
            r = fn()
            if not isinstance(r["value"], (int, float)) or (isinstance(r["value"], float) and math.isnan(r["value"])):
                raise RuntimeError("non-numeric value")
            r.update(fetched=now, stale=False)
            out[id_] = r
            ok.append(id_)
        except Exception as e:  # noqa: BLE001
            old = dict(prev["indicators"].get(id_) or {"value": None, "asof": None, "source": None})
            old.update(stale=True, error=str(e)[:300], attempted=now)
            out[id_] = old
            failed.append(id_)
            traceback.print_exc()

    for id_, m in manual.items():
        if id_.startswith("_"):
            continue
        out[id_] = dict(m, manual=True, stale=False)

    try:
        auc = auctions()
        prev_auc = None
    except Exception as e:  # noqa: BLE001
        auc = prev.get("auctions", [])
        prev_auc = str(e)[:300]
        traceback.print_exc()

    doc = {"generated": now, "indicators": {k: v for k, v in out.items() if v is not None},
           "auctions": auc, "auctions_error": prev_auc, "ok": ok, "failed": failed}
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)

    # compact per-indicator history for sparklines (max 400 points each)
    hpath = os.path.join(DATA, "history.json")
    hist = json.load(open(hpath)) if os.path.exists(hpath) else {}
    for id_, r in doc["indicators"].items():
        if r.get("value") is None or not r.get("asof"):
            continue
        series = [p for p in hist.get(id_, []) if p[0] != r["asof"]]
        series.append([r["asof"], r["value"]])
        hist[id_] = sorted(series)[-400:]
    with open(hpath, "w") as f:
        json.dump(hist, f, separators=(",", ":"), sort_keys=True)

    print(f"ok: {', '.join(ok) or '-'}")
    print(f"failed: {', '.join(failed) or '-'}")


if __name__ == "__main__":
    main()
