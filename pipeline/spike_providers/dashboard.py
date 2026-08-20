"""The decision surface: one HTML file, map + table + verdicts, no server.

Generated from the same payload results.json holds, so the dashboard cannot
disagree with the data beside it — the review-bundle rule, applied to a page.
Self-contained except Leaflet and the OSM raster tiles (this is a local file
for a browser with network; the strict no-external-hosts rule is an Artifact
constraint, not a local one). OSM attribution renders on the map control.

Colours follow the brand system: dark ground, lime for the thing that is ours
and confirmed; each provider gets one hue and keeps it in the map, the table
and the cards, so "which source is this" is never a lookup.
"""

from __future__ import annotations

import json


def provider_summary(payload: dict) -> list[dict]:
    """Per-provider aggregates, computed here so the page only renders."""
    providers: dict[str, dict] = {}
    for result in payload["results"]:
        p = providers.setdefault(
            result["provider"],
            {
                "provider": result["provider"],
                "candidates": 0,
                "shares": [],
                "sac": 0,
                "mtb": 0,
                "places": 0,
            },
        )
        e = result["enrichment"]
        p["candidates"] += 1
        p["shares"].append(e["matched_share"])
        p["sac"] += e["sac_scale"] is not None
        p["mtb"] += e["mtb"]["rideable"] is not None
        p["places"] += len(e["places"])
    out = []
    for name in ("osm", "trailsplits", "ors", "freeroute"):
        p = providers.get(name)
        if p is None:
            out.append(
                {
                    "provider": name,
                    "candidates": 0,
                    "mean_share": None,
                    "sac": 0,
                    "mtb": 0,
                    "places": 0,
                    "notes": payload["notes"].get(name, []),
                }
            )
            continue
        out.append(
            {
                "provider": name,
                "candidates": p["candidates"],
                "mean_share": round(sum(p["shares"]) / len(p["shares"]), 3),
                "sac": p["sac"],
                "mtb": p["mtb"],
                "places": p["places"],
                "notes": payload["notes"].get(name, []),
            }
        )
    return out


TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VaiVia — provider spike</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  :root {
    --ground:#101410; --panel:#161c16; --line:#2a332a; --ink:#e6ede4;
    --dim:#8fa08c; --lime:#b9ff3c; --flare:#ff5a4c;
    --osm:#b9ff3c; --trailsplits:#ffa03c; --ors:#4c9aff; --freeroute:#ff5a4c;
  }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--ground); color:var(--ink);
         font:14px/1.45 "Segoe UI",system-ui,sans-serif; height:100vh;
         display:flex; flex-direction:column; }
  header { padding:10px 16px; border-bottom:1px solid var(--line);
           display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
  header h1 { font-size:15px; font-weight:600; letter-spacing:.02em; }
  header .meta { color:var(--dim); font-size:12px; }
  #cards { display:flex; gap:1px; background:var(--line);
           border-bottom:1px solid var(--line); }
  .card { flex:1; background:var(--panel); padding:8px 12px; min-width:0; }
  .card h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
             display:flex; align-items:center; gap:6px; }
  .card h2 .dot { width:9px; height:9px; display:inline-block; }
  .card .big { font-size:20px; font-weight:600; margin:2px 0; }
  .card .sub { font-size:11px; color:var(--dim); }
  #main { flex:1; display:flex; min-height:0; }
  #map { flex:1.4; }
  #side { flex:1; min-width:380px; max-width:560px; overflow-y:auto;
          border-left:1px solid var(--line); background:var(--panel); }
  #filters { padding:8px 12px; border-bottom:1px solid var(--line);
             display:flex; gap:14px; flex-wrap:wrap; }
  #filters label { display:flex; gap:5px; align-items:center; cursor:pointer;
                   font-size:12px; }
  table { width:100%; border-collapse:collapse; font-size:12px; }
  th { text-align:left; color:var(--dim); font-weight:500; padding:6px 8px;
       border-bottom:1px solid var(--line); position:sticky; top:0;
       background:var(--panel); }
  td { padding:5px 8px; border-bottom:1px solid var(--line); cursor:pointer;
       white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:150px; }
  tr:hover td { background:#1d251d; }
  tr.sel td { background:#232f1a; }
  .num { text-align:right; font-variant-numeric:tabular-nums; }
  .pdot { width:8px; height:8px; display:inline-block; margin-right:5px; }
  #detail { padding:12px; border-top:1px solid var(--line); display:none; }
  #detail h3 { font-size:13px; margin-bottom:6px; }
  #detail .kv { display:grid; grid-template-columns:130px 1fr; gap:2px 10px;
                font-size:12px; }
  #detail .kv div:nth-child(odd) { color:var(--dim); }
  .bar { height:10px; background:var(--line); margin:2px 0; position:relative; }
  .bar span { position:absolute; inset:0 auto 0 0; background:var(--lime); }
  .bar b { position:absolute; right:4px; top:-2px; font-size:10px;
           font-weight:400; color:var(--ink); }
  .warn { color:var(--flare); }
  .ok { color:var(--lime); }
  #notes { padding:10px 12px; font-size:11px; color:var(--dim);
           border-top:1px solid var(--line); }
  #notes h3 { color:var(--ink); font-size:11px; text-transform:uppercase;
              letter-spacing:.06em; margin:6px 0 2px; }
  .leaflet-container { background:#0b0e0b; }
  .leaflet-control-attribution { background:rgba(16,20,16,.8)!important;
                                 color:var(--dim)!important; }
  .leaflet-control-attribution a { color:var(--dim)!important; }
</style>
</head>
<body>
<header>
  <h1>VaiVia · provider spike</h1>
  <span class="meta">__GENERATED__ · __BBOX__ · geometry from each provider, metadata enriched from the curated OSM network</span>
</header>
<div id="cards"></div>
<div id="main">
  <div id="map"></div>
  <div id="side">
    <div id="filters"></div>
    <table id="tbl">
      <thead><tr>
        <th>route</th><th>source</th><th class="num">km</th>
        <th class="num">matched</th><th>SAC</th><th>MTB</th><th class="num">POIs</th>
      </tr></thead>
      <tbody></tbody>
    </table>
    <div id="detail"></div>
    <div id="notes"></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const GEO = __GEOJSON__;
const SUMMARY = __SUMMARY__;
const COLOR = {osm:"#b9ff3c", trailsplits:"#ffa03c", ors:"#4c9aff", freeroute:"#ff5a4c"};
const state = {enabled:{osm:true,trailsplits:true,ors:true,freeroute:true}, sel:null};

const map = L.map("map", {zoomControl:true});
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
  {attribution:"&copy; <a href='https://www.openstreetmap.org/copyright'>OpenStreetMap</a> contributors", opacity:0.55}).addTo(map);

