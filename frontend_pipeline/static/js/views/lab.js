/* Vue LAB — Live Alpha Lab (shadow : capital virtuel 200 000 € par portefeuille, aucun ordre réel).
   Cycle systemd 15 min : producteurs de signaux → 5 portefeuilles shadow, mark-to-market réel.
   Sources : /api/status (lab), /api/lab/cycles, /api/lab/portfolios, /api/lab/marks,
             /api/lab/portfolio/{name}/history, /api/lab/portfolio/{name}/positions.
   Script classique, dépend de window.CC (core.js). Chaque panneau dégrade seul en cas d'erreur. */
(function () {
  "use strict";
  var CC = window.CC;
  if (!CC) { console.error("[lab] CC absent"); return; }
  var esc = CC.esc;
  var F = CC.fmt;
  var T = CC.tokens;
  var DASH = "—";

  var CAP_DEFAULT = 200000;
  var TIMER_MIN_DEFAULT = 15;
  var PF_NAMES = ["P1_EQUAL_RISK", "P1_CONTROL", "P1_VOL_OVERLAY", "P2_DIVERSIFIED", "P3_ALL_CANDIDATES"];
  var RULES = {
    P1_EQUAL_RISK: "budget égal par famille",
    P1_CONTROL: "idem, témoin",
    P1_VOL_OVERLAY: "idem + overlay vol sur le sizing",
    P2_DIVERSIFIED: "1 moteur dominant max par cluster de corrélation",
    P3_ALL_CANDIDATES: "5 % du capital par alpha"
  };
  var LABELS = {
    VALIDATED_FORWARD: { kind: "up", title: "validé indépendamment (forward)" },
    EXPERIMENTAL_SHADOW: { kind: "warn", title: "tourne en shadow, preuve en cours" },
    OVERLAY: { kind: "info", title: "overlay : pas une position" },
    GATE: { kind: "info", title: "gate : pas une position" },
    NO_CAPITAL: { kind: "dn", title: "collecte seule, aucun capital" }
  };
  var FILLS_MAX = 40;
  var CYCLES_TABLE_MAX = 10;
  var CYCLES_STRIP_MAX = 30;

  var root = null;
  var lastNames = PF_NAMES.slice();

  // ── utilitaires locaux ────────────────────────────────────────────────────
  function byId(id) { return root ? root.querySelector("#" + id) : document.getElementById(id); }
  function isNum(v) { return CC.isNum(v); }
  function n(v) { return typeof v === "number" ? v : Number(v); }
  function dur(sec) {
    if (!isNum(sec)) return DASH;
    var s = Math.round(n(sec));
    if (s < 60) return s + " s";
    var m = Math.floor(s / 60), r = s % 60;
    return m + " min " + (r < 10 ? "0" : "") + r + " s";
  }
  function shortAlpha(a) { return a == null ? DASH : String(a).replace(/_V(\d+)$/, " v$1").replace(/_/g, " ").toLowerCase(); }
  function failedList(v) {
    if (Array.isArray(v)) return v.map(function (x) { return x && typeof x === "object" ? String(x.name || x.alpha_id || JSON.stringify(x)) : String(x); });
    if (isNum(v) && n(v) > 0) return [n(v) + " producteur(s) (noms non fournis)"];
    return [];
  }
  function statusKind(st, failed) {
    if (st == null) return "muted";
    var s = String(st).toUpperCase();
    if (s === "OK") return failed.length ? "warn" : "up";
    if (s === "PARTIAL" || s === "WARN" || s === "STALE") return "warn";
    return "dn";
  }
  function labelBadge(l) {
    if (l == null || l === "") return DASH;
    var m = LABELS[l] || { kind: "muted", title: "" };
    return CC.badge(String(l), m.kind, m.title);
  }
  function panelEl(id) { return byId(id); }
  function setErr(id, e) {
    var p = panelEl(id); if (!p) return;
    var box = p.querySelector(".lab-err");
    var msg = (e && e.message) ? String(e.message) : String(e || "erreur");
    if (/401/.test(msg)) return;                         // redirection gérée par core
    if (box) { box.textContent = "indisponible : " + msg.slice(0, 200); box.hidden = false; }
    p.classList.add("dim");
    CC.setAsOf(p, null, "error");
  }
  function clearErr(id, asOf, fresh) {
    var p = panelEl(id); if (!p) return;
    var box = p.querySelector(".lab-err");
    if (box) { box.hidden = true; box.textContent = ""; }
    p.classList.remove("dim");
    if (asOf !== undefined) CC.setAsOf(p, asOf, fresh);
  }
  async function get(url) {
    try { return { ok: true, data: await CC.j(url) }; }
    catch (e) { return { ok: false, error: e }; }
  }
  function labFresh(lab) {
    if (!lab) return "unknown";
    if (lab.live) return "fresh";
    var s = lab.status ? String(lab.status).toUpperCase() : "";
    return s === "OK" ? "stale" : (s ? "error" : "unknown");
  }

  // ── squelette ─────────────────────────────────────────────────────────────
  function css() {
    if (document.getElementById("css-lab")) return;
    var st = document.createElement("style");
    st.id = "css-lab";
    st.textContent =
      ".lab-err{color:var(--dn);font-size:12px;border:1px solid var(--dn);padding:4px 8px;margin-bottom:8px;border-radius:var(--r)}" +
      ".lab-cyc{display:grid;grid-template-columns:minmax(300px,2fr) minmax(0,3fr);gap:12px;align-items:stretch}" +
      ".lab-tiles{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px 12px}" +
      ".lab-tiles .v{font-size:18px;font-weight:600;font-variant-numeric:tabular-nums;white-space:nowrap;line-height:1.2}" +
      ".lab-tiles .v.sm{font-size:13px;font-weight:400;white-space:normal;color:var(--ink2)}" +
      ".lab-strip{height:96px;min-height:80px}" +
      ".lab-bar{display:block;height:4px;background:var(--bg4);margin-top:2px;min-width:80px}" +
      ".lab-bar i{display:block;height:100%}" +
      ".lab-bar.fwd i{background:var(--amber)}.lab-bar.rep i{background:var(--ink3)}" +
      ".lab-barlbl{display:block;font-size:10.5px;color:var(--ink3);white-space:nowrap;margin-top:2px}" +
      ".lab-sparks{margin-top:8px}" +
      ".lab-sparks .panel-b{padding:6px 8px}" +
      ".lab-sparks .spark{height:44px}" +
      ".lab-legend{margin-top:6px}" +
      "#lab-roster-tbl td{padding:5px 6px}#lab-roster-tbl td .sub{white-space:normal;max-width:260px}" +
      "@media (max-width:1100px){.lab-cyc{grid-template-columns:1fr}}" +
      "@media (max-width:720px){.lab-tiles{grid-template-columns:repeat(2,minmax(0,1fr))}.lab-tiles .v{font-size:16px}}";
    document.head.appendChild(st);
  }
  function errBox() { return '<div class="lab-err" hidden></div>'; }

  function init(el) {
    root = el;
    css();
    var cycleBody =
      errBox() +
      '<div class="lab-cyc">' +
      '<div class="lab-tiles" id="lab-cycle-kv">' +
      tile("état", '<span class="v" id="lc-state">' + CC.led("unknown") + " chargement…</span>", "lc-state-sub") +
      tile("dernier cycle", '<span class="v" id="lc-last">' + DASH + "</span>", "lc-last-sub") +
      tile("durée", '<span class="v" id="lc-dur">' + DASH + "</span>", "lc-dur-sub") +
      tile("producteurs ok / lancés", '<span class="v" id="lc-prod">' + DASH + "</span>", "lc-prod-sub") +
      tile("en échec", '<span class="v sm" id="lc-failed">' + DASH + "</span>", "lc-failed-sub") +
      tile("prochain cycle (théorique)", '<span class="v" id="lc-next">' + DASH + "</span>", "lc-next-sub") +
      "</div>" +
      '<div><div class="lbl">' + CYCLES_STRIP_MAX + " derniers cycles — durée (s), couleur = statut</div>" +
      '<div id="lab-strip" class="lab-strip"><div class="chart-na">chargement…</div></div></div>' +
      "</div>" +
      '<div class="lbl mt">' + CYCLES_TABLE_MAX + " derniers cycles</div>" +
      '<div id="lab-cycles-tbl">' + CC.table({ cols: cycleCols(), rows: [], empty: "chargement…" }) + "</div>";

    var pfBody =
      errBox() +
      '<div id="lab-pf-tbl">' + CC.table({ cols: pfCols(), rows: [], empty: "chargement…" }) + "</div>" +
      '<div class="tiles lab-sparks" id="lab-sparks">' + PF_NAMES.map(function (nm) { return sparkTile(nm, null, null); }).join("") + "</div>" +
      '<div class="fn">Capital virtuel <b>' + esc(F.eur0(CAP_DEFAULT)) + "</b> par portefeuille, marqué au prix réel à chaque cycle (15 min). Aucun ordre réel. " +
      "P&amp;L = equity − capital, frais et funding déduits. Drawdown = repli depuis le plus haut d'equity. Sparkline : equity depuis l'ouverture, pointillé = capital.</div>";

    var rosterBody =
      errBox() +
      '<div id="lab-roster-tbl">' + CC.table({ cols: rosterCols(false), rows: [], empty: "chargement…" }) + "</div>" +
      '<div class="fn lab-legend">' +
      labelBadge("VALIDATED_FORWARD") + " validé indépendamment · " +
      labelBadge("EXPERIMENTAL_SHADOW") + " tourne, preuve en cours · " +
      labelBadge("OVERLAY") + " / " + labelBadge("GATE") + " pas une position · " +
      labelBadge("NO_CAPITAL") + " collecte seule, aucun capital.<br>" +
      "<b>forward</b> = décisions émises après le gel (freeze) de l'alpha, jamais vues à la conception : seule preuve qui compte. " +
      "<b>replay</b> = décisions rejouées sur l'historique (évidence de conception, pas de preuve). Barres en échelle log.</div>";

    var marksBody = errBox() + '<div id="lab-marks-tbl">' + CC.table({ cols: marksCols(), rows: [], empty: "chargement…" }) + "</div>" +
      '<div class="fn">Dernier mark par instrument détenu (source : mark_price des dérivés, clôture quotidienne pour les trimestriels). Âge = ancienneté du mark.</div>';

    var fillsBody = errBox() + '<div id="lab-fills-tbl">' + CC.table({ cols: fillCols(), rows: [], empty: "chargement…" }) + "</div>" +
      '<div class="fn">Fusion des <code>fills_recent</code> des 5 portefeuilles (dédoublonnés par identifiant d\'ordre), ' + FILLS_MAX + " plus récents. Fills simulés au mark du cycle, frais modélisés — aucun ordre réel.</div>";

    el.innerHTML =
      '<div class="grid">' +
      CC.panel({ id: "lab-cycle", cls: "c12", code: "LAB ▸ CYCLE", title: "Live Alpha Lab — cycle systemd toutes les 15 min, producteurs de signaux → 5 portefeuilles shadow", fresh: "unknown", body: cycleBody }) +
      "</div>" +
      '<div class="grid">' +
      CC.panel({ id: "lab-pf", cls: "c12", code: "LAB ▸ PORTEFEUILLES", title: "5 en parallèle — mêmes signaux, règles d'allocation différentes (capital virtuel)", fresh: "unknown", body: pfBody }) +
      "</div>" +
      '<div class="grid">' +
      CC.panel({ id: "lab-roster", cls: "c12", code: "LAB ▸ ROSTER", title: "alphas en shadow — statut honnête", fresh: "unknown", body: rosterBody }) +
      "</div>" +
      '<div class="grid">' +
      CC.panel({ id: "lab-marks", cls: "c4", code: "LAB ▸ MARKS", title: "derniers marks exposés", fresh: "unknown", body: marksBody }) +
      CC.panel({ id: "lab-fills", cls: "c8", code: "LAB ▸ FILLS RÉCENTS", title: "tous portefeuilles, plus récents d'abord", fresh: "unknown", body: fillsBody }) +
      "</div>" +
      '<div class="view-fn">Tout est paper/shadow : capital virtuel, aucun ordre réel. ' +
      "<b>Forward vs replay</b> : seules les décisions émises après le <code>freeze_timestamp</code> d'un alpha comptent comme preuve jamais vue ; " +
      "les décisions replay sont l'évidence de conception et ne valident rien. Un alpha n'est promu qu'après validation indépendante forward.</div>";
  }
  function tile(lbl, valueHtml, subId) {
    return '<div><div class="lbl">' + esc(lbl) + "</div>" + valueHtml + '<div class="sub small muted" id="' + esc(subId) + '"></div></div>';
  }
  function sparkTile(name, p, h) {
    var eq = p ? p.equity : null;
    var pnl = p ? p.pnl_eur : null;
    var hist = (h && h.history) || [];
    var base = h && isNum(h.capital_eur) ? n(h.capital_eur) : CAP_DEFAULT;
    var vals = hist.map(function (x) { return x ? x.v : null; });
    return '<section class="panel tile" id="lab-sp-' + esc(name) + '"><div class="panel-b">' +
      '<div class="row spread"><span class="lbl" title="' + esc(RULES[name] || "") + '">' + esc(name) + '</span><span class="small muted">' + esc(hist.length ? hist.length + " pts" : "") + "</span></div>" +
      '<div class="row-b spread"><span class="big-sm">' + esc(F.eur0(eq)) + '</span><span class="' + CC.cls(pnl) + '">' + esc(F.seur0(pnl)) + "</span></div>" +
      CC.spark(null, vals, { base: base, h: 44, w: 240 }) +
      '<div class="small muted">' + (hist.length ? "du " + esc(F.dt(hist[0].t)) + " au " + esc(F.dt(hist[hist.length - 1].t)) : "historique indisponible") + "</div>" +
      "</div></section>";
  }

  // ── colonnes ──────────────────────────────────────────────────────────────
  function cycleCols() {
    return [
      { k: "finished_at", label: "fin", fmt: function (v) { return esc(F.dt(v)); } },
      { k: "started_at", label: "début", fmt: function (v) { return esc(F.hhmmss(v)); } },
      { k: "duration_sec", label: "durée", align: "r", fmt: function (v) { return esc(dur(v)); } },
      { k: "status", label: "statut", fmt: function (v, r) { return v == null ? DASH : CC.badge(String(v), statusKind(v, failedList(r.producers_failed))); } },
      { k: function (r) { return r; }, label: "producteurs ok / lancés", align: "r", fmt: function (r) { return esc(F.int(r.producers_ok)) + " / " + esc(F.int(r.producers_run)); } },
      { k: "producers_failed", label: "en échec", fmt: function (v) { var l = failedList(v); return l.length ? '<span class="dn">' + esc(l.join(", ")) + "</span>" : '<span class="muted">aucun</span>'; } }
    ];
  }
  function pfCols() {
    return [
      { k: "name", label: "portefeuille", fmt: function (v) { return "<b>" + esc(v) + "</b>"; } },
      { k: "name", label: "règle d'allocation", fmt: function (v, r) { return '<span class="wrap">' + esc(RULES[v] || DASH) + (r._overlay != null ? ' <span class="muted">(× ' + esc(F.num(r._overlay, 3)) + " actuel)</span>" : "") + "</span>"; } },
      { k: "equity", label: "equity", align: "r", fmt: function (v) { return "<b>" + esc(F.eur0(v)) + "</b>"; } },
      { k: "pnl_eur", label: "P&L €", align: "r", cls: function (v) { return CC.cls(v); }, fmt: function (v) { return esc(F.seur0(v)); } },
      { k: "pnl_pct", label: "P&L %", align: "r", cls: function (v) { return CC.cls(v); }, fmt: function (v) { return esc(F.pct(v, 2)); } },
      { k: "gross_exposure", label: "gross", title: "exposition brute (somme des notionnels)", align: "r", fmt: function (v) { return esc(F.eur0(v)); } },
      { k: "n_positions", label: "positions", align: "r", fmt: function (v) { return esc(F.int(v)); } },
      { k: "fees", label: "frais", align: "r", fmt: function (v) { return esc(F.eur0(v)); } },
      { k: "funding", label: "funding", align: "r", cls: function (v) { return CC.cls(v); }, fmt: function (v) { return esc(F.seur2(v)); } },
      { k: "drawdown", label: "drawdown", align: "r", cls: function (v) { return isNum(v) && n(v) < 0 ? "dn" : "flat"; }, fmt: function (v) { return esc(F.pct(v, 2)); } },
      { k: "since", label: "depuis", align: "r", fmt: function (v) { return esc(F.dt(v)); } },
      { k: "status", label: "statut", fmt: function (v) { return v == null ? DASH : CC.badge(String(v), statusKind(v, [])); } }
    ];
  }
  function freqOf(r) {
    if (!r) return null;
    var keys = ["actual_freq_per_day", "actual_freq_day", "actual_freq", "freq_per_day", "decisions_per_day"];
    for (var i = 0; i < keys.length; i++) if (isNum(r[keys[i]])) return n(r[keys[i]]);
    return null;
  }
  function rosterCols(withFreq) {
    var cols = [
      { k: "alpha_id", label: "alpha", fmt: function (v, r) { return "<b>" + esc(v) + "</b>" + '<span class="sub">' + esc([r.risk_bucket, r.correlation_family].filter(Boolean).join(" · ") || " ") + "</span>"; } },
      { k: "family", label: "famille" },
      { k: "scientific_status", label: "statut scientifique", title: "statut de la preuve (FROZEN = spec gelée)" },
      { k: "operational_status", label: "statut opérationnel", fmt: function (v, r) { return (v == null ? DASH : esc(v)) + (r.role ? '<span class="sub">rôle ' + esc(r.role) + "</span>" : ""); } },
      { k: "label", label: "label", fmt: function (v) { return labelBadge(v); } },
      { k: function (r) { return r; }, label: "forward / replay", title: "décisions forward (après freeze) / décisions replay (historique) — barres en échelle log", fmt: function (r, _r, ctx) { return fwdBars(r); } },
      { k: "confidence", label: "confiance", title: "confiance forward · épisodes indépendants (dédoublonnés)", fmt: function (v, r) { return (v == null ? DASH : esc(v)) + '<span class="sub">' + (isNum(r.independent_episodes) ? esc(F.int(r.independent_episodes)) + " épisode(s) indép." : "épisodes : " + DASH) + "</span>"; } },
      { k: "freeze_timestamp", label: "freeze", title: "date de gel de la spec · dernier signal émis", align: "r", fmt: function (v, r) { return esc(F.date(v)) + '<span class="sub">' + (isNum(r.last_trigger_h_ago) ? "signal il y a " + esc(F.h(r.last_trigger_h_ago, 0)) : "aucun signal forward") + "</span>"; } }
    ];
    if (withFreq) cols.splice(6, 0, { k: function (r) { return freqOf(r); }, label: "fréq. / j", title: "fréquence réelle de décisions par jour", align: "r", fmt: function (v) { return esc(F.num(v, 2)); } });
    return cols;
  }
  var fwdMax = 1;
  function logw(v) {
    if (!isNum(v) || n(v) <= 0) return 0;
    var w = Math.log10(1 + n(v)) / Math.log10(1 + fwdMax) * 100;
    return Math.max(2, Math.min(100, w));
  }
  function fwdBars(r) {
    var f = r.forward_decisions, p = r.replay_decisions;
    return '<span class="lab-bar fwd" title="forward : ' + esc(F.int(f)) + '"><i style="width:' + logw(f).toFixed(1) + '%"></i></span>' +
      '<span class="lab-bar rep" title="replay : ' + esc(F.int(p)) + '"><i style="width:' + logw(p).toFixed(1) + '%"></i></span>' +
      '<span class="lab-barlbl"><span class="amber">' + esc(F.int(f)) + " fwd</span> · " + esc(F.int(p)) + " replay</span>";
  }
  function marksCols() {
    return [
      { k: "instrument", label: "instrument", fmt: function (v) { return "<b>" + esc(v) + "</b>"; } },
      { k: "price", label: "prix", align: "r", fmt: function (v) { return esc(F.price(v)); } },
      { k: "ts", label: "âge", align: "r", fmt: function (v) { return '<span title="' + esc(F.dtfull(v)) + '">' + esc(F.ago(v)) + "</span>"; } }
    ];
  }
  function fillCols() {
    return [
      { k: "timestamp", label: "heure", fmt: function (v) { return '<span title="' + esc(F.dtfull(v)) + '">' + esc(F.dt(v)) + "</span>"; } },
      { k: "portfolio_id", label: "portefeuille" },
      { k: "instrument", label: "instrument", fmt: function (v) { return "<b>" + esc(v) + "</b>"; } },
      { k: "alpha_id", label: "alpha", fmt: function (v) { return v == null ? DASH : '<span class="small" title="' + esc(v) + '">' + esc(v) + "</span>"; } },
      { k: "side", label: "sens", fmt: function (v, r) { var s = v != null ? String(v).toUpperCase() : (isNum(r.quantity) ? (n(r.quantity) < 0 ? "SELL" : "BUY") : null); return s ? CC.badge(s, s === "BUY" ? "up" : s === "SELL" ? "dn" : "muted") : DASH; } },
      { k: "quantity", label: "quantité", align: "r", fmt: function (v) { return isNum(v) ? esc(F.num(Math.abs(n(v)), Math.abs(n(v)) >= 1000 ? 0 : 4)) : DASH; } },
      { k: "price", label: "prix", align: "r", fmt: function (v) { return esc(F.price(v)); } },
      { k: function (r) { return isNum(r.quantity) && isNum(r.price) ? Math.abs(n(r.quantity) * n(r.price)) : null; }, label: "notional", align: "r", fmt: function (v) { return esc(F.eur0(v)); } },
      { k: "fee", label: "frais", align: "r", fmt: function (v) { return esc(F.eur2(v)); } }
    ];
  }

  // ── rendu : cycle ─────────────────────────────────────────────────────────
  function setText(id, txt) { var e = byId(id); if (e) e.textContent = txt == null ? "" : String(txt); }
  function setHtml(id, html) { var e = byId(id); if (e) e.innerHTML = html; }

  function renderCycle(stRes, cyRes, pfRes) {
    var lab = null, src = "";
    if (stRes.ok && stRes.data && stRes.data.lab) { lab = stRes.data.lab; src = "/api/status"; }
    else if (pfRes.ok && pfRes.data && pfRes.data.cycle) { lab = pfRes.data.cycle; src = "/api/lab/portfolios"; }
    var cycles = cyRes.ok && cyRes.data && Array.isArray(cyRes.data.cycles) ? cyRes.data.cycles : [];
    if (!lab && cycles.length) { lab = cycles[0]; src = "/api/lab/cycles"; }
    if (!lab) {
      setErr("lab-cycle", stRes.error || cyRes.error || pfRes.error || new Error("aucune source de cycle"));
      return;
    }
    var fresh = labFresh(lab);
    var failed = failedList(lab.producers_failed);
    var timerMin = (pfRes.ok && pfRes.data && pfRes.data.cycle && isNum(pfRes.data.cycle.timer_every_min)) ? n(pfRes.data.cycle.timer_every_min) : TIMER_MIN_DEFAULT;
    var stateTxt = fresh === "fresh" ? "live" : fresh === "stale" ? "en retard" : fresh === "error" ? "erreur" : "inconnu";
    setHtml("lc-state", CC.led(fresh) + " " + esc(stateTxt) + (lab.status ? ' <span class="small muted">' + esc(String(lab.status)) + "</span>" : ""));
    setText("lc-state-sub", (isNum(lab.age_min) ? "âge " + F.ageMin(lab.age_min) : "âge inconnu") + " · timer " + F.int(timerMin) + " min · source " + src);
    setText("lc-last", F.hhmmss(lab.finished_at));
    setText("lc-last-sub", lab.finished_at ? F.date(lab.finished_at) + " · " + F.ago(lab.finished_at) : "aucun cycle terminé");
    // durée : le cycle le plus récent du journal (même fin que CYCLE_STATE si aligné)
    var latest = cycles.length ? cycles[0] : null;
    var aligned = latest && latest.finished_at && lab.finished_at && String(latest.finished_at).slice(0, 19) === String(lab.finished_at).slice(0, 19);
    setText("lc-dur", latest ? dur(latest.duration_sec) : DASH);
    setText("lc-dur-sub", latest ? (aligned ? "début " + F.hhmmss(latest.started_at) : "dernier cycle journalisé " + F.dt(latest.finished_at)) : (cyRes.ok ? "journal des cycles vide" : "journal des cycles indisponible"));
    setText("lc-prod", F.int(lab.producers_ok) + " / " + F.int(lab.producers_run));
    var okAll = isNum(lab.producers_ok) && isNum(lab.producers_run) && n(lab.producers_run) > 0 && n(lab.producers_ok) === n(lab.producers_run);
    setText("lc-prod-sub", okAll ? "tous les producteurs ont réussi" : (isNum(lab.producers_run) && n(lab.producers_run) === 0 ? "aucun producteur lancé" : "producteurs partiels"));
    setHtml("lc-failed", failed.length ? '<span class="dn">' + esc(failed.join(", ")) + "</span>" : '<span class="up">aucun</span>');
    setText("lc-failed-sub", failed.length ? failed.length + " en échec sur ce cycle" : "");
    var fin = F.parseDate(lab.finished_at);
    if (fin) {
      var nxt = new Date(fin.getTime() + timerMin * 60000);
      var late = Date.now() > nxt.getTime() + timerMin * 60000;
      setText("lc-next", F.hhmm(nxt));
      setText("lc-next-sub", late ? "attendu, non observé — vérifier le timer" : "timer systemd " + F.int(timerMin) + " min");
    } else { setText("lc-next", DASH); setText("lc-next-sub", ""); }

    // bande 30 cycles
    var stripEl = byId("lab-strip");
    if (stripEl) {
      var hasChart = !!(window.echarts && window.echarts.getInstanceByDom(stripEl));
      if (!cyRes.ok || !cycles.length) { if (hasChart) { try { window.echarts.getInstanceByDom(stripEl).dispose(); } catch (e) { /* détaché */ } } }
      else if (!hasChart) stripEl.innerHTML = "";          // retire le placeholder avant l'init echarts
      if (!cyRes.ok) stripEl.innerHTML = '<div class="chart-na">indisponible : ' + esc(String(cyRes.error && cyRes.error.message || cyRes.error).slice(0, 160)) + "</div>";
      else if (!cycles.length) stripEl.innerHTML = '<div class="chart-na">aucun cycle journalisé</div>';
      else {
        var chrono = cycles.slice(0, CYCLES_STRIP_MAX).reverse();
        var data = chrono.map(function (c) {
          var fl = failedList(c.producers_failed);
          var k = statusKind(c.status, fl);
          var col = k === "up" ? T.up : k === "warn" ? T.warn : k === "dn" ? T.dn : T.ink3;
          return { value: isNum(c.duration_sec) ? Math.round(n(c.duration_sec)) : 0, itemStyle: { color: col } };
        });
        var maxDur = data.reduce(function (m, d) { return Math.max(m, d.value); }, 0);
        CC.chart(stripEl, {
          grid: { left: 44, right: 6, top: 6, bottom: 4 },
          tooltip: {
            trigger: "axis",
            formatter: function (ps) {
              var i = ps && ps.length ? ps[0].dataIndex : -1; var c = chrono[i]; if (!c) return "";
              var fl = failedList(c.producers_failed);
              return esc(F.dtfull(c.finished_at)) + "<br>durée " + esc(dur(c.duration_sec)) + " · statut " + esc(c.status == null ? DASH : String(c.status)) +
                "<br>producteurs " + esc(F.int(c.producers_ok)) + " / " + esc(F.int(c.producers_run)) + (fl.length ? "<br>en échec : " + esc(fl.join(", ")) : "");
            }
          },
          xAxis: { type: "category", data: chrono.map(function (c) { return F.hhmm(c.finished_at); }), axisLabel: { show: false }, axisLine: { show: false } },
          yAxis: { type: "value", min: 0, max: maxDur > 0 ? undefined : 1, splitNumber: 2, axisLabel: { fontSize: 10, formatter: function (v) { return v + " s"; } } },
          series: [{ type: "bar", data: data, barCategoryGap: "25%" }]
        });
      }
    }
    // tableau des 10 derniers cycles
    var tblEl = byId("lab-cycles-tbl");
    if (tblEl) {
      if (!cyRes.ok) tblEl.innerHTML = CC.table({ cols: cycleCols(), rows: [], empty: "indisponible : " + String(cyRes.error && cyRes.error.message || cyRes.error).slice(0, 160) });
      else tblEl.innerHTML = CC.table({ cols: cycleCols(), rows: cycles.slice(0, CYCLES_TABLE_MAX), empty: "aucun cycle journalisé", maxH: "260px" });
    }
    clearErr("lab-cycle", lab.finished_at || (stRes.ok && stRes.data && stRes.data.ts) || null, fresh);
  }

  // ── rendu : portefeuilles ─────────────────────────────────────────────────
  function renderPortfolios(pfRes) {
    if (!pfRes.ok) { setErr("lab-pf", pfRes.error); return null; }
    var d = pfRes.data || {};
    var pfs = Array.isArray(d.portfolios) ? d.portfolios : [];
    var rows = pfs.map(function (p) {
      var r = {}; for (var k in p) r[k] = p[k];
      if (p.name === "P1_VOL_OVERLAY" && isNum(d.vol_overlay_multiplier)) r._overlay = n(d.vol_overlay_multiplier);
      return r;
    });
    var el = byId("lab-pf-tbl");
    if (el) el.innerHTML = CC.table({ cols: pfCols(), rows: rows, empty: "aucun portefeuille (state.json absent)" });
    var ttl = panelEl("lab-pf"); var t = ttl ? ttl.querySelector(".panel-h .ttl") : null;
    if (t) { var s = pfs.length + " en parallèle — mêmes signaux, règles d'allocation différentes (capital virtuel " + F.eur0(isNum(d.capital_eur) ? d.capital_eur : CAP_DEFAULT) + ")"; t.textContent = s; t.setAttribute("title", s); }
    var fresh = labFresh(d.cycle);
    clearErr("lab-pf", d.generated_at || null, fresh);
    return d;
  }
  function renderSparks(names, pfData, hp) {
    var host = byId("lab-sparks"); if (!host) return;
    var pfs = (pfData && Array.isArray(pfData.portfolios)) ? pfData.portfolios : [];
    var byName = {}; pfs.forEach(function (p) { if (p && p.name) byName[p.name] = p; });
    host.innerHTML = names.map(function (nm, i) {
      var h = hp[i] && hp[i].hist;
      if (h && h.ok) return sparkTile(nm, byName[nm] || null, h.data || null);
      var t = sparkTile(nm, byName[nm] || null, null);
      if (h && !h.ok) t = t.replace("historique indisponible", "historique indisponible : " + esc(String(h.error && h.error.message || h.error).slice(0, 80)));
      return t;
    }).join("");
  }

  // ── rendu : roster ────────────────────────────────────────────────────────
  function renderRoster(pfData, pfRes) {
    if (!pfRes.ok) { setErr("lab-roster", pfRes.error); return; }
    var ro = (pfData && Array.isArray(pfData.roster)) ? pfData.roster.slice() : [];
    fwdMax = ro.reduce(function (m, r) { return Math.max(m, isNum(r.forward_decisions) ? n(r.forward_decisions) : 0, isNum(r.replay_decisions) ? n(r.replay_decisions) : 0); }, 1);
    var withFreq = ro.some(function (r) { return freqOf(r) != null; });
    var el = byId("lab-roster-tbl");
    if (el) el.innerHTML = CC.table({ cols: rosterCols(withFreq), rows: ro, empty: "roster indisponible" });
    var p = panelEl("lab-roster"); var t = p ? p.querySelector(".panel-h .ttl") : null;
    if (t) {
      var nVal = ro.filter(function (r) { return r.label === "VALIDATED_FORWARD"; }).length;
      var s = ro.length + " alphas en shadow — " + nVal + " validé(s) forward · statut honnête";
      t.textContent = s; t.setAttribute("title", s);
    }
    clearErr("lab-roster", pfData && pfData.generated_at || null, labFresh(pfData && pfData.cycle));
  }

  // ── rendu : marks ─────────────────────────────────────────────────────────
  function renderMarks(mkRes) {
    var el = byId("lab-marks-tbl"); if (!el) return;
    if (!mkRes.ok) {
      if (mkRes.error && mkRes.error.status === 404) {
        el.innerHTML = CC.table({ cols: marksCols(), rows: [], empty: "aucun mark exposé (endpoint absent)" });
        clearErr("lab-marks", null, "unknown");
      } else setErr("lab-marks", mkRes.error);
      return;
    }
    var d = mkRes.data || {}, marks = d.marks && typeof d.marks === "object" ? d.marks : {};
    var rows = Object.keys(marks).sort().map(function (k) { var m = marks[k] || {}; return { instrument: k, price: m.price, ts: m.ts }; });
    el.innerHTML = CC.table({ cols: marksCols(), rows: rows, empty: "aucun mark exposé", maxH: "360px" });
    var p = panelEl("lab-marks"); var t = p ? p.querySelector(".panel-h .ttl") : null;
    if (t) { var s = rows.length ? rows.length + " instruments détenus — dernier mark" : "derniers marks exposés"; t.textContent = s; t.setAttribute("title", s); }
    var oldest = rows.reduce(function (m, r) { var dd = F.parseDate(r.ts); return dd && (!m || dd < m) ? dd : m; }, null);
    var fresh = rows.length ? (oldest && (Date.now() - oldest.getTime()) < 45 * 60000 ? "fresh" : "stale") : "unknown";
    clearErr("lab-marks", d.as_of || null, fresh);
  }

  // ── rendu : fills fusionnés ───────────────────────────────────────────────
  function renderFills(names, hp) {
    var el = byId("lab-fills-tbl"); if (!el) return;
    var all = [], seen = {}, errs = [], okAny = false, asOf = null;
    names.forEach(function (nm, i) {
      var r = hp[i] && hp[i].pos;
      if (!r) return;
      if (!r.ok) { errs.push(nm + " : " + String(r.error && r.error.message || r.error).slice(0, 60)); return; }
      okAny = true;
      var d = r.data || {};
      var ao = F.parseDate(d.as_of); if (ao && (!asOf || ao > asOf)) asOf = ao;
      (Array.isArray(d.fills_recent) ? d.fills_recent : []).forEach(function (f) {
        if (!f) return;
        var pid = f.portfolio_id || d.name || nm;
        var key = f.order_id ? "id:" + f.order_id : "k:" + pid + "|" + (f.instrument || "") + "|" + (f.timestamp || "") + "|" + (f.side || "") + "|" + (f.quantity != null ? f.quantity : "");
        if (seen[key]) return;
        seen[key] = true;
        var row = {}; for (var k in f) row[k] = f[k];
        row.portfolio_id = pid;
        row._t = F.parseDate(f.timestamp); row._t = row._t ? row._t.getTime() : 0;
        all.push(row);
      });
    });
    if (!okAny) { setErr("lab-fills", new Error(errs.join(" · ") || "aucune donnée")); return; }
    all.sort(function (a, b) { return b._t - a._t; });
    var rows = all.slice(0, FILLS_MAX);
    el.innerHTML = CC.table({ cols: fillCols(), rows: rows, empty: "aucun fill récent", maxH: "420px" });
    var p = panelEl("lab-fills"); var t = p ? p.querySelector(".panel-h .ttl") : null;
    if (t) {
      var s = rows.length + " fills affichés sur " + all.length + " (" + (names.length - errs.length) + "/" + names.length + " portefeuilles)" + (errs.length ? " — indisponible : " + errs.join(" · ") : "");
      t.textContent = s; t.setAttribute("title", s);
    }
    clearErr("lab-fills", asOf, errs.length ? "stale" : (asOf && (Date.now() - asOf.getTime()) < 45 * 60000 ? "fresh" : "stale"));
  }

  // ── cycle de rafraîchissement ─────────────────────────────────────────────
  async function refresh() {
    var res = await Promise.all([get("/api/status"), get("/api/lab/cycles"), get("/api/lab/portfolios"), get("/api/lab/marks")]);
    var stRes = res[0], cyRes = res[1], pfRes = res[2], mkRes = res[3];
    try { renderCycle(stRes, cyRes, pfRes); } catch (e) { console.error("[lab] cycle", e); setErr("lab-cycle", e); }
    var pfData = null;
    try { pfData = renderPortfolios(pfRes); } catch (e) { console.error("[lab] portefeuilles", e); setErr("lab-pf", e); }
    try { renderRoster(pfData, pfRes); } catch (e) { console.error("[lab] roster", e); setErr("lab-roster", e); }
    try { renderMarks(mkRes); } catch (e) { console.error("[lab] marks", e); setErr("lab-marks", e); }

    var names = (pfData && Array.isArray(pfData.portfolios) && pfData.portfolios.length)
      ? pfData.portfolios.map(function (p) { return p && p.name; }).filter(Boolean) : lastNames;
    lastNames = names;
    var hp = await Promise.all(names.map(function (nm) {
      var u = "/api/lab/portfolio/" + encodeURIComponent(nm);
      return Promise.all([get(u + "/history"), get(u + "/positions")]).then(function (r) { return { hist: r[0], pos: r[1] }; });
    }));
    try { renderSparks(names, pfData, hp); } catch (e) { console.error("[lab] sparklines", e); }
    try { renderFills(names, hp); } catch (e) { console.error("[lab] fills", e); setErr("lab-fills", e); }
  }

  CC.register({
    key: "lab", code: "LAB", title: "Live Alpha Lab", icon: "◎", refreshMs: 30000,
    init: init,
    refresh: refresh,
    onShow: function () { if (root) CC.resizeCharts(root); }
  });
})();
