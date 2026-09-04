/* Vue CRYP — Cryptos.
   Univers (/api/universe) en tuiles denses, détail (/api/crypto/{symbol}) avec graphique echarts
   et annotation « ma prévision » (POST /api/forecast, DELETE /api/forecast/{symbol}, admin seulement).
   Tout est paper/shadow : annotation personnelle, aucun ordre réel. */
(function () {
  "use strict";

  var CC = window.CC;
  var esc = CC.esc || function (s) {
    return s == null ? "" : String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var T = CC.tokens || {};
  var DASH = "—";

  // ── état ────────────────────────────────────────────────────────────────
  var root = null;          // section
  var UNI = [];             // assets de /api/universe
  var uniMeta = { live: null, ts: null };
  var tiles = {};           // symbol → élément tuile
  var curSym = null;        // symbole ouvert dans le détail
  var curData = null;       // dernière réponse /api/crypto/{sym}
  var curDir = "neutral";
  var detailSeq = 0;        // anti-réponses en retard
  var pendingSym = null;    // CRYP BTC avant chargement de l'univers
  var lastErrUni = null;

  // ── utilitaires ─────────────────────────────────────────────────────────
  function $(sel) { return root ? root.querySelector(sel) : null; }
  function base(sym) { return String(sym || "").replace(/USDT$/, ""); }
  function pctFromPercent(v) { return CC.isNum(v) ? CC.fmt.pct(Number(v) / 100, 2) : DASH; }
  function priceUsdt(v) { return CC.isNum(v) ? CC.fmt.price(v) + " USDT" : DASH; }
  function volM(v) { return CC.isNum(v) ? CC.fmt.num(Number(v) / 1e6, 0) + " M USDT" : DASH; }
  function dirGlyph(d) { return d === "up" ? "▲" : d === "down" ? "▼" : "■"; }
  function dirLabel(d) { return d === "up" ? "hausse" : d === "down" ? "baisse" : "neutre"; }
  function dirCls(d) { return d === "up" ? "up" : d === "down" ? "dn" : "warn"; }
  function dirColor(d) { return d === "up" ? (T.up || "#00d26a") : d === "down" ? (T.dn || "#ff3b30") : (T.amber || "#ffb000"); }
  function errMsg(e) { var m = e && e.message ? e.message : String(e || "erreur"); return m.length > 160 ? m.slice(0, 157) + "…" : m; }
  function setErr(el, e) {
    if (!el) return;
    if (e) { el.hidden = false; el.textContent = "indisponible : " + errMsg(e); }
    else { el.hidden = true; el.textContent = ""; }
  }

  // ── style spécifique (tokens uniquement) ────────────────────────────────
  function injectCss() {
    if (document.getElementById("css-cryptos")) return;
    var st = document.createElement("style");
    st.id = "css-cryptos";
    st.textContent = [
      ".cryp-tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(172px,1fr));gap:6px}",
      ".cryp-tile{background:var(--bg3);border:1px solid var(--line);border-radius:var(--r);padding:6px 8px 5px;cursor:pointer;min-width:0;position:relative}",
      ".cryp-tile:hover{border-color:var(--line2);background:var(--bg4)}",
      ".cryp-tile.sel{border-color:var(--amber);box-shadow:inset 2px 0 0 var(--amber)}",
      ".cryp-tile:focus-visible{outline:1px solid var(--amber);outline-offset:0}",
      ".cryp-top{display:flex;align-items:center;justify-content:space-between;gap:6px}",
      ".cryp-sym{color:var(--amber2);font-weight:600;font-size:13px;letter-spacing:.04em}",
      ".cryp-fc{font-size:10px}",
      ".cryp-px{font-size:14px;font-weight:600;margin-top:2px}",
      ".cryp-chg{font-size:12.5px}",
      ".cryp-vol{font-size:10.5px;color:var(--ink3);text-align:right;white-space:nowrap}",
      ".cryp-sp{margin-top:4px;height:28px}",
      ".cryp-sp .spark{height:28px}",
      ".cryp-err{color:var(--dn);font-size:12px;margin-bottom:6px}",
      ".cryp-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:6px}",
      ".cryp-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;align-items:end;margin-top:8px}",
      ".cryp-form .full{grid-column:1/-1}",
      ".cryp-form input{width:100%}",
      ".cryp-form .seg{display:flex;width:100%}",
      ".cryp-form .seg button{flex:1 1 0}",
      ".cryp-form .num-in{text-align:right;font-variant-numeric:tabular-nums}",
      "#cryp-filter{width:150px}",
      "@media (max-width:720px){.cryp-tiles{grid-template-columns:repeat(auto-fill,minmax(150px,1fr))}#cryp-filter{width:110px}}"
    ].join("\n");
    document.head.appendChild(st);
  }

  // ── squelette ───────────────────────────────────────────────────────────
  function skeleton() {
    var tools =
      '<input id="cryp-filter" type="search" placeholder="filtrer…" aria-label="filtrer un symbole" autocomplete="off" spellcheck="false" class="btn-sm">'
      + '<select id="cryp-sort" aria-label="tri" class="btn-sm" title="ordre des tuiles">'
      + '<option value="chg">tri : variation 24h</option>'
      + '<option value="sym">tri : symbole</option>'
      + '<option value="fc">tri : mes prévisions</option>'
      + "</select>"
      + '<span id="cryp-live" title="source des prix">' + CC.badge("…", "muted") + "</span>"
      + '<span id="cryp-count" class="muted small" title="actifs affichés / univers">—</span>';
    var uniBody =
      '<div id="cryp-err-uni" class="cryp-err" hidden></div>'
      + '<div id="cryp-tiles" class="cryp-tiles" aria-live="polite"></div>'
      + '<div id="cryp-empty" class="muted small" hidden>aucun symbole ne correspond au filtre</div>';

    var form =
      '<div class="cryp-form" id="cryp-form">'
      + '<div><label class="f lbl" for="cryp-dir-up">direction</label><div class="seg" id="cryp-dir" role="group" aria-label="direction">'
      + '<button type="button" id="cryp-dir-up" data-d="up" aria-pressed="false" title="hausse">▲ hausse</button>'
      + '<button type="button" data-d="neutral" aria-pressed="false" title="neutre">■ neutre</button>'
      + '<button type="button" data-d="down" aria-pressed="false" title="baisse">▼ baisse</button>'
      + "</div></div>"
      + '<div><label class="f lbl" for="cryp-target">cible (USDT)</label><input id="cryp-target" class="num-in" type="number" step="any" inputmode="decimal" placeholder="—"></div>'
      + '<div><label class="f lbl" for="cryp-horizon">horizon (jours)</label><input id="cryp-horizon" class="num-in" type="number" min="1" step="1" inputmode="numeric" placeholder="30"></div>'
      + '<div><label class="f lbl" for="cryp-conv">conviction (1–5)</label><input id="cryp-conv" class="num-in" type="number" min="1" max="5" step="1" value="3" inputmode="numeric"></div>'
      + '<div class="full"><label class="f lbl" for="cryp-note">note</label><input id="cryp-note" type="text" maxlength="500" placeholder="thèse, niveau clé, catalyseur…" autocomplete="off"></div>'
      + '<div class="full row" id="cryp-actions">'
      + '<button type="button" id="cryp-save" class="btn-primary">enregistrer</button>'
      + '<button type="button" id="cryp-del" class="btn-danger">supprimer</button>'
      + '<span id="cryp-ro" class="muted small" hidden>lecture seule (invité) : rien n\'est enregistré</span>'
      + '<span id="cryp-fc-state" class="muted small"></span>'
      + "</div>"
      + "</div>";

    var detBody =
      '<div id="cryp-err-det" class="cryp-err" hidden></div>'
      + '<div class="cryp-head">'
      + '<span class="big-sm" id="cryp-d-price" title="dernier prix (USDT)">—</span>'
      + '<span id="cryp-d-chg" title="variation 24 h">' + CC.badge(DASH, "muted") + "</span>"
      + '<span class="muted small" id="cryp-d-meta"></span>'
      + "</div>"
      + '<div id="cryp-chart" class="chart chart-lg" role="img" aria-label="prix de clôture quotidien"></div>'
      + '<div class="lbl mt">ma prévision</div>'
      + form
      + '<div class="fn">La cible s\'affiche en pointillé sur le graphe ; la projection va du dernier prix à la cible sur l\'horizon (30 j par défaut). Annotation personnelle — aucun ordre réel.</div>';

    return '<div class="grid">'
      + '<div class="c12">' + CC.panel({ id: "cryp-p-uni", code: "CRYP.UNI", title: "Univers cryptos — prix et variation 24 h (clic : détail)", tools: tools, body: uniBody, fresh: "unknown" }) + "</div>"
      + '<div class="c12" id="cryp-det-wrap" hidden>' + CC.panel({ id: "cryp-p-det", code: "CRYP.DET", title: "Détail",
        tools: '<button type="button" class="btn-sm" id="cryp-close" title="Esc">✕ fermer</button>', body: detBody, fresh: "unknown" }) + "</div>"
      + "</div>"
      + '<div class="view-fn">Prix Binance (USDT) : live si le flux répond, sinon dernier close quotidien ; sparkline = 60 derniers closes. '
      + 'Les prévisions sont des annotations personnelles, sans effet sur les portefeuilles shadow — capital virtuel, aucun ordre réel.</div>';
  }

  // ── grille de tuiles (mise à jour en place, sans re-rendu complet) ──────
  function sortedAssets() {
    var f = ($("#cryp-filter") && $("#cryp-filter").value || "").toUpperCase().trim();
    var sort = $("#cryp-sort") ? $("#cryp-sort").value : "chg";
    var a = UNI.filter(function (x) { return x && x.symbol && String(x.symbol).toUpperCase().indexOf(f) >= 0; });
    a.sort(function (x, y) {
      if (sort === "sym") return String(x.symbol).localeCompare(String(y.symbol));
      if (sort === "fc") {
        var d = (y.forecast ? 1 : 0) - (x.forecast ? 1 : 0);
        return d !== 0 ? d : String(x.symbol).localeCompare(String(y.symbol));
      }
      var cx = CC.isNum(x.chg24) ? Number(x.chg24) : -999, cy = CC.isNum(y.chg24) ? Number(y.chg24) : -999;
      return cy - cx;
    });
    return a;
  }

  function makeTile(sym) {
    var el = document.createElement("div");
    el.className = "cryp-tile";
    el.setAttribute("data-sym", sym);
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.innerHTML = '<div class="cryp-top"><span class="cryp-sym"></span><span class="cryp-fc"></span></div>'
      + '<div class="cryp-px num"></div><div class="cryp-chg num"></div><div class="cryp-vol"></div><div class="cryp-sp"></div>';
    el.addEventListener("click", function () { openDetail(sym); });
    el.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail(sym); }
    });
    return el;
  }

  function updateTile(el, x) {
    var sym = x.symbol;
    el.querySelector(".cryp-sym").textContent = base(sym);
    var fc = x.forecast;
    var fcEl = el.querySelector(".cryp-fc");
    if (fc) {
      var t = "ma prévision : " + dirLabel(fc.direction)
        + (CC.isNum(fc.target) ? " · cible " + priceUsdt(fc.target) : "")
        + (CC.isNum(fc.horizon_days) ? " · " + CC.fmt.int(fc.horizon_days) + " j" : "")
        + (CC.isNum(fc.conviction) ? " · conviction " + CC.fmt.int(fc.conviction) + "/5" : "");
      fcEl.innerHTML = CC.badge(dirGlyph(fc.direction) + " " + dirLabel(fc.direction), dirCls(fc.direction), t);
    } else fcEl.innerHTML = "";
    el.querySelector(".cryp-px").textContent = priceUsdt(x.price);
    var chg = el.querySelector(".cryp-chg");
    chg.textContent = pctFromPercent(x.chg24) + (CC.isNum(x.chg24) ? " 24h" : "");
    chg.className = "cryp-chg num " + CC.cls(x.chg24);
    el.querySelector(".cryp-vol").textContent = CC.isNum(x.vol) ? "vol 24h " + volM(x.vol) : "";
    var sp = el.querySelector(".cryp-sp");
    var vals = Array.isArray(x.spark) ? x.spark : [];
    var sig = vals.length + ":" + (vals.length ? vals[vals.length - 1] : "");
    if (sp.getAttribute("data-sig") !== sig) {
      CC.spark(sp, vals, { w: 160, h: 28 });
      sp.setAttribute("data-sig", sig);
    }
    el.title = sym + " · " + priceUsdt(x.price) + " · " + pctFromPercent(x.chg24) + " 24h"
      + (CC.isNum(x.vol) ? " · volume 24h " + volM(x.vol) : "") + " · sparkline 60 closes quotidiens";
    el.classList.toggle("sel", sym === curSym);
  }

  function renderTiles() {
    var host = $("#cryp-tiles"); if (!host) return;
    var list = sortedAssets();
    var shown = {}, inUni = {};
    UNI.forEach(function (x) { if (x && x.symbol) inUni[String(x.symbol)] = true; });
    list.forEach(function (x) {
      var sym = String(x.symbol);
      shown[sym] = true;
      var el = tiles[sym];
      if (!el) { el = tiles[sym] = makeTile(sym); }
      updateTile(el, x);
      host.appendChild(el);        // appendChild déplace → ordre trié
    });
    // tuiles filtrées : détachées mais conservées (cache) ; symbole sorti de l'univers : supprimée
    Object.keys(tiles).forEach(function (sym) {
      if (shown[sym]) return;
      var el = tiles[sym];
      if (el.parentNode) el.parentNode.removeChild(el);
      if (!inUni[sym]) delete tiles[sym];
    });
    var empty = $("#cryp-empty"); if (empty) empty.hidden = list.length > 0;
    var cnt = $("#cryp-count"); if (cnt) cnt.textContent = CC.fmt.int(list.length) + "/" + CC.fmt.int(UNI.length) + " actifs";
  }

  function renderUniMeta() {
    var live = $("#cryp-live");
    if (live) {
      live.innerHTML = uniMeta.live === true ? CC.badge("prix live", "up", "flux Binance actif")
        : uniMeta.live === false ? CC.badge("dernier close", "muted", "flux live indisponible : dernier close quotidien")
        : CC.badge(DASH, "muted");
    }
    var p = $("#cryp-p-uni");
    if (p) CC.setAsOf(p, uniMeta.ts, lastErrUni ? "error" : (uniMeta.live === true ? "fresh" : uniMeta.live === false ? "stale" : "unknown"));
  }

  async function refreshUniverse() {
    try {
      var d = await CC.j("/api/universe");
      UNI = Array.isArray(d && d.assets) ? d.assets : [];
      uniMeta.live = d && typeof d.live === "boolean" ? d.live : null;
      uniMeta.ts = d && d.ts || null;
      lastErrUni = null;
      setErr($("#cryp-err-uni"), null);
      renderTiles();
      if (pendingSym) { var s = resolveSym(pendingSym); pendingSym = null; if (s) openDetail(s); }
    } catch (e) {
      if (e && /401/.test(e.message || "")) return;
      lastErrUni = e;
      setErr($("#cryp-err-uni"), e);
    }
    renderUniMeta();
  }

  function resolveSym(arg) {
    if (!arg) return null;
    var a = String(arg).toUpperCase().trim();
    var i;
    for (i = 0; i < UNI.length; i++) if (UNI[i].symbol === a) return a;
    for (i = 0; i < UNI.length; i++) if (UNI[i].symbol === a + "USDT") return a + "USDT";
    for (i = 0; i < UNI.length; i++) if (String(UNI[i].symbol).indexOf(a) === 0) return UNI[i].symbol;
    return null;
  }

  // ── détail ──────────────────────────────────────────────────────────────
  function setDir(d) {
    curDir = d === "up" || d === "down" ? d : "neutral";
    var btns = root ? root.querySelectorAll("#cryp-dir button") : [];
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].getAttribute("data-d") === curDir;
      btns[i].setAttribute("aria-pressed", on ? "true" : "false");
      btns[i].className = on ? "sel " + dirCls(curDir) : "";
    }
  }

  function fillForm(f) {
    f = f || {};
    setDir(f.direction || "neutral");
    $("#cryp-target").value = CC.isNum(f.target) ? String(f.target) : "";
    $("#cryp-horizon").value = CC.isNum(f.horizon_days) ? String(f.horizon_days) : "";
    $("#cryp-conv").value = CC.isNum(f.conviction) ? String(f.conviction) : "3";
    $("#cryp-note").value = f.note != null ? String(f.note) : "";
  }

  function renderFcState(f) {
    var el = $("#cryp-fc-state"); if (!el) return;
    if (f && f.direction) {
      el.textContent = "enregistrée : " + dirLabel(f.direction)
        + (CC.isNum(f.target) ? " · cible " + priceUsdt(f.target) : "")
        + (CC.isNum(f.horizon_days) ? " · " + CC.fmt.int(f.horizon_days) + " j" : "")
        + (CC.isNum(f.conviction) ? " · conviction " + CC.fmt.int(f.conviction) + "/5" : "")
        + (f.updated_at ? " · maj " + CC.fmt.dt(f.updated_at) : "");
    } else el.textContent = "aucune prévision enregistrée pour ce symbole";
  }

  function applyRole() {
    var admin = CC.isAdmin();
    var save = $("#cryp-save"), del = $("#cryp-del"), ro = $("#cryp-ro");
    if (save) save.hidden = !admin;
    if (del) del.hidden = !admin;
    if (ro) ro.hidden = admin;
  }

  function renderDetailHead(d) {
    var p = $("#cryp-p-det"); if (!p) return;
    var ttl = p.querySelector(".panel-h .ttl");
    var n = Array.isArray(d.close) ? d.close.length : 0;
    if (ttl) { ttl.textContent = base(d.symbol) + " (" + String(d.symbol) + ") — close quotidien, " + CC.fmt.int(n) + " j"; ttl.title = ttl.textContent; }
    $("#cryp-d-price").textContent = priceUsdt(d.price);
    $("#cryp-d-chg").innerHTML = CC.badge(pctFromPercent(d.chg24) + " 24h", CC.isNum(d.chg24) ? CC.cls(d.chg24) : "muted", "variation sur 24 h");
    var lastClose = n ? d.close[n - 1] : null, lastDate = Array.isArray(d.dates) && d.dates.length ? d.dates[d.dates.length - 1] : null;
    $("#cryp-d-meta").textContent = "dernier close " + priceUsdt(lastClose) + (lastDate ? " (" + CC.fmt.date(lastDate) + ")" : "");
    CC.setAsOf(p, uniMeta.ts || new Date().toISOString(), uniMeta.live === true ? "fresh" : uniMeta.live === false ? "stale" : "unknown");
  }

  function drawChart(d) {
    var el = $("#cryp-chart"); if (!el || !d) return;
    var close = Array.isArray(d.close) ? d.close.slice() : [];
    var dates = Array.isArray(d.dates) ? d.dates.slice() : [];
    var tgt = parseFloat($("#cryp-target").value);
    var hor = parseInt($("#cryp-horizon").value, 10);
    var series = [{
      name: base(d.symbol) + " close", type: "line", data: close, showSymbol: false,
      lineStyle: { width: 1.5, color: T.amber || "#ffb000" }, itemStyle: { color: T.amber || "#ffb000" }
    }];
    var legend = [series[0].name];
    if (!isNaN(tgt) && close.length && CC.isNum(close[close.length - 1]) && dates.length) {
      var last = Number(close[close.length - 1]);
      var proj = new Array(close.length).fill(null);
      proj[proj.length - 1] = last;
      var steps = (!isNaN(hor) && hor > 0) ? hor : 30;
      var lastD = CC.fmt.parseDate(dates[dates.length - 1]) || new Date();
      for (var i = 1; i <= steps; i++) {
        var dd = new Date(lastD.getTime()); dd.setUTCDate(dd.getUTCDate() + i);
        dates.push(dd.toISOString().slice(0, 10));
        series[0].data.push(null);
        proj.push(last + (tgt - last) * i / steps);
      }
      series.push({
        name: "ma prévision (" + dirLabel(curDir) + ")", type: "line", data: proj, showSymbol: false,
        lineStyle: { width: 1.5, type: "dashed", color: dirColor(curDir) }, itemStyle: { color: dirColor(curDir) },
        markLine: {
          silent: true, symbol: "none", data: [{ yAxis: tgt }],
          lineStyle: { color: T.amber2 || "#ffcf5a", type: "dotted" },
          label: { formatter: "cible " + CC.fmt.price(tgt), color: T.amber2 || "#ffcf5a", fontSize: 11, fontFamily: T.mono }
        }
      });
      legend.push(series[1].name);
    }
    CC.chart(el, {
      tooltip: {
        trigger: "axis", axisPointer: { type: "cross" },
        formatter: function (ps) {
          if (!ps || !ps.length) return "";
          var out = [esc(ps[0].axisValue)];
          ps.forEach(function (p) { if (p.value != null) out.push(esc(p.seriesName) + " : " + esc(CC.fmt.price(p.value)) + " USDT"); });
          return out.join("<br>");
        }
      },
      legend: { data: legend, top: 0 },
      grid: { left: 72, right: 16, top: 28, bottom: 44 },
      xAxis: { type: "category", data: dates, boundaryGap: false },
      yAxis: { type: "value", scale: true, axisLabel: { formatter: function (v) { return CC.fmt.num(v); } } },
      dataZoom: [{ type: "inside" }, { type: "slider" }],
      series: series
    });
  }

  async function openDetail(sym, opts) {
    opts = opts || {};
    if (!sym) return;
    var wrap = $("#cryp-det-wrap"); if (!wrap) return;
    var first = curSym !== sym;
    curSym = sym;
    Object.keys(tiles).forEach(function (s) { tiles[s].classList.toggle("sel", s === sym); });
    wrap.hidden = false;
    applyRole();
    var seq = ++detailSeq;
    try {
      var d = await CC.j("/api/crypto/" + encodeURIComponent(sym));
      if (seq !== detailSeq || curSym !== sym) return;
      curData = d;
      setErr($("#cryp-err-det"), null);
      renderDetailHead(d);
      if (first || opts.resetForm) { fillForm(d.forecast); }
      renderFcState(d.forecast);
      drawChart(d);
      var p = $("#cryp-p-det");
      if (first && p && !opts.noScroll) { try { p.scrollIntoView({ behavior: "smooth", block: "nearest" }); } catch (e) { /* ignore */ } }
    } catch (e) {
      if (seq !== detailSeq) return;
      if (e && /401/.test(e.message || "")) return;
      setErr($("#cryp-err-det"), e);
      var p2 = $("#cryp-p-det"); if (p2) CC.setAsOf(p2, null, "error");
      var ttl = p2 ? p2.querySelector(".panel-h .ttl") : null;
      if (ttl) ttl.textContent = base(sym) + " (" + sym + ")";
    }
  }

  function closeDetail() {
    var wrap = $("#cryp-det-wrap"); if (wrap) wrap.hidden = true;
    curSym = null; curData = null; detailSeq++;
    Object.keys(tiles).forEach(function (s) { tiles[s].classList.remove("sel"); });
  }

  function redraw() { if (curData) drawChart(curData); }

  // ── prévisions (admin) ──────────────────────────────────────────────────
  async function saveForecast() {
    if (!curSym) return;
    if (!CC.isAdmin()) { CC.toast("lecture seule (invité) : rien n'est enregistré", "warn", 3000); return; }
    var btn = $("#cryp-save"); if (btn) btn.disabled = true;
    var tgt = parseFloat($("#cryp-target").value);
    var hor = parseInt($("#cryp-horizon").value, 10);
    var conv = parseInt($("#cryp-conv").value, 10);
    var body = {
      symbol: curSym, direction: curDir,
      target: isNaN(tgt) ? null : tgt,
      horizon_days: isNaN(hor) || hor <= 0 ? null : hor,
      conviction: isNaN(conv) ? 3 : Math.max(1, Math.min(5, conv)),
      note: ($("#cryp-note").value || "").slice(0, 500)
    };
    try {
      await CC.j("/api/forecast", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      CC.toast("prévision " + base(curSym) + " enregistrée (annotation, aucun ordre)", "up", 2500);
      await refreshUniverse();
      openDetail(curSym, { noScroll: true });
    } catch (e) {
      CC.toast("enregistrement impossible : " + errMsg(e), "dn", 5000);
    } finally { if (btn) btn.disabled = false; }
  }

  async function deleteForecast() {
    if (!curSym) return;
    if (!CC.isAdmin()) { CC.toast("lecture seule (invité) : rien n'est supprimé", "warn", 3000); return; }
    var btn = $("#cryp-del"); if (btn) btn.disabled = true;
    var sym = curSym;
    try {
      await CC.j("/api/forecast/" + encodeURIComponent(sym), { method: "DELETE" });
      CC.toast("prévision " + base(sym) + " supprimée", "muted", 2500);
      closeDetail();
      await refreshUniverse();
    } catch (e) {
      CC.toast("suppression impossible : " + errMsg(e), "dn", 5000);
    } finally { if (btn) btn.disabled = false; }
  }

  // ── câblage ─────────────────────────────────────────────────────────────
  function bind() {
    $("#cryp-filter").addEventListener("input", renderTiles);
    $("#cryp-filter").addEventListener("keydown", function (e) { if (e.key === "Escape") { this.value = ""; renderTiles(); } e.stopPropagation(); });
    $("#cryp-sort").addEventListener("change", renderTiles);
    $("#cryp-close").addEventListener("click", closeDetail);
    var btns = root.querySelectorAll("#cryp-dir button");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () { setDir(this.getAttribute("data-d")); redraw(); });
    }
    $("#cryp-target").addEventListener("input", redraw);
    $("#cryp-horizon").addEventListener("input", redraw);
    $("#cryp-save").addEventListener("click", saveForecast);
    $("#cryp-del").addEventListener("click", deleteForecast);
    // Esc : ferme le détail (hors champ de saisie ; l'overlay d'aide a priorité)
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (CC.state.view !== "cryptos" || !curSym) return;
      var o = document.getElementById("overlay"); if (o && !o.hidden) return;
      var t = e.target, tag = (t && t.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      closeDetail();
    });
    // commande « CRYP BTC »
    CC.on("cmd", function (c) {
      if (!c || c.key !== "cryptos" || !c.args || !c.args.length) return;
      var f = $("#cryp-filter"); if (f) { f.value = ""; renderTiles(); }
      var s = resolveSym(c.args[0]);
      if (s) openDetail(s); else { pendingSym = c.args[0]; if (UNI.length) { pendingSym = null; CC.toast("symbole inconnu : " + c.args[0], "warn", 3000); } }
    });
    CC.on("boot", applyRole);
    // événement inter-vues « cryptos:open » (FCST → ouvrir un symbole dans le détail)
    CC.on("cryptos:open", function (sym) {
      if (!sym) return;
      var f = $("#cryp-filter"); if (f) { f.value = ""; renderTiles(); }
      var s = resolveSym(sym);
      if (s) openDetail(s); else { pendingSym = String(sym); if (UNI.length) { pendingSym = null; CC.toast("symbole inconnu : " + sym, "warn", 3000); } }
    });
  }

  CC.register({
    key: "cryptos", code: "CRYP", title: "Cryptos", icon: "◈", refreshMs: 15000,
    init: function (el) {
      root = el;
      injectCss();
      el.innerHTML = skeleton();
      bind();
      applyRole();
    },
    refresh: async function () {
      await refreshUniverse();
      if (curSym) await openDetail(curSym, { noScroll: true });
    },
    onShow: function () { applyRole(); },
    // exposé pour les tests / autres vues (FCST → ouvrir un symbole)
    open: function (sym) { CC.show("cryptos"); var s = resolveSym(sym) || sym; openDetail(s); },
    close: closeDetail
  });
})();
