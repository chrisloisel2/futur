/* Vue EDGE — Edge lab : croiser deux séries quotidiennes (chasse aux prochains edges).
   Port de l'ancienne vue lab() de command_center.html : /api/sources → sélecteurs,
   /api/cross (x, y, symbol, symbol_y, lag_days) → corr niveaux / corr variations / n + graphique base 100.
   Script classique (pas de module). Lecture seule : aucun ordre, aucune écriture.
   Un candidat n'est jamais un edge sans walk-forward. */
(function () {
  "use strict";

  var CC = window.CC;
  var esc = CC && CC.esc ? CC.esc : function (s) {
    return s == null ? "" : String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var F = CC.fmt;
  var DASH = "—";
  var LS_KEY = "cc.edge.params";
  var CANDIDATE_ABS = 0.15;   // seuil |corr variations| de l'ancienne vue (candidat, pas un edge)

  var root = null;            // section #v-edgelab
  var sourcesLoaded = false;  // /api/sources déjà chargé
  var busy = false;           // un calcul /api/cross en cours
  var last = null;            // dernière réponse /api/cross

  // ── utilitaires ────────────────────────────────────────────────────────────
  function $(id) { return root ? root.querySelector("#" + id) : document.getElementById(id); }
  function lsGet() { try { return JSON.parse(localStorage.getItem(LS_KEY) || "null"); } catch (e) { return null; } }
  function lsSet(o) { try { localStorage.setItem(LS_KEY, JSON.stringify(o)); } catch (e) { /* privé */ } }
  // message d'erreur lisible : le serveur renvoie {"detail": "..."} sur 400/404
  function errMsg(e) {
    var m = e && e.message ? String(e.message) : String(e || "erreur");
    try { var o = JSON.parse(m); if (o && o.detail) m = typeof o.detail === "string" ? o.detail : JSON.stringify(o.detail); } catch (x) { /* texte brut */ }
    return m.length > 160 ? m.slice(0, 157) + "…" : m;
  }
  function setUnavailable(panelId, msg) {
    var p = $(panelId); if (!p) return;
    var b = p.querySelector(".panel-b");
    if (b) b.innerHTML = '<div class="chart-na">indisponible : ' + esc(msg) + "</div>";
    CC.setAsOf(p, new Date(), "error");
  }
  function fillSelect(sel, items, wanted) {
    if (!sel) return;
    var arr = Array.isArray(items) ? items : [];
    sel.innerHTML = arr.map(function (s) { return '<option value="' + esc(s) + '">' + esc(s) + "</option>"; }).join("");
    if (wanted != null && arr.indexOf(wanted) >= 0) sel.value = wanted;
    else if (arr.length) sel.value = arr[0];
  }
  function corrCls(v) {
    if (!CC.isNum(v)) return "flat";
    return Math.abs(Number(v)) > CANDIDATE_ABS ? "amber" : "flat";
  }
  function corrTxt(v) { return CC.isNum(v) ? F.snum(v, 3) : DASH; }

  // ── squelette statique (rendu une fois) ────────────────────────────────────
  function injectCss() {
    if (document.getElementById("css-edgelab")) return;
    var st = document.createElement("style");
    st.id = "css-edgelab";
    st.textContent =
      "#v-edgelab .ctl{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}" +
      "#v-edgelab .ctl .grp{display:flex;flex-direction:column;gap:3px;min-width:0}" +
      "#v-edgelab .ctl .grp .row{gap:4px}" +
      "#v-edgelab select.src{min-width:150px}#v-edgelab select.sym{min-width:150px}" +
      "#v-edgelab #edge-lag{width:76px;text-align:right}" +
      "#v-edgelab .res{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}" +
      "#v-edgelab .res .tile{background:var(--bg3);border:1px solid var(--line);border-radius:var(--r);padding:8px 10px;min-width:0}" +
      "#v-edgelab .res .big-sm{overflow:hidden;text-overflow:ellipsis}" +
      "#v-edgelab .verdict{font-size:12px;color:var(--ink2);margin-top:8px}" +
      "@media (max-width:720px){#v-edgelab select.src,#v-edgelab select.sym{min-width:0;width:calc(50vw - 30px)}}";
    document.head.appendChild(st);
  }

  function skeleton() {
    var controls =
      '<div class="ctl">' +
        '<div class="grp"><span class="lbl" title="série X : source et symbole (base 100 au premier point commun)">X — source · symbole</span>' +
          '<div class="row"><select id="edge-srcx" class="src" aria-label="source X" title="source X"></select>' +
          '<select id="edge-symx" class="sym" aria-label="symbole X" title="symbole X (contrat perpétuel USDT)"></select></div></div>' +
        '<div class="grp"><span class="lbl" title="série Y : source et symbole (base 100 au premier point commun)">Y — source · symbole</span>' +
          '<div class="row"><select id="edge-srcy" class="src" aria-label="source Y" title="source Y"></select>' +
          '<select id="edge-symy" class="sym" aria-label="symbole Y" title="symbole Y (contrat perpétuel USDT)"></select></div></div>' +
        '<div class="grp"><span class="lbl" title="Y décalé de N jours : x(t) comparé à y(t − lag). Lag > 0 = Y passé explique-t-il X ? (prédictivité)">lag Y (j)</span>' +
          '<input id="edge-lag" type="number" step="1" value="0" inputmode="numeric" aria-label="lag Y en jours" title="décalage de Y en jours (x(t) vs y(t − lag))"></div>' +
        '<div class="grp"><span class="lbl">&nbsp;</span>' +
          '<button id="edge-go" class="btn-primary" title="calculer la corrélation (lecture seule, GET /api/cross)">CROISER</button></div>' +
        '<div class="grp" style="flex:1 1 auto;min-width:200px"><span class="lbl">résultat</span>' +
          '<div id="edge-badge" class="small ink2">' + DASH + "</div></div>" +
      "</div>";

    var results =
      '<div class="res">' +
        '<div class="tile"><div class="lbl" title="corrélation de Pearson des niveaux (séries brutes, alignées jour par jour)">corr niveaux (ρ)</div><div id="edge-r-lvl" class="big-sm flat">' + DASH + "</div><div class=\"sub\">Pearson, niveaux</div></div>" +
        '<div class="tile"><div class="lbl" title="corrélation de Pearson des variations quotidiennes (pct_change) — la mesure qui compte pour un edge">corr variations (ρ Δ)</div><div id="edge-r-chg" class="big-sm flat">' + DASH + "</div><div id=\"edge-r-chg-sub\" class=\"sub\">Pearson, variations quotidiennes</div></div>" +
        '<div class="tile"><div class="lbl" title="nombre de jours communs aux deux séries après alignement">n (jours communs)</div><div id="edge-r-n" class="big-sm">' + DASH + "</div><div id=\"edge-r-period\" class=\"sub\">" + DASH + "</div></div>" +
        '<div class="tile"><div class="lbl" title="paramètres effectivement envoyés au serveur">paramètres</div><div id="edge-r-x" class="small">' + DASH + "</div><div id=\"edge-r-y\" class=\"small\">" + DASH + "</div><div id=\"edge-r-lag\" class=\"sub\">" + DASH + "</div></div>" +
      "</div>" +
      '<div id="edge-verdict" class="verdict">' + DASH + "</div>";

    return '<div class="grid">' +
      '<div class="c12">' + CC.panel({ id: "edge-p-ctl", code: "EDGE", title: "Edge lab — croiser deux séries quotidiennes", body: controls }) + "</div>" +
      '<div class="c4">' + CC.panel({ id: "edge-p-res", code: "EDGE·R", title: "Résultat du croisement", body: results, fresh: "unknown" }) + "</div>" +
      '<div class="c8">' + CC.panel({ id: "edge-p-chart", code: "EDGE·G", title: "Séries indexées base 100 (un seul axe)", body: '<div id="edge-chart" class="chart chart-lg"></div>', fresh: "unknown" }) + "</div>" +
      "</div>" +
      '<div class="view-fn">Séries indexées base 100 (un seul axe) sur les jours communs. « corr variations » = corrélation des changements quotidiens (la mesure d\'un edge) ; le lag décale Y pour tester la prédictivité. ' +
      "Seuil |ρ Δ| &gt; 0,15 = simple candidat statistique (ancienne convention de la vue), pas un edge : <b>un candidat n'est jamais un edge sans walk-forward</b>. " +
      "Outil de recherche, lecture seule : capital virtuel, aucun ordre réel.</div>";
  }

  // ── paramètres courants ────────────────────────────────────────────────────
  function params() {
    var sx = $("edge-srcx"), sy = $("edge-srcy"), kx = $("edge-symx"), ky = $("edge-symy"), lg = $("edge-lag");
    var lagRaw = lg ? String(lg.value || "").trim() : "0";
    var lag = parseInt(lagRaw, 10);
    if (!isFinite(lag)) lag = 0;
    return {
      x: sx ? sx.value : "", y: sy ? sy.value : "",
      symbol: kx ? kx.value : "", symbol_y: ky ? ky.value : "",
      lag_days: lag
    };
  }

  // ── /api/sources → sélecteurs ─────────────────────────────────────────────
  async function loadSources() {
    var meta = await CC.j("/api/sources");
    var sources = meta && Array.isArray(meta.sources) ? meta.sources : [];
    var symbols = meta && Array.isArray(meta.symbols) ? meta.symbols : [];
    if (!sources.length) throw new Error("aucune source déclarée");
    if (!symbols.length) throw new Error("aucun symbole disponible (data/derivatives_backfill/binance_vision_metrics)");
    var saved = lsGet() || {};
    var defSym = symbols.indexOf("BTCUSDT") >= 0 ? "BTCUSDT" : symbols[0];
    fillSelect($("edge-srcx"), sources, saved.x || "price");
    fillSelect($("edge-srcy"), sources, saved.y || "funding");      // défaut de l'ancienne vue
    fillSelect($("edge-symx"), symbols, saved.symbol || defSym);
    fillSelect($("edge-symy"), symbols, saved.symbol_y || defSym);
    var lg = $("edge-lag");
    if (lg && CC.isNum(saved.lag_days)) lg.value = String(parseInt(saved.lag_days, 10) || 0);
    sourcesLoaded = true;
    var ctl = $("edge-p-ctl");
    if (ctl) CC.setAsOf(ctl, new Date());
    var t = ctl ? ctl.querySelector(".ttl") : null;
    if (t) {
      t.textContent = "Edge lab — croiser deux séries quotidiennes · " + F.int(sources.length) + " sources · " + F.int(symbols.length) + " symboles";
      t.setAttribute("title", t.textContent);
    }
  }

  // ── /api/cross → résultat + graphique ──────────────────────────────────────
  function renderResult(d, p, when) {
    last = d;
    var lvl = d.pearson_niveaux, chg = d.pearson_variations, n = d.n;
    var dates = Array.isArray(d.dates) ? d.dates : [];
    var el;

    el = $("edge-r-lvl"); if (el) { el.textContent = corrTxt(lvl); el.className = "big-sm " + corrCls(lvl); }
    el = $("edge-r-chg"); if (el) { el.textContent = corrTxt(chg); el.className = "big-sm " + corrCls(chg); }
    el = $("edge-r-chg-sub"); if (el) el.textContent = CC.isNum(chg) ? "Pearson, variations quotidiennes" : "non calculable (moins de 30 variations)";
    el = $("edge-r-n"); if (el) el.textContent = CC.isNum(n) ? F.int(n) + " j" : DASH;
    el = $("edge-r-period"); if (el) el.textContent = dates.length ? F.date(dates[0]) + " → " + F.date(dates[dates.length - 1]) : DASH;
    el = $("edge-r-x"); if (el) el.textContent = "X : " + (d.x_label || DASH);
    el = $("edge-r-y"); if (el) el.textContent = "Y : " + (d.y_label || DASH);
    el = $("edge-r-lag"); if (el) el.textContent = "lag Y : " + F.int(p.lag_days) + " j" + (p.lag_days ? " (x(t) vs y(t − " + F.int(p.lag_days) + "))" : " (contemporain)");

    var badge = $("edge-badge");
    if (badge) {
      badge.innerHTML =
        'corr niveaux <b class="' + corrCls(lvl) + '">' + esc(corrTxt(lvl)) + "</b>" +
        ' · corr variations <b class="' + corrCls(chg) + '">' + esc(corrTxt(chg)) + "</b>" +
        " · n = <b>" + esc(CC.isNum(n) ? F.int(n) : DASH) + "</b>" +
        (CC.isNum(chg) && Math.abs(Number(chg)) > CANDIDATE_ABS ? " " + CC.badge("candidat", "amber", "|ρ Δ| > 0,15 : candidat statistique — pas un edge sans walk-forward") : " " + CC.badge("bruit", "muted", "|ρ Δ| ≤ 0,15 : rien à signaler"));
    }
    var v = $("edge-verdict");
    if (v) {
      if (!CC.isNum(chg)) v.textContent = "Corrélation des variations non calculable : trop peu de points. Aucune conclusion.";
      else if (Math.abs(Number(chg)) > CANDIDATE_ABS) v.textContent = "|ρ Δ| > 0,15 : candidat à instruire (bootstrap IC, multiple testing, walk-forward). Ce n'est pas un edge.";
      else v.textContent = "|ρ Δ| ≤ 0,15 : pas de signal détectable sur les variations quotidiennes avec ces paramètres.";
    }

    var res = $("edge-p-res"); if (res) CC.setAsOf(res, when, "fresh");
    var pc = $("edge-p-chart");
    if (pc) {
      CC.setAsOf(pc, when, "fresh");
      var t = pc.querySelector(".ttl");
      if (t) { t.textContent = "Séries indexées base 100 — " + (d.x_label || "X") + " vs " + (d.y_label || "Y"); t.setAttribute("title", t.textContent); }
      var body = pc.querySelector(".panel-b");
      if (body && !body.querySelector("#edge-chart")) body.innerHTML = '<div id="edge-chart" class="chart chart-lg"></div>';
    }
    drawChart(d);
  }

  function drawChart(d) {
    var el = $("edge-chart"); if (!el) return;
    var T = CC.tokens || {};
    var xl = d.x_label || "X", yl = d.y_label || "Y";
    CC.chart(el, {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        valueFormatter: function (v) { return F.num(v, 1); }
      },
      legend: { data: [xl, yl], top: 0 },
      grid: { left: 60, right: 16, top: 28, bottom: 44 },
      xAxis: { type: "category", data: Array.isArray(d.dates) ? d.dates : [], boundaryGap: false },
      yAxis: { type: "value", scale: true, axisLabel: { formatter: function (v) { return F.num(v, 0); } } },
      dataZoom: [{ type: "inside" }, { type: "slider" }],
      series: [
        { name: xl, type: "line", data: Array.isArray(d.x_indexed) ? d.x_indexed : [], showSymbol: false, lineStyle: { width: 1.5, color: T.amber || "#ffb000" }, itemStyle: { color: T.amber || "#ffb000" } },
        { name: yl, type: "line", data: Array.isArray(d.y_indexed) ? d.y_indexed : [], showSymbol: false, lineStyle: { width: 1.5, color: T.info || "#4cc2ff" }, itemStyle: { color: T.info || "#4cc2ff" } }
      ]
    });
  }

  async function cross() {
    if (busy) return;
    var p = params();
    if (!p.x || !p.y || !p.symbol) {
      var b0 = $("edge-badge"); if (b0) b0.textContent = "indisponible : sources non chargées";
      return;
    }
    busy = true;
    var btn = $("edge-go"); if (btn) btn.disabled = true;
    var badge = $("edge-badge"); if (badge) badge.textContent = "calcul…";
    var q = "x=" + encodeURIComponent(p.x) + "&y=" + encodeURIComponent(p.y) +
      "&symbol=" + encodeURIComponent(p.symbol) + "&symbol_y=" + encodeURIComponent(p.symbol_y || p.symbol) +
      "&lag_days=" + encodeURIComponent(String(p.lag_days));
    try {
      var d = await CC.j("/api/cross?" + q);
      if (!d || typeof d !== "object") throw new Error("réponse vide");
      lsSet(p);
      renderResult(d, p, new Date());
    } catch (e) {
      var msg = errMsg(e);
      if (badge) badge.innerHTML = '<span class="dn">indisponible : ' + esc(msg) + "</span>";
      var res = $("edge-p-res"); if (res) CC.setAsOf(res, new Date(), "error");
      var pc = $("edge-p-chart");
      if (pc) {
        CC.setAsOf(pc, new Date(), "error");
        // on garde le dernier graphique valide s'il existe ; sinon message dans le panneau
        if (!last) setUnavailable("edge-p-chart", msg);
      }
      var v = $("edge-verdict"); if (v) v.textContent = "Aucun résultat : " + msg;
      if (!/401/.test(msg)) console.warn("[EDGE] /api/cross : " + msg);
    } finally {
      busy = false;
      if (btn) btn.disabled = false;
    }
  }

  // ── cycle de vie ───────────────────────────────────────────────────────────
  function init(el) {
    root = el;
    injectCss();
    el.innerHTML = skeleton();
    var btn = $("edge-go");
    if (btn) btn.addEventListener("click", function () { cross(); });
    var lg = $("edge-lag");
    if (lg) lg.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); cross(); } });
  }

  // refreshMs 0 : appelé une fois à l'affichage (et via la commande REFRESH). Ne lève jamais.
  async function refresh() {
    if (!root) return;
    if (!sourcesLoaded) {
      try { await loadSources(); }
      catch (e) {
        var msg = errMsg(e);
        var badge = $("edge-badge"); if (badge) badge.innerHTML = '<span class="dn">indisponible : ' + esc(msg) + "</span>";
        var ctl = $("edge-p-ctl"); if (ctl) CC.setAsOf(ctl, new Date(), "error");
        setUnavailable("edge-p-chart", "sources non chargées (" + msg + ")");
        var res = $("edge-p-res"); if (res) CC.setAsOf(res, new Date(), "error");
        if (!/401/.test(msg)) console.warn("[EDGE] /api/sources : " + msg);
        return;
      }
    }
    await cross();
  }

  CC.register({
    key: "edgelab", code: "EDGE", title: "Edge lab", icon: "⌗", refreshMs: 0,
    init: init,
    refresh: refresh,
    onShow: function () { var c = $("edge-chart"); if (c) CC.resizeCharts(root); }
  });

  // commande "EDGE BTC" → présélectionne le symbole X/Y (si connu) puis croise
  if (CC.on) {
    CC.on("cmd", function (c) {
      if (!c || c.key !== "edgelab" || !c.args || !c.args.length || !sourcesLoaded) return;
      var want = String(c.args[0]).toUpperCase();
      if (!/USDT$/.test(want)) want += "USDT";
      var kx = $("edge-symx"), ky = $("edge-symy");
      var ok = false;
      [kx, ky].forEach(function (s) {
        if (!s) return;
        for (var i = 0; i < s.options.length; i++) if (s.options[i].value === want) { s.value = want; ok = true; }
      });
      if (ok) cross(); else CC.toast("symbole inconnu : " + want, "warn", 3000);
    });
  }
})();
