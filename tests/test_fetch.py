"""Offline tests: feed each parser a sample in the format the real source returns."""
import importlib.util, json, os, sys, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
spec = importlib.util.spec_from_file_location("fetch", os.path.join(ROOT, "scripts", "fetch.py"))
F = importlib.util.module_from_spec(spec); spec.loader.exec_module(F)

TSY = ('Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"\n'
       '09/16/2026,3.96,4.00,4.07,4.14,4.24,4.22,4.45,4.74,4.82,4.86,4.94,5.01,5.39,5.35\n'
       '09/15/2026,3.93,4.00,4.06,4.11,4.19,4.17,4.39,4.67,4.76,4.83,4.91,5.00,5.40,5.36\n')
NYFED = {"repo": {"operations": [
    {"operationDate": "2026-09-17", "operationType": "Repo", "auctionStatus": "Results", "totalAmtAccepted": 0},
    {"operationDate": "2026-09-16", "operationType": "Repo", "auctionStatus": "Results", "totalAmtAccepted": 0},
    {"operationDate": "2026-09-15", "operationType": "Repo", "auctionStatus": "Results", "totalAmtAccepted": 0},
    {"operationDate": "2026-09-15", "operationType": "Repo", "auctionStatus": "Results", "totalAmtAccepted": 102000000}]}}
COT = [
    {"report_date_as_yyyy_mm_dd": "2026-09-08T00:00:00.000", "cftc_contract_market_code": c, "lev_money_positions_long": l, "lev_money_positions_short": s}
    for c, l, s in [("042601", "585711", "1876190"), ("044601", "492845", "2559134"), ("043602", "391836", "2330590"),
                    ("043607", "172897", "599257"), ("020601", "133029", "409994"), ("020604", "86352", "950623")]]
CBOE = "DATE,OPEN,HIGH,LOW,CLOSE\n09/14/2026,16.1,17,15.9,16.8\n09/15/2026,16.8,17.5,16.5,17.2\n"
MOF = ("Interest Rate,,,\nDate,1Y,2Y,3Y,4Y,5Y,6Y,7Y,8Y,9Y,10Y,15Y,20Y,25Y,30Y,40Y\n"
       "2026/9/15,1.57,1.861,2.005,2.197,2.338,2.46,2.584,2.753,2.888,3.028,3.572,3.865,4.12,4.106,4.105\n"
       "2026/9/16,1.558,1.852,1.989,2.169,2.309,2.434,2.553,2.719,2.858,2.998,3.545,3.841,4.097,4.075,4.072\n")


def fred_payload(url):
    import csv
    sid = url.split("series_id=")[1].split("&")[0]
    files = {"SP500": "SP500.csv", "DGS10": "DGS10.csv"}
    if sid in files and os.path.exists(os.path.join(HERE, files[sid])):
        rows = list(csv.reader(open(os.path.join(HERE, files[sid]))))[1:]
        obs = [{"date": r[0], "value": r[1] or "."} for r in rows][::-1]
    elif sid == "CPIAUCNS":
        obs = [{"date": "2026-08-01", "value": "330.0"}] + [{"date": f"2026-0{m}-01", "value": "329"} for m in range(7, 0, -1)] + \
              [{"date": f"2025-{m:02d}-01", "value": "319.1"} for m in range(12, 0, -1)]
    else:
        obs = [{"date": "2026-09-16", "value": "3.75"}, {"date": "2026-09-15", "value": "."}]
    return json.dumps({"observations": obs}).encode()


def fake_get(url, params=None, **kw):
    import urllib.parse
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    if "treasury.gov/resource-center" in url: return TSY.encode()
    if "newyorkfed.org/api" in url: return json.dumps(NYFED).encode()
    if "cftc.gov" in url: return json.dumps(COT).encode()
    if "cboe.com" in url: return CBOE.encode()
    if "mof.go.jp" in url: return MOF.encode()
    if "stlouisfed.org" in url: return fred_payload(url)
    raise RuntimeError("offline: " + url)


def main():
    F.http_get = fake_get
    os.environ["FRED_API_KEY"] = "test"
    assert F.tsy("10 Yr")() ["value"] == 5.01 and F.tsy("30 Yr")()["asof"] == "2026-09-16"
    s = F.srf(); assert s["value"] == 0 and s["asof"] == "2026-09-16", s
    c = F.cot(); assert c["value"] == 815 and c["asof"] == "2026-09-08", c
    v = F.vix(); assert v == {"value": 17.2, "asof": "2026-09-15", "source": "Cboe VIX daily history"}, v
    j = F.jgb10(); assert j["value"] == 3.0 and j["asof"] == "2026-09-16", j
    ff = F.fred_latest("DFEDTARU", "x")(); assert ff["value"] == 3.75
    cpi = F.fred_yoy("CPIAUCNS", "x")(); assert cpi["value"] == 3.4 and cpi["asof"] == "2026-08-01", cpi
    if os.path.exists(os.path.join(HERE, "SP500.csv")):
        r = F.corr(); assert r["value"] == 0.4 and r["asof"] == "2026-09-15", r
    # DXY formula sanity: roughly 97-101 for typical 2025-26 rates
    F._cache["fx"] = {s: {"2026-09-16": v} for s, v in zip(F.FX, [1.17, 147.0, 1.35, 1.38, 9.3, 0.79])}
    d = F.dxy(); assert 90 < d["value"] < 105, d
    # main(): failures keep previous values and mark stale
    tmp = tempfile.mkdtemp(); F.DATA = tmp
    json.dump({"indicators": {"tp10": {"value": 71, "asof": "2026-09-15", "source": "seed"}}}, open(os.path.join(tmp, "latest.json"), "w"))
    json.dump({"boj": {"value": 1.0, "asof": "2026-07-31", "source": "BoJ"}}, open(os.path.join(tmp, "manual.json"), "w"))
    F.main()
    out = json.load(open(os.path.join(tmp, "latest.json")))
    assert out["indicators"]["tp10"]["stale"] and out["indicators"]["tp10"]["value"] == 71
    assert out["indicators"]["boj"]["manual"] and out["indicators"]["ust10"]["value"] == 5.01
    shutil.rmtree(tmp)
    print("all tests passed")


if __name__ == "__main__":
    main()