const layers = {};
const geoLayer = L.geoJSON(GEO, {
  style: f => ({color: COLOR[f.properties.provider] || "#fff",
                weight: f.properties.provider === "osm" ? 4 : 3,
                opacity: 0.9, dashArray: f.properties.provider === "trailsplits" ? "6 5" : null}),
  onEachFeature: (f, layer) => {
    layers[f.properties.id] = layer;
    layer.on("click", () => select(f.properties.id));
    layer.bindTooltip(`${f.properties.name || f.properties.id} · ${f.properties.provider}`);
  }
}).addTo(map);
map.fitBounds(geoLayer.getBounds(), {padding:[24,24]});

const poiLayer = L.layerGroup().addTo(map);

function fmtShare(s){ return s == null ? "—" : Math.round(s*100) + "%"; }
function fmtMtb(m){
  if (m.rideable === null) return "<span class='warn'>?</span>";
  if (!m.rideable) return "<span class='warn'>no</span>";
  return "<span class='ok'>yes" + (m.mtb_scale ? " S" + m.mtb_scale.replace(/^(\\d)$/, "$1") : "") + "</span>";
}
function sacShort(s){
  return s ? {hiking:"T1", mountain_hiking:"T2", demanding_mountain_hiking:"T3",
    alpine_hiking:"T4", demanding_alpine_hiking:"T5", difficult_alpine_hiking:"T6"}[s] || s : "—";
}

function renderCards(){
  document.getElementById("cards").innerHTML = SUMMARY.map(p => `
    <div class="card">
      <h2><span class="dot" style="background:${COLOR[p.provider]}"></span>${p.provider}</h2>
      <div class="big">${p.candidates || "0"}</div>
      <div class="sub">${p.candidates
        ? `routes · matched ${fmtShare(p.mean_share)} · SAC on ${p.sac} · MTB on ${p.mtb} · ${p.places} POIs`
        : (p.notes[0] || "nothing returned")}</div>
    </div>`).join("");
}

function renderFilters(){
  document.getElementById("filters").innerHTML = Object.keys(state.enabled).map(p => `
    <label><input type="checkbox" ${state.enabled[p] ? "checked" : ""} data-p="${p}">
    <span class="pdot" style="background:${COLOR[p]}"></span>${p}</label>`).join("");
  document.querySelectorAll("#filters input").forEach(el =>
    el.addEventListener("change", e => {
      state.enabled[e.target.dataset.p] = e.target.checked; refresh();
    }));
}

