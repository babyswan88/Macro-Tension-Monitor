# Macro Tension Monitor

Free dashboards showing how close macro themes are to their breaking points: levels, triggers, dated events and news. Levels of tension, not trade recommendations.

Everything runs at no cost:

- **GitHub Actions** fetches data every weekday and commits it to `data/`.
- **GitHub Pages** serves the site straight from this repository.
- All data comes from free public feeds (see the table below).

## One-time setup (about 30 minutes)

### 1. Get the free API keys

| Key | Where | Needed for |
|---|---|---|
| `FRED_API_KEY` (required) | https://fred.stlouisfed.org/docs/api/api_key.html (create a free FRED account, then "Request API Key") | fed funds, S&P 500, correlation, CPI, core PCE, diesel, Brent |
| `EIA_API_KEY` (optional) | https://www.eia.gov/opendata/register.php | WTI futures |
| `TWELVEDATA_API_KEY` (optional) | https://twelvedata.com/register (free Basic plan) | USD/JPY and the computed dollar index |

Without an optional key, those cards show their last reading marked **stale** and everything else still works.

### 2. Create the repository

1. Sign in to GitHub and click **New repository**.
2. Name it (for example `macro-tension-monitor`), set it to **Public**, and create it. Public repositories get free Actions minutes and free Pages hosting.
3. On the new repository page, click **uploading an existing file**, drag in **everything inside this folder** (including the hidden `.github` folder and `.nojekyll`), and click **Commit changes**.
   - If your file browser hides `.github`, turn on hidden files (Mac: Cmd+Shift+. in Finder), or use GitHub Desktop instead.
   - Check it worked: the repository should show a `.github/workflows/fetch.yml` file. If it doesn't, click **Add file → Create new file**, type `.github/workflows/fetch.yml` as the name, paste in the contents of that file from this folder, and commit.

### 3. Add the keys as secrets

Repository **Settings → Secrets and variables → Actions → New repository secret**. Add each key using the exact names above.

### 4. Turn on the site

**Settings → Pages →** Source: **Deploy from a branch**, Branch: **main**, folder **/ (root)** → Save. After a minute the site is live at `https://<your-username>.github.io/<repo-name>/`.

### 5. Run the first fetch

**Actions** tab → if prompted, enable workflows → **Fetch data → Run workflow**. When it finishes (1–2 minutes) the site shows live data. After that it runs by itself at 12:00 and 22:30 UTC on weekdays.

## How it fits together

```
scripts/fetch.py          pulls every feed, writes data/latest.json and data/history.json
data/manual.json          values entered by hand or by the Claude editor (BoJ rate, basis-trade estimate, CME margin, auction tails)
data/events.json          curated dated events, tagged by dashboard
data/news/<dashboard>.json  news entries for each dashboard
dashboards/index.json     list of dashboards shown on the home page
dashboards/<id>.json      one dashboard: indicators, triggers, ranges, explanations
index.html, dashboard.html, assets/   the site
```

If a feed fails, the last good value is kept and the card is marked **stale**. The failure reason appears when you hover over the label, and in the Actions run log.

## Sources

| Indicator | Source | Key |
|---|---|---|
| 2y, 10y, 30y yields | U.S. Treasury daily par yield curve | none |
| Fed funds upper bound | FRED DFEDTARU | FRED |
| 10y term premium | NY Fed ACM term premia spreadsheet | none |
| VIX | Cboe VIX daily history CSV | none |
| S&P 500 | FRED SP500 | FRED |
| SPX/10y correlation | FRED SP500 and DGS10, computed | FRED |
| Brent (spot) | FRED DCOILBRENTEU (EIA) | FRED |
| WTI futures | EIA RCLC1 | EIA |
| Diesel | FRED GASDESW (EIA weekly) | FRED |
| CPI y/y | FRED CPIAUCNS, 12-month change | FRED |
| Core PCE y/y | FRED PCEPILFE, 12-month change | FRED |
| 10y JGB | Japan Ministry of Finance CSV | none |
| USD/JPY | Twelve Data daily close | Twelve Data |
| Dollar index | Computed from six Twelve Data FX closes with the ICE DXY formula | Twelve Data |
| SRF usage | NY Fed Markets API | none |
| Leveraged-fund Treasury futures shorts | CFTC Traders in Financial Futures API | none |
| Treasury auctions (results and announced) | Treasury Fiscal Data API | none |
| BoJ rate, basis-trade size, CME margin, auction tails | `data/manual.json` | n/a |

Removed because no free feed exists: MOVE index, 10y SOFR swap spread.

## Adding a dashboard

1. Copy `dashboards/liquidity.json` to `dashboards/<new-id>.json` and edit the groups and indicators. Every indicator `id` must exist in `scripts/fetch.py` (add a fetcher if needed) or in `data/manual.json`.
2. Add an entry to `dashboards/index.json`.
3. Optionally add `data/news/<new-id>.json` (start with `[]`) and tag events in `data/events.json` with the new id.

## Tests

`python3 tests/test_fetch.py` checks every parser against saved samples of each source's real format, without network access.
