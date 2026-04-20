"""
ACKO GMC AI Visibility Dashboard Generator
Calls Ahrefs Brand Radar API, processes data, generates HTML dashboard + email summary.
Runs weekly via GitHub Actions.
"""
import requests, json, os, sys, smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ─── CONFIG ───
API_KEY = os.environ.get("ShbugPaZgMtWKeSNf_uPNIZFreKsLIXJzQ6LSbJ-", "")
REPORT_ID = "019c2715-499d-7f4a-8ff5-ee535e0b7c65"
BRAND = "Acko"
COMPETITORS = "GoDigit,Tata AIG,PolicyBazaar,InsuranceDekho,ICICI Lombard,HDFC ERGO,Bajaj Allianz,star health,pazcare,plumhq,onsurity,manipal cigna,ekincare,care health,niva bupa"
COUNTRY = "in"
BASE = "https://api.ahrefs.com/v3"
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

BRAND_LIST = ["Acko","Tata AIG","PolicyBazaar","ICICI Lombard","HDFC ERGO","Bajaj Allianz",
              "Star Health","Pazcare","Plum","Onsurity","Niva Bupa","Care Health","GoDigit","InsuranceDekho"]

today = datetime.now().strftime("%Y-%m-%d")
week_ago = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

def api(endpoint, params):
    params["report_id"] = REPORT_ID
    params["brand"] = BRAND
    params["competitors"] = COMPETITORS
    params["country"] = COUNTRY
    params["data_source"] = "chatgpt"
    params["prompts"] = "custom"
    r = requests.get(f"{BASE}/{endpoint}", headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()

# ─── PULL DATA ───
print("Pulling SoV overview...")
sov_raw = api("brand-radar/sov-overview", {"select": "brand,share_of_voice"})
sov = sorted(
    [{"brand": m["brand"], "sov": round(m["share_of_voice"] * 100, 1)}
     for m in sov_raw["metrics"] if m["brand"] != "Any brand"],
    key=lambda x: -x["sov"]
)

print("Pulling SoV history...")
sov_hist_raw = api("brand-radar/sov-history", {"date_from": week_ago, "date_to": today})
sov_history = {}
for day in sov_hist_raw.get("metrics", []):
    date_str = day["date"][:10]
    sov_history[date_str] = {}
    for entry in day["share_of_voice"]:
        if entry["brand"] != "Any brand":
            sov_history[date_str][entry["brand"]] = round(entry["share_of_voice"] * 100, 1)

print("Pulling mentions...")
mentions_raw = api("brand-radar/mentions-overview", {"select": "brand,total,only_target_brand,only_competitors_brands,target_and_competitors_brands"})
mentions = sorted(
    [{"brand": m["brand"], "total": m["total"]} for m in mentions_raw["metrics"]],
    key=lambda x: -x["total"]
)

print("Pulling cited domains...")
domains_raw = api("brand-radar/cited-domains", {"select": "domain,responses", "limit": "25", "date": today})
cited_domains = domains_raw.get("domains", [])

print("Pulling cited pages...")
pages_raw = api("brand-radar/cited-pages", {"select": "url,responses", "limit": "200", "date": today})
acko_pages = [p for p in pages_raw.get("pages", []) if "acko.com" in p["url"]]

print("Pulling AI responses...")
resp_raw = api("brand-radar/ai-responses", {
    "select": "search_queries,links,volume,data_source,tags,question,response,country",
    "date": today, "limit": "200"
})
ai_responses = resp_raw.get("ai_responses", [])

# ─── PROCESS PROMPT DATA ───
questions = []
for r in ai_responses:
    q = r.get("question", "")
    resp_text = r.get("response", "")
    vol = r.get("volume", 0) or 0
    links = r.get("links", [])
    acko_cited = any("acko.com" in (l.get("url", "")).lower() for l in links)
    acko_mentioned = "acko" in resp_text.lower()
    brands_found = [b for b in BRAND_LIST if b.lower() in resp_text.lower()]
    questions.append({
        "q": q, "vol": vol, "m": acko_mentioned, "c": acko_cited,
        "b": ", ".join(brands_found), "bc": len(brands_found)
    })

questions.sort(key=lambda x: -x["vol"])

total_q = len(questions)
acko_mentioned_count = sum(1 for q in questions if q["m"])
acko_cited_count = sum(1 for q in questions if q["c"])
total_volume = sum(q["vol"] for q in questions)
acko_volume = sum(q["vol"] for q in questions if q["m"])

acko_sov = next((s["sov"] for s in sov if s["brand"] == "Acko"), 0)
acko_rank = next((i + 1 for i, s in enumerate(sov) if s["brand"] == "Acko"), "?")
top_competitor = sov[0] if sov else {"brand": "N/A", "sov": 0}

# Brand mentions from text analysis
brand_text_mentions = {}
for q in questions:
    for b in q["b"].split(", "):
        b = b.strip()
        if b:
            brand_text_mentions[b] = brand_text_mentions.get(b, 0) + 1
brand_mentions_sorted = sorted(brand_text_mentions.items(), key=lambda x: -x[1])

print(f"Processed: {total_q} prompts, ACKO SoV={acko_sov}%, mentioned in {acko_mentioned_count}")

# ─── KPI HISTORY (accumulates across weekly runs) ───
kpi_history_file = "kpi_history.json"
kpi_history = {}
if os.path.exists(kpi_history_file):
    with open(kpi_history_file) as f:
        kpi_history = json.load(f)

kpi_history[today] = {
    "mentioned_count": acko_mentioned_count,
    "mentioned_total": total_q,
    "cited_count": acko_cited_count,
    "cited_total": total_q,
    "total_volume": total_volume,
    "volume_reach": acko_volume
}

with open(kpi_history_file, "w") as f:
    json.dump(kpi_history, f, indent=2)

print(f"KPI history: {len(kpi_history)} snapshots saved to {kpi_history_file}")

kpi_history_json = json.dumps(kpi_history)

# ─── GENERATE HTML ───

# Build date options for selects
dates_sorted = sorted(sov_history.keys())
date_options_a = "\n".join(
    f'<option value="{d}" {"selected" if d == dates_sorted[-1] else ""}>{datetime.strptime(d, "%Y-%m-%d").strftime("%b %d, %Y")}</option>'
    for d in dates_sorted
)
date_options_b = "\n".join(
    f'<option value="{d}" {"selected" if d == dates_sorted[-2] and len(dates_sorted) > 1 else ""}>{datetime.strptime(d, "%Y-%m-%d").strftime("%b %d, %Y")}</option>'
    for d in dates_sorted
)

# Brand mentions data for JS
bm_data = json.dumps([{"brand": b, "count": c} for b, c in brand_mentions_sorted])

# Cited domains data for initial render (pin ACKO at top)
acko_domain = None
other_domains = []
for d in cited_domains[:20]:
    if "acko" in d["domain"]:
        acko_domain = d
    else:
        other_domains.append(d)

domain_table_rows = ""
if acko_domain:
    max_d = cited_domains[0]["responses"] if cited_domains else 1
    pct = acko_domain["responses"] / max_d * 100
    domain_table_rows += f'''<tr style="background: rgba(108, 99, 255, 0.1);">
<td><strong style="color: #6c63ff;">{acko_domain["domain"]}</strong></td>
<td style="width: 80px;"><strong style="color: #6c63ff;">{acko_domain["responses"]}</strong></td>
<td style="flex: 1;"><div style="background: #6c63ff; height: 6px; width: {pct:.1f}%; border-radius: 2px;"></div></td>
</tr>'''

max_d = cited_domains[0]["responses"] if cited_domains else 1
for d in other_domains[:19]:
    pct = d["responses"] / max_d * 100
    domain_table_rows += f'''<tr>
<td>{d["domain"]}</td>
<td style="width: 80px;">{d["responses"]}</td>
<td style="flex: 1;"><div style="background: #3b82f6; height: 6px; width: {pct:.1f}%; border-radius: 2px;"></div></td>
</tr>'''

# ACKO pages table
acko_page_rows = ""
for p in acko_pages:
    acko_page_rows += f'''<tr style="background: rgba(108, 99, 255, 0.1);">
<td><strong style="color: #6c63ff;">{p["url"]}</strong></td>
<td style="color: #6c63ff;"><strong>{p["responses"]}</strong></td>
</tr>'''

# Questions JSON for filtering
questions_json = json.dumps([{
    "q": q["q"],
    "vol": q["vol"],
    "mentioned": q["m"],
    "cited": q["c"],
    "brands": q["b"],
    "bc": q["bc"]
} for q in questions])

# Gap analysis
gaps = [q for q in questions if not q["m"] and q["vol"] > 0][:15]
wins = [q for q in questions if q["m"] and q["c"]][:15]

gap_rows = ""
for q in gaps:
    gap_rows += f'''<tr>
<td style="font-size: 0.85rem;">{q["q"]}</td>
<td><span style="display: inline-block; background: #374151; color: #9ca3af; padding: 0.25rem 0.5rem; border-radius: 0.25rem; font-size: 0.8rem; white-space: nowrap;">{q["vol"]}</span></td>
<td style="font-size: 0.8rem;">{q["b"]}</td>
</tr>'''

win_rows = ""
for q in wins:
    win_rows += f'''<tr>
<td style="font-size: 0.85rem;">{q["q"]}</td>
<td><span style="display: inline-block; background: #374151; color: #9ca3af; padding: 0.25rem 0.5rem; border-radius: 0.25rem; font-size: 0.8rem; white-space: nowrap;">{q["vol"]}</span></td>
</tr>'''

# Build SoV_HISTORY object for JS
sov_history_json = json.dumps(sov_history)

html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ACKO GMC AI Visibility Dashboard</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0a0e1a;
            color: #e5e7eb;
            line-height: 1.6;
        }}
        .header {{
            background: linear-gradient(135deg, #111827 0%, #1e2a4a 100%);
            border-bottom: 2px solid #6c63ff;
            padding: 2rem;
            margin-bottom: 1.5rem;
        }}
        .header h1 {{
            font-size: 1.8rem;
            color: #6c63ff;
            margin-bottom: 0.5rem;
        }}
        .header p {{
            font-size: 0.9rem;
            color: #9ca3af;
        }}
        .container {{
            max-width: 1400px;
            margin: 0 auto;
            padding: 0 1rem;
        }}
        .controls-bar {{
            background: #111827;
            border: 1px solid #1e2a4a;
            border-radius: 0.5rem;
            padding: 1.5rem;
            margin-bottom: 2rem;
            display: flex;
            gap: 2rem;
            align-items: flex-end;
            flex-wrap: wrap;
        }}
        .control-group {{
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }}
        .control-group label {{
            font-size: 0.8rem;
            color: #9ca3af;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            font-weight: 600;
        }}
        .control-group select {{
            padding: 0.75rem;
            background: #1e2a4a;
            border: 1px solid #374151;
            border-radius: 0.5rem;
            color: #e5e7eb;
            font-size: 0.9rem;
        }}
        .compare-check {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}
        .compare-check input[type="checkbox"] {{
            width: 20px;
            height: 20px;
            cursor: pointer;
        }}
        .compare-check label {{
            margin: 0;
            text-transform: none;
            letter-spacing: normal;
            cursor: pointer;
            color: #e5e7eb;
            font-size: 0.9rem;
        }}
        .compare-section {{
            display: none;
        }}
        .compare-section.active {{
            display: flex;
        }}
        .apply-btn {{
            padding: 0.75rem 1.5rem;
            background: #6c63ff;
            border: none;
            border-radius: 0.5rem;
            color: #fff;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.3s;
        }}
        .apply-btn:hover {{
            background: #5a51e0;
        }}
        .header-status {{
            font-size: 0.9rem;
            color: #9ca3af;
            margin-bottom: 1rem;
            padding: 0 1rem;
        }}
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }}
        .kpi-card {{
            background: #111827;
            border: 1px solid #1e2a4a;
            border-radius: 0.5rem;
            padding: 1.5rem;
            text-align: center;
        }}
        .kpi-card .label {{
            font-size: 0.85rem;
            color: #9ca3af;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 0.5rem;
        }}
        .kpi-card .value {{
            font-size: 2rem;
            font-weight: bold;
            color: #6c63ff;
            margin-bottom: 0.5rem;
        }}
        .kpi-card .subtext {{
            font-size: 0.8rem;
            color: #6b7280;
            margin-bottom: 0.5rem;
        }}
        .kpi-card .delta {{
            font-size: 0.85rem;
            font-weight: 600;
            margin-top: 0.5rem;
        }}
        .delta.up {{
            color: #10b981;
        }}
        .delta.down {{
            color: #ef4444;
        }}
        .section {{
            margin-bottom: 2rem;
        }}
        .section-title {{
            font-size: 1.3rem;
            font-weight: 600;
            color: #fff;
            margin-bottom: 1rem;
            padding-bottom: 0.5rem;
            border-bottom: 2px solid #6c63ff;
        }}
        .bar-chart {{
            display: flex;
            flex-direction: column;
            gap: 0.8rem;
        }}
        .bar-row {{
            display: flex;
            align-items: center;
            gap: 1rem;
        }}
        .bar-label {{
            min-width: 140px;
            font-size: 0.9rem;
            font-weight: 500;
        }}
        .bar-label.acko {{
            color: #6c63ff;
            font-weight: 600;
        }}
        .bar-container {{
            flex: 1;
            height: 28px;
            background: #1e2a4a;
            border-radius: 0.25rem;
            overflow: hidden;
            position: relative;
        }}
        .bar {{
            height: 100%;
            background: #3b82f6;
            border-radius: 0.25rem;
            transition: width 0.3s;
            display: flex;
            align-items: center;
            justify-content: flex-end;
            padding-right: 0.5rem;
        }}
        .bar.acko {{
            background: #6c63ff;
        }}
        .bar-value {{
            color: #fff;
            font-size: 0.8rem;
            font-weight: 600;
        }}
        .table-wrapper {{
            background: #111827;
            border: 1px solid #1e2a4a;
            border-radius: 0.5rem;
            overflow-x: auto;
            max-height: 500px;
            overflow-y: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 0.9rem;
        }}
        thead {{
            position: sticky;
            top: 0;
            background: #1e2a4a;
        }}
        th {{
            padding: 1rem;
            text-align: left;
            color: #9ca3af;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.8rem;
            letter-spacing: 0.05em;
            border-bottom: 1px solid #374151;
        }}
        td {{
            padding: 0.8rem 1rem;
            border-bottom: 1px solid #1e2a4a;
        }}
        tr:hover {{
            background: #1e2a4a;
        }}
        .badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .badge-yes {{
            background: #064e3b;
            color: #10b981;
        }}
        .badge-no {{
            background: #450a0a;
            color: #ef4444;
        }}
        .controls {{
            display: flex;
            gap: 1rem;
            margin-bottom: 1.5rem;
            align-items: center;
            flex-wrap: wrap;
        }}
        .controls select,
        .controls input {{
            background: #1e2a4a;
            border: 1px solid #374151;
            border-radius: 0.5rem;
            padding: 0.75rem;
            color: #e5e7eb;
            font-size: 0.9rem;
        }}
        .controls input::placeholder {{
            color: #6b7280;
        }}
        .gap-panels {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 2rem;
        }}
        .volume-badge {{
            display: inline-block;
            background: #374151;
            color: #9ca3af;
            padding: 0.25rem 0.5rem;
            border-radius: 0.25rem;
            font-size: 0.8rem;
            white-space: nowrap;
        }}
        @media (max-width: 1024px) {{
            .gap-panels {{
                grid-template-columns: 1fr;
            }}
            .kpi-grid {{
                grid-template-columns: repeat(2, 1fr);
            }}
            .controls-bar {{
                flex-direction: column;
                align-items: flex-start;
            }}
        }}
        @media (max-width: 640px) {{
            .kpi-grid {{
                grid-template-columns: 1fr;
            }}
            .bar-row {{
                flex-direction: column;
                align-items: stretch;
            }}
            .bar-label {{
                min-width: unset;
            }}
            .header h1 {{
                font-size: 1.4rem;
            }}
            .controls-bar {{
                flex-direction: column;
                align-items: flex-start;
            }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>ACKO GMC AI Visibility Dashboard</h1>
        <p>Generative Engine Optimisation | Data Source: ChatGPT | {total_q} Tracked Prompts | Report ID: BR-{today}</p>
    </div>

    <div class="container">
        <!-- DATE CONTROLS -->
        <div class="controls-bar">
            <div class="control-group">
                <label>Period A</label>
                <select id="dateSelectA">
                    {date_options_a}
                </select>
            </div>
            <div class="compare-check">
                <input type="checkbox" id="compareToggle">
                <label for="compareToggle">Compare with Period B</label>
            </div>
            <div class="compare-section" id="comparePeriodSection">
                <div class="control-group">
                    <label>Period B</label>
                    <select id="dateSelectB">
                        {date_options_b}
                    </select>
                </div>
            </div>
            <button class="apply-btn" id="applyBtn">Apply</button>
        </div>

        <div class="header-status" id="headerStatus">Viewing: {datetime.strptime(dates_sorted[-1], "%Y-%m-%d").strftime("%b %d, %Y")}</div>

        <!-- KPI CARDS -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="label">ACKO Share of Voice</div>
                <div class="value" id="kpiSoV">{acko_sov}%</div>
                <div class="subtext" id="kpiSoVRank">Rank #{acko_rank} of {len(sov)} brands</div>
                <div class="delta" id="kpiSoVDelta" style="display: none;"></div>
            </div>
            <div class="kpi-card">
                <div class="label">ACKO Mentioned In</div>
                <div class="value" id="kpiMentioned">{acko_mentioned_count}/{total_q}</div>
                <div class="subtext" id="kpiMentionedSub">{acko_mentioned_count/total_q*100:.0f}% of AI responses</div>
                <div class="delta" id="kpiMentionedDelta" style="display: none;"></div>
            </div>
            <div class="kpi-card">
                <div class="label">ACKO Cited In</div>
                <div class="value" id="kpiCited">{acko_cited_count}/{total_q}</div>
                <div class="subtext" id="kpiCitedSub">acko.com linked in {acko_cited_count/total_q*100:.0f}% responses</div>
                <div class="delta" id="kpiCitedDelta" style="display: none;"></div>
            </div>
            <div class="kpi-card">
                <div class="label">Total AI Search Volume</div>
                <div class="value" id="kpiVolume">{total_volume:,}</div>
                <div class="subtext" id="kpiVolumeSub">Across {total_q} tracked prompts</div>
                <div class="delta" id="kpiVolumeDelta" style="display: none;"></div>
            </div>
            <div class="kpi-card">
                <div class="label">ACKO Volume Reach</div>
                <div class="value" id="kpiReach">{acko_volume:,}</div>
                <div class="subtext" id="kpiReachSub">{acko_volume/total_volume*100:.0f}% of total volume</div>
                <div class="delta" id="kpiReachDelta" style="display: none;"></div>
            </div>
            <div class="kpi-card">
                <div class="label">Top Competitor</div>
                <div class="value" id="kpiTopCompetitor">{top_competitor["brand"]}</div>
                <div class="subtext" id="kpiTopCompetitorValue">{top_competitor["sov"]}% SoV</div>
            </div>
        </div>

        <!-- SECTION 1: SHARE OF VOICE -->
        <div class="section">
            <h2 class="section-title" id="sovChartTitle">Share of Voice — All Brands ({datetime.strptime(dates_sorted[-1], "%Y-%m-%d").strftime("%b %d")})</h2>
            <div class="bar-chart" id="sovBarChart">
            </div>
        </div>

        <!-- SECTION 2: BRAND MENTIONS -->
        <div class="section">
            <h2 class="section-title">Brand Mentions in AI Responses</h2>
            <div class="bar-chart" id="brandMentionsChart">
            </div>
        </div>

        <!-- SECTION 3: TOP 20 CITED DOMAINS -->
        <div class="section">
            <h2 class="section-title">Top 20 Cited Domains</h2>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Domain</th>
                            <th style="width: 80px;">Responses</th>
                            <th style="flex: 1;">Visual</th>
                        </tr>
                    </thead>
                    <tbody>
                        {domain_table_rows}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- SECTION 4: ACKO CITED PAGES -->
        <div class="section">
            <h2 class="section-title">ACKO Cited Pages</h2>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Page URL</th>
                            <th style="width: 120px;">Responses</th>
                        </tr>
                    </thead>
                    <tbody>
                        {acko_page_rows if acko_page_rows else '<tr><td colspan="2" style="text-align: center; color: #6b7280;">No ACKO pages cited yet</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- SECTION 5: PROMPT-LEVEL BREAKDOWN -->
        <div class="section">
            <h2 class="section-title">Prompt-Level Breakdown ({total_q} Total)</h2>
            <div class="controls">
                <select id="filterSelect">
                    <option value="all">All Prompts</option>
                    <option value="mentioned">ACKO Mentioned</option>
                    <option value="notMentioned">ACKO Not Mentioned</option>
                    <option value="cited">ACKO Cited</option>
                    <option value="notCited">ACKO Not Cited</option>
                </select>
                <input type="text" id="searchInput" placeholder="Search prompts...">
            </div>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th style="width: 350px;">Prompt</th>
                            <th style="width: 80px;">AI Vol</th>
                            <th style="width: 80px;">Mentioned</th>
                            <th style="width: 80px;">Cited</th>
                            <th style="width: 200px;">Brands Present</th>
                        </tr>
                    </thead>
                    <tbody id="promptsTableBody">
                    </tbody>
                </table>
            </div>
        </div>

        <!-- SECTION 6: GAP ANALYSIS -->
        <div class="section">
            <h2 class="section-title">Gap Analysis</h2>
            <div class="gap-panels">
                <div>
                    <h3 style="color: #ef4444; margin-bottom: 1rem; font-size: 1rem;">High-Volume Gaps — ACKO Missing</h3>
                    <div class="table-wrapper">
                        <table>
                            <thead>
                                <tr>
                                    <th>Prompt</th>
                                    <th style="width: 80px;">AI Vol</th>
                                    <th style="width: 150px;">Brands Present</th>
                                </tr>
                            </thead>
                            <tbody>
                                {gap_rows if gap_rows else '<tr><td colspan="3" style="text-align: center; color: #6b7280;">No gaps found — great coverage!</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
                <div>
                    <h3 style="color: #10b981; margin-bottom: 1rem; font-size: 1rem;">ACKO Wins — Mentioned + Cited</h3>
                    <div class="table-wrapper">
                        <table>
                            <thead>
                                <tr>
                                    <th>Prompt</th>
                                    <th style="width: 80px;">AI Vol</th>
                                </tr>
                            </thead>
                            <tbody>
                                {win_rows if win_rows else '<tr><td colspan="2" style="text-align: center; color: #6b7280;">No wins found yet</td></tr>'}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
const SOV_HISTORY = {sov_history_json};
const QUESTIONS = {questions_json};
const BRAND_MENTIONS = {bm_data};
const KPI_HISTORY = {kpi_history_json};

let currentDate = "{dates_sorted[-1]}";
let compareDate = null;
let isComparing = false;

function getDateLabel(date) {{
  const d = new Date(date + "T00:00:00");
  return d.toLocaleDateString('en-US', {{month: 'short', day: 'numeric', year: 'numeric'}});
}}

function getSoVRank(date) {{
  const data = SOV_HISTORY[date];
  const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]);
  return sorted.findIndex(e => e[0] === "Acko") + 1;
}}

function getTopCompetitor(date) {{
  const data = SOV_HISTORY[date];
  const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]);
  return {{name: sorted[0][0], value: sorted[0][1].toFixed(1)}};
}}

function renderSoVChart(date) {{
  const data = SOV_HISTORY[date];
  const sorted = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const maxVal = sorted[0][1];

  const chart = document.getElementById('sovBarChart');
  chart.innerHTML = sorted.map(([brand, val]) => {{
    const pct = (val / maxVal) * 100;
    const isAcko = brand === "Acko";
    return `
      <div class="bar-row">
        <div class="bar-label ${{isAcko ? 'acko' : ''}}">${{brand}}</div>
        <div class="bar-container"><div class="bar ${{isAcko ? 'acko' : ''}}" style="width: ${{pct}}%"><span class="bar-value">${{val.toFixed(1)}}%</span></div></div>
      </div>
    `;
  }}).join('');

  document.getElementById('sovChartTitle').textContent = `Share of Voice — All Brands (${{getDateLabel(date)}})`;
}}

function renderBrandMentionsChart() {{
  const maxVal = BRAND_MENTIONS[0].count;
  const chart = document.getElementById('brandMentionsChart');
  chart.innerHTML = BRAND_MENTIONS.map(item => {{
    const pct = (item.count / maxVal) * 100;
    const isAcko = item.brand === "Acko";
    return `
      <div class="bar-row">
        <div class="bar-label ${{isAcko ? 'acko' : ''}}">${{item.brand}}</div>
        <div class="bar-container"><div class="bar ${{isAcko ? 'acko' : ''}}" style="width: ${{pct}}%"><span class="bar-value">${{item.count}}</span></div></div>
      </div>
    `;
  }}).join('');
}}

function renderSoVTable() {{}}
function updateTrendHighlight() {{}}

function calculateDelta(current, previous) {{
  const diff = current - previous;
  const pp = diff.toFixed(1);

  if (diff > 0) {{
    return `▲ +${{pp}}pp`;
  }} else if (diff < 0) {{
    return `▼ ${{pp}}pp`;
  }} else {{
    return '→ 0pp';
  }}
}}

function findNearestKPI(date) {{
  // Find nearest KPI_HISTORY entry <= selected date
  const dates = Object.keys(KPI_HISTORY).sort();
  let best = null;
  for (const d of dates) {{
    if (d <= date) best = d;
  }}
  return best ? KPI_HISTORY[best] : null;
}}

function showDelta(elId, current, previous, suffix) {{
  const el = document.getElementById(elId);
  if (!el) return;
  const diff = current - previous;
  if (diff > 0) {{
    el.textContent = `▲ +${{diff}}${{suffix || ''}}`;
    el.className = 'delta up';
  }} else if (diff < 0) {{
    el.textContent = `▼ ${{diff}}${{suffix || ''}}`;
    el.className = 'delta down';
  }} else {{
    el.textContent = `→ 0${{suffix || ''}}`;
    el.className = 'delta';
  }}
  el.style.display = 'block';
}}

function hideDelta(elId) {{
  const el = document.getElementById(elId);
  if (el) el.style.display = 'none';
}}

function updateKPIs() {{
  const ackoSoV = SOV_HISTORY[currentDate]["Acko"];
  const rank = getSoVRank(currentDate);
  const topComp = getTopCompetitor(currentDate);

  document.getElementById('kpiSoV').textContent = ackoSoV.toFixed(1) + '%';
  document.getElementById('kpiSoVRank').textContent = `Rank #${{rank}} of ${{Object.keys(SOV_HISTORY[currentDate]).length}} brands`;
  document.getElementById('kpiTopCompetitor').textContent = topComp.name;
  document.getElementById('kpiTopCompetitorValue').textContent = topComp.value + '% SoV';

  // Update other KPI cards from KPI_HISTORY if available
  const currentKPI = findNearestKPI(currentDate);
  if (currentKPI) {{
    document.getElementById('kpiMentioned').textContent = `${{currentKPI.mentioned_count}}/${{currentKPI.mentioned_total}}`;
    document.getElementById('kpiMentionedSub').textContent = `${{Math.round(currentKPI.mentioned_count / currentKPI.mentioned_total * 100)}}% of AI responses`;
    document.getElementById('kpiCited').textContent = `${{currentKPI.cited_count}}/${{currentKPI.cited_total}}`;
    document.getElementById('kpiCitedSub').textContent = `acko.com linked in ${{Math.round(currentKPI.cited_count / currentKPI.cited_total * 100)}}% responses`;
    document.getElementById('kpiVolume').textContent = currentKPI.total_volume.toLocaleString();
    document.getElementById('kpiVolumeSub').textContent = `Across ${{currentKPI.mentioned_total}} tracked prompts`;
    document.getElementById('kpiReach').textContent = currentKPI.volume_reach.toLocaleString();
    document.getElementById('kpiReachSub').textContent = `${{Math.round(currentKPI.volume_reach / currentKPI.total_volume * 100)}}% of total volume`;
  }}

  // Deltas
  if (isComparing && compareDate) {{
    // SoV delta (always available from SOV_HISTORY)
    const compareSoV = SOV_HISTORY[compareDate]["Acko"];
    const sovDiff = (ackoSoV - compareSoV).toFixed(1);
    const sovEl = document.getElementById('kpiSoVDelta');
    if (sovDiff > 0) {{
      sovEl.textContent = `▲ +${{sovDiff}}pp`;
      sovEl.className = 'delta up';
    }} else if (sovDiff < 0) {{
      sovEl.textContent = `▼ ${{sovDiff}}pp`;
      sovEl.className = 'delta down';
    }} else {{
      sovEl.textContent = '→ 0pp';
      sovEl.className = 'delta';
    }}
    sovEl.style.display = 'block';

    // Other KPI deltas (from KPI_HISTORY snapshots)
    const compareKPI = findNearestKPI(compareDate);
    if (currentKPI && compareKPI) {{
      showDelta('kpiMentionedDelta', currentKPI.mentioned_count, compareKPI.mentioned_count, '');
      showDelta('kpiCitedDelta', currentKPI.cited_count, compareKPI.cited_count, '');
      showDelta('kpiVolumeDelta', currentKPI.total_volume, compareKPI.total_volume, '');
      showDelta('kpiReachDelta', currentKPI.volume_reach, compareKPI.volume_reach, '');
    }} else {{
      hideDelta('kpiMentionedDelta');
      hideDelta('kpiCitedDelta');
      hideDelta('kpiVolumeDelta');
      hideDelta('kpiReachDelta');
    }}
  }} else {{
    hideDelta('kpiSoVDelta');
    hideDelta('kpiMentionedDelta');
    hideDelta('kpiCitedDelta');
    hideDelta('kpiVolumeDelta');
    hideDelta('kpiReachDelta');
  }}
}}

function updateHeaderStatus() {{
  const status = document.getElementById('headerStatus');
  if (isComparing && compareDate) {{
    status.textContent = `Comparing ${{getDateLabel(currentDate)}} vs ${{getDateLabel(compareDate)}}`;
  }} else {{
    status.textContent = `Viewing: ${{getDateLabel(currentDate)}}`;
  }}
}}

function updateDashboard() {{
  renderSoVChart(currentDate);
  renderBrandMentionsChart();
  renderSoVTable();
  updateTrendHighlight();
  updateKPIs();
  updateHeaderStatus();
}}

document.getElementById('compareToggle').addEventListener('change', (e) => {{
  isComparing = e.target.checked;
  document.getElementById('comparePeriodSection').classList.toggle('active', isComparing);
  if (!isComparing) {{
    compareDate = null;
  }} else {{
    compareDate = document.getElementById('dateSelectB').value;
  }}
  updateDashboard();
}});

document.getElementById('applyBtn').addEventListener('click', () => {{
  currentDate = document.getElementById('dateSelectA').value;
  if (isComparing) {{
    compareDate = document.getElementById('dateSelectB').value;
  }}
  updateDashboard();
}});

// Filter & Search
let filteredData = [...QUESTIONS];

document.getElementById('filterSelect').addEventListener('change', (e) => {{
    const filter = e.target.value;
    filteredData = QUESTIONS.filter(item => {{
        if (filter === 'mentioned') return item.mentioned;
        if (filter === 'notMentioned') return !item.mentioned;
        if (filter === 'cited') return item.cited;
        if (filter === 'notCited') return !item.cited;
        return true;
    }});
    applySearch();
}});

document.getElementById('searchInput').addEventListener('input', applySearch);

function applySearch() {{
    const query = document.getElementById('searchInput').value.toLowerCase();
    const display = filteredData.filter(item => item.q.toLowerCase().includes(query));
    renderPrompts(display);
}}

function renderPrompts(data) {{
    const tbody = document.getElementById('promptsTableBody');
    tbody.innerHTML = data.map((item, idx) => `
        <tr>
            <td style="font-size: 0.85rem;">${{item.q}}</td>
            <td><span class="volume-badge">${{item.vol.toLocaleString()}}</span></td>
            <td><span class="badge ${{item.mentioned ? 'badge-yes' : 'badge-no'}}">${{item.mentioned ? 'YES' : 'NO'}}</span></td>
            <td><span class="badge ${{item.cited ? 'badge-yes' : 'badge-no'}}">${{item.cited ? 'YES' : 'NO'}}</span></td>
            <td style="font-size: 0.8rem;">${{item.brands}}</td>
        </tr>
    `).join('');
}}

// Initial render
updateDashboard();
renderPrompts(QUESTIONS);
    </script>
</body>
</html>'''

with open("index.html", "w") as f:
    f.write(html)

print(f"Dashboard generated: index.html ({len(html):,} bytes)")
print(f"Date: {today}")
print(f"Prompts: {total_q}, ACKO SoV: {acko_sov}%")

# ─── GENERATE EMAIL SUMMARY ───
dashboard_url = os.environ.get("DASHBOARD_URL", "https://aloke-acko.github.io/acko-geo-dashboard/")

# Week-over-week comparison
prev_week_sov = None
if len(dates_sorted) >= 2:
    prev_date = dates_sorted[0]
    prev_week_sov = sov_history.get(prev_date, {}).get("Acko", None)

sov_change = ""
if prev_week_sov is not None and prev_week_sov > 0:
    diff = acko_sov - prev_week_sov
    arrow = "▲" if diff > 0 else "▼" if diff < 0 else "→"
    sov_change = f" ({arrow} {diff:+.1f}pp vs last week)"

# Top 5 gaps
top_gaps = [q for q in questions if not q["m"] and not q["c"] and q["vol"] > 0][:5]
gap_lines = ""
for g in top_gaps:
    gap_lines += f'<li>{g["q"]} <span style="color:#f59e0b">({g["vol"]:,} vol)</span></li>'

# Top 5 wins
top_wins = [q for q in questions if q["m"] and q["c"]][:5]
win_lines = ""
for w in top_wins:
    win_lines += f'<li>{w["q"]} <span style="color:#10b981">({w["vol"]:,} vol)</span></li>'

email_html = f"""
<div style="font-family:'Segoe UI',Arial,sans-serif;max-width:640px;margin:0 auto;background:#0a0e1a;color:#e0e6ed;padding:24px;border-radius:12px">
  <h1 style="font-size:20px;color:#6c63ff;margin-bottom:4px">ACKO GMC AI Visibility — Weekly Summary</h1>
  <p style="color:#8892a4;font-size:13px;margin-bottom:20px">{today} · {total_q} tracked prompts · Source: ChatGPT</p>

  <table style="width:100%;border-collapse:collapse;margin-bottom:20px">
    <tr>
      <td style="background:#111827;border:1px solid #1e2a4a;border-radius:8px;padding:14px;text-align:center;width:33%">
        <div style="font-size:11px;color:#8892a4;text-transform:uppercase">Share of Voice</div>
        <div style="font-size:26px;font-weight:800;color:#6c63ff">{acko_sov}%</div>
        <div style="font-size:11px;color:#8892a4">Rank #{acko_rank}{sov_change}</div>
      </td>
      <td style="background:#111827;border:1px solid #1e2a4a;border-radius:8px;padding:14px;text-align:center;width:33%">
        <div style="font-size:11px;color:#8892a4;text-transform:uppercase">Mentioned In</div>
        <div style="font-size:26px;font-weight:800;color:#10b981">{acko_mentioned_count}/{total_q}</div>
        <div style="font-size:11px;color:#8892a4">{acko_mentioned_count/total_q*100:.0f}% of responses</div>
      </td>
      <td style="background:#111827;border:1px solid #1e2a4a;border-radius:8px;padding:14px;text-align:center;width:33%">
        <div style="font-size:11px;color:#8892a4;text-transform:uppercase">Cited In</div>
        <div style="font-size:26px;font-weight:800;color:#06b6d4">{acko_cited_count}/{total_q}</div>
        <div style="font-size:11px;color:#8892a4">{acko_cited_count/total_q*100:.0f}% of responses</div>
      </td>
    </tr>
  </table>

  <div style="background:#111827;border:1px solid #1e2a4a;border-radius:8px;padding:14px;margin-bottom:16px">
    <h3 style="font-size:13px;color:#ef4444;margin-bottom:8px">Top Gaps — ACKO Missing (High Volume)</h3>
    <ol style="font-size:12px;color:#e0e6ed;padding-left:20px;line-height:1.8">{gap_lines if gap_lines else '<li style="color:#8892a4">No gaps found — great coverage!</li>'}</ol>
  </div>

  <div style="background:#111827;border:1px solid #1e2a4a;border-radius:8px;padding:14px;margin-bottom:16px">
    <h3 style="font-size:13px;color:#10b981;margin-bottom:8px">Top Wins — ACKO Mentioned + Cited</h3>
    <ol style="font-size:12px;color:#e0e6ed;padding-left:20px;line-height:1.8">{win_lines if win_lines else '<li style="color:#8892a4">No wins yet — keep optimising!</li>'}</ol>
  </div>

  <div style="text-align:center;margin-top:20px">
    <a href="{dashboard_url}" style="display:inline-block;background:#6c63ff;color:#fff;padding:10px 24px;border-radius:8px;text-decoration:none;font-size:13px;font-weight:600">View Full Dashboard →</a>
  </div>

  <p style="text-align:center;font-size:11px;color:#6b7280;margin-top:16px">Auto-generated by ACKO GEO Dashboard · Powered by Ahrefs Brand Radar</p>
</div>
"""

with open("email_summary.html", "w") as f:
    f.write(email_html)

print(f"Email summary generated: email_summary.html")

# ─── SEND EMAIL (if SMTP configured) ───
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
EMAIL_TO = os.environ.get("EMAIL_TO", "")

if SMTP_HOST and SMTP_USER and SMTP_PASS and EMAIL_TO:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"ACKO AI Visibility Report — {today} | SoV {acko_sov}% (#{acko_rank})"
        msg["From"] = SMTP_USER
        msg["To"] = EMAIL_TO

        # Plain text fallback
        plain = f"""ACKO GMC AI Visibility — Weekly Summary ({today})

SoV: {acko_sov}% (Rank #{acko_rank}){sov_change}
Mentioned: {acko_mentioned_count}/{total_q} ({acko_mentioned_count/total_q*100:.0f}%)
Cited: {acko_cited_count}/{total_q} ({acko_cited_count/total_q*100:.0f}%)
Volume Reach: {acko_volume:,} / {total_volume:,}

Dashboard: {dashboard_url}
"""
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(email_html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            recipients = [e.strip() for e in EMAIL_TO.split(",")]
            server.sendmail(SMTP_USER, recipients, msg.as_string())

        print(f"Email sent to: {EMAIL_TO}")
    except Exception as e:
        print(f"Email sending failed: {e}")
else:
    print("SMTP not configured — skipping email")