function renderTable(){
  const rows = DATA.results.filter(r => state.enabled[r.provider]).map(r => {
    const e = r.enrichment;
    return `<tr data-id="${r.id}" class="${state.sel === r.id ? "sel" : ""}">
      <td title="${r.name || r.id}">${r.name || r.id}</td>
      <td><span class="pdot" style="background:${COLOR[r.provider]}"></span>${r.provider}</td>
      <td class="num">${(e.line_length_m/1000).toFixed(1)}</td>
      <td class="num">${fmtShare(e.matched_share)}</td>
      <td>${sacShort(e.sac_scale)}</td>
      <td>${fmtMtb(e.mtb)}</td>
      <td class="num">${e.places.length}</td>
    </tr>`;
  }).join("");
  document.querySelector("#tbl tbody").innerHTML = rows;
  document.querySelectorAll("#tbl tbody tr").forEach(tr =>
    tr.addEventListener("click", () => select(tr.dataset.id)));
}

function renderDetail(){
  const el = document.getElementById("detail");
  const r = DATA.results.find(x => x.id === state.sel);
  if (!r){ el.style.display = "none"; return; }
  const e = r.enrichment;
  const surf = Object.entries(e.surface).sort((a,b) => b[1]-a[1]).map(([k,v]) =>
    `<div>${k}</div><div class="bar"><span style="width:${Math.round(v*100)}%"></span><b>${Math.round(v*100)}%</b></div>`).join("");
  const pois = e.places.slice(0, 12).map(p =>
    `<div>${p.kind}</div><div>${p.name || "(unnamed)"} · ${p.offset_m} m off${p.distance_along_m != null ? " · " + (p.distance_along_m/1000).toFixed(1) + " km along" : ""}</div>`).join("");
  el.style.display = "block";
  el.innerHTML = `<h3>${r.name || r.id}</h3>
    <div class="kv">
      <div>source</div><div>${r.provider}</div>
      <div>length</div><div>${(e.line_length_m/1000).toFixed(2)} km (${r.pieces} piece${r.pieces>1?"s":""})</div>
      <div>matched to our net</div><div>${fmtShare(e.matched_share)} · ${e.matched_edges} edges</div>
      <div>difficulty (≥5% rule)</div><div>${e.sac_scale || "ungraded"}</div>
      <div>MTB</div><div>${fmtMtb(e.mtb)}${e.mtb.reason ? " — " + e.mtb.reason : ""}</div>
      <div>surface</div><div style="max-width:260px">${surf || "unknown"}</div>
      <div>places ≤100 m</div><div>${e.places.length}${pois ? "" : ""}</div>
      ${pois}
    </div>`;
}

function refresh(){
  GEO.features.forEach(f => {
    const layer = layers[f.properties.id];
    const on = state.enabled[f.properties.provider];
    if (on && !map.hasLayer(layer)) layer.addTo(map);
    if (!on && map.hasLayer(layer)) map.removeLayer(layer);
    layer.setStyle({weight: f.properties.id === state.sel ? 6 : (f.properties.provider === "osm" ? 4 : 3),
                    opacity: state.sel && f.properties.id !== state.sel ? 0.45 : 0.9});
  });
  poiLayer.clearLayers();
  const r = DATA.results.find(x => x.id === state.sel);
  if (r){
    r.enrichment.places.forEach(p => {
      L.circleMarker([p.lat, p.lon], {
        radius: p.is_start ? 6 : 4,
        color: p.is_start ? "#b9ff3c" : "#e6ede4",
        weight: p.is_start ? 2 : 1,
        fillColor: p.is_start ? "#b9ff3c" : "#5a6a58",
        fillOpacity: 0.85,
      }).bindTooltip(`${p.name || p.kind}${p.is_start ? " · start" : ""} · ${p.offset_m} m off`)
        .addTo(poiLayer);
    });
  }
  renderTable(); renderDetail();
}

function select(id){
  state.sel = state.sel === id ? null : id;
  if (state.sel && layers[id]) map.fitBounds(layers[id].getBounds(), {padding:[40,40]});
  refresh();
}

function renderNotes(){
  document.getElementById("notes").innerHTML =
    "<h3>provider notes</h3>" + Object.entries(DATA.notes).map(([p, ns]) =>
      ns.length ? `<h3 style="color:${COLOR[p]}">${p}</h3>` + ns.map(n => `· ${n}`).join("<br>") : ""
    ).join("");
}

renderCards(); renderFilters(); renderTable(); renderNotes();
</script>
</body>
</html>
"""


def render_dashboard(payload: dict, collection: dict) -> str:
    return (
        TEMPLATE.replace("__GENERATED__", payload["generated"])
        .replace("__BBOX__", payload["bbox"])
        .replace("__DATA__", json.dumps(payload, ensure_ascii=False))
        .replace("__GEOJSON__", json.dumps(collection, ensure_ascii=False))
        .replace(
            "__SUMMARY__", json.dumps(provider_summary(payload), ensure_ascii=False)
        )
    )
