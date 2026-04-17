"""
ACKO GEO Dashboard Generator
Calls Ahrefs Brand Radar API, processes data, generates HTML dashboard.
Runs weekly via GitHub Actions.
"""
import requests, json, os, sys
from datetime import datetime, timedelta

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
week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

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
pages_raw = api("brand-radar/cited-pages", {"select": "url,responses", "limit": "30", "date": today})
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

# ─── GENERATE HTML ───
def bar_row(label, value, max_val, is_acko=False, suffix="%"):
    pct = (value / max_val * 100) if max_val > 0 else 0
    color = "#6c63ff" if is_acko else "#1e2a4a"
    border = "#6c63ff" if is_acko else "#2d3a5c"
    label_style = "color:#6c63ff;font-weight:700" if is_acko else "color:#e0e6ed"
    return f'''<div style="display:flex;align-items:center;gap:12px;padding:6px 0;border-bottom:1px solid #141c2e">
<span style="min-width:140px;font-size:13px;{label_style}">{label}</span>
<div style="flex:1;background:#0a0e1a;border-radius:4px;height:18px;overflow:hidden">
<div style="width:{pct:.1f}%;background:{color};border:1px solid {border};height:100%;border-radius:4px;transition:width .3s"></div>
</div>
<span style="min-width:50px;text-align:right;font-size:13px;color:#8892a4">{value}{suffix}</span>
</div>'''

def badge(val, good_text="YES", bad_text="NO"):
    if val:
        return f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;background:#064e3b;color:#10b981">{good_text}</span>'
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;background:#450a0a;color:#ef4444">{bad_text}</span>'

# SoV bars
sov_bars = "\n".join(bar_row(s["brand"], s["sov"], 100, s["brand"] == "Acko") for s in sov)

# Brand mentions bars
max_bm = brand_mentions_sorted[0][1] if brand_mentions_sorted else 1
bm_bars = "\n".join(bar_row(b, c, max_bm, b == "Acko", suffix=f"/{total_q}") for b, c in brand_mentions_sorted)

# Cited domains table
domain_rows = ""
for i, d in enumerate(cited_domains[:20]):
    is_acko = "acko" in d["domain"]
    max_d = cited_domains[0]["responses"]
    pct = d["responses"] / max_d * 100
    hl = 'background:#0f1629;' if is_acko else ''
    ds = 'color:#6c63ff;font-weight:700' if is_acko else 'color:#e0e6ed'
    domain_rows += f'''<tr style="{hl}"><td style="color:#8892a4;padding:8px">{i+1}</td>
<td style="padding:8px;{ds}">{d["domain"]}</td>
<td style="padding:8px"><div style="display:flex;align-items:center;gap:8px">
<div style="width:{pct:.0f}%;height:6px;background:{'#6c63ff' if is_acko else '#2d3a5c'};border-radius:3px;min-width:2px"></div>
<span style="font-size:12px;color:#8892a4">{d["responses"]}</span></div></td></tr>'''

# ACKO pages table
acko_page_rows = ""
for p in acko_pages:
    acko_page_rows += f'<tr><td style="padding:8px;color:#6c63ff;font-size:12px">{p["url"]}</td><td style="padding:8px;color:#f59e0b;font-weight:600">{p["responses"]}</td></tr>'

# Prompts table rows (generated in JS for filtering)
questions_json = json.dumps(questions)

# SoV history for trend
dates_sorted = sorted(sov_history.keys())
acko_trend = [(d, sov_history[d].get("Acko", 0)) for d in dates_sorted]
max_trend = max((v for _, v in acko_trend), default=1) or 1
trend_bars = ""
for d, v in acko_trend:
    h = max(v / max_trend * 120, 4)
    trend_bars += f'<div style="display:flex;flex-direction:column;align-items:center;gap:4px"><div style="width:28px;height:{h:.0f}px;background:#6c63ff;border-radius:4px 4px 0 0;opacity:0.8"></div><span style="font-size:10px;color:#8892a4">{d[5:]}</span><span style="font-size:11px;color:#6c63ff;font-weight:600">{v}%</span></div>'

# SoV history table
sov_table_header = "<th style='text-align:left;padding:8px;background:#0f1629;color:#8892a4;font-size:11px;border-bottom:1px solid #1e2a4a'>Brand</th>"
for d in dates_sorted:
    sov_table_header += f"<th style='text-align:right;padding:8px;background:#0f1629;color:#8892a4;font-size:11px;border-bottom:1px solid #1e2a4a'>{d[5:]}</th>"

sov_table_rows = ""
all_brands = sorted(set(b for day in sov_history.values() for b in day.keys()))
for brand in sorted(all_brands, key=lambda b: -(sov_history.get(dates_sorted[-1], {}).get(b, 0))):
    is_acko = brand == "Acko"
    bs = "color:#6c63ff;font-weight:700" if is_acko else "color:#e0e6ed"
    row = f"<td style='padding:8px;{bs};font-size:12px'>{brand}</td>"
    for d in dates_sorted:
        v = sov_history.get(d, {}).get(brand, 0)
        row += f"<td style='padding:8px;text-align:right;font-size:12px;color:#8892a4'>{v}%</td>"
    sov_table_rows += f"<tr style='border-bottom:1px solid #141c2e'>{row}</tr>"

