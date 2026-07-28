"""
AVOKE Web-Dashboard — Leichtgewichtiges Flask-Dashboard.

Bietet eine Übersicht über:
  • Bot-Status und Uptime
  • Ticket-Statistiken (offene Tickets, Supporter-Analytics)
  • Security-Vorfälle
  • Anti-Nuke Log
  • Case-Übersicht

Start:
  python dashboard/app.py

Umgebungsvariablen (.env):
  DASHBOARD_HOST     — Host (Standard: 0.0.0.0)
  DASHBOARD_PORT     — Port (Standard: 5000)
  DASHBOARD_SECRET   — Geheimes Token für einfache Auth (optional)
  DASHBOARD_TOKEN    — API-Token für /api/* Endpunkte
"""

import json
import os
import datetime
from pathlib import Path

from flask import Flask, jsonify, request, abort, render_template_string

# ── Pfade (relativ zum Projekt-Root) ──────────────────────────────────────────
ROOT        = Path(__file__).parent.parent
DATA        = ROOT / "data"

TICKET_OPEN = DATA / "tickets_open.json"
ANALYTICS   = DATA / "ticket_analytics.json"
SECURITY    = DATA / "security_history.json"
ANTINUKE    = DATA / "antinuke_log.json"
CASES       = DATA / "cases.json"
APPEALS     = DATA / "appeals.json"
SLA_CFG     = DATA / "ticket_sla.json"
AUTOMATION  = DATA / "automation_config.json"
CAPTCHA_CFG = DATA / "captcha_config.json"
ANTINUKE_CFG = DATA / "antinuke_config.json"


