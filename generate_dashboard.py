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
API_KEY = os.environ.get("AHREFS_API_KEY", "")
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

# dates_sorted is needed by email section below
dates_sorted = sorted(sov_history.keys())

# ─── BUILD GEO_DATA JSON for React UI ───

# Cited domains: pin ACKO at top
acko_domain_entry = None
other_domain_entries = []
for d in cited_domains[:20]:
    if "acko" in d["domain"].lower():
        acko_domain_entry = d
    else:
        other_domain_entries.append(d)

top_domains_list = []
if acko_domain_entry:
    top_domains_list.append({"domain": acko_domain_entry["domain"], "responses": acko_domain_entry["responses"], "isAcko": True})
for d in other_domain_entries[:19]:
    top_domains_list.append({"domain": d["domain"], "responses": d["responses"]})

# ACKO pages list
acko_pages_list = [{"url": p["url"], "responses": p["responses"]} for p in acko_pages]

# Prompts list
prompts_list = [{
    "prompt": q["q"],
    "vol": q["vol"],
    "mentioned": q["m"],
    "cited": q["c"],
    "brands": [b.strip() for b in q["b"].split(", ") if b.strip()] if q["b"] else []
} for q in questions]

# Gap analysis: top 15 missing
gaps_missing = []
for q in questions:
    if not q["m"] and q["vol"] > 0:
        brands = [b.strip() for b in q["b"].split(", ") if b.strip()] if q["b"] else []
        gaps_missing.append({"prompt": q["q"], "vol": q["vol"], "brands": brands})
    if len(gaps_missing) >= 15:
        break

# Gap analysis: top 15 wins (mentioned + cited)
gaps_wins = []
for q in questions:
    if q["m"] and q["c"]:
        gaps_wins.append({"prompt": q["q"], "vol": q["vol"], "brands": []})
    if len(gaps_wins) >= 15:
        break

# SoV list for bar chart
sov_list = [{"brand": s["brand"], "sov": s["sov"], "isAcko": s["brand"] == "Acko"} for s in sov]

# Brand mentions list
mentions_list = [{"name": b, "mentions": c, "isAcko": b == "Acko"} for b, c in brand_mentions_sorted]

# ─── KPI DELTAS (week-over-week from kpi_history) ───
kpi_dates = sorted(kpi_history.keys())
mentioned_delta = None
mentioned_delta_dir = None
cited_delta = None
cited_delta_dir = None
volume_reach_delta = None
volume_reach_delta_dir = None

if len(kpi_dates) >= 2:
    current_kpi = kpi_history[kpi_dates[-1]]
    prev_kpi = kpi_history[kpi_dates[-2]]
    
    m_diff = current_kpi["mentioned_count"] - prev_kpi["mentioned_count"]
    if m_diff != 0:
        mentioned_delta = f"{m_diff:+d}"
        mentioned_delta_dir = "up" if m_diff > 0 else "down"
    
    c_diff = current_kpi["cited_count"] - prev_kpi["cited_count"]
    if c_diff != 0:
        cited_delta = f"{c_diff:+d}"
        cited_delta_dir = "up" if c_diff > 0 else "down"
    
    vr_diff = current_kpi["volume_reach"] - prev_kpi["volume_reach"]
    if vr_diff != 0:
        volume_reach_delta = f"{vr_diff:+,d}"
        volume_reach_delta_dir = "up" if vr_diff > 0 else "down"

# Period display
period_display = datetime.strptime(today, "%Y-%m-%d").strftime("%b %d, %Y")

# Build GEO_DATA object
geo_data = {
    "meta": {
        "title": "ACKO GMC AI Visibility Dashboard",
        "subtitle": "Generative Engine Optimisation",
        "source": "ChatGPT",
        "trackedPrompts": total_q,
        "reportId": f"BR-{today}",
        "period": period_display,
        "generatedAt": period_display
    },
    "kpis": {
        "shareOfVoice": {
            "value": acko_sov,
            "unit": "%",
            "caption": f"Rank #{acko_rank} of {len(sov)} brands"
        },
        "mentioned": {
            "value": f"{acko_mentioned_count}/{total_q}",
            "caption": f"{acko_mentioned_count/total_q*100:.0f}% of AI responses",
            "delta": mentioned_delta,
            "deltaDir": mentioned_delta_dir
        },
        "cited": {
            "value": f"{acko_cited_count}/{total_q}",
            "caption": f"acko.com linked in {acko_cited_count/total_q*100:.0f}% responses",
            "delta": cited_delta,
            "deltaDir": cited_delta_dir
        },
        "aiSearchVol": {
            "value": total_volume,
            "caption": f"Across {total_q} tracked prompts"
        },
        "volumeReach": {
            "value": acko_volume,
            "caption": f"{acko_volume/total_volume*100:.0f}% of total volume",
            "delta": volume_reach_delta,
            "deltaDir": volume_reach_delta_dir
        },
        "topCompetitor": {
            "value": top_competitor["brand"],
            "caption": f"{top_competitor['sov']}% SoV"
        }
    },
    "shareOfVoice": sov_list,
    "mentions": mentions_list,
    "topDomains": top_domains_list,
    "ackoPages": acko_pages_list,
    "prompts": prompts_list,
    "gapsMissing": gaps_missing,
    "gapsWins": gaps_wins
}

geo_data_json = json.dumps(geo_data, indent=2)

# ─── BUILD SOV_HISTORY (weekly: one entry per ISO week, keep latest date) ───
from collections import OrderedDict

weekly_sov = {}
for date_str in sorted(sov_history.keys()):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    iso_year, iso_week, _ = dt.isocalendar()
    week_key = f"{iso_year}-W{iso_week:02d}"
    weekly_sov[week_key] = (date_str, sov_history[date_str])

sov_history_weekly = {}
for week_key, (date_str, data) in weekly_sov.items():
    sov_history_weekly[date_str] = data

sov_history_weekly_json = json.dumps(sov_history_weekly)

print(f"GEO_DATA JSON: {len(geo_data_json):,} bytes")
print(f"SOV_HISTORY weekly: {len(sov_history_weekly)} dates")

