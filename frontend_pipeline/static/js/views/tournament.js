/* Vue TOUR — Tournoi ALPHA_20, ARRÊTÉ le 03/09/2026 (lecture seule).
   Port de tournamentLive()/tournamentSelection() de command_center.html : mêmes endpoints,
   mêmes calculs, présentation terminal. Aucun ordre réel n'est jamais parti : capital virtuel.
   Endpoints : /api/tournament/live (lent, plusieurs secondes), /api/tournament/selection,
   /api/tournament/events?limit=40. Poll 5 min. */
(function () {
  "use strict";

  var CC = window.CC;
  var esc = (CC && CC.esc) || function (s) {
    return s == null ? "" : String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var F = CC.fmt;
  var DASH = "—";

  var STOPPED_AT = "2026-09-03T17:35:00+02:00";           // 17:35 CEST
  var STOPPED_LABEL = "03/09/2026 17:35 CEST";
  var EVENTS_LIMIT = 40;

  // statut de sélection → classe de badge (port de TN_SEL_CLASS)
  var SEL_KIND = {
    ELIGIBLE: "up", SELECTED_PROVISIONAL: "up", SELECTED_CONFIRMED: "up",
    OBSERVING: "muted", FRAGILE: "warn", REJECTED: "dn", INELIGIBLE: "dn"
  };
  var SEL_TITLE = {
    ELIGIBLE: "éligible à la sélection", SELECTED_PROVISIONAL: "sélectionné (provisoire)", SELECTED_CONFIRMED: "sélectionné (confirmé)",
    OBSERVING: "en observation", FRAGILE: "fragile : tests de robustesse non concluants", REJECTED: "rejeté", INELIGIBLE: "inéligible"
  };
  var RISK_TITLE = { risk_on: "risque normal", risk_reduced: "risque réduit", cash: "en cash", kill: "kill switch" };
  var KIND_CLS = { decision: "info", reject: "dn", funding: "up", fill: "amber" };

  // état de la vue
  var S = { root: null, live: null, sel: null, events: null, seq: 0, cardsHtml: "", selHtml: "", evHtml: "" };

  function isNum(v) { return CC.isNum ? CC.isNum(v) : (typeof v === "number" && isFinite(v)); }
  function usdt(v, digits, signed) {
    if (!isNum(v)) return DASH;
    return (signed ? F.snum(v, digits) : F.num(v, digits)) + " USDT";
  }
  function byId(id) { return S.root ? S.root.querySelector("#" + id) : null; }
  function setBody(id, html) { var el = byId(id); if (el && el.innerHTML !== html) el.innerHTML = html; }
  function panelEl(id) { var b = byId(id); return b ? b.closest(".panel") : null; }
  function setLed(id, state) {
    var p = panelEl(id); if (!p) return;
    var h = p.querySelector(".panel-h"), led = h ? h.querySelector(".led") : null;
    if (led) led.className = "led " + (CC.freshCls ? CC.freshCls(state) : state);
  }
  function setAsOf(id, iso, state, title) {
    var p = panelEl(id); if (!p) return;
    CC.setAsOf(p, iso, state);
    if (title) { var a = p.querySelector(".asof"); if (a) a.setAttribute("title", title); }
  }
  function unavailable(msg) {
    return '<div class="muted small">indisponible : ' + esc(msg || "erreur inconnue") + "</div>";
  }
  function errMsg(e) { return (e && e.message ? String(e.message) : String(e || "erreur")).slice(0, 200); }

  // ── style spécifique (tokens uniquement) ─────────────────────────────────
  function injectCss() {
    if (document.getElementById("css-tournament")) return;
    var st = document.createElement("style");
    st.id = "css-tournament";
    st.textContent =
      "#v-tournament .tgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:8px}" +
      "#v-tournament .tcard .spark{height:34px;margin-top:6px}" +
      "#v-tournament .tcard .tmeta{font-size:11px;color:var(--ink3);margin-top:6px;white-space:normal}" +
      "#v-tournament .tcard .tdec{font-size:11.5px;color:var(--ink2);margin-top:6px;line-height:1.4;white-space:normal;overflow-wrap:anywhere}" +
      "#v-tournament .tcard .tpnl{font-size:12.5px;margin-top:2px}" +
      "#v-tournament .tcard .unit{font-size:13px;color:var(--ink3);font-weight:400}" +
      "#v-tournament .stopbanner{color:var(--amber);font-weight:600;font-size:14px}" +
      "#v-tournament .stopbanner .sub{color:var(--ink2);font-weight:400;font-size:12.5px;margin-top:4px;line-height:1.4}" +
      "#v-tournament .tbl td.reason{white-space:normal;color:var(--ink3);font-size:11px;min-width:220px}";
    document.head.appendChild(st);
  }

  // ── squelette statique (rendu une seule fois) ─────────────────────────────
  function skeleton() {
    var banner = CC.panel({
      code: "TOUR", title: "Tournoi ALPHA_20 — arrêté le " + STOPPED_LABEL + " — lecture seule", asOf: STOPPED_AT, fresh: "stopped", cls: "hl",
      body: '<div class="stopbanner">TOUR ▸ ARRÊTÉ le ' + esc(STOPPED_LABEL) + ' — lecture seule, dernier état des ledgers'
        + '<div class="sub">Les runners (dont carry_basis_v12) ne prennent plus aucune décision depuis l\'arrêt des timers futur-alpha20-* : '
        + 'seul le Live Alpha Lab (vue F2 LAB) fait encore du paper trading, en shadow, capital virtuel, aucun ordre réel.</div></div>'
    });
    var runners = CC.panel({
      code: "RUN", title: "Runners — dernier état des ledgers (comptes isolés, capital virtuel)", fresh: "unknown",
      body: '<div id="tn-runners"><div class="muted small">chargement… (/api/tournament/live relit les ledgers : plusieurs secondes)</div></div>'
    });
    var sel = CC.panel({
      code: "SEL", title: "Sélection mécanique (figée à l'avance)", fresh: "unknown",
      tools: '<span id="tn-verdict"></span>',
      body: '<div id="tn-sel"><div class="muted small">chargement…</div></div>'
    });
    var ev = CC.panel({
      code: "EVT", title: "Fil d'événements — tous runners (" + EVENTS_LIMIT + " derniers)", fresh: "unknown",
      tools: '<span id="tn-evcount" class="muted small"></span>',
      body: '<div id="tn-events"><div class="muted small">chargement…</div></div>'
    });
    return '<div class="grid">'
      + '<div class="c12">' + banner + "</div>"
      + '<div class="c12">' + runners + "</div>"
      + '<div class="c4">' + sel + "</div>"
      + '<div class="c8">' + ev + "</div>"
      + "</div>"
      + '<div class="view-fn">Tournoi ALPHA_20 : chaque runner disposait d\'un compte isolé de 200 000 USDT virtuels, même bus de marché, même broker paper, ledger append-only séparé. '
      + 'Sélection mécanique figée à l\'avance — aucun runner n\'est déclaré rentable avant réconciliation + tests de robustesse. '
      + 'Arrêté le ' + esc(STOPPED_LABEL) + ' : les chiffres ci-dessus sont le dernier état des ledgers et n\'évoluent plus. Capital virtuel, aucun ordre réel.</div>';
  }

  // ── cartes runners (port de tnCard) ───────────────────────────────────────
  function riskKind(rs) { return rs === "kill" ? "dn" : (rs === "cash" || rs === "risk_reduced") ? "warn" : "up"; }
  function card(r) {
    r = r || {};
    var statuses = (S.sel && S.sel.statuses) || {};
    var sel = statuses[r.runner_id] || {};
    var selBadge = sel.status ? CC.badge(sel.status, SEL_KIND[sel.status] || "muted", SEL_TITLE[sel.status] || "statut de sélection") : "";
    var stBadge = r.status ? CC.badge(r.status, r.status === "ACTIVE" ? "muted" : "warn", "statut du runner dans le registre (avant l'arrêt)") : "";
    var pnlCls = CC.cls(r.pnl_usdt);
    var vals = (r.curve || []).map(function (p) { return p && p.v; });
    var lastT = null;
    for (var i = (r.curve || []).length - 1; i >= 0; i--) { if (r.curve[i] && r.curve[i].t) { lastT = r.curve[i].t; break; } }
    var rec = r.reconciliation || {};
    var recLed = rec.status === "evaluated" && rec.passed ? "fresh" : (rec.status === "invalid_ledger" ? "error" : "unknown");
    var ld = r.last_decision;
    var ldTxt = ld
      ? "<b>" + esc(ld.sleeve || DASH) + "</b> " + esc(ld.signal || "") + (ld.reason ? " — " + esc(ld.reason) : "")
        + (ld.ts ? ' <span class="muted">(' + esc(F.dt(ld.ts)) + ")</span>" : "")
      : '<span class="muted">aucune décision enregistrée</span>';
    var assets = Array.isArray(r.assets) && r.assets.length ? r.assets.join(", ") : DASH;
    return '<section class="panel tile tcard">'
      + '<header class="panel-h"><span class="code" title="identifiant du runner">' + esc(r.runner_id || DASH) + "</span>"
      + '<span class="ttl" title="famille : ' + esc(r.family || "") + ' · actifs : ' + esc(assets) + '">' + esc(r.family || DASH) + " · " + esc(assets) + "</span>"
      + '<span class="asof" title="dernier point de la courbe NAV ' + esc(F.dtfull(lastT)) + '">' + (lastT ? "as of " + esc(F.dt(lastT)) : "") + "</span>"
      + "</header>"
      + '<div class="panel-b">'
      + '<div class="row spread"><span class="lbl" title="valeur liquidative du compte isolé (USDT virtuels)">NAV</span>'
      + '<span class="row" style="gap:4px">' + stBadge + selBadge + "</span></div>"
      + '<div class="big-sm">' + (isNum(r.nav_usdt) ? esc(F.num(r.nav_usdt, 0)) + ' <span class="unit">USDT</span>' : DASH) + "</div>"
      + '<div class="tpnl ' + pnlCls + '" title="P&amp;L depuis le départ, USDT virtuels">' + (pnlCls === "up" ? "▲ " : pnlCls === "dn" ? "▼ " : "") + esc(usdt(r.pnl_usdt, 2, true)) + " (" + esc(F.pct(r.pnl_pct, 3)) + ")</div>"
      + CC.spark(null, vals, { h: 34, w: 170 })
      + '<div class="tmeta"><span title="drawdown courant depuis le plus haut">DD ' + esc(F.pct0(r.drawdown, 2)) + "</span> · "
      + CC.badge(r.risk_state || DASH, riskKind(r.risk_state), RISK_TITLE[r.risk_state] || "état de risque") + " · "
      + CC.led(recLed, "réconciliation ledger ↔ broker paper : " + (rec.status || "inconnue")) + " réconciliation " + esc(rec.status || DASH)
      + (rec.consecutive_ok ? " (" + esc(F.int(rec.consecutive_ok)) + " ✓ consécutifs)" : "") + "</div>"
      + '<div class="tdec" title="dernière décision du runner">' + ldTxt + "</div>"
      + '<div class="tmeta">' + esc(F.int(r.n_decisions)) + " décisions · " + esc(F.int(r.n_fills)) + " fills · " + esc(F.int(r.n_rejects)) + " rejets · âge " + esc(F.days(r.age_days, 1)) + "</div>"
      + "</div></section>";
  }
  function renderCards() {
    if (!S.live) return;
    var runners = Array.isArray(S.live.runners) ? S.live.runners : [];
    var html = runners.length
      ? '<div class="tgrid">' + runners.map(card).join("") + "</div>"
      : '<div class="muted small">aucun runner dans le registre (ledgers vides)</div>';
    if (html !== S.cardsHtml) { S.cardsHtml = html; setBody("tn-runners", html); }
  }

  // ── sélection ─────────────────────────────────────────────────────────────
  function renderSelection() {
    if (!S.sel) return;
    var d = S.sel;
    var statuses = d.statuses || {};
    var rows = Object.keys(statuses).map(function (k) {
      var s = statuses[k] || {};
      return { runner_id: k, status: s.status, phase: s.phase, reasons: Array.isArray(s.reasons) ? s.reasons : [] };
    });
    var v = byId("tn-verdict");
    if (v) {
      var verdict = d.verdict || DASH;
      v.innerHTML = CC.badge("verdict " + verdict, verdict === "NO_SELECTION" ? "muted" : "up",
        verdict === "NO_SELECTION" ? "aucun runner sélectionné à l'arrêt" : "verdict de sélection à l'arrêt");
    }
    var selected = Array.isArray(d.selected) ? d.selected : [];
    var html = CC.table({
      cols: [
        { k: "runner_id", label: "runner" },
        { k: "status", label: "statut", fmt: function (x) { return x ? CC.badge(x, SEL_KIND[x] || "muted", SEL_TITLE[x] || "") : DASH; } },
        { k: "phase", label: "phase", title: "observation → sélection" },
        { k: "reasons", label: "raisons", fmt: function (x) { return x && x.length ? '<span class="muted small">' + esc(x.join(", ")) + "</span>" : '<span class="muted">—</span>'; }, cls: "reason" }
      ],
      rows: rows, empty: "aucun statut de sélection", maxH: "320px"
    }) + '<div class="fn">sélectionnés : ' + (selected.length ? esc(selected.join(", ")) : "aucun") + " · " + F.int(rows.length) + " runners évalués</div>";
    if (html !== S.selHtml) { S.selHtml = html; setBody("tn-sel", html); }
  }

  // ── événements ────────────────────────────────────────────────────────────
  function renderEvents() {
    if (!S.events) return;
    var evs = Array.isArray(S.events.events) ? S.events.events : [];
    var c = byId("tn-evcount");
    if (c) c.textContent = F.int(evs.length) + " récents";
    var html = CC.table({
      cols: [
        { k: "ts", label: "heure", fmt: function (x) { return '<span title="' + esc(F.dtfull(x)) + '">' + esc(F.dt(x)) + "</span>"; }, title: "heure locale de l'événement" },
        { k: "runner_id", label: "runner" },
        { k: "kind", label: "type", fmt: function (x) { return x ? CC.badge(x, KIND_CLS[x] || "muted") : DASH; } },
        { k: "sleeve", label: "sleeve", title: "sous-livre / actif visé" },
        { k: "amount_usdt", label: "montant", align: "r", fmt: function (x) { return esc(usdt(x, 4, true)); }, cls: function (x) { return CC.cls(x); }, title: "montant en USDT virtuels" },
        { k: function (e) { return (e.signal || "") + (e.reason ? (e.signal ? " — " : "") + e.reason : ""); }, label: "motif", fmt: function (x) { return x ? esc(x) : '<span class="muted">—</span>'; }, cls: "reason" }
      ],
      rows: evs, empty: "aucun événement dans les ledgers", maxH: "520px"
    });
    if (html !== S.evHtml) { S.evHtml = html; setBody("tn-events", html); }
  }

  // ── chargeurs indépendants (un endpoint en échec ne dégrade que son panneau) ──
  async function loadLive(seq) {
    try {
      var d = await CC.j("/api/tournament/live");
      if (seq !== S.seq) return;
      S.live = d && typeof d === "object" ? d : { runners: [] };
      renderCards();
      setAsOf("tn-runners", S.live.ts, "stopped", "lecture des ledgers " + F.dtfull(S.live.ts) + " · tournoi arrêté");
    } catch (e) {
      if (seq !== S.seq) return;
      if (e && /401/.test(e.message)) return;
      if (!S.live) { S.cardsHtml = ""; setBody("tn-runners", unavailable(errMsg(e))); }
      else CC.toast("TOUR runners : " + errMsg(e), "warn", 4000);
      setLed("tn-runners", "error");
    }
  }
  async function loadSelection(seq) {
    try {
      var d = await CC.j("/api/tournament/selection");
      if (seq !== S.seq) return;
      S.sel = d && typeof d === "object" ? d : {};
      renderSelection();
      renderCards();   // badges de sélection sur les cartes
      var now = new Date().toISOString();
      setAsOf("tn-sel", now, "stopped", "heure de lecture du fichier de sélection (figé à l'arrêt)");
    } catch (e) {
      if (seq !== S.seq) return;
      if (e && /401/.test(e.message)) return;
      if (!S.sel) { S.selHtml = ""; setBody("tn-sel", unavailable(errMsg(e))); }
      setLed("tn-sel", "error");
    }
  }
  async function loadEvents(seq) {
    try {
      var d = await CC.j("/api/tournament/events?limit=" + EVENTS_LIMIT);
      if (seq !== S.seq) return;
      S.events = d && typeof d === "object" ? d : { events: [] };
      renderEvents();
      var evs = Array.isArray(S.events.events) ? S.events.events : [];
      setAsOf("tn-events", evs.length ? evs[0].ts : null, "stopped", "dernier événement " + F.dtfull(evs.length ? evs[0].ts : null) + " · tournoi arrêté");
    } catch (e) {
      if (seq !== S.seq) return;
      if (e && /401/.test(e.message)) return;
      if (!S.events) { S.evHtml = ""; setBody("tn-events", unavailable(errMsg(e))); }
      setLed("tn-events", "error");
    }
  }

  CC.register({
    key: "tournament", code: "TOUR", title: "Tournoi (arrêté)", icon: "▦", refreshMs: 300000,
    init: function (el) {
      S.root = el;
      injectCss();
      el.innerHTML = skeleton();
    },
    refresh: function () {
      if (!S.root) return Promise.resolve();
      var seq = ++S.seq;
      // les trois chargements sont indépendants ; aucun ne peut lever (chacun dégrade son panneau)
      return Promise.all([loadSelection(seq), loadEvents(seq), loadLive(seq)]).then(function () { return null; });
    }
  });
})();