def _read(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _check_auth() -> None:
    """Einfache Bearer-Token-Authentifizierung für API-Endpunkte."""
    token = os.getenv("DASHBOARD_TOKEN")
    if not token:
        return   # Kein Token → Auth deaktiviert (nur für lokale Entwicklung)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer ") or auth_header[7:] != token:
        abort(401, description="Ungültiger API-Token")


# ── Flask App ─────────────────────────────────────────────────────────────────

app = Flask(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# API-Endpunkte (JSON)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/tickets", methods=["GET"])
def api_tickets():
    """Gibt alle offenen Tickets zurück."""
    _check_auth()
    data = _read(TICKET_OPEN)
    return jsonify({
        "open_count": len(data),
        "tickets": [
            {
                "channel_id":  ch_id,
                "user_id":     info.get("user_id"),
                "panel":       info.get("anzeige_name"),
                "priority":    info.get("priority", "normal"),
                "claimed_by":  info.get("claimed_by"),
                "created_at":  info.get("created_at"),
            }
            for ch_id, info in data.items()
        ],
    })


@app.route("/api/analytics", methods=["GET"])
def api_analytics():
    """Gibt Supporter-Analytics zurück."""
    _check_auth()
    data = _read(ANALYTICS)
    guild_id = request.args.get("guild_id")

    if guild_id:
        stats_key   = f"stats_{guild_id}"
        guild_stats = data.get(stats_key, {})
        result = []
        for uid, s in guild_stats.items():
            avg_resp = (
                round(s["total_response_s"] / s["response_count"] / 60, 1)
                if s.get("response_count", 0) > 0 else None
            )
            avg_rating = (
                round(s["ratings_sum"] / s["ratings_count"], 2)
                if s.get("ratings_count", 0) > 0 else None
            )
            result.append({
                "user_id":       uid,
                "closed":        s.get("closed", 0),
                "avg_response_min": avg_resp,
                "avg_rating":    avg_rating,
            })
        return jsonify({"guild_id": guild_id, "supporters": result})

    return jsonify({"all": data})


@app.route("/api/security", methods=["GET"])
def api_security():
    """Gibt Security-Vorfälle zurück."""
    _check_auth()
    data     = _read(SECURITY)
    guild_id = request.args.get("guild_id")
    if guild_id:
        return jsonify({"guild_id": guild_id, "incidents": data.get(guild_id, [])[-50:]})
    return jsonify(data)


@app.route("/api/antinuke", methods=["GET"])
def api_antinuke():
    """Gibt Anti-Nuke-Log zurück."""
    _check_auth()
    data     = _read(ANTINUKE)
    guild_id = request.args.get("guild_id")
    if guild_id:
        return jsonify({"guild_id": guild_id, "incidents": data.get(guild_id, [])[-50:]})
    return jsonify(data)


@app.route("/api/cases", methods=["GET"])
def api_cases():
    """Gibt Fälle zurück (optional gefiltert nach guild_id und user_id)."""
    _check_auth()
    data     = _read(CASES)
    guild_id = request.args.get("guild_id")
    user_id  = request.args.get("user_id")
    if guild_id:
        guild_cases = data.get(guild_id, {}).get("cases", {})
        if user_id:
            guild_cases = {
                k: v for k, v in guild_cases.items()
                if str(v.get("user_id")) == user_id
            }
        return jsonify({"guild_id": guild_id, "cases": guild_cases})
    return jsonify(data)


@app.route("/api/automations", methods=["GET"])
def api_automations():
    """Gibt alle Automations zurück."""
    _check_auth()
    data     = _read(AUTOMATION)
    guild_id = request.args.get("guild_id")
    if guild_id:
        return jsonify({
            "guild_id":   guild_id,
            "automations": data.get(guild_id, {}).get("automations", {}),
        })
    return jsonify(data)


@app.route("/api/sla", methods=["GET"])
def api_sla():
    """Gibt SLA-Konfiguration und Statistiken zurück."""
    _check_auth()
    data     = _read(SLA_CFG)
    analytics = _read(ANALYTICS)
    guild_id = request.args.get("guild_id")
    if guild_id:
        stored  = data.get(guild_id, {})
        config  = {
            "enabled":            stored.get("enabled", False),
            "sla_hours":          stored.get("sla_hours", 24),
            "warn_hours":         stored.get("warn_hours", 20),
            "auto_close_hours":   stored.get("auto_close_hours", 48),
            "auto_close_enabled": stored.get("auto_close_enabled", False),
            "log_channel_id":     stored.get("log_channel_id"),
        }
        stats_key   = f"stats_{guild_id}"
        guild_stats = analytics.get(stats_key, {})
        total_closed   = sum(s.get("closed", 0) for s in guild_stats.values())
        total_resp     = sum(s.get("response_count", 0) for s in guild_stats.values())
        total_resp_s   = sum(s.get("total_response_s", 0) for s in guild_stats.values())
        avg_resp_min   = round(total_resp_s / total_resp / 60, 1) if total_resp > 0 else None
        return jsonify({
            "guild_id":       guild_id,
            "config":         config,
            "total_closed":   total_closed,
            "avg_response_min": avg_resp_min,
            "supporters":     len(guild_stats),
        })
    return jsonify({"sla": data})


@app.route("/api/captcha", methods=["GET"])
def api_captcha():
    """Gibt Captcha-Konfiguration zurück."""
    _check_auth()
    data     = _read(CAPTCHA_CFG)
    guild_id = request.args.get("guild_id")
    if guild_id:
        stored = data.get(guild_id, {})
        return jsonify({
            "guild_id":    guild_id,
            "enabled":     stored.get("enabled", False),
            "alt_min_days": stored.get("alt_min_days", 30),
            "alt_action":  stored.get("alt_action", "log"),
        })
    return jsonify(data)


@app.route("/api/appeals", methods=["GET"])
def api_appeals():
    """Gibt Einsprüche zurück (optional nach guild_id und Status gefiltert)."""
    _check_auth()
    data     = _read(APPEALS)
    guild_id = request.args.get("guild_id")
    status   = request.args.get("status")   # pending | accepted | denied
    if guild_id:
        guild_appeals = data.get(guild_id, {})
        if status:
            guild_appeals = {k: v for k, v in guild_appeals.items() if v.get("status") == status}
        return jsonify({"guild_id": guild_id, "appeals": guild_appeals})
    return jsonify(data)


@app.route("/api/status", methods=["GET"])
def api_status():
    """Gibt Bot-Status-Informationen zurück."""
    return jsonify({
        "status":    "online",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "version":   "3.0.0",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Web-Dashboard HTML (Single-Page)
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AVOKE Bot Dashboard</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0d1117; color: #c9d1d9; font-family: "Segoe UI", system-ui, sans-serif; font-size: 14px; line-height: 1.6; }
  a { color: #58a6ff; text-decoration: none; }
  header { background: #161b22; border-bottom: 1px solid #30363d; padding: 16px 32px; display: flex; align-items: center; gap: 16px; }
  header h1 { font-size: 20px; color: #58d68d; font-weight: 700; }
  header span { font-size: 12px; color: #8b949e; background: #1f2937; padding: 3px 10px; border-radius: 20px; }
  .container { max-width: 1100px; margin: 32px auto; padding: 0 16px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 32px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
  .card h2 { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 8px; }
  .card .value { font-size: 32px; font-weight: 700; color: #58d68d; }
  .card .sub { font-size: 12px; color: #8b949e; margin-top: 4px; }
  .section { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 24px; margin-bottom: 24px; }
  .section h3 { font-size: 16px; font-weight: 600; margin-bottom: 16px; color: #e6edf3; border-bottom: 1px solid #30363d; padding-bottom: 12px; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #8b949e; border-bottom: 1px solid #30363d; }
  td { padding: 10px 12px; border-bottom: 1px solid #21262d; font-size: 13px; }
  tr:last-child td { border-bottom: none; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: 600; }
  .badge-green { background: #1a3a2a; color: #58d68d; }
  .badge-red   { background: #3a1a1a; color: #f85149; }
  .badge-blue  { background: #1a2a3a; color: #58a6ff; }
  .badge-yellow{ background: #3a2a1a; color: #d29922; }
  .nav { display: flex; gap: 8px; margin-bottom: 24px; flex-wrap: wrap; }
  .nav button { background: #21262d; border: 1px solid #30363d; color: #c9d1d9; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .nav button.active { background: #388bfd22; border-color: #388bfd; color: #58a6ff; }
  .tab { display: none; }
  .tab.active { display: block; }
  footer { text-align: center; padding: 32px; font-size: 12px; color: #484f58; border-top: 1px solid #21262d; margin-top: 32px; }
  .loading { color: #8b949e; font-style: italic; }
  .error { color: #f85149; }
</style>
</head>
<body>
<header>
  <h1>⚡ AVOKE Dashboard</h1>
  <span id="status-badge">Lade…</span>
</header>
<div class="container">
  <div class="grid" id="stat-cards">
    <div class="card"><h2>Offene Tickets</h2><div class="value loading" id="stat-tickets">—</div><div class="sub">Aktive Supportgespräche</div></div>
    <div class="card"><h2>Security Vorfälle</h2><div class="value loading" id="stat-security">—</div><div class="sub">Letzte 24h</div></div>
    <div class="card"><h2>Automationen</h2><div class="value loading" id="stat-automations">—</div><div class="sub">Aktiv auf diesem Server</div></div>
    <div class="card"><h2>Fälle</h2><div class="value loading" id="stat-cases">—</div><div class="sub">Moderations-Cases gesamt</div></div>
  </div>

  <div class="nav">
    <button class="active" onclick="showTab('tickets', this)">🎫 Tickets</button>
    <button onclick="showTab('analytics', this)">📊 Analytics</button>
    <button onclick="showTab('security', this)">🛡️ Security</button>
    <button onclick="showTab('antinuke', this)">🔒 Anti-Nuke</button>
    <button onclick="showTab('cases', this)">📋 Cases</button>
    <button onclick="showTab('appeals', this)">📩 Einsprüche</button>
    <button onclick="showTab('automations', this)">🤖 Automationen</button>
    <button onclick="showTab('sla', this)">⏱️ SLA</button>
  </div>

  <div id="tab-tickets" class="tab active section">
    <h3>🎫 Offene Tickets</h3>
    <table><thead><tr><th>Channel</th><th>Nutzer</th><th>Panel</th><th>Priorität</th><th>Erstellt</th></tr></thead>
    <tbody id="tickets-body"><tr><td colspan="5" class="loading">Lade…</td></tr></tbody></table>
  </div>

  <div id="tab-analytics" class="tab section">
    <h3>📊 Supporter-Analytics</h3>
    <table><thead><tr><th>Supporter</th><th>Geschlossen</th><th>Ø Antwortzeit</th><th>Ø Bewertung</th></tr></thead>
    <tbody id="analytics-body"><tr><td colspan="4" class="loading">Lade…</td></tr></tbody></table>
  </div>

  <div id="tab-security" class="tab section">
    <h3>🛡️ Security-Vorfälle</h3>
    <table><thead><tr><th>Nutzer</th><th>Grund</th><th>Risiko</th><th>Aktion</th><th>Zeitpunkt</th></tr></thead>
    <tbody id="security-body"><tr><td colspan="5" class="loading">Lade…</td></tr></tbody></table>
  </div>

  <div id="tab-antinuke" class="tab section">
    <h3>🔒 Anti-Nuke Log</h3>
    <table><thead><tr><th>Nutzer</th><th>Grund</th><th>Zeitpunkt</th></tr></thead>
    <tbody id="antinuke-body"><tr><td colspan="3" class="loading">Lade…</td></tr></tbody></table>
  </div>

  <div id="tab-cases" class="tab section">
    <h3>📋 Moderations-Cases</h3>
    <table><thead><tr><th>Fall #</th><th>Aktion</th><th>Nutzer</th><th>Moderator</th><th>Grund</th><th>Datum</th></tr></thead>
    <tbody id="cases-body"><tr><td colspan="6" class="loading">Lade…</td></tr></tbody></table>
  </div>

  <div id="tab-appeals" class="tab section">
    <h3>📩 Einsprüche</h3>
    <table><thead><tr><th>Fall #</th><th>Antragsteller</th><th>Status</th><th>Eingereicht</th></tr></thead>
    <tbody id="appeals-body"><tr><td colspan="4" class="loading">Lade…</td></tr></tbody></table>
  </div>

  <div id="tab-automations" class="tab section">
    <h3>🤖 Automationen</h3>
    <table><thead><tr><th>Name</th><th>ID</th><th>Trigger</th><th>Aktion</th><th>Status</th></tr></thead>
    <tbody id="automations-body"><tr><td colspan="5" class="loading">Lade…</td></tr></tbody></table>
  </div>

  <div id="tab-sla" class="tab section">
    <h3>⏱️ SLA-Übersicht</h3>
    <div id="sla-content" class="loading">Lade…</div>
  </div>
</div>
<footer>AVOKE Bot Dashboard v3.0 &mdash; nur für autorisierte Administratoren</footer>

<script>
const GUILD_ID = new URLSearchParams(location.search).get("guild_id") || "";
const HEADERS  = {};
const TOKEN    = new URLSearchParams(location.search).get("token");
if (TOKEN) HEADERS["Authorization"] = "Bearer " + TOKEN;

async function fetchJSON(url) {
  const resp = await fetch(url, { headers: HEADERS });
  if (!resp.ok) throw new Error(resp.status);
  return resp.json();
}

function esc(s) {
  if (!s) return "—";
  return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}

function prio(p) {
  const map = { dringend: "badge-red", hoch: "badge-yellow", normal: "badge-blue", niedrig: "badge-green" };
  return `<span class="badge ${map[p]||'badge-blue'}">${esc(p)||"normal"}</span>`;
}

function ts(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("de-DE");
}

function showTab(name, btn) {
  document.querySelectorAll(".tab").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".nav button").forEach(el => el.classList.remove("active"));
  document.getElementById("tab-" + name).classList.add("active");
  if (btn) btn.classList.add("active");
}

async function loadAll() {
  // Status
  try {
    const s = await fetchJSON("/api/status");
    document.getElementById("status-badge").textContent = "✅ Online";
    document.getElementById("status-badge").style.color = "#58d68d";
  } catch { document.getElementById("status-badge").textContent = "⚠️ Verbindungsfehler"; }

  // Tickets
  try {
    const t = await fetchJSON("/api/tickets");
    document.getElementById("stat-tickets").textContent = t.open_count;
    const tbody = document.getElementById("tickets-body");
    if (!t.tickets.length) { tbody.innerHTML = "<tr><td colspan='5'>Keine offenen Tickets</td></tr>"; return; }
    tbody.innerHTML = t.tickets.map(tk =>
      `<tr><td>#${esc(tk.channel_id)}</td><td>${esc(tk.user_id)}</td><td>${esc(tk.panel)}</td><td>${prio(tk.priority)}</td><td>${ts(tk.created_at)}</td></tr>`
    ).join("");
  } catch { document.getElementById("tickets-body").innerHTML = "<tr><td colspan='5' class='error'>Fehler beim Laden</td></tr>"; }

  // Analytics
  try {
    const base = "/api/analytics" + (GUILD_ID ? "?guild_id=" + GUILD_ID : "");
    const a = await fetchJSON(base);
    const supporters = a.supporters || [];
    const tbody = document.getElementById("analytics-body");
    document.getElementById("stat-automations").textContent = "—";
    if (!supporters.length) { tbody.innerHTML = "<tr><td colspan='4'>Keine Daten</td></tr>"; }
    else tbody.innerHTML = supporters.map(s =>
      `<tr><td><@${esc(s.user_id)}></td><td>${esc(s.closed)}</td><td>${s.avg_response_min != null ? s.avg_response_min + "min" : "—"}</td><td>${s.avg_rating != null ? s.avg_rating + " ⭐" : "—"}</td></tr>`
    ).join("");
  } catch {}

  // Security
  try {
    const base = "/api/security" + (GUILD_ID ? "?guild_id=" + GUILD_ID : "");
    const sec = await fetchJSON(base);
    const items = sec.incidents || [];
    document.getElementById("stat-security").textContent = items.filter(i => {
      try { return (Date.now() - new Date(i.timestamp).getTime()) < 86400000; } catch { return false; }
    }).length;
    const tbody = document.getElementById("security-body");
    if (!items.length) tbody.innerHTML = "<tr><td colspan='5'>Keine Vorfälle</td></tr>";
    else tbody.innerHTML = items.slice(-20).reverse().map(i =>
      `<tr><td><@${esc(i.user_id)}></td><td>${esc(i.reason)}</td><td>${esc(i.risk)}/100</td><td>${esc(i.action)}</td><td>${ts(i.timestamp)}</td></tr>`
    ).join("");
  } catch {}

  // Anti-Nuke
  try {
    const base = "/api/antinuke" + (GUILD_ID ? "?guild_id=" + GUILD_ID : "");
    const an = await fetchJSON(base);
    const items = an.incidents || [];
    const tbody = document.getElementById("antinuke-body");
    if (!items.length) tbody.innerHTML = "<tr><td colspan='3'>Keine Vorfälle</td></tr>";
    else tbody.innerHTML = items.slice(-20).reverse().map(i =>
      `<tr><td>${esc(i.user_id)}</td><td>${esc(i.reason)}</td><td>${ts(i.timestamp)}</td></tr>`
    ).join("");
  } catch {}

  // Appeals
  try {
    const base = "/api/appeals?status=pending" + (GUILD_ID ? "&guild_id=" + GUILD_ID : "");
    const ap = await fetchJSON(base);
    const apItems = Object.entries(ap.appeals || {});
    const tbody = document.getElementById("appeals-body");
    if (!apItems.length) tbody.innerHTML = "<tr><td colspan='4'>Keine ausstehenden Einsprüche</td></tr>";
    else tbody.innerHTML = apItems.map(([cid, a]) =>
      `<tr><td>#${esc(cid)}</td><td>${esc(a.user_id)}</td><td><span class="badge badge-yellow">${esc(a.status)}</span></td><td>${ts(a.submitted)}</td></tr>`
    ).join("");
  } catch {}

  // SLA-Report
  try {
    const base = "/api/sla" + (GUILD_ID ? "?guild_id=" + GUILD_ID : "");
    const sla = await fetchJSON(base);
    const el = document.getElementById("sla-content");
    if (sla.config) {
      const cfg = sla.config;
      el.innerHTML = `
        <table><tbody>
          <tr><td><b>Status</b></td><td><span class="badge ${cfg.enabled ? 'badge-green' : 'badge-red'}">${cfg.enabled ? 'Aktiv' : 'Inaktiv'}</span></td></tr>
          <tr><td><b>SLA-Frist</b></td><td>${cfg.sla_hours}h</td></tr>
          <tr><td><b>Warnung</b></td><td>${cfg.warn_hours}h vor Ablauf</td></tr>
          <tr><td><b>Auto-Close</b></td><td>${cfg.auto_close_enabled ? cfg.auto_close_hours + 'h Inaktivität' : 'Deaktiviert'}</td></tr>
          <tr><td><b>Tickets geschlossen</b></td><td>${esc(sla.total_closed)}</td></tr>
          <tr><td><b>Ø Antwortzeit</b></td><td>${sla.avg_response_min != null ? sla.avg_response_min + ' min' : '—'}</td></tr>
          <tr><td><b>Aktive Supporter</b></td><td>${esc(sla.supporters)}</td></tr>
        </tbody></table>`;
    } else {
      el.innerHTML = "<p>Bitte guild_id Parameter setzen für SLA-Daten.</p>";
    }
  } catch {}

  // Cases
  try {
    const base = "/api/cases" + (GUILD_ID ? "?guild_id=" + GUILD_ID : "");
    const c = await fetchJSON(base);
    const cases = Object.entries(c.cases || c || {}).filter(([k]) => k !== "cases");
    const tbody = document.getElementById("cases-body");
    if (!cases.length) tbody.innerHTML = "<tr><td colspan='6'>Keine Fälle</td></tr>";
    else {
      const all = Object.entries(c.cases || {});
      document.getElementById("stat-cases").textContent = all.length;
      tbody.innerHTML = all.slice(-20).reverse().map(([id, v]) =>
        `<tr><td>#${esc(id)}</td><td>${esc(v.action)}</td><td>${esc(v.user_id)}</td><td>${esc(v.mod_id)}</td><td>${esc(v.reason)}</td><td>${ts(v.timestamp)}</td></tr>`
      ).join("");
    }
  } catch {}

  // Automations
  try {
    const base = "/api/automations" + (GUILD_ID ? "?guild_id=" + GUILD_ID : "");
    const aut = await fetchJSON(base);
    const autos = Object.values(aut.automations || {});
    document.getElementById("stat-automations").textContent = autos.filter(a => a.enabled !== false).length;
    const tbody = document.getElementById("automations-body");
    if (!autos.length) tbody.innerHTML = "<tr><td colspan='5'>Keine Automationen</td></tr>";
    else tbody.innerHTML = autos.map(a =>
      `<tr><td>${esc(a.name)}</td><td><code>${esc(a.id)}</code></td><td>${esc(a.trigger)}</td><td>${esc(a.action)}</td><td><span class="badge ${a.enabled !== false ? 'badge-green' : 'badge-red'}">${a.enabled !== false ? 'Aktiv' : 'Inaktiv'}</span></td></tr>`
    ).join("");
  } catch {}
}

loadAll();
setInterval(loadAll, 30000);  // alle 30s aktualisieren
</script>
</body>
</html>"""


@app.route("/", methods=["GET"])
def dashboard():
    return DASHBOARD_HTML


# ─────────────────────────────────────────────────────────────────────────────
# Standalone-Start
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    debug = os.getenv("DASHBOARD_DEBUG", "false").lower() == "true"
    print(f"AVOKE Dashboard läuft auf http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)