# ─── BUILD HTML ───
html_template = '''<!DOCTYPE html>
<html lang="en" data-variant="editorial">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>ACKO GMC AI Visibility Dashboard</title>
<style>
/* ==========================================================================
   ACKO GEO Dashboard — Design System
   Three variants on a shared dark base. Switch via [data-variant] on <html>.
   ========================================================================== */

@import url('https://fonts.googleapis.com/css2?family=Geist:wght@300;400;500;600;700;800&family=Geist+Mono:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=swap');

* { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  /* ACKO purple anchor */
  --acko-50:  #f3f0ff;
  --acko-100: #e6dfff;
  --acko-200: #cdbcff;
  --acko-300: #b39aff;
  --acko-400: #9577ff;
  --acko-500: #7c5cff;   /* primary */
  --acko-600: #6a47f0;
  --acko-700: #5734d4;
  --acko-800: #4527a8;
  --acko-900: #2f1c75;

  /* Neutrals — deep ink */
  --ink-0:  #06060c;
  --ink-1:  #0a0a14;
  --ink-2:  #0f0f1c;
  --ink-3:  #141425;
  --ink-4:  #1c1c30;
  --ink-5:  #25253d;
  --ink-6:  #34344f;
  --ink-7:  #4a4a6a;

  --fg-0:  #ffffff;
  --fg-1:  #e8e8f5;
  --fg-2:  #b8b8d0;
  --fg-3:  #8a8aa8;
  --fg-4:  #5e5e80;

  --good: #4ade80;
  --bad:  #ff6470;
  --warn: #ffb547;
  --info: #5aa9ff;

  --radius-sm: 6px;
  --radius:    10px;
  --radius-lg: 16px;
  --radius-xl: 22px;

  /* Typography defaults (overridden per variant) */
  --font-sans: 'Geist', -apple-system, system-ui, sans-serif;
  --font-mono: 'Geist Mono', ui-monospace, 'SF Mono', Menlo, monospace;
  --font-display: 'Geist', sans-serif;

  --tracking-tight: -0.02em;
  --tracking-normal: 0;
  --tracking-wide: 0.08em;

  /* Card surface defaults */
  --surface: var(--ink-2);
  --surface-2: var(--ink-3);
  --surface-3: var(--ink-4);
  --border: rgba(255,255,255,0.06);
  --border-strong: rgba(255,255,255,0.12);

  --shadow-sm: 0 1px 2px rgba(0,0,0,0.35);
  --shadow:    0 8px 24px rgba(0,0,0,0.4);
  --shadow-lg: 0 24px 60px rgba(0,0,0,0.5);
}

html, body {
  background: var(--ink-0);
  color: var(--fg-1);
  font-family: var(--font-sans);
  font-size: 14px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}

/* =========================================================================
   VARIANT A — EDITORIAL (default)
   Confident, generous, large display type, subtle gradient washes.
   ========================================================================= */
[data-variant="editorial"] {
  --surface: #0d0d18;
  --surface-2: #14142a;
  --surface-3: #1a1a36;
  --border: rgba(124,92,255,0.10);
  --border-strong: rgba(124,92,255,0.22);
  --font-display: 'Instrument Serif', 'Geist', serif;
}
[data-variant="editorial"] body {
  background:
    radial-gradient(1200px 600px at 0% -10%, rgba(124,92,255,0.18), transparent 60%),
    radial-gradient(900px 500px at 100% 110%, rgba(106,71,240,0.10), transparent 60%),
    var(--ink-0);
  background-attachment: fixed;
}

/* =========================================================================
   VARIANT B — TERMINAL (dense, monospace, hairline rules)
   ========================================================================= */
[data-variant="terminal"] {
  --surface: #08080f;
  --surface-2: #0c0c18;
  --surface-3: #11111f;
  --border: rgba(255,255,255,0.08);
  --border-strong: rgba(124,92,255,0.35);
  --font-sans: 'Geist Mono', ui-monospace, monospace;
  --font-display: 'Geist Mono', monospace;
  --radius: 2px;
  --radius-sm: 0;
  --radius-lg: 4px;
  --radius-xl: 4px;
}
[data-variant="terminal"] body {
  background: var(--ink-0);
}
[data-variant="terminal"] .card { box-shadow: none; }

/* =========================================================================
   VARIANT C — GLOW (soft glassy cards, purple aura)
   ========================================================================= */
[data-variant="glow"] {
  --surface: rgba(20,20,38,0.7);
  --surface-2: rgba(28,28,52,0.7);
  --surface-3: rgba(36,36,68,0.7);
  --border: rgba(255,255,255,0.06);
  --border-strong: rgba(124,92,255,0.4);
  --radius: 18px;
  --radius-lg: 24px;
  --radius-xl: 28px;
}
[data-variant="glow"] body {
  background:
    radial-gradient(900px 500px at 15% 0%, rgba(124,92,255,0.25), transparent 55%),
    radial-gradient(800px 600px at 85% 100%, rgba(60,40,180,0.20), transparent 55%),
    radial-gradient(600px 400px at 50% 50%, rgba(124,92,255,0.06), transparent 60%),
    #050510;
  background-attachment: fixed;
}
[data-variant="glow"] .card {
  backdrop-filter: blur(20px) saturate(140%);
  -webkit-backdrop-filter: blur(20px) saturate(140%);
  background: linear-gradient(180deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01));
  border: 1px solid var(--border);
  box-shadow: 0 1px 0 rgba(255,255,255,0.04) inset, 0 30px 60px -20px rgba(0,0,0,0.6);
}
[data-variant="glow"] .kpi { position: relative; overflow: hidden; }
[data-variant="glow"] .kpi::after {
  content: ""; position: absolute; inset: -1px; border-radius: inherit; pointer-events: none;
  background: radial-gradient(400px 200px at var(--mx, 50%) 0%, rgba(124,92,255,0.18), transparent 60%);
}

/* =========================================================================
   APP SHELL
   ========================================================================= */
.app {
  display: grid;
  grid-template-columns: 208px minmax(0, 1fr);
  min-height: 100vh;
}
@media (max-width: 900px) {
  .app { grid-template-columns: 1fr; }
  .sidebar { position: relative; height: auto; }
}
.sidebar {
  position: sticky; top: 0; height: 100vh;
  border-right: 1px solid var(--border);
  background: linear-gradient(180deg, rgba(124,92,255,0.04), transparent 40%), var(--ink-1);
  display: flex; flex-direction: column;
  padding: 22px 16px;
  z-index: 5;
}
[data-variant="terminal"] .sidebar { background: var(--ink-1); }
[data-variant="glow"] .sidebar {
  background: linear-gradient(180deg, rgba(124,92,255,0.08), rgba(0,0,0,0.4));
  backdrop-filter: blur(14px);
}

.sidebar-brand {
  display: flex; align-items: center; gap: 10px;
  padding: 4px 6px 22px 6px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 16px;
}
.sidebar-brand .acko-wordmark {
  height: 28px; width: auto; display: block;
  color: var(--fg-0);
}
.src-badge {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; color: var(--fg-2);
  white-space: nowrap;
}

.nav-section {
  margin-bottom: 18px;
}
.nav-section-label {
  text-transform: uppercase; letter-spacing: 0.12em; font-size: 10px;
  color: var(--fg-4); padding: 0 8px 6px; font-weight: 600;
}
.nav-item {
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; border-radius: 8px;
  color: var(--fg-2); font-size: 13px; cursor: pointer;
  user-select: none;
  border: 1px solid transparent;
  transition: background .15s ease, color .15s ease, border-color .15s ease;
  white-space: nowrap;
}
.nav-item > span:first-of-type { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.nav-item:hover { background: rgba(255,255,255,0.04); color: var(--fg-1); }
.nav-item.active {
  background: linear-gradient(180deg, rgba(124,92,255,0.16), rgba(124,92,255,0.06));
  color: var(--fg-0);
  border-color: rgba(124,92,255,0.25);
}
.nav-item svg { width: 16px; height: 16px; flex: 0 0 16px; opacity: 0.85; }
.nav-item .badge {
  margin-left: auto; font-size: 10px; padding: 2px 6px; border-radius: 999px;
  background: rgba(124,92,255,0.18); color: var(--acko-200);
  font-family: var(--font-mono); font-weight: 600;
}

.sidebar-footer {
  margin-top: auto;
  padding: 12px;
  border-top: 1px solid var(--border);
  font-size: 11px; color: var(--fg-4);
  display: flex; align-items: flex-start; gap: 8px;
}
.sidebar-footer > div { min-width: 0; flex: 1; }
.sidebar-footer > div > div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sidebar-footer .dot {
  width: 6px; height: 6px; border-radius: 50%; background: var(--good);
  box-shadow: 0 0 8px var(--good);
}

/* Main content */
.main { min-width: 0; padding: 28px 32px 80px; max-width: 100%; overflow-x: hidden; }
@media (max-width: 1100px) { .main { padding: 22px 20px 60px; } }
[data-variant="terminal"] .main { padding: 18px 24px 60px; }

/* =========================================================================
   HEADER
   ========================================================================= */
.page-header {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 24px;
  padding-bottom: 22px; margin-bottom: 22px;
  border-bottom: 1px solid var(--border);
  flex-wrap: nowrap;
}
.page-title {
  font-family: var(--font-display);
  font-size: 28px; font-weight: 600; letter-spacing: var(--tracking-tight);
  line-height: 1.1;
  color: var(--fg-0);
}
[data-variant="editorial"] .page-title {
  font-size: 36px; font-weight: 400; letter-spacing: -0.02em;
}
[data-variant="editorial"] .page-title em {
  font-style: italic; color: var(--acko-300); font-weight: 400;
}
[data-variant="terminal"] .page-title {
  font-size: 22px; text-transform: uppercase; letter-spacing: 0.04em;
}
[data-variant="terminal"] .page-title em { font-style: normal; color: var(--acko-400); }

.page-subtitle {
  margin-top: 8px; color: var(--fg-3); font-size: 13px;
  display: flex; flex-wrap: wrap; align-items: center; gap: 14px;
}
.page-subtitle .pill {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 999px;
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--border);
  color: var(--fg-2); font-size: 12px;
  font-family: var(--font-mono);
  white-space: nowrap;
}
.page-subtitle .pill .key { color: var(--fg-4); }
.page-subtitle .pill svg { width: 12px; height: 12px; }

.page-header-left { min-width: 0; flex: 1 1 auto; }
.page-header-left .page-subtitle { row-gap: 8px; }
.header-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 9px 14px; border-radius: var(--radius);
  background: rgba(255,255,255,0.04);
  border: 1px solid var(--border-strong);
  color: var(--fg-1); font-size: 13px; font-weight: 500;
  cursor: pointer; transition: all .15s ease;
  font-family: inherit;
}
.btn:hover { background: rgba(255,255,255,0.08); }
.btn svg { width: 14px; height: 14px; }
.btn.primary {
  background: linear-gradient(180deg, var(--acko-500), var(--acko-700));
  border-color: var(--acko-600);
  color: white;
  box-shadow: 0 6px 18px -4px rgba(124,92,255,0.45);
}
.btn.primary:hover { filter: brightness(1.08); }

/* Period bar */
.period-bar {
  display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  padding: 10px 16px; margin-bottom: 22px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 12px;
}
.period-bar .date-pill { white-space: nowrap; }
.period-bar .label {
  text-transform: uppercase; font-size: 10px; letter-spacing: 0.14em;
  color: var(--fg-4); font-weight: 600; padding-right: 4px;
}
.date-pill {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 7px 12px; border-radius: var(--radius-sm);
  background: var(--surface-2); border: 1px solid var(--border);
  color: var(--fg-1); font-family: var(--font-mono); font-size: 12px; cursor: pointer;
}
.date-pill:hover { border-color: var(--border-strong); }
.date-pill svg { width: 12px; height: 12px; opacity: 0.7; }
.range-presets { display: flex; gap: 4px; }
.range-preset {
  padding: 6px 10px; border-radius: var(--radius-sm);
  background: transparent; border: 1px solid transparent;
  color: var(--fg-3); font-size: 11px; cursor: pointer; font-family: var(--font-mono);
}
.range-preset:hover { color: var(--fg-1); }
.range-preset.active { background: rgba(124,92,255,0.14); color: var(--acko-200); border-color: rgba(124,92,255,0.3); }
.compare-toggle {
  display: inline-flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;
  font-size: 12px; color: var(--fg-2);
  white-space: nowrap;
}
.period-bar .updated {
  margin-left: auto; color: var(--fg-4); font-size: 11px; font-family: var(--font-mono);
  white-space: nowrap;
}
.compare-toggle .switch {
  width: 28px; height: 16px; border-radius: 999px; background: var(--ink-5); position: relative;
  transition: background .15s;
}
.compare-toggle .switch::after {
  content: ""; position: absolute; left: 2px; top: 2px; width: 12px; height: 12px;
  border-radius: 50%; background: var(--fg-2); transition: all .15s;
}
.compare-toggle.on .switch { background: var(--acko-600); }
.compare-toggle.on .switch::after { left: 14px; background: white; }

/* =========================================================================
   CARDS
   ========================================================================= */
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
}
.card-header {
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
  padding: 18px 22px 12px;
  flex-wrap: wrap;
}
.card-header > div:first-child { min-width: 0; flex: 1; }
.card-title {
  font-size: 14px; font-weight: 600; letter-spacing: -0.01em;
  color: var(--fg-0); display: flex; align-items: center; gap: 10px;
  flex-wrap: wrap;
  min-width: 0;
}
.card-title > span:not(.accent):not(.count) { white-space: nowrap; }
[data-variant="editorial"] .card-title {
  font-family: var(--font-display); font-weight: 400; font-size: 20px; letter-spacing: -0.01em;
}
[data-variant="editorial"] .card-title em { font-style: italic; color: var(--acko-300); }
[data-variant="terminal"] .card-title {
  text-transform: uppercase; font-size: 12px; letter-spacing: 0.12em; font-weight: 600;
}
.card-title .accent {
  display: inline-block; width: 4px; height: 16px; background: var(--acko-500); border-radius: 2px;
}
[data-variant="editorial"] .card-title .accent { display: none; }
.card-subtitle { color: var(--fg-3); font-size: 12px; }
.card-actions { display: flex; gap: 6px; align-items: center; }
.card-body { padding: 6px 22px 22px; }
.card-body.flush { padding: 0; }

/* =========================================================================
   KPI TILES
   ========================================================================= */
.kpi-grid {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px;
  margin-bottom: 28px;
}
@media (max-width: 760px)  { .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
.kpi-value { word-break: break-word; }

.kpi {
  position: relative;
  padding: 18px 18px 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.kpi.acko {
  border-color: rgba(124,92,255,0.3);
  background: linear-gradient(180deg, rgba(124,92,255,0.08), transparent 50%), var(--surface);
}
[data-variant="terminal"] .kpi.acko {
  background: var(--surface);
  border-color: var(--acko-700);
  border-left: 3px solid var(--acko-500);
}
.kpi-label {
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em;
  color: var(--fg-3); font-weight: 600;
  display: flex; align-items: center; gap: 6px;
}
.kpi-label .dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--acko-500); box-shadow: 0 0 8px var(--acko-500);
}
.kpi-value {
  margin-top: 14px;
  font-family: var(--font-display);
  font-size: 36px; font-weight: 600;
  letter-spacing: -0.03em; line-height: 1;
  color: var(--fg-0);
  font-variant-numeric: tabular-nums;
}
[data-variant="editorial"] .kpi-value {
  font-weight: 400; font-size: 44px; letter-spacing: -0.02em;
}
[data-variant="editorial"] .kpi-value .unit {
  font-style: italic; color: var(--acko-300); font-size: 0.7em;
}
[data-variant="terminal"] .kpi-value {
  font-size: 32px; font-weight: 500;
}
.kpi-value .unit { color: var(--acko-300); margin-left: 2px; }
.kpi-value.text { font-size: 26px; letter-spacing: -0.01em; }
[data-variant="editorial"] .kpi-value.text { font-size: 32px; }
.kpi-caption {
  margin-top: 10px; color: var(--fg-3); font-size: 12px;
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
}
.kpi-spark {
  display: block;
  height: 26px; width: 100%; margin-top: 8px; opacity: 0.85;
}
.kpi-delta {
  display: inline-flex; align-items: center; gap: 3px;
  font-family: var(--font-mono); font-size: 11px; font-weight: 600;
  padding: 2px 6px; border-radius: 4px;
}
.kpi-delta.up   { color: var(--good); background: rgba(74,222,128,0.10); }
.kpi-delta.down { color: var(--bad);  background: rgba(255,100,112,0.10); }
.kpi-delta svg { width: 10px; height: 10px; }

/* =========================================================================
   BAR CHART (horizontal)
   ========================================================================= */
.bars { display: flex; flex-direction: column; gap: 4px; }
.bar-row {
  display: grid; grid-template-columns: 130px 1fr 64px;
  align-items: center; gap: 14px;
  padding: 6px 4px;
  border-radius: 6px;
  cursor: pointer;
  transition: background .12s;
}
.bar-row:hover { background: rgba(255,255,255,0.03); }
.bar-row.acko .bar-name { color: var(--acko-300); font-weight: 600; }
.bar-name {
  font-size: 13px; color: var(--fg-2);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-variant-numeric: tabular-nums;
}
.bar-track {
  height: 22px; background: rgba(255,255,255,0.03);
  border-radius: 4px; overflow: hidden; position: relative;
}
[data-variant="terminal"] .bar-track {
  height: 14px; border-radius: 0;
  background: rgba(255,255,255,0.04);
}
.bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #4d8eff, #5aa9ff);
  border-radius: inherit;
  position: relative;
  transition: width .6s cubic-bezier(.2,.8,.2,1);
}
.bar-row.acko .bar-fill {
  background: linear-gradient(90deg, var(--acko-600), var(--acko-400));
  box-shadow: 0 0 16px rgba(124,92,255,0.4);
}
[data-variant="terminal"] .bar-fill {
  background: var(--info);
}
[data-variant="terminal"] .bar-row.acko .bar-fill { background: var(--acko-500); box-shadow: none; }
.bar-value {
  font-family: var(--font-mono); font-size: 12px; color: var(--fg-1);
  text-align: right; font-variant-numeric: tabular-nums; font-weight: 500;
}
.bar-row.acko .bar-value { color: var(--acko-200); }

/* =========================================================================
   TABLES
   ========================================================================= */
.table-wrap { overflow-x: auto; max-height: 680px; overflow-y: auto; }
.table-wrap::-webkit-scrollbar { width: 6px; }
.table-wrap::-webkit-scrollbar-track { background: var(--ink-2); border-radius: 3px; }
.table-wrap::-webkit-scrollbar-thumb { background: var(--ink-6); border-radius: 3px; }
.table-wrap::-webkit-scrollbar-thumb:hover { background: var(--ink-7); }
.table thead { position: sticky; top: 0; z-index: 5; background: var(--surface); }
.table {
  width: 100%; border-collapse: collapse;
  font-size: 13px;
}
.table th {
  text-align: left; font-size: 10px; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--fg-3); font-weight: 600;
  padding: 12px 16px;
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
  user-select: none;
}
.table th.sortable { cursor: pointer; }
.table th.sortable:hover { color: var(--fg-1); }
.table th .sort-arrow {
  display: inline-block; margin-left: 4px; opacity: 0.6;
  font-size: 9px;
}
.table td {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  color: var(--fg-1);
  vertical-align: middle;
}
.table tr:last-child td { border-bottom: none; }
.table tr:hover td { background: rgba(124,92,255,0.04); }
.table tr.acko td { background: rgba(124,92,255,0.06); }
.table tr.acko td:first-child { box-shadow: inset 3px 0 0 var(--acko-500); }
.table .num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; text-align: right; }
.table .url {
  color: var(--acko-300); font-family: var(--font-mono); font-size: 12px;
  display: inline-flex; align-items: center; gap: 6px;
  max-width: 100%;
}
.table .url > span.urltext {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 380px; display: inline-block; vertical-align: bottom;
}
.table .url svg { flex-shrink: 0; opacity: 0.6; width: 11px; height: 11px; }
.table .url:hover { text-decoration: underline; }
.table svg { width: 14px; height: 14px; }
.table .url svg { width: 11px; height: 11px; }

.cell-num-pill {
  display: inline-block;
  padding: 3px 9px; border-radius: 5px;
  background: rgba(255,255,255,0.05);
  font-family: var(--font-mono); font-size: 11px;
  color: var(--fg-1); font-variant-numeric: tabular-nums; font-weight: 500;
}
.tag-yes, .tag-no {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 9px; border-radius: 999px;
  font-size: 10px; font-weight: 600; letter-spacing: 0.06em;
  text-transform: uppercase; font-family: var(--font-mono);
}
.tag-yes { color: var(--good); background: rgba(74,222,128,0.10); border: 1px solid rgba(74,222,128,0.25); }
.tag-no  { color: var(--bad);  background: rgba(255,100,112,0.10); border: 1px solid rgba(255,100,112,0.25); }
.tag-yes::before, .tag-no::before { content: ""; width: 5px; height: 5px; border-radius: 50%; }
.tag-yes::before { background: var(--good); box-shadow: 0 0 6px var(--good); }
.tag-no::before  { background: var(--bad); }

.brand-chip {
  display: inline-block;
  padding: 2px 7px; border-radius: 4px;
  background: rgba(255,255,255,0.05);
  font-size: 11px; color: var(--fg-2);
  margin: 1px 3px 1px 0;
  border: 1px solid var(--border);
  white-space: nowrap;
  font-family: var(--font-mono);
}
.brand-chip.acko { background: rgba(124,92,255,0.18); color: var(--acko-200); border-color: rgba(124,92,255,0.3); }

.dom-bar {
  display: inline-block;
  height: 4px; background: var(--info);
  border-radius: 999px; vertical-align: middle;
}
[data-variant="terminal"] .dom-bar { border-radius: 0; height: 2px; }
.acko-row .dom-bar { background: var(--acko-500); }

/* =========================================================================
   SECTION GRIDS
   ========================================================================= */
.row-2col { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; margin-bottom: 24px; }
@media (max-width: 1100px) { .row-2col { grid-template-columns: 1fr; } }

.section-title {
  font-family: var(--font-display);
  font-size: 22px; font-weight: 600; letter-spacing: -0.01em;
  margin: 12px 0 14px; color: var(--fg-0);
  display: flex; align-items: baseline; gap: 12px;
}
[data-variant="editorial"] .section-title {
  font-size: 32px; font-weight: 400;
}
[data-variant="editorial"] .section-title em { font-style: italic; color: var(--acko-300); }
[data-variant="terminal"] .section-title {
  font-size: 13px; text-transform: uppercase; letter-spacing: 0.12em; font-weight: 600;
}
.section-title .count {
  font-family: var(--font-mono); font-size: 13px; color: var(--fg-4); font-weight: 400;
}

/* Filter bar */
.filter-bar {
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 14px 22px;
  border-bottom: 1px solid var(--border);
}
.search {
  position: relative; flex: 1; min-width: 240px; max-width: 340px;
}
.search input {
  width: 100%; padding: 8px 12px 8px 32px; border-radius: var(--radius-sm);
  background: var(--surface-2); border: 1px solid var(--border);
  color: var(--fg-1); font-size: 13px; font-family: inherit;
  outline: none; transition: border-color .15s;
}
.search input:focus { border-color: var(--acko-600); }
.search svg { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); width: 14px; height: 14px; color: var(--fg-4); }

.filter-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: var(--radius-sm);
  background: var(--surface-2); border: 1px solid var(--border);
  color: var(--fg-2); font-size: 12px; cursor: pointer;
  user-select: none;
  font-family: inherit;
}
.filter-chip:hover { color: var(--fg-1); border-color: var(--border-strong); }
.filter-chip.active {
  background: rgba(124,92,255,0.14); color: var(--acko-200); border-color: rgba(124,92,255,0.35);
}
.filter-chip .ct {
  font-family: var(--font-mono); font-size: 10px; opacity: 0.7;
}

/* Volume bucket (small inline bar in table) */
.vol-cell {
  display: inline-flex; align-items: center; gap: 8px;
  font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 12px;
  font-weight: 500;
}
.vol-cell .vbar {
  width: 60px; height: 4px; border-radius: 999px;
  background: rgba(255,255,255,0.05); position: relative; overflow: hidden;
}
.vol-cell .vbar > span { position: absolute; left: 0; top: 0; bottom: 0; background: var(--info); border-radius: inherit; }
.vol-cell.high   .vbar > span { background: var(--bad); }
.vol-cell.med    .vbar > span { background: var(--warn); }
.vol-cell.low    .vbar > span { background: var(--info); }

/* Drill-down panel */
.drawer-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.55);
  backdrop-filter: blur(6px); z-index: 50;
  display: flex; justify-content: flex-end;
  animation: fade .18s ease;
}
@keyframes fade { from { opacity: 0; } to { opacity: 1; } }
.drawer {
  width: min(560px, 100%); height: 100%;
  background: var(--surface);
  border-left: 1px solid var(--border-strong);
  overflow-y: auto;
  animation: slidein .22s cubic-bezier(.2,.8,.2,1);
}
@keyframes slidein { from { transform: translateX(40px); opacity: 0; } to { transform: none; opacity: 1; } }
.drawer-head {
  padding: 22px 26px 16px; border-bottom: 1px solid var(--border);
  display: flex; align-items: flex-start; justify-content: space-between; gap: 12px;
}
.drawer-head h3 {
  font-family: var(--font-display); font-size: 22px; font-weight: 600;
  letter-spacing: -0.01em; line-height: 1.2;
}
[data-variant="editorial"] .drawer-head h3 { font-weight: 400; font-size: 28px; }
.drawer-head .close {
  background: rgba(255,255,255,0.05); border: 1px solid var(--border); border-radius: var(--radius-sm);
  width: 32px; height: 32px; display: grid; place-items: center; cursor: pointer; color: var(--fg-2);
}
.drawer-body { padding: 22px 26px; display: flex; flex-direction: column; gap: 22px; }
.drawer-stat-grid {
  display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 10px;
}
.drawer-stat {
  padding: 14px; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius);
}
.drawer-stat .label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--fg-3); font-weight: 600; }
.drawer-stat .val {
  font-family: var(--font-display); font-size: 24px; font-weight: 600; margin-top: 6px;
  letter-spacing: -0.02em; font-variant-numeric: tabular-nums;
}

/* =========================================================================
   GAP ANALYSIS — quadrant
   ========================================================================= */
.quadrant {
  position: relative;
  padding: 0;
  height: 460px;
}
.quadrant-svg { width: 100%; height: 100%; display: block; }
.quad-legend {
  display: flex; gap: 14px; flex-wrap: wrap;
  padding: 14px 22px; border-top: 1px solid var(--border);
  font-size: 11px; color: var(--fg-3);
}
.quad-legend .lg-dot {
  display: inline-block; width: 8px; height: 8px; border-radius: 50%; vertical-align: middle; margin-right: 6px;
}

.priority-list { display: flex; flex-direction: column; }
.priority-item {
  display: grid; grid-template-columns: 28px 1fr auto auto; gap: 14px;
  padding: 12px 22px; align-items: center;
  border-bottom: 1px solid var(--border);
  cursor: pointer; transition: background .12s;
}
.priority-item:hover { background: rgba(124,92,255,0.04); }
.priority-item:last-child { border-bottom: none; }
.priority-rank {
  font-family: var(--font-mono); font-size: 11px; color: var(--fg-4); font-weight: 600;
  text-align: right;
}
.priority-prompt { font-size: 13px; color: var(--fg-1); line-height: 1.4; }
.priority-vol { font-family: var(--font-mono); font-size: 12px; color: var(--fg-2); font-variant-numeric: tabular-nums; }
.priority-tag {
  font-size: 10px; padding: 3px 8px; border-radius: 999px; font-weight: 600;
  text-transform: uppercase; letter-spacing: 0.08em; font-family: var(--font-mono);
}
.priority-tag.miss { color: var(--bad); background: rgba(255,100,112,0.1); border: 1px solid rgba(255,100,112,0.25); }
.priority-tag.win  { color: var(--good); background: rgba(74,222,128,0.1); border: 1px solid rgba(74,222,128,0.25); }

/* =========================================================================
   UTILITIES
   ========================================================================= */
.hr { height: 1px; background: var(--border); margin: 24px 0; }
.mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
.muted { color: var(--fg-3); }
.dim { color: var(--fg-4); }
.flex { display: flex; }
.between { display: flex; justify-content: space-between; align-items: center; }
.ms-auto { margin-left: auto; }
.gap-2 { gap: 8px; } .gap-3 { gap: 12px; }

/* Global SVG safety — never explode */
svg { max-width: 100%; }
.nav-item svg, .btn svg, .table svg, .card-title svg, .filter-chip svg, .priority-tag svg, .tag-yes svg, .tag-no svg, .date-pill svg, .compare-toggle svg, .search svg, .drawer-head .close svg, .sort-arrow svg { width: 14px; height: 14px; }
.kpi svg:not(.kpi-spark) { width: 14px; height: 14px; }
.kpi-spark { width: 100% !important; height: 26px !important; }
/* Scrollbar */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--ink-5); border-radius: 999px; }
::-webkit-scrollbar-thumb:hover { background: var(--ink-6); }

/* Scroll-anchor offset for sidebar nav */
.scroll-anchor { scroll-margin-top: 24px; }

/* Period controls */
.period-bar { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.period-select { background:var(--surface-2); border:1px solid var(--border-strong); color:var(--fg-1); font-family:var(--font-mono); font-size:12px; padding:6px 28px 6px 10px; border-radius:var(--radius-sm); cursor:pointer; appearance:none; -webkit-appearance:none; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238a8aa8'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right 8px center; }
.period-select:focus { outline:none; border-color:var(--acko-500); }
.compare-toggle { display:flex; align-items:center; gap:8px; cursor:pointer; font-size:12px; color:var(--fg-3); user-select:none; }
.compare-toggle input[type="checkbox"] { width:16px; height:16px; accent-color:var(--acko-500); cursor:pointer; }
.compare-toggle:hover { color:var(--fg-2); }
.period-label { font-size:11px; text-transform:uppercase; letter-spacing:0.1em; color:var(--fg-4); font-weight:600; }
.compare-badge { display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:var(--radius-sm); background:rgba(124,92,255,0.12); color:var(--acko-300); font-size:11px; font-family:var(--font-mono); }

/* Info tooltip */
.info-tip { position:relative; display:inline-flex; align-items:center; justify-content:center; width:18px; height:18px; border-radius:50%; background:rgba(255,255,255,0.06); border:1px solid var(--border); color:var(--fg-4); font-size:10px; font-family:var(--font-mono); font-weight:600; cursor:help; margin-left:8px; flex-shrink:0; transition:all 0.15s; z-index:10; }
.info-tip:hover { background:rgba(124,92,255,0.15); border-color:var(--acko-500); color:var(--fg-2); z-index:200; }
.info-tip .info-popup { display:none; position:absolute; left:50%; top:calc(100% + 8px); transform:translateX(-50%); width:280px; padding:12px 14px; background:#0c0c1c; border:1px solid var(--border-strong); border-radius:var(--radius); box-shadow:0 12px 40px rgba(0,0,0,0.7); color:var(--fg-2); font-size:12px; font-family:var(--font-sans); font-weight:400; line-height:1.5; letter-spacing:0; text-transform:none; z-index:200; pointer-events:none; white-space:normal; }
.info-tip:hover .info-popup { display:block; }
/* Arrow on tooltip */
.info-tip .info-popup::before { content:''; position:absolute; top:-6px; left:50%; transform:translateX(-50%); width:12px; height:6px; clip-path:polygon(50% 0%,0% 100%,100% 100%); background:#0c0c1c; }
.info-tip .info-popup::after { content:''; position:absolute; top:-7px; left:50%; transform:translateX(-50%); width:14px; height:7px; clip-path:polygon(50% 0%,0% 100%,100% 100%); background:var(--border-strong); z-index:-1; }

/* KPI label highlight */
.kpi-label { font-weight:600 !important; color:var(--fg-1) !important; font-size:12.5px !important; letter-spacing:0.04em !important; }
.kpi.acko .kpi-label { color:var(--acko-200) !important; }
.kpi.acko .kpi-label .dot { background:var(--acko-400); }

</style>
</head>
<body>
<div id="root"></div>

<script src="https://unpkg.com/react@18.3.1/umd/react.production.min.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js" crossorigin="anonymous"></script>
<script src="https://unpkg.com/@babel/standalone@7.29.0/babel.min.js" crossorigin="anonymous"></script>

<script>

window.GEO_DATA = GEO_DATA_PLACEHOLDER;
window.ACKO_SVG = `<svg id="Layer_2" data-name="Layer 2" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 527.58 126.62" width="120" height="28" style="display:block"
  <defs></defs>
  <g id="Layer_1-2" data-name="Layer 1">
    <g>
      <g>
        <path fill="#ffffff" d="M225.37,21.1h22.24l35.44,84.42h-24.74l-6.07-14.97h-32.11l-5.95,14.97h-24.26l35.44-84.42ZM245.59,72.47l-9.28-23.83-9.39,23.83h18.67Z"></path>
        <path fill="#ffffff" d="M277.56,63.55v-.24c0-24.26,18.67-43.29,43.88-43.29,17.01,0,27.95,7.14,35.32,17.36l-17.36,13.44c-4.76-5.95-10.23-9.75-18.2-9.75-11.65,0-19.86,9.87-19.86,22v.24c0,12.49,8.21,22.24,19.86,22.24,8.68,0,13.8-4.04,18.79-10.11l17.36,12.37c-7.85,10.82-18.43,18.79-36.87,18.79-23.78,0-42.93-18.2-42.93-43.05Z"></path>
        <path fill="#ffffff" d="M364.6,21.1h23.07v34.25l28.66-34.25h27.35l-31.51,36.3,32.59,48.12h-27.71l-20.93-31.35-8.44,9.53v21.83h-23.07V21.1Z"></path>
        <path fill="#ffffff" d="M437.91,63.55v-.24c0-23.9,19.27-43.29,44.95-43.29s44.72,19.15,44.72,43.05v.24c0,23.91-19.27,43.29-44.95,43.29s-44.72-19.15-44.72-43.05ZM504.03,63.55v-.24c0-12.01-8.68-22.48-21.41-22.48s-21.05,10.23-21.05,22.24v.24c0,12.01,8.68,22.48,21.29,22.48s21.17-10.23,21.17-22.24Z"></path>
      </g>
      <g>
        <path fill="#ffffff" d="M168.83,44.06v38.35c0,2.94-1.37,5.49-4.14,7.25-34.68,22-60.94,31.24-70.12,33.81-12.67,3.55-19.38,3.18-20.48,3.1-23.13-1.62-62.1-32.45-62.1-32.45,0,0,31.79,7.52,105.26-26.34,13.77-6.35,29.15-13.2,47.02-21.22,0,0,3.9-1.63,4.56-2.5Z"></path>
        <path fill="#ffffff" d="M166.79,42.99c.44-.27,1.03-.64,1.23-1.37.18-.66.1-1.48-.27-2.07-.68-1.08-1.81-1.8-3.06-2.59C130.01,14.97,103.75,5.72,94.56,3.15,81.89-.4,75.18-.03,74.09.05,50.96,1.67,11.99,32.5,11.99,32.5c0,0,31.79-7.52,105.26,26.34,2.36,1.09,4.78,2.19,7.24,3.32l40.13-18.02s1.48-.71,2.17-1.14Z"></path>
        <path fill="#ffffff" d="M49.22,63.28c.04-8.18,1.1-16.37,3.04-24.46,0,0,.26-1.01.26-1.01-14.47-3.3-25.13-3.99-31.54-3.99-5.29,0-8.09.46-9.05.66-1.72.37-2.94,1.69-3.81,2.81C2.96,43.94,0,53.41,0,63.28H0c0,9.87,2.96,19.34,8.13,25.98.87,1.11,2.08,2.44,3.81,2.81.96.2,3.76.66,9.05.66,6.4,0,17.07-.69,31.54-3.99,0,0-.26-1.01-.26-1.01-1.94-8.09-3-16.28-3.04-24.46Z"></path>
      </g>
    </g>
  </g>
</svg>`;


window.SOV_HISTORY = SOV_HISTORY_PLACEHOLDER;
</script>

<script type="text/babel">
const { useState, useEffect, useMemo, useRef } = React;

// ─── ICONS ───
const Icon = {
  Dashboard: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></svg>,
  Brand: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M12 2 4 6v6c0 5 3.5 9 8 10 4.5-1 8-5 8-10V6l-8-4z"/></svg>,
  Globe: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>,
  List: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/></svg>,
  Target: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>,
  Calendar: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>,
  Refresh: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M3 21v-5h5"/></svg>,
  Download: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/></svg>,
  Search: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>,
  Up: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m6 15 6-6 6 6"/></svg>,
  Down: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6"/></svg>,
  X: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>,
  External: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M21 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h6"/></svg>,
  Doc: () => <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M16 13H8M16 17H8M10 9H8"/></svg>,
};

function fmtNum(n) { return typeof n === 'number' ? n.toLocaleString('en-US') : n; }
function fmtCompact(n) { return n >= 1000 ? (n/1000).toFixed(n >= 10000 ? 0 : 1).replace(/\\.0$/, '') + 'k' : String(n); }

// ─── SPARKLINE ───
function Sparkline({ seed = 1, color = 'var(--acko-400)', width = 80, height = 26, trend = 'up' }) {
  const points = useMemo(() => {
    const arr = []; let v = 0.5;
    for (let i = 0; i < 16; i++) {
      const drift = trend === 'up' ? 0.025 : trend === 'down' ? -0.025 : 0;
      const noise = (Math.sin((i+seed)*1.7)*0.5 + Math.sin((i+seed)*0.7)*0.5) * 0.18;
      v = Math.max(0.05, Math.min(0.95, v + drift + noise * 0.3));
      arr.push(v);
    }
    return arr;
  }, [seed, trend]);
  const pts = points.map((v,i) => [(i/(points.length-1))*width, height - v*(height-4) - 2]);
  const d = pts.map((p,i) => (i?'L':'M')+p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
  const fillD = d + ` L ${width},${height} L 0,${height} Z`;
  const id = 'sg'+seed;
  return <svg className="kpi-spark" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" width="100%" height={height}>
    <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={color} stopOpacity="0.35"/><stop offset="100%" stopColor={color} stopOpacity="0"/></linearGradient></defs>
    <path d={fillD} fill={`url(#${id})`}/><path d={d} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round"/>
    <circle cx={pts[pts.length-1][0]} cy={pts[pts.length-1][1]} r="2" fill={color}/>
  </svg>;
}

function Tip({ label, children }) {
  const [show, setShow] = useState(false);
  return <span style={{position:'relative',display:'inline-block'}} onMouseEnter={()=>setShow(true)} onMouseLeave={()=>setShow(false)}>
    {children}
    {show && <span style={{position:'absolute',bottom:'calc(100% + 6px)',left:'50%',transform:'translateX(-50%)',background:'#000',color:'#fff',fontSize:11,padding:'5px 9px',borderRadius:6,whiteSpace:'nowrap',border:'1px solid var(--border-strong)',zIndex:30,fontFamily:'var(--font-mono)',letterSpacing:0.02}}>{label}</span>}
  </span>;
}

// ─── SIDEBAR ───
function Sidebar({ active, onNav }) {
  const items = [
    { id:'overview', label:'Overview', icon:'Dashboard' },
    { id:'brands', label:'Brands', icon:'Brand', badge:String(window.GEO_DATA.shareOfVoice.length) },
    { id:'domains', label:'Domains', icon:'Globe' },
    { id:'prompts', label:'Prompts', icon:'List', badge:String(window.GEO_DATA.prompts.length) },
    { id:'gaps', label:'Gap Analysis', icon:'Target' },
  ];
  return <aside className="sidebar">
    <div className="sidebar-brand">
      <span className="acko-wordmark" dangerouslySetInnerHTML={{__html: window.ACKO_SVG}} style={{height:28,width:'auto',display:'block'}}/>
    </div>
    <div className="nav-section">
      <div className="nav-section-label">Workspace</div>
      {items.map(it => {
        const I = Icon[it.icon];
        return <div key={it.id} className={'nav-item '+(active===it.id?'active':'')} onClick={()=>{onNav(it.id); document.getElementById('section-'+it.id)?.scrollIntoView({behavior:'smooth'})}}>
          <I/><span>{it.label}</span>{it.badge && <span className="badge">{it.badge}</span>}
        </div>;
      })}
    </div>
    <div className="nav-section">
      <div className="nav-section-label">Sources</div>
      <div className="nav-item"><Icon.Doc/><span>ChatGPT</span><span className="badge" style={{background:'rgba(74,222,128,.12)',color:'#7ee9a8'}}>LIVE</span></div>
    </div>
    <div className="sidebar-footer">
      <span className="dot"/><div><div style={{color:'var(--fg-2)'}}>Report {window.GEO_DATA.meta.reportId}</div><div>Generated {window.GEO_DATA.meta.generatedAt}</div></div>
    </div>
  </aside>;
}

// ─── HEADER ───
function Header() {
  const m = window.GEO_DATA.meta;
  return <div className="page-header">
    <div className="page-header-left">
      <h1 className="page-title">ACKO GMC <em>AI Visibility</em></h1>
      <div className="page-subtitle">
        <span className="src-badge">Live Data from Ahrefs Brand Radar</span>
        <span className="pill"><span className="key">SOURCE</span> {m.source}</span>
        <span className="pill"><span className="key">PROMPTS</span> {m.trackedPrompts}</span>
        <span className="pill"><span className="key">REPORT</span> {m.reportId}</span>
      </div>
    </div>
  </div>;
}

function PeriodBar() {
  const dates = Object.keys(window.SOV_HISTORY).sort();
  const [periodA, setPeriodA] = window.__periodA;
  const [periodB, setPeriodB] = window.__periodB;
  const [comparing, setComparing] = window.__comparing;
  const fmtDate = (d) => { const dt = new Date(d+"T00:00:00"); return dt.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'}); };
  return <div className="period-bar">
    <span className="period-label">Period A</span>
    <select className="period-select" value={periodA} onChange={e=>{setPeriodA(e.target.value);window.__triggerUpdate();}}>
      {dates.map(d=><option key={d} value={d}>{fmtDate(d)}</option>)}
    </select>
    <label className="compare-toggle">
      <input type="checkbox" checked={comparing} onChange={e=>{setComparing(e.target.checked);window.__triggerUpdate();}}/>
      <span>Compare with Period B</span>
    </label>
    {comparing && <>
      <span className="period-label">Period B</span>
      <select className="period-select" value={periodB} onChange={e=>{setPeriodB(e.target.value);window.__triggerUpdate();}}>
        {dates.map(d=><option key={d} value={d}>{fmtDate(d)}</option>)}
      </select>
      <span className="compare-badge">vs {fmtDate(periodB)}</span>
    </>}
    <span className="ms-auto"></span>
    <span className="updated">Updated · {fmtDate(periodA)}</span>
  </div>;
}


function InfoTip({text}) {
  return <span className="info-tip">i<span className="info-popup">{text}</span></span>;
}

// ─── KPI ───
function Kpi({ label, value, caption, isAcko, sparkSeed, sparkTrend, delta, deltaDir }) {
  const valNode = typeof value === 'string' && value.includes('%')
    ? <>{value.replace('%','')}<span className="unit">%</span></> : value;
  const isText = typeof value === 'string' && (value.includes('/') || /[A-Za-z]/.test(value));
  return <div className={'kpi '+(isAcko?'acko':'')}>
    <div className="kpi-label">{isAcko && <span className="dot"/>}{label}</div>
    <div className={'kpi-value '+(isText?'text':'')}>{valNode}</div>
    <Sparkline seed={sparkSeed} trend={sparkTrend} color={isAcko?'var(--acko-400)':'var(--info)'}/>
    <div className="kpi-caption"><span>{caption}</span>
      {delta && <span className={'kpi-delta '+(deltaDir==='up'?'up':'down')}>{deltaDir==='up'?<Icon.Up/>:<Icon.Down/>}{delta}</span>}
    </div>
  </div>;
}

function KpiGrid() {
  const k = window.GEO_DATA.kpis;
  const [periodA] = window.__periodA || [null];
  const [periodB] = window.__periodB || [null];
  const [comparing] = window.__comparing || [false];

  // Compute SoV from selected period
  const sovA = periodA && window.SOV_HISTORY[periodA] ? window.SOV_HISTORY[periodA]["Acko"] : k.shareOfVoice.value;
  const sovB = periodB && comparing && window.SOV_HISTORY[periodB] ? window.SOV_HISTORY[periodB]["Acko"] : null;

  // Compute rank from selected period
  const getRank = (date) => {
    if(!date || !window.SOV_HISTORY[date]) return null;
    const sorted = Object.entries(window.SOV_HISTORY[date]).sort((a,b)=>b[1]-a[1]);
    return {rank: sorted.findIndex(e=>e[0]==="Acko")+1, total: sorted.length};
  };
  const rankA = getRank(periodA);
  const sovCaption = rankA ? `Rank #${rankA.rank} of ${rankA.total} brands` : k.shareOfVoice.caption;

  // Top competitor from selected period
  const getTopComp = (date) => {
    if(!date || !window.SOV_HISTORY[date]) return null;
    const sorted = Object.entries(window.SOV_HISTORY[date]).sort((a,b)=>b[1]-a[1]);
    return {name: sorted[0][0], sov: sorted[0][1].toFixed(1)};
  };
  const topA = getTopComp(periodA);
  const topB = getTopComp(periodB);

  // Delta computation
  const sovDelta = comparing && sovB !== null ? (sovA - sovB).toFixed(1) : null;
  const sovDeltaDir = sovDelta > 0 ? 'up' : sovDelta < 0 ? 'down' : null;
  const topDelta = comparing && topA && topB ? (parseFloat(topA.sov) - parseFloat(topB.sov)).toFixed(1) : null;
  const topDeltaDir = topDelta > 0 ? 'up' : topDelta < 0 ? 'down' : null;

  return <div className="kpi-grid">
    <Kpi label="ACKO Share of Voice" value={sovA.toFixed?sovA.toFixed(1)+'%':sovA+'%'} caption={sovCaption} isAcko sparkSeed={1} sparkTrend={sovDeltaDir||"flat"} delta={sovDelta?sovDelta+'pp':null} deltaDir={sovDeltaDir}/>
    <Kpi label="ACKO Mentioned" value={k.mentioned.value} caption={k.mentioned.caption} isAcko sparkSeed={2} sparkTrend={k.mentioned.deltaDir||"flat"} delta={k.mentioned.delta||null} deltaDir={k.mentioned.deltaDir||null}/>
    <Kpi label="ACKO Cited" value={k.cited.value} caption={k.cited.caption} isAcko sparkSeed={3} sparkTrend={k.cited.deltaDir||"flat"} delta={k.cited.delta||null} deltaDir={k.cited.deltaDir||null}/>
    <Kpi label="Total AI Search Volume" value={fmtNum(k.aiSearchVol.value)} caption={k.aiSearchVol.caption} sparkSeed={4} sparkTrend="flat"/>
    <Kpi label="ACKO Volume Reach" value={fmtNum(k.volumeReach.value)} caption={k.volumeReach.caption} isAcko sparkSeed={5} sparkTrend={k.volumeReach.deltaDir||"flat"} delta={k.volumeReach.delta||null} deltaDir={k.volumeReach.deltaDir||null}/>
    <Kpi label="Top Competitor" value={topA?topA.name:k.topCompetitor.value} caption={topA?topA.sov+'% SoV':k.topCompetitor.caption} sparkSeed={6} sparkTrend={topDeltaDir||"flat"} delta={topDelta?topDelta+'pp':null} deltaDir={topDeltaDir}/>
  </div>;
}

// ─── BAR CHARTS ───
function BarChart({ data, valueKey, formatValue, max }) {
  const computedMax = max || Math.max(...data.map(d=>d[valueKey]));
  return <div className="bars" style={{padding:'0 22px 22px'}}>
    {data.map(d => {
      const pct = (d[valueKey]/computedMax)*100;
      return <Tip key={d.name||d.brand} label={`${d.name||d.brand} · ${formatValue?formatValue(d[valueKey]):d[valueKey]}`}>
        <div className={'bar-row '+(d.isAcko?'acko':'')} style={{width:'100%'}}>
          <div className="bar-name">{d.name||d.brand}</div>
          <div className="bar-track"><div className="bar-fill" style={{width:Math.max(pct,0.5)+'%'}}/></div>
          <div className="bar-value">{formatValue?formatValue(d[valueKey]):d[valueKey]}</div>
        </div>
      </Tip>;
    })}
  </div>;
}

function ShareOfVoiceCard() {
  const [periodA] = window.__periodA || [null];
  const sovData = useMemo(()=>{
    if(periodA && window.SOV_HISTORY[periodA]){
      const h=window.SOV_HISTORY[periodA];
      return Object.entries(h).sort((a,b)=>b[1]-a[1]).map(([brand,sov])=>({brand,sov,isAcko:brand==='Acko'}));
    }
    return window.GEO_DATA.shareOfVoice;
  },[periodA]);
  const fmtDate=(d)=>{const dt=new Date(d+"T00:00:00");return dt.toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});};
  const dateLabel = periodA ? fmtDate(periodA) : 'May 04, 2026';
  return <div className="card">
    <div className="card-header"><div><div className="card-title"><span className="accent"/><span>Share of Voice — All Brands</span><InfoTip text="Share of Voice (SoV) measures how often each brand appears in AI-generated responses relative to all tracked brands. Higher SoV means the brand dominates AI answers for your tracked prompts."/></div><div className="card-subtitle">{sovData.length} brands tracked · {dateLabel}</div></div></div>
    <BarChart data={sovData} valueKey="sov" formatValue={v=>v.toFixed(1)+'%'} max={100}/>
  </div>;
}

function MentionsCard() {
  return <div className="card">
    <div className="card-header"><div><div className="card-title"><span className="accent"/><span>Brand Mentions in AI Responses</span><InfoTip text="Counts how many of the 100 tracked AI prompts mention each brand by name in the response. A mention means the brand was explicitly referenced in the AI-generated answer."/></div><div className="card-subtitle">Count of responses naming each brand</div></div></div>
    <BarChart data={window.GEO_DATA.mentions} valueKey="mentions"/>
  </div>;
}

// ─── TABLES ───
function DomainsCard() {
  const data = window.GEO_DATA.topDomains;
  const max = Math.max(...data.map(d=>d.responses));
  const [sortDir, setSortDir] = useState('desc');
  const sorted = useMemo(()=>[...data].sort((a,b)=>sortDir==='desc'?b.responses-a.responses:a.responses-b.responses),[sortDir]);
  return <div className="card">
    <div className="card-header"><div><div className="card-title"><span className="accent"/><span>Top Cited Domains</span><InfoTip text="Shows which domains are most frequently cited (linked) by AI as sources in responses to tracked prompts. Being cited means the AI trusts and references your content."/></div><div className="card-subtitle">Domains linked in AI responses</div></div></div>
    <div className="card-body flush"><div className="table-wrap"><table className="table"><thead><tr><th>Domain</th><th className="num sortable" onClick={()=>setSortDir(d=>d==='desc'?'asc':'desc')}>Responses <span className="sort-arrow">{sortDir==='desc'?'▼':'▲'}</span></th><th style={{width:220}}>Visual</th></tr></thead><tbody>
      {sorted.map(d=><tr key={d.domain} className={d.isAcko?'acko':''}><td><span className="url"><span className="urltext">{d.domain}</span></span></td><td className="num">{d.responses}</td><td className={d.isAcko?'acko-row':''}><span className="dom-bar" style={{width:(d.responses/max)*180}}/></td></tr>)}
    </tbody></table></div></div>
  </div>;
}

function AckoPagesCard() {
  const data = window.GEO_DATA.ackoPages;
  const max = Math.max(...data.map(d=>d.responses));
  return <div className="card">
    <div className="card-header"><div><div className="card-title"><span className="accent"/><span>ACKO Cited Pages</span></div><div className="card-subtitle">Specific URLs cited from acko.com</div></div></div>
    <div className="card-body flush"><div className="table-wrap"><table className="table"><thead><tr><th>Page URL</th><th className="num" style={{width:140}}>Responses</th><th style={{width:140}}>Reach</th></tr></thead><tbody>
      {data.map(d=><tr key={d.url}><td><a href={'https://'+d.url} className="url" target="_blank" rel="noreferrer"><span className="urltext">{d.url}</span><Icon.External/></a></td><td className="num"><span className="cell-num-pill">{d.responses}</span></td><td><span className="dom-bar" style={{width:(d.responses/max)*100,background:'var(--acko-500)'}}/></td></tr>)}
    </tbody></table></div></div>
  </div>;
}

// ─── PROMPTS ───
function PromptDrawer({ row, onClose }) {
  if (!row) return null;
  return <div className="drawer-overlay" onClick={onClose}><div className="drawer" onClick={e=>e.stopPropagation()}>
    <div className="drawer-head"><div><div style={{fontSize:10,textTransform:'uppercase',letterSpacing:'0.12em',color:'var(--fg-3)',fontWeight:600,marginBottom:8}}>Prompt detail</div><h3>{row.prompt}</h3></div><button className="close" onClick={onClose}><Icon.X/></button></div>
    <div className="drawer-body">
      <div className="drawer-stat-grid">
        <div className="drawer-stat"><div className="label">AI Volume</div><div className="val">{fmtNum(row.vol)}</div></div>
        <div className="drawer-stat"><div className="label">ACKO Mentioned</div><div className="val" style={{color:row.mentioned?'var(--good)':'var(--bad)'}}>{row.mentioned?'Yes':'No'}</div></div>
        <div className="drawer-stat"><div className="label">ACKO Cited</div><div className="val" style={{color:row.cited?'var(--good)':'var(--bad)'}}>{row.cited?'Yes':'No'}</div></div>
      </div>
      <div><div style={{fontSize:10,textTransform:'uppercase',letterSpacing:'0.12em',color:'var(--fg-3)',fontWeight:600,marginBottom:10}}>Brands present</div>
        {row.brands.length ? <div style={{display:'flex',flexWrap:'wrap',gap:6}}>{row.brands.map(b=><span key={b} className={'brand-chip '+(b==='Acko'?'acko':'')}>{b}</span>)}</div> : <div className="muted" style={{fontSize:13}}>No brand entities detected.</div>}
      </div>
      <div><div style={{fontSize:10,textTransform:'uppercase',letterSpacing:'0.12em',color:'var(--fg-3)',fontWeight:600,marginBottom:10}}>Status</div>
        <div style={{padding:14,background:'var(--surface-2)',border:'1px solid var(--border)',borderRadius:'var(--radius)',fontSize:13,lineHeight:1.6}}>
          {row.mentioned&&row.cited&&<span style={{color:'var(--good)'}}>✓ ACKO is performing well — both mentioned and cited.</span>}
          {!row.mentioned&&!row.cited&&<span style={{color:'var(--bad)'}}>✗ ACKO is missing entirely from this response.</span>}
          {!row.mentioned&&row.cited&&<span style={{color:'var(--warn)'}}>◐ ACKO is cited but not mentioned by name.</span>}
          {row.mentioned&&!row.cited&&<span style={{color:'var(--warn)'}}>◐ ACKO is mentioned but not cited as a source.</span>}
        </div>
      </div>
    </div>
  </div></div>;
}

function volBucket(v) { return v>=5000?{cls:'high'}:v>=1500?{cls:'med'}:{cls:'low'}; }

function PromptsCard() {
  const data = window.GEO_DATA.prompts;
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState({key:'vol',dir:'desc'});
  const [active, setActive] = useState(null);
  const maxVol = Math.max(...data.map(d=>d.vol));
  const counts = {all:data.length, mentioned:data.filter(d=>d.mentioned).length, notMentioned:data.filter(d=>!d.mentioned).length, cited:data.filter(d=>d.cited).length, notCited:data.filter(d=>!d.cited).length};
  const rows = useMemo(()=>{
    let r = data;
    if(filter==='mentioned') r=r.filter(d=>d.mentioned);
    if(filter==='notMentioned') r=r.filter(d=>!d.mentioned);
    if(filter==='cited') r=r.filter(d=>d.cited);
    if(filter==='notCited') r=r.filter(d=>!d.cited);
    if(search){const q=search.toLowerCase();r=r.filter(d=>d.prompt.toLowerCase().includes(q)||d.brands.some(b=>b.toLowerCase().includes(q)));}
    r=[...r].sort((a,b)=>{const dir=sort.dir==='desc'?-1:1;if(sort.key==='vol')return(a.vol-b.vol)*dir;if(sort.key==='prompt')return a.prompt.localeCompare(b.prompt)*dir;return 0;});
    return r;
  },[filter,search,sort]);
  const setSortKey=(k)=>setSort(s=>s.key===k?{...s,dir:s.dir==='desc'?'asc':'desc'}:{key:k,dir:'desc'});
  const arrow=(k)=>sort.key===k?<span className="sort-arrow">{sort.dir==='desc'?'▼':'▲'}</span>:<span className="sort-arrow">⇅</span>;
  return <div className="card">
    <div className="card-header"><div><div className="card-title"><span className="accent"/><span>Prompt-Level Breakdown</span><InfoTip text="Every tracked AI search prompt with its monthly search volume, whether ACKO is mentioned in the response, and whether acko.com is cited as a source. Filter to find gaps and opportunities."/><span className="count">{rows.length} of {data.length} prompts</span></div></div></div>
    <div className="filter-bar">
      <div className="search"><Icon.Search/><input placeholder="Search prompts or brands…" value={search} onChange={e=>setSearch(e.target.value)}/></div>
      {[['all','All Prompts'],['mentioned','ACKO Mentioned'],['notMentioned','ACKO Not Mentioned'],['cited','ACKO Cited'],['notCited','ACKO Not Cited']].map(([k,l])=>
        <button key={k} className={'filter-chip '+(filter===k?'active':'')} onClick={()=>setFilter(k)}>{l} <span className="ct">{counts[k]}</span></button>
      )}
    </div>
    <div className="table-wrap"><table className="table"><thead><tr>
      <th className="sortable" onClick={()=>setSortKey('prompt')}>Prompt {arrow('prompt')}</th>
      <th className="num sortable" style={{width:160}} onClick={()=>setSortKey('vol')}>AI Volume {arrow('vol')}</th>
      <th style={{width:110}}>Mentioned</th><th style={{width:110}}>Cited</th><th>Brands Present</th><th style={{width:36}}></th>
    </tr></thead><tbody>
      {rows.map(r=>{const b=volBucket(r.vol);return <tr key={r.prompt} onClick={()=>setActive(r)} style={{cursor:'pointer'}}>
        <td style={{maxWidth:480}}>{r.prompt}</td>
        <td className="num"><span className={'vol-cell '+b.cls}><span className="vbar"><span style={{width:(r.vol/maxVol)*100+'%'}}/></span>{fmtNum(r.vol)}</span></td>
        <td>{r.mentioned?<span className="tag-yes">Yes</span>:<span className="tag-no">No</span>}</td>
        <td>{r.cited?<span className="tag-yes">Yes</span>:<span className="tag-no">No</span>}</td>
        <td>{r.brands.length?r.brands.map(b=><span key={b} className={'brand-chip '+(b==='Acko'?'acko':'')}>{b}</span>):<span className="dim">—</span>}</td>
        <td style={{color:'var(--fg-4)',textAlign:'right'}}>›</td>
      </tr>;})}
    </tbody></table></div>
    <PromptDrawer row={active} onClose={()=>setActive(null)}/>
  </div>;
}

// ─── GAP ANALYSIS ───
function GapQuadrant() {
  const data = window.GEO_DATA.prompts;
  const W=720,H=420,PAD=50;
  const maxVol = Math.max(...data.map(d=>d.vol));
  const score=(d)=>(d.mentioned?1.5:0)+(d.cited?1.5:0);
  const points = data.map((d,i)=>{
    const lx=Math.log10(d.vol+10),lxMax=Math.log10(maxVol+10);
    const x=PAD+(lx/lxMax)*(W-PAD*2), y=H-PAD-(score(d)/3)*(H-PAD*2);
    return {...d,x,y,score:score(d),idx:i};
  });
  const [hover,setHover]=useState(null);
  return <div className="quadrant"><svg className="quadrant-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
    <defs><pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="rgba(255,255,255,0.03)" strokeWidth="1"/></pattern>
      <linearGradient id="qbg-tl" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stopColor="rgba(74,222,128,0.08)"/><stop offset="100%" stopColor="transparent"/></linearGradient>
      <linearGradient id="qbg-br" x1="1" y1="1" x2="0" y2="0"><stop offset="0%" stopColor="rgba(255,100,112,0.10)"/><stop offset="100%" stopColor="transparent"/></linearGradient>
    </defs>
    <rect width={W} height={H} fill="url(#grid)"/>
    <rect x={W/2} y={PAD} width={W/2-PAD} height={(H-PAD*2)/2} fill="url(#qbg-tl)"/>
    <rect x={W/2} y={H/2} width={W/2-PAD} height={(H-PAD*2)/2} fill="url(#qbg-br)"/>
    <line x1={PAD} y1={H-PAD} x2={W-PAD} y2={H-PAD} stroke="rgba(255,255,255,0.15)"/>
    <line x1={PAD} y1={PAD} x2={PAD} y2={H-PAD} stroke="rgba(255,255,255,0.15)"/>
    <line x1={W/2} y1={PAD} x2={W/2} y2={H-PAD} stroke="rgba(255,255,255,0.06)" strokeDasharray="3 4"/>
    <line x1={PAD} y1={H/2} x2={W-PAD} y2={H/2} stroke="rgba(255,255,255,0.06)" strokeDasharray="3 4"/>
    <text x={W-PAD-12} y={PAD+18} textAnchor="end" fill="var(--good)" fontSize="11" fontFamily="var(--font-mono)" letterSpacing="0.08em">★ HIGH-VOLUME WINS</text>
    <text x={W-PAD-12} y={H-PAD-10} textAnchor="end" fill="var(--bad)" fontSize="11" fontFamily="var(--font-mono)" letterSpacing="0.08em">⚠ HIGH-VOLUME GAPS</text>
    <text x={PAD+12} y={PAD+18} fill="var(--fg-4)" fontSize="11" fontFamily="var(--font-mono)">LOW-VOL WINS</text>
    <text x={PAD+12} y={H-PAD-10} fill="var(--fg-4)" fontSize="11" fontFamily="var(--font-mono)">LOW-VOL GAPS</text>
    <text x={W/2} y={H-14} textAnchor="middle" fill="var(--fg-3)" fontSize="11" fontFamily="var(--font-mono)">AI SEARCH VOLUME →</text>
    <text x={16} y={H/2} textAnchor="middle" fill="var(--fg-3)" fontSize="11" fontFamily="var(--font-mono)" transform={`rotate(-90 16 ${H/2})`}>ACKO PERFORMANCE →</text>
    {points.map(p=>{
      const color=p.mentioned&&p.cited?'var(--good)':!p.mentioned&&!p.cited?'var(--bad)':'var(--warn)';
      const r=5+Math.log10(p.vol+10);
      return <g key={p.idx} onMouseEnter={()=>setHover(p)} onMouseLeave={()=>setHover(null)} style={{cursor:'pointer'}}>
        <circle cx={p.x} cy={p.y} r={r+4} fill={color} opacity="0.15"/><circle cx={p.x} cy={p.y} r={r} fill={color} opacity="0.85" stroke="var(--ink-1)" strokeWidth="1.5"/>
      </g>;
    })}
    {hover && <g pointerEvents="none">
      <rect x={Math.min(hover.x+12,W-280)} y={Math.max(hover.y-50,10)} width="270" height="48" rx="6" fill="#000" stroke="var(--border-strong)"/>
      <text x={Math.min(hover.x+22,W-270)} y={Math.max(hover.y-32,28)} fill="var(--fg-1)" fontSize="11">{hover.prompt.length>40?hover.prompt.slice(0,38)+'…':hover.prompt}</text>
      <text x={Math.min(hover.x+22,W-270)} y={Math.max(hover.y-16,44)} fill="var(--fg-3)" fontSize="10" fontFamily="var(--font-mono)">vol {fmtNum(hover.vol)} · {hover.mentioned?'mentioned':'no mention'} · {hover.cited?'cited':'no citation'}</text>
    </g>}
  </svg></div>;
}

function PriorityList() {
  const missing = window.GEO_DATA.gapsMissing;
  const wins = window.GEO_DATA.gapsWins;
  const items = [...missing.map(m=>({...m,type:'miss'})), ...wins.map(w=>({...w,type:'win',brands:[]}))].sort((a,b)=>b.vol-a.vol);
  return <div className="priority-list">{items.map((it,i)=>
    <div key={it.prompt+i} className="priority-item">
      <div className="priority-rank">{String(i+1).padStart(2,'0')}</div>
      <div><div className="priority-prompt">{it.prompt}</div>
        {it.brands&&it.brands.length>0&&<div style={{marginTop:4}}><span className="dim" style={{fontSize:11,marginRight:6}}>Competitors:</span>{it.brands.map(b=><span key={b} className="brand-chip">{b}</span>)}</div>}
      </div>
      <div className="priority-vol">{fmtNum(it.vol)}</div>
      <div className={'priority-tag '+(it.type==='miss'?'miss':'win')}>{it.type==='miss'?'Gap':'Win'}</div>
    </div>
  )}</div>;
}

function GapAnalysisSection() {
  const [view,setView]=useState('quadrant');
  return <div className="card">
    <div className="card-header"><div><div className="card-title"><span className="accent"/><span>Gap Analysis</span><InfoTip text="Visualises where ACKO is winning (mentioned + cited) vs missing (neither mentioned nor cited) across all tracked prompts. High-volume gaps in the bottom-right quadrant are top priorities for GEO optimisation."/></div><div className="card-subtitle">Where ACKO is winning vs missing across tracked prompts</div></div>
      <div className="card-actions"><button className={'filter-chip '+(view==='quadrant'?'active':'')} onClick={()=>setView('quadrant')}>Quadrant</button><button className={'filter-chip '+(view==='list'?'active':'')} onClick={()=>setView('list')}>Priority list</button></div>
    </div>
    {view==='quadrant'?<><GapQuadrant/><div className="quad-legend">
      <span><span className="lg-dot" style={{background:'var(--good)'}}/>ACKO mentioned + cited</span>
      <span><span className="lg-dot" style={{background:'var(--warn)'}}/>Mentioned or cited (partial)</span>
      <span><span className="lg-dot" style={{background:'var(--bad)'}}/>ACKO missing</span>
      <span style={{marginLeft:'auto',color:'var(--fg-4)'}}>Bubble size = log(volume)</span>
    </div></>:<PriorityList/>}
  </div>;
}

// ─── APP ───
function App() {
  const [active, setActive] = useState('overview');
  const dates = Object.keys(window.SOV_HISTORY).sort();
  const periodAState = useState(dates[dates.length-1]);
  const periodBState = useState(dates.length>1 ? dates[dates.length-2] : dates[0]);
  const comparingState = useState(false);
  const [updateTick, setUpdateTick] = useState(0);
  window.__periodA = periodAState;
  window.__periodB = periodBState;
  window.__comparing = comparingState;
  window.__triggerUpdate = () => setUpdateTick(t=>t+1);
  const periodA = periodAState[0];
  const periodB = periodBState[0];
  const comparing = comparingState[0];
  // Track scroll position to highlight sidebar
  useEffect(()=>{
    const handler=()=>{
      const sections=['overview','brands','domains','prompts','gaps'];
      for(let i=sections.length-1;i>=0;i--){
        const el=document.getElementById('section-'+sections[i]);
        if(el&&el.getBoundingClientRect().top<200){setActive(sections[i]);break;}
      }
    };
    const main=document.querySelector('.main');
    if(main) main.addEventListener('scroll',handler);
    window.addEventListener('scroll',handler);
    return ()=>{if(main)main.removeEventListener('scroll',handler);window.removeEventListener('scroll',handler);};
  },[]);

  return <div className="app">
    <Sidebar active={active} onNav={setActive}/>
    <main className="main">
      <div id="section-overview" className="scroll-anchor"/>
      <Header/>
      <PeriodBar/>
      <KpiGrid/>

      <div id="section-brands" className="scroll-anchor"/>
      <div className="row-2col"><ShareOfVoiceCard/><MentionsCard/></div>

      <div id="section-domains" className="scroll-anchor"/>
      <div className="row-2col"><DomainsCard/><AckoPagesCard/></div>

      <div id="section-prompts" className="scroll-anchor"/>
      <div style={{marginBottom:24}}><PromptsCard/></div>

      <div id="section-gaps" className="scroll-anchor"/>
      <GapAnalysisSection/>

      <div style={{marginTop:60,padding:'24px 0',borderTop:'1px solid var(--border)',display:'flex',justifyContent:'space-between',color:'var(--fg-4)',fontSize:11,fontFamily:'var(--font-mono)'}}>
        <span>ACKO GEO · Report {window.GEO_DATA.meta.reportId} · Source {window.GEO_DATA.meta.source}</span>
        <span>© 2026 Acko · Generated {window.GEO_DATA.meta.generatedAt}</span>
      </div>
    </main>
  </div>;
}

ReactDOM.createRoot(document.getElementById('root')).render(<App/>);
</script>
</body>
</html>'''

html = html_template.replace('GEO_DATA_PLACEHOLDER', geo_data_json).replace('SOV_HISTORY_PLACEHOLDER', sov_history_weekly_json)

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