# Gap & wins
gaps = [q for q in questions if not q["m"] and q["vol"] > 0][:15]
wins = [q for q in questions if q["m"] and q["c"]][:15]

gap_rows = ""
for q in gaps:
    gap_rows += f'<tr><td style="padding:8px;color:#e0e6ed;font-size:12px;max-width:350px">{q["q"]}</td><td style="padding:8px;color:#f59e0b;font-weight:600">{q["vol"]:,}</td><td style="padding:8px;color:#6b7280;font-size:11px">{q["b"]}</td></tr>'

win_rows = ""
for q in wins:
    win_rows += f'<tr><td style="padding:8px;color:#e0e6ed;font-size:12px;max-width:350px">{q["q"]}</td><td style="padding:8px;color:#10b981;font-weight:600">{q["vol"]:,}</td></tr>'

html = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ACKO GEO Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,sans-serif;background:#0a0e1a;color:#e0e6ed;min-height:100vh}}
.hdr{{background:linear-gradient(135deg,#0f1629,#1a2342);padding:20px 28px;border-bottom:1px solid #1e2a4a;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}}
.hdr h1{{font-size:20px;font-weight:700;color:#fff}}.hdr h1 b{{color:#6c63ff}}
.hdr .m{{font-size:12px;color:#8892a4}}.hdr .m b{{color:#6c63ff}}
.wrap{{max-width:1400px;margin:0 auto;padding:20px}}
.krow{{display:grid;grid-template-columns:repeat(6,1fr);gap:14px;margin-bottom:24px}}
.kpi{{background:#111827;border:1px solid #1e2a4a;border-radius:10px;padding:16px;text-align:center;position:relative;overflow:hidden}}
.kpi::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px}}
.kpi.g::before{{background:#10b981}}.kpi.b::before{{background:#6c63ff}}.kpi.o::before{{background:#f59e0b}}.kpi.r::before{{background:#ef4444}}.kpi.p::before{{background:#8b5cf6}}.kpi.c::before{{background:#06b6d4}}
.kpi .v{{font-size:28px;font-weight:800;margin:6px 0 2px}}.kpi .l{{font-size:10px;color:#8892a4;text-transform:uppercase;letter-spacing:.8px}}.kpi .s{{font-size:11px;color:#6b7280;margin-top:2px}}
.kpi.g .v{{color:#10b981}}.kpi.b .v{{color:#6c63ff}}.kpi.o .v{{color:#f59e0b}}.kpi.r .v{{color:#ef4444}}.kpi.p .v{{color:#8b5cf6}}.kpi.c .v{{color:#06b6d4}}
.g2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}}
.card{{background:#111827;border:1px solid #1e2a4a;border-radius:10px;padding:16px}}
.card h3{{font-size:13px;font-weight:600;color:#fff;margin-bottom:12px}}
.fw{{background:#111827;border:1px solid #1e2a4a;border-radius:10px;padding:16px;margin-bottom:24px}}
.fw h3{{font-size:13px;font-weight:600;color:#fff;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px;background:#0f1629;color:#8892a4;font-size:10px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #1e2a4a;position:sticky;top:0}}
td{{padding:8px;border-bottom:1px solid #141c2e}}
tr:hover td{{background:#0f1629}}
.scr{{max-height:500px;overflow-y:auto;border-radius:6px}}
.scr::-webkit-scrollbar{{width:5px}}.scr::-webkit-scrollbar-track{{background:#0a0e1a}}.scr::-webkit-scrollbar-thumb{{background:#2d3748;border-radius:3px}}
.fr{{display:flex;gap:10px;margin-bottom:12px;align-items:center;flex-wrap:wrap}}
.fr select,.fr input{{background:#0f1629;border:1px solid #1e2a4a;color:#e0e6ed;padding:7px 10px;border-radius:6px;font-size:12px;outline:none}}
.fr select:focus,.fr input:focus{{border-color:#6c63ff}}.fr label{{font-size:11px;color:#8892a4}}
@media(max-width:1100px){{.krow{{grid-template-columns:repeat(3,1fr)}}.g2{{grid-template-columns:1fr}}}}
@media(max-width:600px){{.krow{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body>
<div class="hdr">
<h1><b>ACKO</b> GEO Dashboard — Brand Radar</h1>
<div class="m">Report: <b>{REPORT_ID[:8]}</b> · Source: <b>ChatGPT</b> · Updated: <b>{today}</b> · Prompts: <b>{total_q} tracked</b></div>
</div>
<div class="wrap">

<div class="krow">
<div class="kpi b"><div class="l">ACKO Share of Voice</div><div class="v">{acko_sov}%</div><div class="s">Rank #{acko_rank} of {len(sov)} brands</div></div>
<div class="kpi g"><div class="l">ACKO Mentioned In</div><div class="v">{acko_mentioned_count}/{total_q}</div><div class="s">{acko_mentioned_count/total_q*100:.0f}% of AI responses</div></div>
<div class="kpi c"><div class="l">ACKO Cited In</div><div class="v">{acko_cited_count}/{total_q}</div><div class="s">acko.com linked in {acko_cited_count/total_q*100:.0f}% responses</div></div>
<div class="kpi o"><div class="l">Total AI Search Volume</div><div class="v">{total_volume:,}</div><div class="s">Across {total_q} tracked prompts</div></div>
<div class="kpi p"><div class="l">ACKO Volume Reach</div><div class="v">{acko_volume:,}</div><div class="s">{acko_volume/total_volume*100:.0f}% of total volume</div></div>
<div class="kpi r"><div class="l">Top Competitor</div><div class="v">{top_competitor["brand"]}</div><div class="s">{top_competitor["sov"]}% SoV</div></div>
</div>

<div class="fw">
<h3>ACKO SoV Trend</h3>
<div style="display:flex;align-items:flex-end;gap:16px;justify-content:center;padding:16px 0;min-height:160px">{trend_bars}</div>
</div>

<div class="g2">
<div class="card"><h3>Share of Voice — All Brands</h3>{sov_bars}</div>
<div class="card"><h3>Brand Mentions in AI Responses</h3>{bm_bars}</div>
</div>

<div class="g2">
<div class="card"><h3>Top 20 Cited Domains</h3><div class="scr"><table><thead><tr><th>#</th><th>Domain</th><th>Responses</th></tr></thead><tbody>{domain_rows}</tbody></table></div></div>
<div class="card">
<h3>ACKO Cited Pages</h3><div class="scr"><table><thead><tr><th>URL</th><th>Responses</th></tr></thead><tbody>{acko_page_rows}</tbody></table></div>
<h3 style="margin-top:20px">SoV History by Date</h3><div class="scr" style="max-height:300px"><table><thead><tr>{sov_table_header}</tr></thead><tbody>{sov_table_rows}</tbody></table></div>
</div>
</div>

<div class="fw">
<h3>Prompt-Level Breakdown — All {total_q} Tracked Prompts</h3>
<div class="fr">
<label>Filter:</label>
<select id="flt" onchange="ft()"><option value="all">All Prompts</option><option value="m">ACKO Mentioned</option><option value="nm">ACKO NOT Mentioned</option><option value="c">ACKO Cited</option><option value="nc">ACKO NOT Cited</option></select>
<input type="text" id="src" placeholder="Search prompts..." oninput="ft()" style="width:260px">
<label id="rc" style="margin-left:auto;color:#6c63ff"></label>
</div>
<div class="scr" style="max-height:600px"><table><thead><tr><th>#</th><th>Prompt</th><th>AI Vol</th><th>Mentioned</th><th>Cited</th><th>Brands</th></tr></thead><tbody id="pt"></tbody></table></div>
</div>

<div class="g2">
<div class="card"><h3 style="color:#ef4444">High-Volume Gaps — ACKO Missing</h3><div class="scr"><table><thead><tr><th>Prompt</th><th>AI Vol</th><th>Brands Present</th></tr></thead><tbody>{gap_rows}</tbody></table></div></div>
<div class="card"><h3 style="color:#10b981">ACKO Wins — Mentioned + Cited</h3><div class="scr"><table><thead><tr><th>Prompt</th><th>AI Vol</th></tr></thead><tbody>{win_rows}</tbody></table></div></div>
</div>

</div>
<script>
const Q={questions_json};
function bd(v,g,b){{return v?'<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;background:'+g+'">YES</span>':'<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;background:'+b+'">NO</span>';}}
function rn(d){{const t=document.getElementById('pt');t.innerHTML='';d.forEach((q,i)=>{{t.innerHTML+='<tr><td style="color:#8892a4">'+(i+1)+'</td><td style="color:#e0e6ed;max-width:400px">'+q.q+'</td><td style="color:#f59e0b;font-weight:600">'+(q.vol>0?q.vol.toLocaleString():'-')+'</td><td>'+bd(q.m,'#064e3b','#450a0a')+'</td><td>'+bd(q.c,'#064e3b','#450a0a')+'</td><td style="color:#6b7280;font-size:11px">'+q.b+'</td></tr>';}});document.getElementById('rc').textContent=d.length+' prompts';}}
function ft(){{let f=document.getElementById('flt').value,s=document.getElementById('src').value.toLowerCase(),d=Q;if(f==='m')d=d.filter(q=>q.m);else if(f==='nm')d=d.filter(q=>!q.m);else if(f==='c')d=d.filter(q=>q.c);else if(f==='nc')d=d.filter(q=>!q.c);if(s)d=d.filter(q=>q.q.toLowerCase().includes(s));rn(d);}}
rn(Q);
</script></body></html>'''

with open("index.html", "w") as f:
    f.write(html)

print(f"Dashboard generated: index.html ({len(html):,} bytes)")
print(f"Date: {today}")
print(f"Prompts: {total_q}, ACKO SoV: {acko_sov}%")
