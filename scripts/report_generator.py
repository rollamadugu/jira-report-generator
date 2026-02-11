#!/usr/bin/env python3
"""
Jira Report Generator for GitHub Actions
"""

import os
import json
import requests
from datetime import datetime
from typing import List, Optional
import re

# =========================
# CONFIG
# =========================
JIRA_BASE_URL = "https://honeywell.atlassian.net"
JIRA_EMAIL = "krishnasai.rollamadugu@honeywell.com"
JIRA_TOKEN = os.environ.get("JIRA_API_TOKEN", "")

PROJECT_THEME = "PW1TI"
PAGE_SIZE = 100

START_DATE_FIELD = "customfield_10015"
DUE_DATE_FIELD = "duedate"
PARENT_LINK_JQL = '"Parent Link"'
EPIC_LINK_JQL = '"Epic Link"'

OUTPUT_DIR = "output"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "index.html")

PODS = {
    "ERP": {"theme_label": "ERP_Bolt-ons", "title": "ERP"},
    "Integration": {"theme_label": "Integration", "title": "Integration"},
    "Finance": {"theme_label": "Finance", "title": "Finance"},
    "HR": {"theme_label": "HR", "title": "HR"},
    "GRE": {"theme_label": "GRE", "title": "GRE"},
    "Legal": {"theme_label": "Legal", "title": "Legal"},
    "ISC_Trade": {"theme_label": "ISC_Trade", "title": "ISC - Trade"},
    "ISC_Logistics": {"theme_label": "ISC_Logistics", "title": "ISC - Logistics"},
    "ISC_Labeling": {"theme_label": "ISC_Labeling", "title": "ISC - Labeling"},
    "ISC_Procurement": {"theme_label": "ISC_Procurement", "title": "ISC - Sourcing & Procurement"},
    "ISC_Manufacturing": {"theme_label": "ISC_Manufacturing", "title": "ISC - Manufacturing"},
    "ISC_Planning": {"theme_label": "ISC_Planning", "title": "ISC - Planning"},
    "Commercial": {"theme_label": "Commercial", "title": "Commercial"},
    "IMA": {"theme_label": "IMA", "title": "IMA"},
    "RnD": {"theme_label": "R&D", "title": "R&D"},
    "RnD_PE": {"theme_label": "R&D_PE", "title": "R&D_PE"},
}

# =========================
# HELPERS
# =========================
DONE_STATUSES = {"DONE", "CLOSED", "RESOLVED", "COMPLETE", "COMPLETED"}
BLOCKED_HINTS = {"BLOCK", "IMPED", "IMPEDIMENT", "ON HOLD", "HOLD", "BLOCKED"}
IN_PROGRESS_HINTS = {"IN PROGRESS", "IN-PROGRESS", "WORKING", "STARTED", "IMPLEMENTING"}
DELAYED_HINTS = {"DELAY", "DELAYED", "LATE", "OVERDUE", "BEHIND"}
NOT_APPLICABLE_HINTS = {"NOT APPLICABLE", "N/A", "NA"}


def _up(s: str) -> str:
    return (s or "").upper().strip()


def categorize_status(status: str) -> str:
    s = _up(status)
    if any(re.search(r"\b" + re.escape(x) + r"\b", s) for x in NOT_APPLICABLE_HINTS):
        return "NA"
    if any(re.search(r"\b" + re.escape(x) + r"\b", s) for x in DONE_STATUSES):
        return "DONE"
    if any(re.search(r"\b" + re.escape(x) + r"\b", s) for x in BLOCKED_HINTS):
        return "BLOCKED"
    if any(re.search(r"\b" + re.escape(x) + r"\b", s) for x in DELAYED_HINTS):
        return "DELAYED"
    if any(re.search(r"\b" + re.escape(x) + r"\b", s) for x in IN_PROGRESS_HINTS):
        return "IN_PROGRESS"
    return "TODO"


