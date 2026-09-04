/* Vue WLD — World Monitor (agents autonomes : ingestion → qualité → corrélateur).
   Source unique : GET /api/worldmon. Lecture seule. Aucun ordre réel.
   Script classique, s'appuie sur window.CC (core.js) chargé avant. */
(function () {
  "use strict";

  var CC = window.CC;
  var F = CC.fmt;
  var esc = CC.esc || function (s) {
    var M = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    return s == null ? "" : String(s).replace(/[&<>"']/g, function (c) { return M[c]; });
  };
  var DASH = "—";

  var AGENT_LABEL = {
    ingestor: "ingestor · collecte des sources",
    enricher: "enricher · enrichissement",
    quality: "quality · gate de fiabilité",
    correlator: "correlator · corrélations world→prix"
  };
  var DEFAULT_WARN = "Corrélateur : IC bootstrap 95 % + avertissement multiple-testing. Un candidat n'est jamais un edge sans walk-forward.";
  var STALE_H = 3;            // heartbeat > 3 h → stale (cycle attendu quotidien/horaire)

  var root = null;
  var lastOk = null;          // dernier payload valide (pour figer l'affichage si l'endpoint tombe)

  // ── helpers locaux ────────────────────────────────────────────────────────
  function $(id) { return root ? root.querySelector("#" + id) : null; }
  function setHTML(id, html) { var el = $(id); if (el && el.innerHTML !== html) el.innerHTML = html; }
  function unavailable(msg) { return '<div class="muted small">indisponible : ' + esc(msg || "erreur inconnue") + "</div>"; }
  function isPrim(v) { return v == null || typeof v !== "object"; }
  function prim(v) {
    if (v == null) return DASH;
    if (typeof v === "boolean") return v ? "oui" : "non";
    if (typeof v === "number") return Number.isInteger(v) ? F.int(v) : F.num(v);
    return String(v);
  }
  function ageH(iso) {
    var d = F.parseDate(iso); if (!d) return null;
    return (Date.now() - d.getTime()) / 3600000;
  }
  function freshOf(d) {
    if (!d) return "unknown";
    if (d.backend === "unavailable") return "error";
    var a = ageH(d.heartbeat);
    if (d.pipeline_healthy === false) return "error";
    if (a == null) return "unknown";
    return a <= STALE_H ? "fresh" : "stale";
  }
  // détail d'agent → texte lisible ; les sous-objets plats deviennent "k: v · k: v" ; les autres sont résumés
  function detailLines(det, skipKeys) {
    var out = [];
    if (!det || typeof det !== "object") return out;
    Object.keys(det).forEach(function (k) {
      if (skipKeys && skipKeys.indexOf(k) >= 0) return;
      var v = det[k];
      if (isPrim(v)) { out.push(esc(k) + " : <b>" + esc(prim(v)) + "</b>"); return; }
      if (Array.isArray(v)) { out.push(esc(k) + " : <b>" + esc(F.int(v.length)) + "</b> éléments"); return; }
      var ks = Object.keys(v);
      var flat = ks.every(function (x) { return isPrim(v[x]); });
      if (flat && ks.length <= 8) {
        out.push(esc(k) + " : " + ks.map(function (x) { return esc(x) + " <b>" + esc(prim(v[x])) + "</b>"; }).join(" · "));
      } else {
        out.push(esc(k) + " : " + esc(F.int(ks.length)) + " entrées");
      }
    });
    return out;
  }

  // ── squelette ─────────────────────────────────────────────────────────────
  function skeleton() {
    return ''
      + '<div class="grid">'
      +   '<div class="c12">' + CC.panel({ id: "wld-head", code: "WLD-01", title: "World Monitor — pipeline agents autonomes", fresh: "unknown", body: '<div id="wld-head-b" class="muted small">chargement…</div>' }) + "</div>"
      + "</div>"
      + '<div class="grid">'
      +   '<div class="c6">' + CC.panel({ id: "wld-agents", code: "WLD-02", title: "Agents — dernier cycle", fresh: "unknown", body: '<div id="wld-agents-b" class="muted small">chargement…</div>' }) + "</div>"
      +   '<div class="c3">' + CC.panel({ id: "wld-quality", code: "WLD-03", title: "Qualité — gate", fresh: "unknown", body: '<div id="wld-quality-b" class="muted small">chargement…</div>' }) + "</div>"
      +   '<div class="c3">' + CC.panel({ id: "wld-ingest", code: "WLD-04", title: "Ingestion", fresh: "unknown", body: '<div id="wld-ingest-b" class="muted small">chargement…</div>' }) + "</div>"
      + "</div>"
      + '<div class="grid">'
      +   '<div class="c5">' + CC.panel({ id: "wld-sources", code: "WLD-05", title: "Fraîcheur par source", fresh: "unknown", body: '<div id="wld-sources-b" class="muted small">chargement…</div>' }) + "</div>"
      +   '<div class="c7">' + CC.panel({ id: "wld-cand", code: "WLD-06", title: "Candidats de signal (IC 95 % exclut 0)", fresh: "unknown", body: '<div id="wld-cand-b" class="muted small">chargement…</div><div id="wld-cand-chart" class="chart chart-sm" hidden></div><div id="wld-cand-fn" class="fn"></div>' }) + "</div>"
      + "</div>"
      + '<div class="view-fn">World Monitor = recherche de données (événements monde → features quotidiennes → corrélations avec les prix). Aucune position, aucun ordre : rien ici n\'est un edge validé. Tout le terminal est paper/shadow : capital virtuel, aucun ordre réel.</div>';
  }

  function injectCss() {
    if (document.getElementById("css-world")) return;
    var st = document.createElement("style");
    st.id = "css-world";
    st.textContent = ""
      + ".wld-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px 16px}"
      + ".wld-tiles .lbl{margin-bottom:3px}"
      + ".wld-det{font-size:11px;color:var(--ink3);line-height:1.4;white-space:normal}"
      + ".wld-det b{color:var(--ink2);font-weight:400}"
      + ".wld-det span{display:inline-block;margin-right:10px}"
      + ".wld-ledtd{width:14px;padding-right:0!important}";
    document.head.appendChild(st);
  }

  // ── rendu ─────────────────────────────────────────────────────────────────
  function renderHead(d) {
    var agents = d.agents || {};
    var keys = Object.keys(agents);
    var nOk = keys.filter(function (k) { return agents[k] && agents[k].ok; }).length;
    var backendTxt = d.backend === "unavailable" ? "store indisponible" : "store : " + (d.backend || DASH);
    var backendKind = d.backend === "unavailable" ? "dn" : (d.backend === "mongodb" ? "info" : "muted");
    var healthTxt, healthKind;
    if (d.pipeline_healthy === true) { healthTxt = "pipeline OK"; healthKind = "up"; }
    else if (d.pipeline_healthy === false) { healthTxt = "pipeline dégradé"; healthKind = "dn"; }
    else { healthTxt = "pipeline : état inconnu"; healthKind = "muted"; }
    var q = d.quality || {};
    var trustTxt = q.data_trustworthy === true ? "données fiables" : (q.data_trustworthy === false ? "données non fiables (gate)" : "fiabilité inconnue");
    var trustKind = q.data_trustworthy === true ? "up" : (q.data_trustworthy === false ? "warn" : "muted");
    var hb = d.heartbeat;
    var html = ''
      + '<div class="row mb">' + CC.badge(backendTxt, backendKind, "Backend du BigDataStore (jsonl local ou MongoDB)")
      + " " + CC.badge(healthTxt, healthKind, "Tous les agents ont terminé sans erreur au dernier cycle")
      + " " + CC.badge(trustTxt, trustKind, "Gate qualité : schéma OK, > 98 % d\'événements uniques, couverture ≥ 50 %, |z volume| < 6")
      + '<span class="muted small">heartbeat ' + esc(F.dtfull(hb)) + (hb ? " (" + esc(F.ago(hb)) + ")" : "") + "</span></div>"
      + '<div class="wld-tiles">'
      +   '<div><div class="lbl" title="Événements stockés (toutes sources, tout historique)">événements stockés</div><div class="big-sm">' + esc(F.int(d.total_events)) + "</div></div>"
      +   '<div><div class="lbl" title="Événements sur la fenêtre glissante 90 jours">événements 90 j</div><div class="big-sm">' + esc(F.int(q.n_events_90d)) + "</div></div>"
      +   '<div><div class="lbl">agents OK</div><div class="big-sm ' + (keys.length && nOk === keys.length ? "up" : (keys.length ? "dn" : "")) + '">' + esc(F.int(nOk)) + " / " + esc(F.int(keys.length)) + "</div></div>"
      +   '<div><div class="lbl" title="Corrélations dont l\'IC 95 % bootstrap exclut 0 et |r| ≥ 0,1">candidats</div><div class="big-sm">' + esc(F.int((d.candidates || []).length)) + "</div><div class=\"sub muted\">" + (d.n_tests != null ? esc(F.int(d.n_tests)) + " tests" : "tests : " + DASH) + "</div></div>"
      + "</div>";
    setHTML("wld-head-b", html);
    CC.setAsOf($("wld-head"), hb, freshOf(d));
  }

  function renderAgents(d) {
    var agents = d.agents || {};
    var keys = Object.keys(agents);
    var fresh = freshOf(d);
    if (!keys.length) {
      setHTML("wld-agents-b", '<div class="muted small">aucun agent rapporté dans agent_health</div>');
      CC.setAsOf($("wld-agents"), d.heartbeat, fresh);
      return;
    }
    var rows = keys.map(function (k) {
      var a = agents[k] || {};
      var det = a.detail || {};
      var lines = detailLines(det, ["staleness", "generated_at"]);
      if (det && det.staleness && typeof det.staleness === "object") {
        var sk = Object.keys(det.staleness);
        var nf = sk.filter(function (s) { return det.staleness[s] && det.staleness[s].fresh; }).length;
        lines.push("fraîcheur : <b>" + esc(F.int(nf)) + " / " + esc(F.int(sk.length)) + "</b> sources fraîches");
      }
      var state = a.ok === true ? "fresh" : (a.ok === false ? "error" : "unknown");
      var stateTxt = a.ok === true ? "OK" : (a.ok === false ? "ERREUR" : "?");
      if (a.ok === true && det && det.skipped) { state = "stale"; stateTxt = "SAUTÉ"; }
      return "<tr>"
        + '<td class="wld-ledtd">' + CC.led(state) + "</td>"
        + '<td title="' + esc(AGENT_LABEL[k] || k) + '"><b>' + esc(k) + "</b></td>"
        + '<td class="' + (state === "fresh" ? "up" : state === "error" ? "dn" : "warn") + '">' + esc(stateTxt) + "</td>"
        + '<td class="wrap"><div class="wld-det">' + (lines.length ? lines.map(function (l) { return "<span>" + l + "</span>"; }).join("") : DASH) + "</div></td>"
        + "</tr>";
    }).join("");
    var html = '<div class="tblwrap" style="max-height:320px"><table class="tbl"><thead><tr>'
      + "<th></th><th>agent</th><th>état</th><th>détail du dernier cycle</th></tr></thead><tbody>" + rows + "</tbody></table></div>";
    setHTML("wld-agents-b", html);
    CC.setAsOf($("wld-agents"), d.heartbeat, fresh);
  }

  function qualityAsOf(d) {
    var qa = d.agents && d.agents.quality && d.agents.quality.detail;
    return (qa && qa.generated_at) || d.heartbeat;
  }

  function renderQuality(d) {
    var q = d.quality || {};
    var qa = (d.agents && d.agents.quality && d.agents.quality.detail) || {};
    var okCls = function (b) { return b === true ? "up" : (b === false ? "warn" : "muted"); };
    var rows = [
      ["données fiables", q.data_trustworthy === true ? "oui" : (q.data_trustworthy === false ? "non" : DASH), okCls(q.data_trustworthy),
        "Gate global : conditionne le corrélateur"],
      ["schéma OK", qa.schema_ok === true ? "oui" : (qa.schema_ok === false ? "non" : DASH), okCls(qa.schema_ok),
        "Colonnes requises présentes dans le store"],
      ["couverture sources", F.pct0(q.source_coverage, 0), CC.isNum(q.source_coverage) ? (q.source_coverage >= 0.5 ? "up" : "warn") : "muted",
        "Part des sources attendues actives sur 90 j (seuil ≥ 50 %)"],
      ["événements uniques", F.pct0(q.dedup_ratio, 1), CC.isNum(q.dedup_ratio) ? (q.dedup_ratio > 0.98 ? "up" : "warn") : "muted",
        "Part d\'événements sans doublon (hash de contenu) ; seuil > 98 %"],
      ["anomalie volume (z)", F.snum(q.volume_z_today, 2), CC.isNum(q.volume_z_today) ? (Math.abs(q.volume_z_today) < 6 ? "up" : "warn") : "muted",
        "z-score du volume d\'événements du jour vs historique (seuil |z| < 6)"],
      ["événements 90 j", F.int(q.n_events_90d), "", "Taille de la fenêtre analysée"]
    ];
    var html = '<div class="tblwrap"><table class="tbl"><tbody>' + rows.map(function (r) {
      return '<tr><td title="' + esc(r[3]) + '">' + esc(r[0]) + '</td><td class="num ' + r[2] + '">' + esc(r[1]) + "</td></tr>";
    }).join("") + "</tbody></table></div>";
    setHTML("wld-quality-b", html);
    var a = qualityAsOf(d);
    var ah = ageH(a);
    CC.setAsOf($("wld-quality"), a, ah == null ? "unknown" : (ah <= 26 ? "fresh" : "stale"));
  }

  function renderIngest(d) {
    var ing = d.agents && d.agents.ingestor;
    var det = (ing && ing.detail) || null;
    if (!det) {
      setHTML("wld-ingest-b", '<div class="muted small">aucun rapport d\'ingestion</div>');
      CC.setAsOf($("wld-ingest"), d.heartbeat, "unknown");
      return;
    }
    var ps = det.per_source || {};
    var srcRows = Object.keys(ps).sort(function (a, b) { return (Number(ps[b]) || 0) - (Number(ps[a]) || 0); }).map(function (s) {
      return "<tr><td>" + esc(s) + '</td><td class="num">' + esc(F.int(ps[s])) + "</td></tr>";
    }).join("");
    var html = '<div class="kv">'
      + '<dt title="Événements récupérés au dernier cycle">récupérés</dt><dd>' + esc(F.int(det.fetched)) + "</dd>"
      + '<dt title="Événements nouveaux écrits (après dédup)">écrits (nouveaux)</dt><dd>' + esc(F.int(det.written_new)) + "</dd>"
      + '<dt>total store</dt><dd>' + esc(F.int(det.total_events != null ? det.total_events : d.total_events)) + "</dd>"
      + "</div>"
      + (srcRows ? '<div class="tblwrap mt"><table class="tbl"><thead><tr><th>source</th><th class="num">récupérés</th></tr></thead><tbody>' + srcRows + "</tbody></table></div>"
                 : '<div class="muted small mt">détail par source absent</div>');
    setHTML("wld-ingest-b", html);
    CC.setAsOf($("wld-ingest"), d.heartbeat, ing.ok === true ? freshOf(d) : (ing.ok === false ? "error" : "unknown"));
  }

  function renderSources(d) {
    var qa = (d.agents && d.agents.quality && d.agents.quality.detail) || {};
    var st = qa.staleness;
    if (!st || typeof st !== "object" || !Object.keys(st).length) {
      setHTML("wld-sources-b", '<div class="muted small">fraîcheur par source non rapportée par l\'agent quality</div>');
      CC.setAsOf($("wld-sources"), qualityAsOf(d), "unknown");
      return;
    }
    var keys = Object.keys(st).sort(function (a, b) {
      var fa = st[a] && st[a].fresh ? 0 : 1, fb = st[b] && st[b].fresh ? 0 : 1;
      if (fa !== fb) return fa - fb;
      return (Number(st[b] && st[b].age_h) || 0) - (Number(st[a] && st[a].age_h) || 0);
    });
    var nf = keys.filter(function (k) { return st[k] && st[k].fresh; }).length;
    var rows = keys.map(function (k) {
      var s = st[k] || {};
      var fresh = s.fresh === true;
      return "<tr>"
        + '<td class="wld-ledtd">' + CC.led(fresh ? "fresh" : (s.fresh === false ? "stale" : "unknown")) + "</td>"
        + "<td>" + esc(k) + "</td>"
        + '<td class="num">' + esc(F.h(s.age_h, 1)) + "</td>"
        + '<td class="' + (fresh ? "up" : "warn") + '">' + (s.fresh == null ? DASH : (fresh ? "fraîche" : "en retard")) + "</td>"
        + "</tr>";
    }).join("");
    var html = '<div class="small muted mb">' + esc(F.int(nf)) + " / " + esc(F.int(keys.length)) + " sources fraîches (seuil propre à chaque source, défaut 168 h)</div>"
      + '<div class="tblwrap" style="max-height:300px"><table class="tbl"><thead><tr><th></th><th>source</th><th class="num" title="Âge du dernier événement reçu">âge</th><th>état</th></tr></thead><tbody>' + rows + "</tbody></table></div>";
    setHTML("wld-sources-b", html);
    CC.setAsOf($("wld-sources"), qualityAsOf(d), nf === keys.length ? "fresh" : "stale");
  }

  function renderCandidates(d) {
    var cands = Array.isArray(d.candidates) ? d.candidates : [];
    var corr = d.agents && d.agents.correlator;
    var cdet = (corr && corr.detail) || {};
    var chartEl = $("wld-cand-chart");
    var fn = [];
    if (!cands.length) {
      var why = cdet.skipped ? "corrélateur sauté — " + (cdet.reason || "gate qualité") : (cdet.reason ? String(cdet.reason) : "features world en accumulation (≥ 40 j requis)");
      setHTML("wld-cand-b", '<div class="muted small">aucun candidat · ' + esc(why) + "</div>");
      if (chartEl) chartEl.hidden = true;
    } else {
      var rows = cands.slice(0, 20).map(function (c) {
        var ci = Array.isArray(c.ci95) ? c.ci95 : [null, null];
        return "<tr>"
          + "<td>" + esc(c.feature) + "</td>"
          + "<td>" + esc(String(c.asset || DASH).replace("USDT", "")) + "</td>"
          + '<td class="num">' + esc(F.days(c.lag_days)) + "</td>"
          + '<td class="num ' + CC.cls(c.pearson) + '">' + esc(F.snum(c.pearson, 3)) + "</td>"
          + '<td class="num">[' + esc(F.snum(ci[0], 3)) + " ; " + esc(F.snum(ci[1], 3)) + "]</td>"
          + '<td class="num">' + esc(F.snum(c.t_stat, 2)) + "</td>"
          + '<td class="num">' + esc(F.int(c.n)) + "</td>"
          + "</tr>";
      }).join("");
      setHTML("wld-cand-b", '<div class="tblwrap" style="max-height:300px"><table class="tbl"><thead><tr>'
        + "<th>feature</th><th>actif</th><th class=\"num\" title=\"Décalage de la feature (jours) : prédictivité\">lag</th>"
        + '<th class="num" title="Corrélation de Pearson des variations quotidiennes">r</th>'
        + '<th class="num" title="Intervalle de confiance bootstrap 95 %">IC 95 %</th>'
        + '<th class="num" title="Statistique t">t</th><th class="num" title="Observations">n</th>'
        + "</tr></thead><tbody>" + rows + "</tbody></table></div>");
      if (chartEl) {
        chartEl.hidden = false;
        var top = cands.slice(0, 12);
        CC.chart(chartEl, {
          grid: { left: 150, right: 24, top: 8, bottom: 22 },
          tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
          xAxis: { type: "value", min: -1, max: 1, axisLabel: { formatter: function (v) { return F.snum(v, 1); } } },
          yAxis: { type: "category", inverse: true, data: top.map(function (c) { return String(c.asset || "").replace("USDT", "") + " · " + c.feature + " · " + F.days(c.lag_days); }), axisLabel: { fontSize: 10 } },
          series: [{
            type: "bar", barMaxWidth: 10,
            data: top.map(function (c) { return { value: c.pearson, itemStyle: { color: c.pearson >= 0 ? CC.tokens.up : CC.tokens.dn } }; }),
            label: { show: true, position: "right", color: CC.tokens.ink2, fontSize: 10, formatter: function (p) { return F.snum(p.value, 3); } }
          }]
        });
      }
    }
    if (d.n_tests != null) fn.push("<b>" + esc(F.int(d.n_tests)) + "</b> tests effectués (attendus ~" + esc(F.int(d.n_tests * 0.05)) + " faux positifs à 5 %).");
    fn.push(esc(d.candidates_warning || DEFAULT_WARN));
    setHTML("wld-cand-fn", fn.join(" "));
    var st = cands.length ? "fresh" : (cdet.skipped ? "stale" : (corr && corr.ok === false ? "error" : "stopped"));
    CC.setAsOf($("wld-cand"), d.heartbeat, st);
  }

  function renderAll(d) {
    var steps = [renderHead, renderAgents, renderQuality, renderIngest, renderSources, renderCandidates];
    var ids = ["wld-head-b", "wld-agents-b", "wld-quality-b", "wld-ingest-b", "wld-sources-b", "wld-cand-b"];
    steps.forEach(function (fn, i) {
      try { fn(d); }
      catch (e) { console.error("[WLD] " + ids[i], e); setHTML(ids[i], unavailable("rendu : " + (e && e.message))); }
    });
  }

  function renderUnavailable(msg) {
    var ids = ["wld-agents-b", "wld-quality-b", "wld-ingest-b", "wld-sources-b", "wld-cand-b"];
    var pids = ["wld-agents", "wld-quality", "wld-ingest", "wld-sources", "wld-cand"];
    setHTML("wld-head-b", '<div class="row mb">' + CC.badge("store indisponible", "dn") + " " + CC.badge("pipeline : état inconnu", "muted") + "</div>" + unavailable(msg));
    CC.setAsOf($("wld-head"), null, "error");
    ids.forEach(function (id, i) { setHTML(id, unavailable(msg)); CC.setAsOf($(pids[i]), null, "error"); });
    var ch = $("wld-cand-chart"); if (ch) ch.hidden = true;
    setHTML("wld-cand-fn", "");
  }

  // ── cycle de vie ──────────────────────────────────────────────────────────
  function init(el) {
    root = el;
    injectCss();
    el.innerHTML = skeleton();
  }

  async function refresh() {
    if (!root) return;
    var d;
    try { d = await CC.j("/api/worldmon"); }
    catch (e) {
      if (e && /401/.test(e.message || "")) return;   // redirection login gérée par core
      var msg = (e && e.message) || "erreur réseau";
      if (lastOk) {
        // on fige le dernier état connu et on le signale sur l'en-tête
        setHTML("wld-head-b", '<div class="row mb">' + CC.badge("endpoint indisponible", "dn") + '<span class="muted small">dernier état connu conservé · ' + esc(msg) + "</span></div>");
        CC.setAsOf($("wld-head"), lastOk.heartbeat, "error");
      } else {
        renderUnavailable(msg);
      }
      return;
    }
    if (!d || typeof d !== "object") { renderUnavailable("réponse vide"); return; }
    if (d.backend === "unavailable") {
      renderUnavailable("BigDataStore : " + (d.error || "erreur inconnue"));
      return;
    }
    lastOk = d;
    renderAll(d);
  }

  CC.register({
    key: "world", code: "WLD", title: "World", icon: "◍", refreshMs: 60000,
    init: init,
    refresh: refresh,
    onShow: function () { var ch = $("wld-cand-chart"); if (ch && !ch.hidden && root) CC.resizeCharts(root); }
  });
})();
