/* Vue PORT — Portefeuille (vue par défaut).
   Portefeuilles shadow du Live Alpha Lab (5 × 200 000 € virtuels, mark-to-market réel toutes les 15 min),
   stratégie événementielle (shadow quotidien), ancien paper Mongo (gelé, lecture seule),
   équité backtest (jambes + combiné, base 100). Tout est paper/shadow : aucun ordre réel.
   Script classique : dépend de window.CC (core.js) chargé avant. */
(function () {
  "use strict";
  var CC = window.CC;
  if (!CC || !CC.register) { console.error("[PORT] core.js absent"); return; }
  var F = CC.fmt, T = CC.tokens;
  var esc = CC.esc || function (s) { return s == null ? "" : String(s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var DASH = "—";

  var CAP = 200000, DEFAULT_PF = "P1_EQUAL_RISK", LS_PF = "cc.port.pf", SLOW_MS = 300000;
  var LEGS_ORDER = ["V1.2", "STACK", "BASIS", "COMBINÉ"];
  var LEGS_COLOR = { "V1.2": T.info, "STACK": "#b48cff", "BASIS": T.amber, "COMBINÉ": T.up };
  // label roster → couleur de tag (la couleur ne fait que renforcer : le libellé est toujours affiché)
  var LABEL_KIND = { VALIDATED_FORWARD: "up", NO_CAPITAL: "dn", GATE: "info", OVERLAY: "info", EXPERIMENTAL_SHADOW: "warn" };
  var LABEL_TITLE = {
    VALIDATED_FORWARD: "validé indépendamment (forward)",
    NO_CAPITAL: "collecte seule, aucun capital",
    GATE: "gate : pas une position",
    OVERLAY: "overlay : pas une position",
    EXPERIMENTAL_SHADOW: "tourne en shadow, preuve en cours"
  };

  var S = {
    root: null, sel: DEFAULT_PF, data: null, roster: {}, hist: null, pos: null,
    slowAt: 0, selNames: "", asOf: {}, rosterOpen: true
  };
  try { S.sel = localStorage.getItem(LS_PF) || DEFAULT_PF; } catch (e) { /* stockage privé */ }

  // ── utilitaires ────────────────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function sym(s) { return s == null ? DASH : String(s).replace(/USDT$/, ""); }
  function labelKind(l) { return LABEL_KIND[l] || "muted"; }
  function tag(l) {
    if (!l) return "";
    return '<span class="tag ' + labelKind(l) + '" title="' + esc(LABEL_TITLE[l] || l) + '">' + esc(l) + "</span>";
  }
  function nFailed(v) { return Array.isArray(v) ? v.length : (CC.isNum(v) ? Number(v) : 0); }
  function panelHtml(o) { return CC.panel(o); }
  function setTitle(id, txt) { var el = $(id); if (!el) return; var t = el.querySelector(".panel-h .ttl"); if (t) { t.textContent = txt; t.setAttribute("title", txt); } }
  function setAsOf(id, iso, fresh) {
    var el = $(id); if (!el) return;
    if (iso) S.asOf[id] = iso;
    CC.setAsOf(el, iso || S.asOf[id] || null, fresh);
  }
  function fail(id, e) {
    var el = $(id); if (!el) return;
    var msg = (e && e.message) ? String(e.message) : String(e || "erreur");
    if (/401/.test(msg)) return;
    var box = el.querySelector(".pf-err");
    if (box) { box.hidden = false; box.textContent = "indisponible : " + msg.slice(0, 200); }
    setAsOf(id, null, "error");
  }
  function ok(id) { var el = $(id); if (!el) return; var box = el.querySelector(".pf-err"); if (box) { box.hidden = true; box.textContent = ""; } }
  function labFresh(cy) {
    cy = cy || {};
    if (cy.live) return "fresh";
    if (cy.status === "OK" || cy.status === "ok") return "stale";
    return cy.status ? "error" : "unknown";
  }
  function alpha(v) { return v == null || v === "" ? DASH : esc(v); }
  function alphaSmall(v) { return '<span class="alpha" title="' + esc(v || "") + '">' + alpha(v) + "</span>"; }
  function signed(fmt) { return function (v) { return '<span class="' + CC.cls(v) + '">' + esc(fmt(v)) + "</span>"; }; }

  // ── CSS propre à la vue (tokens seulement, jamais terminal.css) ─────────────
  function injectCss() {
    if ($("css-portfolio")) return;
    var st = document.createElement("style");
    st.id = "css-portfolio";
    st.textContent =
      "#v-portfolio .pf-top{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap;margin-top:8px}" +
      "#v-portfolio .pf-strip{display:flex;flex-wrap:wrap;gap:6px 18px;margin-top:10px}" +
      "#v-portfolio .pf-strip .it{display:flex;flex-direction:column;min-width:0}" +
      "#v-portfolio .pf-strip .it .v{font-size:13px;font-variant-numeric:tabular-nums;white-space:nowrap}" +
      "#v-portfolio .pf-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}" +
      "#v-portfolio .pf-chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--bg3);padding:2px 6px;font-size:12px;border-radius:var(--r);white-space:nowrap;font-variant-numeric:tabular-nums}" +
      "#v-portfolio .pf-chip .sub{color:var(--ink3);font-size:11px}" +
      "#v-portfolio .pf-err{color:var(--dn);font-size:12px;margin-top:6px;white-space:normal}" +
      "#v-portfolio .tbl.cmp{font-size:12px}" +
      "#v-portfolio .tbl .alpha{font-size:11px;color:var(--ink2)}" +
      "#v-portfolio .pf-cap{font-size:11px;color:var(--ink3);margin-top:6px}" +
      "#v-portfolio .pf-eb{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}" +
      "#v-portfolio .pf-eb .l{flex:0 0 auto;min-width:200px}#v-portfolio .pf-eb .r{flex:1 1 260px;min-width:0}" +
      "#v-portfolio details .kv{margin-top:8px}" +
      "#v-portfolio .badge{vertical-align:baseline}";
    document.head.appendChild(st);
  }

  // ── squelette (rendu une seule fois) ───────────────────────────────────────
  function init(el) {
    S.root = el;
    S.selNames = "";                                 // le squelette est reconstruit : sélecteur à rebâtir
    injectCss();
    try { S.rosterOpen = window.innerWidth >= 1280; } catch (e) { S.rosterOpen = true; }
    el.innerHTML =
      '<div class="grid">'
      + panelHtml({ id: "pf-hero", code: "PORT", title: "Shadow live — Live Alpha Lab", cls: "c8", fresh: "unknown", body:
          '<div class="row spread"><div class="seg" id="pf-sel" role="tablist" aria-label="portefeuille"><span class="muted small">chargement…</span></div>'
          + '<span class="small muted" id="pf-cycle" title="dernier cycle Live Alpha Lab"></span></div>'
          + '<div class="pf-top"><div class="big" id="pf-equity" title="équité (capital virtuel + P&amp;L)">' + DASH + "</div>"
          + '<div id="pf-pnl" class="mid" title="P&amp;L depuis l\'ouverture, en € et en fraction du capital virtuel">' + DASH + "</div>"
          + '<div id="pf-since" class="small muted"></div></div>'
          + '<div class="pf-strip" id="pf-strip"></div>'
          + '<div class="pf-chips" id="pf-chips"></div>'
          + '<div class="pf-cap" id="pf-chips-cap" hidden>P&amp;L par alpha : <b>net</b> = brut − frais attribués à l\'alpha ; le funding n\'est pas attribué par alpha (écart avec le P&amp;L total = funding + résidu marks).</div>'
          + '<div class="pf-err" hidden></div>' })
      + panelHtml({ id: "pf-curve-panel", code: "CRB", title: "Courbe — équité, base 200 000 €", cls: "c4", body:
          '<div id="pf-curve" class="chart chart-sm" aria-label="courbe d\'équité"></div>'
          + '<div id="pf-cmp" class="mt"></div>'
          + '<div class="pf-err" hidden></div>' })
      + "</div>"
      + '<div class="grid">'
      + panelHtml({ id: "pf-pos", code: "POS", title: "Positions ouvertes", cls: "c8", fresh: "unknown", body: '<div id="pf-pos-tbl"></div><div class="pf-err" hidden></div>' })
      + panelHtml({ id: "pf-fills", code: "FILL", title: "Derniers fills", cls: "c4", fresh: "unknown", body: '<div id="pf-fills-tbl"></div><div class="pf-err" hidden></div>' })
      + "</div>"
      + '<div class="grid">'
      + panelHtml({ id: "pf-roster", code: "ROST", title: "Roster — alphas, statut honnête", cls: "c12", body:
          "<details" + (S.rosterOpen ? " open" : "") + '><summary id="pf-roster-sum">roster : chargement…</summary>'
          + '<div id="pf-roster-tbl" class="mt"></div>'
          + '<div class="fn"><b>VALIDATED_FORWARD</b> = validé indépendamment ; <b>EXPERIMENTAL_SHADOW</b> = tourne, preuve en cours ; <b>OVERLAY/GATE</b> = pas une position ; <b>NO_CAPITAL</b> = collecte seule, aucun capital. '
          + "Confiance : TOO_EARLY / EARLY / MEANINGFUL selon le nombre d'épisodes indépendants forward.</div>"
          + "</details>"
          + '<div class="pf-err" hidden></div>' })
      + "</div>"
      + '<div class="grid">'
      + panelHtml({ id: "pf-event", code: "EVT", title: "Stratégie événementielle — shadow quotidien", cls: "c12", body:
          '<div class="pf-eb"><div class="l">'
          + '<div class="row" id="pf-eb-badges"></div>'
          + '<div class="lbl mt" title="équité paper indexée 100">équité shadow (base 100)</div>'
          + '<div class="big-sm" id="pf-eb-eq">' + DASH + "</div>"
          + '<div class="small muted" id="pf-eb-meta"></div>'
          + '</div><div class="r"><div id="pf-eb-curve" class="chart chart-sm" aria-label="courbe équité événementielle"></div></div></div>'
          + '<div id="pf-eb-tbl" class="mt"></div>'
          + '<div class="fn" id="pf-eb-note"></div>'
          + '<div class="pf-err" hidden></div>' })
      + "</div>"
      + '<div class="grid">'
      + panelHtml({ id: "pf-legacy", code: "LEG", title: "Ancien portefeuille paper (gelé le 03/09/2026) — lecture seule", cls: "c12 dim", body:
          '<details><summary id="pf-leg-sum">chargement…</summary><div id="pf-leg-body"></div></details>'
          + '<div class="pf-err" hidden></div>' })
      + "</div>"
      + '<div class="grid">'
      + panelHtml({ id: "pf-bt", code: "BT", title: "Équité backtest — jambes + combiné (base 100)", cls: "c12", body:
          '<div class="tiles" id="pf-bt-tiles"></div>'
          + '<div id="pf-bt-eq" class="chart chart-lg mt" aria-label="équité backtest par jambe"></div>'
          + '<div class="fn">Chaque jambe indexée 100 à sa 1ʳᵉ donnée ; COMBINÉ = produit sur la fenêtre commune. <b>Backtest / paper historique, pas d\'argent live.</b></div>'
          + '<div class="pf-err" hidden></div>' })
      + "</div>"
      + '<div class="grid">'
      + panelHtml({ id: "pf-yr", code: "YR", title: "Rendement par année — combiné (backtest)", cls: "c6", body: '<div id="pf-yr-ch" class="chart" aria-label="rendement annuel"></div>' })
      + panelHtml({ id: "pf-corr", code: "CORR", title: "Corrélations des jambes (backtest, rendements quotidiens)", cls: "c6", body: '<div id="pf-corr-ch" class="chart" aria-label="corrélations"></div>' })
      + "</div>"
      + '<div class="view-fn">Paper trading shadow : capital virtuel 200 000 € par portefeuille, recalcul toutes les 15 min par le cycle Live Alpha Lab (mark-to-market réel, pas de tick). '
      + "Les 5 portefeuilles tournent en parallèle sur les mêmes signaux avec des règles d'allocation différentes. <b>Aucun ordre réel.</b></div>";

    // sélection portefeuille : boutons + lignes du tableau comparatif
    $("pf-sel").addEventListener("click", function (e) {
      var b = e.target && e.target.closest ? e.target.closest("button[data-pf]") : null;
      if (b) select(b.getAttribute("data-pf"));
    });
    $("pf-cmp").addEventListener("click", function (e) {
      var tr = e.target && e.target.closest ? e.target.closest("tr[data-pf]") : null;
      if (tr) select(tr.getAttribute("data-pf"));
    });
    CC.on("cmd", function (c) {
      if (!c || c.key !== "portfolio" || !c.args || !c.args.length) return;
      var want = String(c.args[0]).toUpperCase();
      var pfs = (S.data && S.data.portfolios) || [];
      for (var i = 0; i < pfs.length; i++) { if (String(pfs[i].name).toUpperCase() === want) { select(pfs[i].name); return; } }
      CC.toast("portefeuille inconnu : " + want, "warn", 3000);
    });
  }

  // ── rafraîchissement ───────────────────────────────────────────────────────
  async function refresh() {
    var now = Date.now();
    var slow = (now - S.slowAt) >= SLOW_MS;
    if (slow) S.slowAt = now;
    var tasks = [loadLab()];
    if (slow) tasks.push(loadEventBook(), loadLegacy(), loadBacktest());
    await Promise.all(tasks);   // chaque tâche attrape ses propres erreurs
  }

  function select(name) {
    if (!name || name === S.sel) return;
    S.sel = name;
    try { localStorage.setItem(LS_PF, name); } catch (e) { /* privé */ }
    S.hist = null; S.pos = null;
    renderHero(); renderCompare();
    loadDetail();
  }

  // ── Live Alpha Lab : /api/lab/portfolios + history + positions ─────────────
  async function loadLab() {
    try {
      var d = await CC.j("/api/lab/portfolios");
      S.data = d; S.roster = {};
      (d.roster || []).forEach(function (r) { if (r && r.alpha_id) S.roster[r.alpha_id] = r; });
      var pfs = d.portfolios || [];
      if (pfs.length && !pfs.some(function (x) { return x.name === S.sel; })) {
        S.sel = pfs.some(function (x) { return x.name === DEFAULT_PF; }) ? DEFAULT_PF : pfs[0].name;
      }
      ok("pf-hero"); ok("pf-roster");
      renderHero(); renderCompare(); renderRoster();
      await loadDetail();
    } catch (e) {
      fail("pf-hero", e);
      fail("pf-roster", e);
    }
  }

  async function loadDetail() {
    var name = S.sel;
    var url = "/api/lab/portfolio/" + encodeURIComponent(name);
    var rh = await CC.j(url + "/history").then(function (h) { return { ok: true, v: h }; }, function (e) { return { ok: false, e: e }; });
    var rp = await CC.j(url + "/positions").then(function (p) { return { ok: true, v: p }; }, function (e) { return { ok: false, e: e }; });
    if (name !== S.sel) return;                      // sélection changée entre-temps
    if (rh.ok) { S.hist = rh.v; ok("pf-curve-panel"); renderCurve(); }
    else fail("pf-curve-panel", rh.e);
    if (rp.ok) { S.pos = rp.v; ok("pf-pos"); ok("pf-fills"); renderPositions(); renderFills(); }
    else { fail("pf-pos", rp.e); fail("pf-fills", rp.e); }
  }

  function currentPf() {
    var pfs = (S.data && S.data.portfolios) || [];
    for (var i = 0; i < pfs.length; i++) if (pfs[i].name === S.sel) return pfs[i];
    return null;
  }

  function renderHero() {
    var d = S.data; if (!d) return;
    var pfs = d.portfolios || [], cy = d.cycle || {};
    var fresh = labFresh(cy);
    setAsOf("pf-hero", d.generated_at, fresh);
    // sélecteur (reconstruit seulement si la liste change)
    var names = pfs.map(function (p) { return p.name; }).join("|");
    var selEl = $("pf-sel");
    if (names !== S.selNames) {
      S.selNames = names;
      selEl.innerHTML = pfs.map(function (p) {
        return '<button type="button" data-pf="' + esc(p.name) + '" role="tab">' + esc(p.name) + "</button>";
      }).join("") || '<span class="muted small">aucun portefeuille (state.json absent)</span>';
    }
    var btns = selEl.querySelectorAll("button[data-pf]");
    for (var i = 0; i < btns.length; i++) {
      var p0 = null; for (var k = 0; k < pfs.length; k++) if (pfs[k].name === btns[i].getAttribute("data-pf")) p0 = pfs[k];
      var on = btns[i].getAttribute("data-pf") === S.sel;
      btns[i].classList.toggle("sel", on);
      btns[i].setAttribute("aria-pressed", on ? "true" : "false");
      btns[i].setAttribute("aria-selected", on ? "true" : "false");
      if (p0) btns[i].setAttribute("title", (p0.status || DASH) + " · " + F.int(p0.n_positions) + " positions · " + F.eur0(p0.equity));
    }
    var nf = nFailed(cy.producers_failed);
    $("pf-cycle").innerHTML = "dernier cycle <b class=\"ink2\">" + esc(F.hhmm(cy.finished_at)) + "</b> · "
      + esc(cy.producers_ok != null ? F.int(cy.producers_ok) : "?") + "/" + esc(cy.producers_run != null ? F.int(cy.producers_run) : "?") + " producteurs"
      + (nf ? ' · <span class="dn">' + nf + " en échec</span>" : "")
      + (cy.age_min != null ? " · âge " + esc(F.ageMin(cy.age_min)) : "")
      + (cy.timer_every_min ? " · toutes les " + esc(F.int(cy.timer_every_min)) + " min" : "");
    $("pf-cycle").setAttribute("title", "cycle Live Alpha Lab terminé " + F.dtfull(cy.finished_at) + " · statut " + (cy.status || DASH));

    var p = currentPf();
    if (!p) {
      $("pf-equity").textContent = DASH; $("pf-pnl").textContent = DASH; $("pf-since").textContent = "";
      $("pf-strip").innerHTML = ""; $("pf-chips").innerHTML = ""; $("pf-chips-cap").hidden = true;
      return;
    }
    var cap = d.capital_eur != null ? d.capital_eur : CAP;
    $("pf-equity").textContent = F.eur0(p.equity);
    $("pf-equity").setAttribute("title", "équité " + F.eur2(p.equity) + " · capital virtuel " + F.eur0(cap));
    var pc = CC.cls(p.pnl_eur);
    $("pf-pnl").innerHTML = '<span class="' + pc + '">' + esc(F.seur0(p.pnl_eur)) + "</span> "
      + '<span class="' + pc + ' small">(' + esc(F.pct(p.pnl_pct, 3)) + ")</span>";
    $("pf-since").innerHTML = "depuis l'ouverture · capital virtuel " + esc(F.eur0(cap))
      + (p.since ? ' · <span title="1er point d\'équité">ouvert le ' + esc(F.dt(p.since)) + "</span>" : "")
      + (p.status && p.status !== "OK" ? ' · <span class="warn">' + esc(p.status) + "</span>" : "");

    var items = [
      ["brut", F.eur0(p.gross_exposure), "exposition brute (somme des |notionnels|)", ""],
      ["net", F.eur0(p.net_exposure), "exposition nette (longs − shorts)", ""],
      ["positions", F.int(p.n_positions), "positions ouvertes", ""],
      ["réalisé", F.seur0(p.realized_pnl), "P&L réalisé (fermetures), frais non déduits", CC.cls(p.realized_pnl)],
      ["latent", F.seur0(p.unrealized_pnl), "P&L latent au dernier mark", CC.cls(p.unrealized_pnl)],
      ["frais", F.eur0(p.fees), "frais de transaction cumulés (modèle de coûts)", ""],
      ["funding", F.seur2(p.funding), "funding cumulé (perpétuels)", CC.cls(p.funding)],
      ["drawdown", F.pct(p.drawdown, 2), "drawdown courant vs plus haut d'équité", p.drawdown < 0 ? "dn" : ""]
    ];
    if (p.name === "P1_VOL_OVERLAY" && d.vol_overlay_multiplier != null) {
      items.push(["overlay vol", F.ratio(d.vol_overlay_multiplier, 3), "multiplicateur de taille appliqué par l'overlay de volatilité", "info"]);
    }
    if (Array.isArray(d.screened_symbols) && d.screened_symbols.length) {
      items.push(["écartés (gate)", d.screened_symbols.map(sym).join(" "), "symboles écartés par le gate whale/LSR : " + d.screened_symbols.join(", "), "muted"]);
    }
    $("pf-strip").innerHTML = items.map(function (it) {
      return '<div class="it" title="' + esc(it[2]) + '"><span class="lbl">' + esc(it[0]) + '</span><span class="v ' + esc(it[3]) + '">' + esc(it[1]) + "</span></div>";
    }).join("");

    // P&L par alpha : NET = brut − frais attribués ; UNATTRIBUTED = résidu marks
    var gross = p.pnl_by_alpha || {}, costs = p.cost_by_alpha || {}, keys = {}, a;
    for (a in gross) keys[a] = 1;
    for (a in costs) keys[a] = 1;
    var chips = Object.keys(keys).map(function (id) {
      var g = CC.isNum(gross[id]) ? Number(gross[id]) : 0, c = CC.isNum(costs[id]) ? Number(costs[id]) : 0;
      return { id: id, g: g, c: c, net: g - c };
    }).sort(function (x, y) { return Math.abs(y.net) - Math.abs(x.net); });
    $("pf-chips").innerHTML = chips.map(function (ch) {
      var r = S.roster[ch.id];
      var isUn = ch.id === "UNATTRIBUTED";
      var name = isUn ? "non attribué (résidu marks)" : ch.id;
      var t = (isUn ? "résidu de mark-to-market non attribuable à un alpha" : (r ? "alpha du roster (" + r.label + ")" : "hors roster"))
        + " · brut " + F.seur2(ch.g) + " / frais " + F.eur2(ch.c) + " · net = brut − frais";
      return '<span class="pf-chip" title="' + esc(t) + '">' + (isUn ? '<span class="tag muted">RÉSIDU</span>' : (r ? tag(r.label) : ""))
        + "<span>" + esc(name) + "</span>"
        + '<b class="' + CC.cls(ch.net) + '">' + esc(F.seur0(ch.net)) + "</b>"
        + '<span class="sub">brut ' + esc(F.seur0(ch.g)) + " / frais " + esc(F.eur0(ch.c)) + "</span></span>";
    }).join("");
    $("pf-chips-cap").hidden = !chips.length;
  }

  function renderCompare() {
    var d = S.data; if (!d) return;
    var pfs = d.portfolios || [];
    $("pf-cmp").innerHTML = CC.table({
      cls: "cmp", empty: "aucun portefeuille",
      cols: [
        { k: "name", label: "portefeuille", fmt: function (v) { return "<b>" + esc(v) + "</b>"; } },
        { k: "equity", label: "équité", align: "r", fmt: function (v) { return esc(F.eur0(v)); } },
        { k: "pnl_eur", label: "P&L", align: "r", fmt: function (v, r) { return esc(F.seur0(v)) + '<span class="sub">' + esc(F.pct(r.pnl_pct, 2)) + "</span>"; }, cls: function (v) { return CC.cls(v); } },
        { k: "gross_exposure", label: "brut", align: "r", fmt: function (v) { return esc(F.eur0(v)); }, title: "exposition brute" },
        { k: "n_positions", label: "pos.", align: "r", fmt: function (v) { return esc(F.int(v)); }, title: "positions ouvertes" }
      ],
      rows: pfs,
      rowCls: function (r) { return "clickable" + (r.name === S.sel ? " sel" : ""); },
      rowAttrs: function (r) { return 'data-pf="' + esc(r.name) + '" title="afficher ' + esc(r.name) + '"'; }
    });
    setTitle("pf-curve-panel", "Courbe — " + S.sel + ", base " + F.eur0(d.capital_eur != null ? d.capital_eur : CAP) + " · " + F.int(pfs.length) + " portefeuilles");
  }

  function renderCurve() {
    var h = S.hist || {}, hist = h.history || [], base = h.capital_eur != null ? h.capital_eur : CAP;
    var el = $("pf-curve"); if (!el) return;
    if (hist.length) setAsOf("pf-curve-panel", hist[hist.length - 1].t);
    if (!hist.length) { el.innerHTML = '<div class="chart-na">aucun point d\'équité pour ' + esc(S.sel) + "</div>"; return; }
    if (el.firstChild && el.firstChild.className === "chart-na") el.innerHTML = "";
    var vals = hist.map(function (x) { return x.v; }), last = vals[vals.length - 1];
    var up = CC.isNum(last) && Number(last) >= base, col = up ? T.up : T.dn;
    var mn = Math.min.apply(null, vals.concat([base])), mx = Math.max.apply(null, vals.concat([base]));
    CC.chart(el, {
      tooltip: { trigger: "axis", formatter: function (ps) {
        var x = hist[ps[0].dataIndex] || {};
        return esc(F.dt(x.t)) + "<br>" + esc(F.eur0(x.v)) + " · brut " + esc(F.eur0(x.gross)) + " · " + esc(F.int(x.n_positions)) + " pos." + (x.status && x.status !== "OK" ? " · " + esc(x.status) : "");
      } },
      grid: { left: 4, right: 4, top: 6, bottom: 6 },
      xAxis: { type: "category", show: false, data: hist.map(function (x) { return x.t; }) },
      yAxis: { type: "value", show: false, min: mn, max: mx },
      series: [{ type: "line", data: vals, showSymbol: false, lineStyle: { width: 1.5, color: col }, itemStyle: { color: col },
        areaStyle: { color: col, opacity: 0.08 },
        markLine: { silent: true, symbol: "none", data: [{ yAxis: base }], lineStyle: { color: T.ink3, type: "dashed" }, label: { show: false } } }]
    });
  }

  function renderPositions() {
    var p = S.pos || {}, rows = (p.positions || []).slice();
    rows.sort(function (a, b) { return Math.abs(Number(b.notional_entry) || 0) - Math.abs(Number(a.notional_entry) || 0); });
    setTitle("pf-pos", "Positions ouvertes — " + S.sel + " · " + F.int(rows.length));
    setAsOf("pf-pos", p.as_of, labFresh(S.data && S.data.cycle));
    $("pf-pos-tbl").innerHTML = CC.table({
      maxH: "340px", empty: "aucune position ouverte" + (p.as_of ? " — état au " + F.dtfull(p.as_of) : ""),
      cols: [
        { k: "instrument", label: "actif", fmt: function (v) { return '<b title="' + esc(v) + '">' + esc(sym(v)) + "</b>"; } },
        { k: "owner_alpha", label: "alpha", fmt: alphaSmall },
        { k: "quantity", label: "quantité", align: "r", fmt: function (v) { return esc(F.snum(v)); }, title: "quantité signée (négatif = short)" },
        { k: "entry_price", label: "entrée", align: "r", fmt: function (v) { return esc(F.price(v)); }, title: "prix d'entrée moyen (USDT)" },
        { k: "notional_entry", label: "notional entrée", align: "r", fmt: function (v) { return esc(F.eur0(v)); }, title: "notionnel à l'entrée" },
        { k: "realized_pnl", label: "réalisé", align: "r", fmt: signed(F.seur2), title: "P&L réalisé sur l'instrument" },
        { k: "fees_paid", label: "frais", align: "r", fmt: function (v) { return esc(F.eur2(v)); } },
        { k: "funding_paid", label: "funding", align: "r", fmt: signed(F.seur2) }
      ],
      rows: rows
    });
  }

  function renderFills() {
    var p = S.pos || {}, rows = (p.fills_recent || []).slice();
    rows.sort(function (a, b) { return String(b.timestamp || "").localeCompare(String(a.timestamp || "")); });
    setTitle("pf-fills", "Derniers fills — " + S.sel + " · " + F.int(rows.length));
    setAsOf("pf-fills", rows.length ? rows[0].timestamp : p.as_of, labFresh(S.data && S.data.cycle));
    $("pf-fills-tbl").innerHTML = CC.table({
      maxH: "340px", empty: "aucun fill récent",
      cols: [
        { k: "timestamp", label: "heure", fmt: function (v) { return '<span title="' + esc(F.dtfull(v)) + '">' + esc(F.dt(v)) + "</span>"; } },
        { k: "instrument", label: "actif", fmt: function (v) { return '<b title="' + esc(v) + '">' + esc(sym(v)) + "</b>"; } },
        { k: "side", label: "côté", fmt: function (v) { var s = String(v || "").toUpperCase(); return s ? '<span class="' + (s === "BUY" ? "up" : s === "SELL" ? "dn" : "flat") + '">' + esc(s === "BUY" ? "achat" : s === "SELL" ? "vente" : s) + "</span>" : DASH; } },
        { k: "quantity", label: "quantité", align: "r", fmt: function (v) { return esc(F.snum(v)); } },
        { k: "price", label: "prix", align: "r", fmt: function (v) { return esc(F.price(v)); }, title: "prix d'exécution simulé (USDT)" },
        { k: "fee", label: "frais", align: "r", fmt: function (v) { return esc(F.eur2(v)); } },
        { k: "alpha_id", label: "alpha", fmt: alphaSmall }
      ],
      rows: rows
    });
  }

  function renderRoster() {
    var d = S.data || {}, ro = d.roster || [];
    setAsOf("pf-roster", d.generated_at);
    setTitle("pf-roster", "Roster — " + F.int(ro.length) + " alphas, statut honnête");
    var counts = {};
    ro.forEach(function (r) { counts[r.label] = (counts[r.label] || 0) + 1; });
    $("pf-roster-sum").innerHTML = esc(F.int(ro.length)) + " alphas · " + Object.keys(counts).map(function (l) { return tag(l) + " " + counts[l]; }).join(" · ");
    $("pf-roster-tbl").innerHTML = CC.table({
      maxH: "420px", empty: "roster indisponible",
      cols: [
        { k: "alpha_id", label: "alpha_id", fmt: function (v, r) { return "<b>" + esc(v) + "</b>" + '<span class="sub">' + esc([r.risk_bucket, r.correlation_family].filter(Boolean).join(" · ")) + "</span>"; } },
        { k: "family", label: "famille" },
        { k: "scientific_status", label: "statut scientifique", title: "FROZEN = paramètres gelés, aucun retuning" },
        { k: "operational_status", label: "statut opérationnel", fmt: function (v, r) { return esc(v == null ? DASH : v) + (r.role ? '<span class="sub">rôle ' + esc(r.role) + "</span>" : ""); } },
        { k: "label", label: "label", fmt: function (v) { return tag(v) || DASH; } },
        { k: function (r) { return r; }, label: "forward / replay", align: "r", title: "décisions forward (live) / décisions replay (historique)", fmt: function (r) {
          return esc(F.int(r.forward_decisions)) + " / " + esc(F.int(r.replay_decisions))
            + '<span class="sub">' + esc((r.independent_episodes != null ? F.int(r.independent_episodes) + " épisodes indép." : "") + (r.last_trigger_h_ago != null ? " · dernier signal il y a " + F.h(r.last_trigger_h_ago, 0) : "")) + "</span>";
        } },
        { k: "confidence", label: "confiance", title: "TOO_EARLY / EARLY / MEANINGFUL selon les épisodes indépendants forward", fmt: function (v) {
          var s = v == null ? DASH : String(v);
          return '<span class="' + (s === "MEANINGFUL" ? "up" : s === "EARLY" ? "warn" : "muted") + '">' + esc(s) + "</span>";
        } },
        { k: "freeze_timestamp", label: "freeze", fmt: function (v) { return '<span title="' + esc(F.dtfull(v)) + '">' + esc(F.date(v)) + "</span>"; } }
      ],
      rows: ro
    });
  }

  // ── Stratégie événementielle : /api/event_book (shadow quotidien) ──────────
  async function loadEventBook() {
    try {
      var d = await CC.j("/api/event_book");
      ok("pf-event");
      setAsOf("pf-event", new Date().toISOString());
      var eq = $("pf-eb-eq");
      if (!d || !d.exists) {
        $("pf-eb-badges").innerHTML = CC.badge("aucun livre", "muted");
        eq.textContent = DASH; eq.className = "big-sm";
        $("pf-eb-meta").textContent = "livre événementiel absent (exists : false)";
        $("pf-eb-tbl").innerHTML = ""; $("pf-eb-note").textContent = "";
        $("pf-eb-curve").innerHTML = "";
        return;
      }
      var s = d.stats || {};
      $("pf-eb-badges").innerHTML =
        CC.badge("shadow quotidien", "info", "timer futur-event-shadow (quotidien) : aucun ordre réel")
        + " " + CC.badge("J" + F.int(d.forward_days) + "/" + F.int(d.gate_days) + " forward", "muted", "jours de forward-test / jours requis par le gate")
        + " " + (s.pf != null ? CC.badge("PF " + F.num(s.pf, 2) + " · WR " + F.pct0(s.wr, 0), (Number(s.pf) || 0) >= 1.3 ? "up" : "warn", "profit factor · taux de réussite (décisions closes)") : CC.badge("labels en attente", "warn"))
        + (d.sizing_pct != null ? " " + CC.badge("sizing " + F.num(d.sizing_pct, 0) + " %", "muted", "taille par décision, en % du capital virtuel") : "");
      eq.textContent = s.equity != null ? F.num(s.equity, 2) : DASH;
      eq.className = "big-sm " + (s.roi_pct != null ? CC.cls(s.roi_pct) : "");
      eq.setAttribute("title", s.roi_pct != null ? "ROI " + F.pct(Number(s.roi_pct) / 100, 3) : "");
      var be = d.by_engine || {};
      $("pf-eb-meta").innerHTML =
        esc(F.int(d.n_total)) + " décisions · " + esc(F.int(d.n_closed)) + " closes · " + esc(F.int(d.n_pending)) + " ouvertes"
        + (s.mean_bps != null ? ' · edge moyen <span class="' + CC.cls(s.mean_bps) + '">' + esc(F.bps(s.mean_bps, 1)) + "</span>" : "")
        + (s.roi_pct != null ? ' · ROI <span class="' + CC.cls(s.roi_pct) + '">' + esc(F.pct(Number(s.roi_pct) / 100, 3)) + "</span>" : "")
        + (d.n_probe != null ? ' · <span title="tier PROBE (score 0,50–0,70) : labels seulement, hors P&amp;L">probe ' + esc(F.int(d.n_probe_labeled)) + "/" + esc(F.int(d.n_probe)) + " labellisés</span>" : "")
        + (Object.keys(be).length ? "<br>" + Object.keys(be).map(function (e) { var v = be[e] || {}; return esc(e) + " : PF " + esc(F.num(v.pf, 2)) + " (n " + esc(F.int(v.n)) + ")"; }).join(" · ") : "");
      $("pf-eb-note").textContent = d.note || "";
      var c = d.curve || [], cel = $("pf-eb-curve");
      if (c.length > 1) {
        if (cel.firstChild && cel.firstChild.className === "chart-na") cel.innerHTML = "";
        var vals = c.map(function (x) { return x.v; }), last = vals[vals.length - 1];
        var col = CC.isNum(last) && Number(last) >= 100 ? T.up : T.dn;
        CC.chart(cel, {
          tooltip: { trigger: "axis", formatter: function (ps) { var x = c[ps[0].dataIndex] || {}; return esc(x.t) + "<br>base 100 : " + esc(F.num(x.v, 3)); } },
          grid: { left: 4, right: 4, top: 6, bottom: 6 },
          xAxis: { type: "category", show: false, data: c.map(function (x) { return x.t; }) },
          yAxis: { type: "value", show: false, scale: true },
          series: [{ type: "line", data: vals, showSymbol: false, lineStyle: { width: 1.5, color: col }, itemStyle: { color: col }, areaStyle: { color: col, opacity: 0.08 },
            markLine: { silent: true, symbol: "none", data: [{ yAxis: 100 }], lineStyle: { color: T.ink3, type: "dashed" }, label: { show: false } } }]
        });
      } else {
        cel.innerHTML = '<div class="chart-na">courbe : moins de 2 points</div>';
      }
      $("pf-eb-tbl").innerHTML = CC.table({
        maxH: "260px", empty: "le livre se remplit au fil des événements (données J-2, cascades intermittentes)",
        cols: [
          { k: "t", label: "heure event", title: "horodatage de l'événement (UTC)" },
          { k: "symbol", label: "symbole", fmt: function (v) { return '<b title="' + esc(v) + '">' + esc(sym(v)) + "</b>"; } },
          { k: "engine", label: "moteur", fmt: function (v) { return esc(v == null ? DASH : String(v).replace(/_/g, " ")); } },
          { k: "score", label: "score", align: "r", fmt: function (v) { return esc(F.num(v, 3)); }, title: "score de confiance du signal (0–1)" },
          { k: "status", label: "statut", fmt: function (v) { return v == null ? DASH : '<span class="' + (v === "clos" ? "ink2" : "warn") + '">' + esc(v) + "</span>"; } },
          { k: "net_bps", label: "net bps", align: "r", fmt: function (v) { return v == null ? '<span class="muted" title="en attente du label">en attente</span>' : '<span class="' + CC.cls(v) + '">' + esc(F.bps(v, 0)) + "</span>"; }, title: "résultat net de coûts, en points de base" }
        ],
        rows: d.recent || []
      });
    } catch (e) { fail("pf-event", e); }
  }

  // ── Ancien portefeuille Mongo : /api/portfolio/live (gelé, lecture seule, jamais de POST) ──
  async function loadLegacy() {
    try {
      var d = await CC.j("/api/portfolio/live");
      ok("pf-legacy");
      setAsOf("pf-legacy", new Date().toISOString());
      var sum = $("pf-leg-sum"), body = $("pf-leg-body");
      if (!d || !d.exists) {
        sum.textContent = "aucun" + (d && d.backend === "unavailable" ? " (Mongo indisponible)" : "");
        body.innerHTML = "";
        return;
      }
      var pc = CC.cls(d.pnl_eur);
      sum.innerHTML = CC.badge(d.stopped ? "gelé" : "état inconnu", d.stopped ? "muted" : "warn") + " valeur <b>" + esc(F.eur0(d.value_eur)) + "</b> · P&amp;L <span class=\"" + pc + "\">"
        + esc(F.seur0(d.pnl_eur)) + " (" + esc(F.pct(d.pnl_pct, 3)) + ")</span> · gelé le " + esc(F.dtfull(d.stopped_at)) + " · détails";
      body.innerHTML = '<dl class="kv">'
        + "<dt>valeur</dt><dd>" + esc(F.eur2(d.value_eur)) + "</dd>"
        + "<dt>capital</dt><dd>" + esc(F.eur0(d.capital_eur)) + "</dd>"
        + '<dt>P&amp;L</dt><dd class="' + pc + '">' + esc(F.seur2(d.pnl_eur)) + " (" + esc(F.pct(d.pnl_pct, 3)) + ")</dd>"
        + "<dt>ouvert le</dt><dd>" + esc(F.dtfull(d.created_at)) + "</dd>"
        + "<dt>dernier rebalancement</dt><dd>" + esc(F.dtfull(d.rebalanced_at)) + "</dd>"
        + "<dt>arrêté le</dt><dd>" + esc(F.dtfull(d.stopped_at)) + "</dd>"
        + "<dt>points d'historique</dt><dd>" + esc(F.int(d.history_points)) + "</dd>"
        + '<dt>ex-politique</dt><dd class="left small ink2">' + esc(d.policy_label || d.preset || DASH) + "</dd>"
        + (d.hint ? '<dt>note</dt><dd class="left small muted">' + esc(d.hint) + "</dd>" : "")
        + "</dl>"
        + '<div class="fn">Moteur paper Mongo arrêté le 03/09/2026 : plus aucune ré-allocation, lecture seule. Ne concerne pas les portefeuilles du Live Alpha Lab.</div>';
    } catch (e) { fail("pf-legacy", e); $("pf-leg-sum").textContent = "indisponible"; }
  }

  // ── Backtest : /api/summary + /api/equity (jambes + combiné, base 100) ──────
  async function loadBacktest() {
    try {
      var rs = await Promise.all([CC.j("/api/summary"), CC.j("/api/equity")]);
      var sum = rs[0] || {}, eq = rs[1] || {};
      ok("pf-bt");
      var dates = eq.dates || [], series = eq.series || {};
      var lastDate = dates.length ? dates[dates.length - 1] : null;
      setAsOf("pf-bt", new Date().toISOString());
      setTitle("pf-bt", "Équité backtest — jambes + combiné (base 100)" + (lastDate ? " · dernier point " + F.date(lastDate) : "") + " · " + F.int(dates.length) + " jours");
      var c = (sum.legs || {})["COMBINÉ"] || {}, sh = sum.shadow || {};
      function tile(k, v, s, cls, title) {
        return '<div class="panel tile"' + (title ? ' title="' + esc(title) + '"' : "") + '><div class="panel-b"><div class="lbl">' + esc(k) + '</div><div class="big-sm ' + esc(cls || "") + '">' + esc(v) + '</div><div class="sub">' + esc(s || "") + "</div></div></div>";
      }
      $("pf-bt-tiles").innerHTML = [
        tile("rendement / an — combiné", F.pct(c.roi_ann, 1), "fenêtre commune 3 jambes", CC.cls(c.roi_ann), "ROI annualisé du combiné (backtest)"),
        tile("drawdown max", F.pct(c.maxdd, 1), "gate paper ≤ 3 %", c.maxdd != null && Number(c.maxdd) < -0.03 ? "dn" : "warn", "drawdown maximal du combiné (backtest)"),
        tile("Sharpe", F.num(c.sharpe, 2), "quotidien annualisé", "", "ratio de Sharpe du combiné (backtest)"),
        tile("ROI total", F.pct(c.roi_total, 1), "depuis fenêtre commune", CC.cls(c.roi_total), "ROI total du combiné (backtest)"),
        tile("shadow forward", sh.day != null ? "J" + F.int(sh.day) + "/" + F.int(sh.target != null ? sh.target : 30) : DASH, sh.verdict_date ? "verdict " + F.date(sh.verdict_date) : "", "warn", "avancement du forward-test shadow de l'ancien moteur événementiel")
      ].join("");

      // jambes + combiné
      var legs = LEGS_ORDER.filter(function (k) { return Array.isArray(series[k]); });
      var eqEl = $("pf-bt-eq");
      if (!legs.length || !dates.length) {
        eqEl.innerHTML = '<div class="chart-na">aucune série d\'équité</div>';
      } else {
        if (eqEl.firstChild && eqEl.firstChild.className === "chart-na") eqEl.innerHTML = "";
        CC.chart(eqEl, {
          tooltip: { trigger: "axis", axisPointer: { type: "cross" }, valueFormatter: function (v) { return F.num(v, 3); } },
          legend: { data: legs },
          grid: { left: 56, right: 70, top: 28, bottom: 52 },
          xAxis: { type: "category", data: dates },
          yAxis: { type: "value", scale: true, axisLabel: { formatter: function (v) { return F.num(v, 2); } } },
          dataZoom: [{ type: "inside" }, { type: "slider" }],
          series: legs.map(function (k) {
            return { name: k, type: "line", data: series[k], showSymbol: false, connectNulls: false,
              lineStyle: { width: k === "COMBINÉ" ? 2 : 1.2, color: LEGS_COLOR[k] }, itemStyle: { color: LEGS_COLOR[k] }, emphasis: { focus: "series" },
              endLabel: { show: true, formatter: k, color: LEGS_COLOR[k], fontSize: 11, fontFamily: T.mono } };
          })
        });
      }

      // rendement par année (combiné) : dernier / premier point de l'année − 1
      var arr = series["COMBINÉ"] || [], by = {};
      dates.forEach(function (d0, i) { var v = arr[i]; if (v == null || !CC.isNum(v)) return; var y = String(d0).slice(0, 4); if (!by[y]) by[y] = { f: v }; by[y].l = v; });
      var yrs = Object.keys(by).sort(), yv = yrs.map(function (y) { return by[y].l / by[y].f - 1; });
      var yrEl = $("pf-yr-ch");
      setAsOf("pf-yr", new Date().toISOString());
      if (!yrs.length) {
        yrEl.innerHTML = '<div class="chart-na">aucune année</div>';
      } else {
        if (yrEl.firstChild && yrEl.firstChild.className === "chart-na") yrEl.innerHTML = "";
        CC.chart(yrEl, {
          tooltip: { trigger: "item", formatter: function (p) { return esc(p.name) + " : " + esc(F.pct(p.value, 2)); } },
          grid: { left: 56, right: 14, top: 20, bottom: 28 },
          xAxis: { type: "category", data: yrs },
          yAxis: { type: "value", axisLabel: { formatter: function (v) { return F.pct0(v, 0); } } },
          series: [{ type: "bar", barWidth: "55%",
            data: yv.map(function (v) { return { value: v, itemStyle: { color: v >= 0 ? T.up : T.dn } }; }),
            label: { show: true, position: "top", color: T.ink2, fontSize: 11, fontFamily: T.mono, formatter: function (p) { return F.pct(p.value, 1); } } }]
        });
      }

      // corrélations des jambes
      var cm = sum.corr || {}, names = Object.keys(cm), cells = [];
      names.forEach(function (a, i) { names.forEach(function (b, k) {
        var v = cm[a] ? cm[a][b] : null, x = CC.isNum(v) ? Number(v) : 0;
        // texte sombre sur les cases saturées (|corr| > 0,5), clair ailleurs : contraste ≥ 4,5:1
        cells.push({ value: [k, i, x], label: { color: Math.abs(x) > 0.5 ? T.bg : T.ink } });
      }); });
      var coEl = $("pf-corr-ch");
      setAsOf("pf-corr", new Date().toISOString());
      if (!names.length) {
        coEl.innerHTML = '<div class="chart-na">aucune matrice de corrélation</div>';
      } else {
        if (coEl.firstChild && coEl.firstChild.className === "chart-na") coEl.innerHTML = "";
        CC.chart(coEl, {
          tooltip: { formatter: function (p) { return esc(names[p.value[1]]) + " × " + esc(names[p.value[0]]) + " : " + esc(F.num(p.value[2], 3)); } },
          grid: { left: 66, right: 14, top: 8, bottom: 28 },
          xAxis: { type: "category", data: names, splitArea: { show: false } },
          yAxis: { type: "category", data: names, splitLine: { show: false } },
          visualMap: { min: -1, max: 1, show: false, inRange: { color: [T.dn, T.line, T.up] } },
          series: [{ type: "heatmap", data: cells, label: { show: true, color: T.ink, fontSize: 12, fontFamily: T.mono, formatter: function (p) { return F.num(p.value[2], 2); } },
            itemStyle: { borderColor: T.bg2, borderWidth: 2 } }]
        });
      }
    } catch (e) { fail("pf-bt", e); }
  }

  CC.register({
    key: "portfolio", code: "PORT", title: "Portefeuille", icon: "▤", refreshMs: 30000,
    init: init,
    refresh: refresh,
    onShow: function () { if (S.root) CC.resizeCharts(S.root); }
  });
})();