def html_escape(s: Optional[str]) -> str:
    if s is None:
        return ""
    s = str(s)
    return (s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def phase_from_epic_summary(epic_summary: str) -> str:
    up = _up(epic_summary)
    m = re.match(r"^\s*(\d+)\s+(.+)$", up)
    if m:
        n = int(m.group(1))
        if n == 1: return "DISCOVERY"
        if n in (2, 3): return "INFRASTRUCTURE"
        if n == 4: return "APPLICATION READINESS"
        if n == 5: return "APPLICATION TEST"
        if n >= 6: return "HAND-OFF"
    if "DISCOVERY" in up: return "DISCOVERY"
    if "INFRA" in up: return "INFRASTRUCTURE"
    if "APP READINESS" in up or "APPLICATION READINESS" in up: return "APPLICATION READINESS"
    if "APP TEST" in up or "APPLICATION TEST" in up: return "APPLICATION TEST"
    if "HAND" in up or "CUTOVER" in up: return "HAND-OFF"
    return "OTHER"


def safe_assignee_display(fields: dict) -> Optional[str]:
    a = (fields or {}).get("assignee")
    if isinstance(a, dict):
        return a.get("displayName") or a.get("name") or a.get("emailAddress")
    return None


# =========================
# JIRA API
# =========================
def create_session():
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    return session


def jira_get(session, path, params=None):
    url = f"{JIRA_BASE_URL}{path}"
    r = session.get(url, auth=(JIRA_EMAIL, JIRA_TOKEN), params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def jira_search_all(session, jql, fields):
    out = []
    next_token = None
    while True:
        params = {"jql": jql, "maxResults": PAGE_SIZE, "fields": fields}
        if next_token:
            params["nextPageToken"] = next_token
        data = jira_get(session, "/rest/api/3/search/jql", params)
        out.extend(data.get("issues", []))
        next_token = data.get("nextPageToken")
        if data.get("isLast") or not next_token:
            break
    return out


# =========================
# DATA FETCHING
# =========================
def fetch_all_data(session) -> List[dict]:
    all_subtasks = []
    
    for pod_key, pod_cfg in PODS.items():
        print(f"📦 Processing pod: {pod_key}")
        
        try:
            jql = f'project = {PROJECT_THEME} AND issuetype = Theme AND labels = "{pod_cfg["theme_label"]}"'
            themes = jira_search_all(session, jql, "key,summary,status")
            print(f"   Found {len(themes)} themes")
            
            for theme in themes:
                theme_key = theme["key"]
                theme_summary = theme["fields"].get("summary", "")
                
                jql = f'project = {PROJECT_THEME} AND issuetype = Initiative AND parent = {theme_key}'
                initiatives = jira_search_all(session, jql, "key,summary,status")
                
                for ini in initiatives:
                    ini_key = ini["key"]
                    ini_summary = ini["fields"].get("summary", "")
                    
                    jql = f'issuetype = Epic AND {PARENT_LINK_JQL} = {ini_key}'
                    try:
                        epics = jira_search_all(session, jql, "key,summary,status")
                    except:
                        epics = []
                    
                    for epic in epics:
                        epic_key = epic["key"]
                        epic_summary = epic["fields"].get("summary", "")
                        epic_phase = phase_from_epic_summary(epic_summary)
                        
                        jql = f'{EPIC_LINK_JQL} = {epic_key} AND issuetype = Story'
                        stories = jira_search_all(session, jql, "key,summary,status")
                        
                        story_keys = [s["key"] for s in stories]
                        story_map = {s["key"]: s for s in stories}
                        
                        if story_keys:
                            for i in range(0, len(story_keys), 50):
                                chunk = story_keys[i:i+50]
                                sub_jql = f"parent in ({','.join(chunk)}) AND issuetype = Sub-task"
                                sub_fields = f"key,summary,status,{DUE_DATE_FIELD},assignee,{START_DATE_FIELD},parent"
                                subtasks = jira_search_all(session, sub_jql, sub_fields)
                                
                                for sub in subtasks:
                                    sf = sub.get("fields", {})
                                    assignee = safe_assignee_display(sf)
                                    if not assignee:
                                        continue
                                    
                                    parent_key = (sf.get("parent") or {}).get("key")
                                    story_data = story_map.get(parent_key, {})
                                    story_fields = story_data.get("fields", {})
                                    status = (sf.get("status") or {}).get("name", "UNKNOWN")
                                    
                                    all_subtasks.append({
                                        "pod": pod_cfg["title"],
                                        "theme_key": theme_key,
                                        "theme_summary": theme_summary,
                                        "ini_key": ini_key,
                                        "ini_summary": ini_summary,
                                        "ini_url": f"{JIRA_BASE_URL}/browse/{ini_key}",
                                        "epic_key": epic_key,
                                        "epic_summary": epic_summary,
                                        "epic_url": f"{JIRA_BASE_URL}/browse/{epic_key}",
                                        "epic_phase": epic_phase,
                                        "story_key": parent_key,
                                        "story_summary": story_fields.get("summary", ""),
                                        "story_url": f"{JIRA_BASE_URL}/browse/{parent_key}",
                                        "subtask_key": sub["key"],
                                        "subtask_summary": sf.get("summary", ""),
                                        "subtask_url": f"{JIRA_BASE_URL}/browse/{sub['key']}",
                                        "subtask_status": status,
                                        "subtask_status_cat": categorize_status(status),
                                        "start_date": sf.get(START_DATE_FIELD),
                                        "end_date": sf.get(DUE_DATE_FIELD),
                                        "assignee": assignee
                                    })
        except Exception as e:
            print(f"   ⚠️ Error: {e}")
            continue
    
    return all_subtasks


# =========================
# HTML GENERATION
# =========================
def build_html(title: str, generated: str, data: List[dict]) -> str:
    scrum_json = json.dumps(data, ensure_ascii=False)
    
    html = f'''<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{html_escape(title)}</title>
<style>
:root{{--bg:#fff;--surface:#fff;--surface2:#f7f9fc;--border:#d9e1ec;--text:#0f172a;--text2:#334155;--muted:#64748b;--accent:#2563eb;--accent2:#1d4ed8;--shadow:0 10px 30px rgba(15,23,42,.1);--shadow2:0 6px 18px rgba(15,23,42,.08);--radius:12px}}
html{{scroll-behavior:smooth}}body{{margin:0;min-height:100vh;font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Arial;color:var(--text);background:var(--bg)}}
.wrap{{max-width:1560px;margin:0 auto;padding:18px}}
.hdr{{position:sticky;top:0;z-index:50;padding:14px 16px;font-size:18px;font-weight:700;background:var(--surface);border-bottom:1px solid var(--border);box-shadow:var(--shadow2);border-top:4px solid var(--accent)}}
.meta{{margin-top:12px;padding:10px 12px;border-radius:var(--radius);border:1px solid var(--border);background:var(--surface2);font-size:12px;color:var(--text2)}}
.meta b{{color:var(--text)}}
.card{{margin-top:12px;padding:12px;border-radius:var(--radius);background:var(--surface);border:1px solid var(--border);box-shadow:var(--shadow2)}}
.filters{{display:flex;flex-wrap:wrap;gap:10px;align-items:center}}
.filters label{{font-weight:700;font-size:12px;color:var(--text2)}}
.input,.select{{padding:9px 11px;border-radius:10px;border:1px solid var(--border);background:#fff;color:var(--text);font-size:12px;outline:none}}
.input:focus,.select:focus{{border-color:var(--accent);box-shadow:0 0 0 3px rgba(37,99,235,.15)}}
.btn{{padding:9px 12px;border-radius:10px;border:1px solid var(--border);background:#fff;color:var(--text);font-weight:700;font-size:12px;cursor:pointer}}
.btn:hover{{background:var(--surface2)}}
#smApply{{background:var(--accent);color:#fff;border-color:var(--accent2)}}
#smApply:hover{{background:var(--accent2)}}
.countBox{{padding:9px 11px;border-radius:10px;border:1px solid var(--border);background:var(--surface2);font-size:12px;font-weight:700;color:var(--text)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}}
@media(max-width:1100px){{.grid2{{grid-template-columns:1fr}}}}
.pill{{display:inline-block;padding:2px 9px;border-radius:999px;font-weight:700;font-size:11px;border:1px solid rgba(15,23,42,.12)}}
.pill.done{{background:#dcfce7;border-color:#86efac;color:#14532d}}
.pill.inprog{{background:#dbeafe;border-color:#93c5fd;color:#1e3a8a}}
.pill.todo{{background:#f1f5f9;border-color:#cbd5e1;color:#334155}}
.pill.blocked{{background:#fee2e2;border-color:#fca5a5;color:#7f1d1d}}
.pill.delayed{{background:#ffedd5;border-color:#fdba74;color:#7c2d12}}
.pill.na{{background:#e5e7eb;border-color:#cbd5e1;color:#334155}}
.panel{{border:1px solid var(--border);border-radius:var(--radius);padding:12px;background:var(--surface);box-shadow:var(--shadow2)}}
.panel h3{{margin:0;font-size:13px;font-weight:700;color:var(--text)}}
.subtle{{background:var(--surface2);padding:6px 10px;border-radius:10px;font-size:12px;margin-top:4px;color:var(--text2)}}
.kpis{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}
.kpi{{border:1px solid var(--border);border-radius:999px;padding:8px 12px;background:#fff;font-size:12px;font-weight:700;color:var(--text);display:flex;align-items:center;gap:6px}}
details{{border:1px solid var(--border);border-radius:var(--radius);margin-top:10px;background:#fff}}
summary{{cursor:pointer;padding:10px 12px;font-weight:700;background:var(--surface2);display:flex;justify-content:space-between;align-items:center;list-style:none;color:var(--text)}}
summary::-webkit-details-marker{{display:none}}
summary::before{{content:"▸";margin-right:8px;transition:transform .2s}}
details[open] summary::before{{transform:rotate(90deg)}}
.detailsBody{{padding:12px}}
.smBody{{margin-top:12px;max-height:70vh;overflow:auto}}
.hierarchy-card{{border-radius:var(--radius);padding:16px;margin:12px 0;background:var(--surface);border:1px solid var(--border);box-shadow:var(--shadow2)}}
.hierarchy-chain{{display:flex;flex-wrap:wrap;gap:8px;font-size:11px;margin-bottom:10px;align-items:center}}
.hierarchy-chain span{{background:var(--surface2);padding:2px 8px;border-radius:10px;font-weight:700;color:var(--text2)}}
.hierarchy-chain .subtask-key-chip{{background-color:var(--accent);color:#fff}}
.hierarchy-item{{padding:10px;border-left:4px solid var(--border);margin:6px 0;background:#fff;border-radius:10px}}
.hierarchy-label{{font-size:10px;color:var(--muted);font-weight:700;text-transform:uppercase}}
.hierarchy-key{{font-weight:700;font-size:12px;margin-top:4px;color:var(--text)}}
.hierarchy-summary{{font-size:12px;margin-top:4px;color:var(--text2)}}
.hierarchy-meta{{font-size:11px;margin-top:8px;padding-top:8px;border-top:1px solid #f1f5f9;color:var(--text2)}}
.hierarchy-item.subtask{{background:#eff6ff;border:1px solid #bfdbfe;border-left:4px solid var(--accent)}}
.hierarchy-item.subtask .hierarchy-summary{{font-weight:700;color:var(--accent2)}}
.empty{{text-align:center;color:var(--muted);padding:40px;font-weight:700}}
.refresh-info{{display:inline-flex;align-items:center;gap:6px;background:#dcfce7;color:#14532d;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:600;margin-left:10px}}
</style>
</head>
<body>
<div class="wrap">
<div class="hdr">{html_escape(title)}<span class="refresh-info">🔄 Auto-updates every 15 min</span></div>
<div class="meta"><b>Generated:</b> {html_escape(generated)} | <b>Total Subtasks:</b> {len(data)}</div>
<div class="card">
<div class="filters">
<select id="smAssignee" class="select"><option value="">Select Assignee...</option></select>
<label>Start:</label><input type="date" id="smStartDate" class="input"/>
<label>End:</label><input type="date" id="smEndDate" class="input"/>
<button id="smApply" class="btn">Apply</button>
<button id="smReset" class="btn">Reset</button>
<div id="smCount" class="countBox">In range: 0 | All: 0</div>
</div>
<div class="grid2" id="dashGrid" style="display:none;">
<div class="panel"><h3>📅 In Selected Range</h3><div class="kpis" id="kpiInRange"></div><div id="sectionsInRange"></div></div>
<div class="panel"><h3>📦 All Tasks</h3><div class="kpis" id="kpiAll"></div><div id="sectionsAll"></div></div>
</div>
<div id="smBody" class="smBody"><div class="empty">Select assignee and date range, then click Apply</div></div>
</div>
</div>
<script>
window.__SCRUM_MASTER_INDEX={scrum_json};
(function(){{
const DATA=window.__SCRUM_MASTER_INDEX||[];
const assigneeSelect=document.getElementById("smAssignee");
const startDateInput=document.getElementById("smStartDate");
const endDateInput=document.getElementById("smEndDate");
const applyBtn=document.getElementById("smApply");
const resetBtn=document.getElementById("smReset");
const bodyDiv=document.getElementById("smBody");
const countDiv=document.getElementById("smCount");
const dashGrid=document.getElementById("dashGrid");
const kpiInRange=document.getElementById("kpiInRange");
const kpiAll=document.getElementById("kpiAll");
const sectionsInRange=document.getElementById("sectionsInRange");
const sectionsAll=document.getElementById("sectionsAll");
const STATUS_ORDER=["TODO","IN_PROGRESS","BLOCKED","DELAYED","DONE","NA"];
const STATUS_LABEL={{"TODO":"To Do","IN_PROGRESS":"In Progress","BLOCKED":"Blocked","DELAYED":"Delayed","DONE":"Done","NA":"N/A"}};
function esc(s){{return s==null?"":String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")}}
function pillClass(cat){{if(cat==="DONE")return"pill done";if(cat==="IN_PROGRESS")return"pill inprog";if(cat==="BLOCKED")return"pill blocked";if(cat==="DELAYED")return"pill delayed";if(cat==="NA")return"pill na";return"pill todo"}}
const assignees=new Set();DATA.forEach(r=>{{if(r.assignee)assignees.add(r.assignee)}});
Array.from(assignees).sort().forEach(a=>{{const opt=document.createElement("option");opt.value=a;opt.textContent=a;assigneeSelect.appendChild(opt)}});
endDateInput.value=new Date().toISOString().split("T")[0];
function ymdToNum(ymd){{return ymd?Number(ymd.replace(/-/g,"")):null}}
function inRange(row,sd,ed){{const s=ymdToNum(row.start_date),e=ymdToNum(row.end_date);if(!s&&!e)return false;return(s&&s>=sd&&s<=ed)||(e&&e>=sd&&e<=ed)}}
function computeCounts(rows){{const counts={{}};STATUS_ORDER.forEach(s=>counts[s]=0);rows.forEach(r=>{{counts[r.subtask_status_cat]=(counts[r.subtask_status_cat]||0)+1}});return counts}}
function renderKpis(el,counts,total){{let html=`<div class="kpi"><b>Total:</b> ${{total}}</div>`;STATUS_ORDER.forEach(cat=>{{if(counts[cat]>0)html+=`<div class="kpi"><span class="${{pillClass(cat)}}">${{STATUS_LABEL[cat]}}</span> ${{counts[cat]}}</div>`}});el.innerHTML=html}}
function renderCard(r){{return`<div class="hierarchy-card"><div class="hierarchy-chain"><span>${{esc(r.theme_key)}}</span><span>→</span><span>${{esc(r.ini_key)}}</span><span>→</span><span>${{esc(r.epic_key)}}</span><span>→</span><span>${{esc(r.story_key)}}</span><span>→</span><span class="subtask-key-chip">${{esc(r.subtask_key)}}</span></div><div class="hierarchy-item"><div class="hierarchy-label">Theme</div><div class="hierarchy-key">${{esc(r.theme_key)}}</div><div class="hierarchy-summary">${{esc(r.theme_summary)}}</div></div><div class="hierarchy-item" style="margin-left:16px"><div class="hierarchy-label">Initiative</div><div class="hierarchy-key">${{esc(r.ini_key)}}</div><div class="hierarchy-summary">${{esc(r.ini_summary)}}</div></div><div class="hierarchy-item" style="margin-left:32px"><div class="hierarchy-label">Epic (${{esc(r.epic_phase)}})</div><div class="hierarchy-key">${{esc(r.epic_key)}}</div><div class="hierarchy-summary">${{esc(r.epic_summary)}}</div></div><div class="hierarchy-item" style="margin-left:48px"><div class="hierarchy-label">Story</div><div class="hierarchy-key">${{esc(r.story_key)}}</div><div class="hierarchy-summary">${{esc(r.story_summary)}}</div></div><div class="hierarchy-item subtask" style="margin-left:64px"><div class="hierarchy-label">Subtask</div><div class="hierarchy-key">${{esc(r.subtask_key)}}</div><div class="hierarchy-summary">${{esc(r.subtask_summary)}}</div><div class="hierarchy-meta"><b>Status:</b> <span class="${{pillClass(r.subtask_status_cat)}}">${{esc(r.subtask_status)}}</span><br/><b>Start:</b> ${{esc(r.start_date||"Not Set")}} | <b>End:</b> ${{esc(r.end_date||"Not Set")}}<br/><b>Assignee:</b> ${{esc(r.assignee)}}</div></div></div>`}}
function renderSections(el,rows,counts){{let html="";STATUS_ORDER.forEach(cat=>{{const items=rows.filter(r=>r.subtask_status_cat===cat);if(items.length===0)return;html+=`<details><summary><span class="${{pillClass(cat)}}">${{STATUS_LABEL[cat]}}</span><span>${{items.length}} tasks</span></summary><div class="detailsBody">${{items.map(renderCard).join("")}}</div></details>`}});el.innerHTML=html||'<div class="empty">No tasks</div>'}}
function render(){{const assignee=assigneeSelect.value.trim();const sd=startDateInput.value,ed=endDateInput.value;if(!assignee||!sd||!ed){{bodyDiv.innerHTML='<div class="empty">Please select assignee and both dates</div>';dashGrid.style.display="none";return}}const sdNum=ymdToNum(sd),edNum=ymdToNum(ed);if(sdNum>edNum){{bodyDiv.innerHTML='<div class="empty">Start date must be before end date</div>';return}}const allRows=DATA.filter(r=>r.assignee===assignee);const inRangeRows=allRows.filter(r=>inRange(r,sdNum,edNum));countDiv.textContent=`In range: ${{inRangeRows.length}} | All: ${{allRows.length}}`;dashGrid.style.display="grid";const countsAll=computeCounts(allRows);const countsIn=computeCounts(inRangeRows);renderKpis(kpiAll,countsAll,allRows.length);renderKpis(kpiInRange,countsIn,inRangeRows.length);renderSections(sectionsAll,allRows,countsAll);renderSections(sectionsInRange,inRangeRows,countsIn);if(inRangeRows.length===0){{bodyDiv.innerHTML='<div class="empty">No subtasks found in selected range</div>'}}else{{bodyDiv.innerHTML=inRangeRows.map(renderCard).join("")}}}}
applyBtn.addEventListener("click",render);
resetBtn.addEventListener("click",()=>{{assigneeSelect.value="";startDateInput.value="";endDateInput.value=new Date().toISOString().split("T")[0];dashGrid.style.display="none";bodyDiv.innerHTML='<div class="empty">Select assignee and date range</div>';countDiv.textContent="In range: 0 | All: 0"}});
const saved=localStorage.getItem("jira_view_state");if(saved){{try{{const st=JSON.parse(saved);if(st.assignee)assigneeSelect.value=st.assignee;if(st.startDate)startDateInput.value=st.startDate;if(st.endDate)endDateInput.value=st.endDate;if(st.assignee&&st.startDate&&st.endDate)render()}}catch(e){{}}}}
function saveState(){{localStorage.setItem("jira_view_state",JSON.stringify({{assignee:assigneeSelect.value,startDate:startDateInput.value,endDate:endDateInput.value}}))}}
assigneeSelect.addEventListener("change",saveState);startDateInput.addEventListener("change",saveState);endDateInput.addEventListener("change",saveState);
}})();
</script>
</body>
</html>'''
    
    return html


# =========================
# MAIN
# =========================
def main():
    print("=" * 60)
    print(f"🚀 Jira Report Generator - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)
    
    if not JIRA_TOKEN:
        print("❌ ERROR: JIRA_API_TOKEN not set!")
        exit(1)
    
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    session = create_session()
    
    print("\n📥 Fetching data from Jira...")
    data = fetch_all_data(session)
    print(f"\n✅ Fetched {len(data)} subtasks")
    
    print("\n📝 Generating HTML...")
    generated = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    html = build_html("Scrum Master Daily View", generated, data)
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"✅ Saved: {OUTPUT_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
