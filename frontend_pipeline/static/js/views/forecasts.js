/* Vue FCST — Prévisions (mes prévisions par crypto).
   Port de l'ancien onglet "PRÉVISIONS" de command_center.html :
     /api/forecasts  → liste {symbol, direction, target, horizon_days, conviction, note, updated_at}
     /api/universe   → prix courants (assets[].price) pour l'écart "vs prix", ts, live
   Édition/suppression : uniquement admin (le serveur tranche aussi, 403). Invité : lecture seule.
   Tout est paper : ces prévisions sont des annotations manuelles, aucun ordre réel. */
(function () {
  "use strict";

  var esc = function (s) {
    return s == null ? "" : String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var F = CC.fmt;
  var root = null;
  var last = { forecasts: null, prices: null, priceTs: null, priceLive: null, backend: null };
  var selectedSym = null;

  var DIR = {
    up: { glyph: "▲", label: "hausse", kind: "up" },
    down: { glyph: "▼", label: "baisse", kind: "dn" },
    neutral: { glyph: "■", label: "neutre", kind: "muted" }
  };
  function dirOf(d) { return DIR[d] || { glyph: "?", label: d ? String(d) : "—", kind: "muted" }; }
  function stars(n) {
    n = CC.isNum(n) ? Math.max(0, Math.min(5, Math.round(Number(n)))) : null;
    if (n == null) return "—";
    var s = "";
    for (var i = 1; i <= 5; i++) s += i <= n ? "★" : "☆";
    return s;
  }
  function short(sym) { return String(sym || "").replace("USDT", ""); }

  // ── squelette ─────────────────────────────────────────────────────────────
  function tile(id, code, title, sub) {
    return CC.panel({
      code: code, title: title, cls: "tile", id: "fc-tile-" + id,
      body: '<div class="big" id="fc-v-' + id + '">—</div><div class="sub" id="fc-s-' + id + '">' + esc(sub || "") + "</div>"
    });
  }

  function init(el) {
    root = el;
    if (!document.getElementById("css-forecasts")) {
      var st = document.createElement("style");
      st.id = "css-forecasts";
      st.textContent =
        ".fc-note{max-width:360px;overflow:hidden;text-overflow:ellipsis;color:var(--ink2)}" +
        ".fc-stars{color:var(--amber);letter-spacing:.06em}" +
        ".fc-stars .off{color:var(--ink3)}" +
        ".fc-dir{font-weight:600;white-space:nowrap}" +
        ".fc-act{display:inline-flex;gap:4px;justify-content:flex-end}" +
        "#fc-tiles .big.dn{color:var(--dn)}#fc-tiles .big.up{color:var(--up)}#fc-tiles .big.amber{color:var(--amber)}";
      document.head.appendChild(st);
    }
    el.innerHTML =
      '<div class="tiles" id="fc-tiles">'
      + tile("n", "FCST", "prévisions actives", "cryptos annotées")
      + tile("up", "▲", "biais haussier", "directions hausse")
      + tile("dn", "▼", "biais baissier", "directions baisse")
      + tile("nt", "■", "neutres", "directions neutre")
      + tile("conv", "CONV", "conviction moyenne", "sur 5")
      + "</div>"
      + '<div class="grid mt">'
      + CC.panel({
        code: "FCST", title: "mes prévisions par crypto — édition dans CRYP", id: "fc-main", cls: "c12", fresh: "unknown",
        tools: '<span class="small muted" id="fc-price-asof">prix : —</span>',
        body: '<div id="fc-table"><div class="muted small">chargement…</div></div>'
      })
      + "</div>"
      + '<div class="view-fn" id="fc-fn">Annotations manuelles (capital virtuel, aucun ordre réel). « vs prix » = écart entre la cible et le prix courant (cible ÷ prix − 1). '
      + 'Clique une ligne pour ouvrir la crypto dans CRYP'
      + (CC.isAdmin() ? " et éditer la prévision." : " (édition réservée à l'admin, session en lecture seule).")
      + "</div>";

    // délégation : clic ligne → CRYP + ouverture du détail ; bouton suppr. (admin)
    var tbl = document.getElementById("fc-table");
    tbl.addEventListener("click", function (e) {
      var t = e.target;
      var btn = t.closest ? t.closest("button[data-act]") : null;
      if (btn) {
        e.stopPropagation();
        var act = btn.getAttribute("data-act"), sym = btn.getAttribute("data-sym");
        if (act === "del") { delForecast(sym); return; }
        if (act === "open") { openInCryptos(sym); return; }
      }
      var tr = t.closest ? t.closest("tr[data-sym]") : null;
      if (tr) openInCryptos(tr.getAttribute("data-sym"));
    });

    // commande "FCST BTC" → surligne la ligne
    CC.on("cmd", function (c) {
      if (!c || c.key !== "forecasts" || !c.args || !c.args.length) return;
      var a = String(c.args[0]).toUpperCase();
      selectedSym = a.indexOf("USDT") > 0 ? a : a + "USDT";
      highlight();
    });
  }

  function openInCryptos(sym) {
    if (!sym) return;
    selectedSym = sym;
    CC.show("cryptos");
    CC.emit("cryptos:open", sym);
  }

  async function delForecast(sym) {
    if (!CC.isAdmin() || !sym) return;
    if (!window.confirm("Supprimer la prévision " + short(sym) + " ?")) return;
    try {
      await CC.j("/api/forecast/" + encodeURIComponent(sym), { method: "DELETE" });
      CC.toast("prévision " + short(sym) + " supprimée", "muted", 2500);
      await refresh();
    } catch (e) {
      CC.toast("suppression impossible : " + String(e && e.message || e).slice(0, 120), "dn", 5000);
    }
  }

  function highlight() {
    var rows = root ? root.querySelectorAll("#fc-table tr[data-sym]") : [];
    for (var i = 0; i < rows.length; i++) rows[i].classList.toggle("sel", rows[i].getAttribute("data-sym") === selectedSym);
  }

  // ── rendu ────────────────────────────────────────────────────────────────
  function setText(id, txt, cls) {
    var el = document.getElementById(id); if (!el) return;
    el.textContent = txt;
    if (cls !== undefined) el.className = "big" + (cls ? " " + cls : "");
  }

  function renderTiles(fs, uniCount) {
    if (!fs) {
      ["n", "up", "dn", "nt", "conv"].forEach(function (k) { setText("fc-v-" + k, "—", ""); });
      return;
    }
    var up = 0, dn = 0, nt = 0, sum = 0;
    fs.forEach(function (f) {
      if (f.direction === "up") up++; else if (f.direction === "down") dn++; else nt++;
      sum += CC.isNum(f.conviction) ? Number(f.conviction) : 3;   // défaut 3 comme l'ancien code
    });
    setText("fc-v-n", F.int(fs.length), fs.length ? "amber" : "");
    setText("fc-s-n", "cryptos annotées" + (CC.isNum(uniCount) ? " / " + F.int(uniCount) + " dans l'univers" : ""));
    setText("fc-v-up", F.int(up), up && up >= dn ? "up" : "");
    setText("fc-v-dn", F.int(dn), dn && dn > up ? "dn" : "");
    setText("fc-v-nt", F.int(nt), "");
    setText("fc-v-conv", fs.length ? F.num(sum / fs.length, 1) + " / 5" : "—", fs.length ? "amber" : "");
  }

  function renderTable(fs, px, uniErr) {
    var host = document.getElementById("fc-table"); if (!host) return;
    var admin = CC.isAdmin();
    var rows = fs.slice().sort(function (a, b) { return String(a.symbol).localeCompare(String(b.symbol)); });
    var cols = [
      { k: "symbol", label: "symbole", fmt: function (v) { return "<b>" + esc(short(v)) + "</b>" + (v ? '<span class="sub">' + esc(v) + "</span>" : ""); } },
      { k: "direction", label: "direction", fmt: function (v) { var d = dirOf(v); return '<span class="fc-dir ' + d.kind + '" title="' + esc(String(v || "")) + '">' + d.glyph + " " + esc(d.label) + "</span>"; } },
      { k: "target", label: "cible USDT", align: "r", title: "prix cible saisi (USDT)", fmt: function (v) { return esc(F.price(v)); } },
      {
        k: function (r) { var p = px ? px[r.symbol] : null; return (CC.isNum(r.target) && CC.isNum(p) && Number(p) !== 0) ? Number(r.target) / Number(p) - 1 : null; },
        label: "vs prix", align: "r", title: "cible ÷ prix courant − 1",
        cls: function (v) { return CC.cls(v); },
        fmt: function (v, r) {
          var p = px ? px[r.symbol] : null;
          return esc(F.pct(v, 1)) + '<span class="sub">prix ' + esc(CC.isNum(p) ? F.price(p) : (uniErr ? "indispo." : "—")) + "</span>";
        }
      },
      { k: "horizon_days", label: "horizon", align: "r", title: "horizon en jours", fmt: function (v) { return esc(F.days(v)); } },
      {
        k: "conviction", label: "conviction", align: "r", title: "conviction 1 à 5",
        fmt: function (v) {
          var n = CC.isNum(v) ? Math.max(0, Math.min(5, Math.round(Number(v)))) : null;
          if (n == null) return "—";
          var s = "";
          for (var i = 1; i <= 5; i++) s += i <= n ? "★" : '<span class="off">☆</span>';
          return '<span class="fc-stars" title="' + n + ' / 5">' + s + "</span> " + n + "/5";
        }
      },
      {
        k: "note", label: "note", cls: "fc-note",
        fmt: function (v, r) { return (v ? esc(v) : '<span class="muted">—</span>') + (r.updated_at ? '<span class="sub">màj ' + esc(F.dt(r.updated_at)) + "</span>" : ""); }
      },
      {
        k: "symbol", label: "action", align: "r",
        fmt: function (v) {
          return '<span class="fc-act"><button class="btn-sm" data-act="open" data-sym="' + esc(v) + '" title="ouvrir dans CRYP">' + (admin ? "éditer ›" : "voir ›") + "</button>"
            + (admin ? '<button class="btn-sm btn-danger" data-act="del" data-sym="' + esc(v) + '" title="supprimer la prévision">suppr.</button>' : "") + "</span>";
        }
      }
    ];
    host.innerHTML = CC.table({
      cols: cols, rows: rows, maxH: "62vh",
      empty: admin ? "aucune prévision — ouvre CRYP, choisis une crypto et enregistre ta vue" : "aucune prévision enregistrée (édition réservée à l'admin)",
      rowCls: function (r) { return "clickable" + (r.symbol === selectedSym ? " sel" : ""); },
      rowAttrs: function (r) { return 'data-sym="' + esc(r.symbol) + '" title="ouvrir ' + esc(short(r.symbol)) + " dans CRYP\""; }
    });
  }

  function setUnavailable(msg) {
    var host = document.getElementById("fc-table");
    if (host) host.innerHTML = '<div class="dn small">indisponible : ' + esc(msg) + "</div>";
    CC.setAsOf("fc-main", new Date(), "error");
  }

  async function refresh() {
    var fcRes = null, fcErr = null, uni = null, uniErr = null;
    var results = await Promise.all([
      CC.j("/api/forecasts").then(function (r) { fcRes = r; }, function (e) { fcErr = e; }),
      CC.j("/api/universe").then(function (r) { uni = r; }, function (e) { uniErr = e; })
    ]);
    void results;

    var px = null, uniCount = null;
    if (uni && Array.isArray(uni.assets)) {
      px = {};
      uni.assets.forEach(function (a) { if (a && a.symbol) px[a.symbol] = a.price; });
      uniCount = uni.assets.length;
      last.prices = px; last.priceTs = uni.ts; last.priceLive = !!uni.live;
    }
    var pa = document.getElementById("fc-price-asof");
    if (pa) {
      if (uni) pa.textContent = "prix " + (uni.live ? "live" : "dernier close") + " " + F.hhmmss(uni.ts);
      else pa.textContent = "prix indisponibles : " + String(uniErr && uniErr.message || uniErr || "").slice(0, 80);
      pa.className = "small " + (uni ? "muted" : "dn");
    }

    if (fcErr) {
      renderTiles(null);
      setUnavailable(String(fcErr && fcErr.message || fcErr).slice(0, 200));
      return;
    }
    var fs = (fcRes && Array.isArray(fcRes.forecasts)) ? fcRes.forecasts.filter(function (f) { return f && f.symbol; }) : [];
    last.forecasts = fs; last.backend = fcRes && fcRes.backend;
    renderTiles(fs, uniCount);
    renderTable(fs, px, !!uniErr);
    var main = document.getElementById("fc-main");
    if (main) {
      var h = main.querySelector(".panel-h .ttl");
      if (h) {
        var backendNote = last.backend === "unavailable" ? " — stockage indisponible (MongoDB)" : "";
        h.textContent = "mes prévisions par crypto — édition dans CRYP" + backendNote;
      }
    }
    CC.setAsOf("fc-main", new Date(), last.backend === "unavailable" ? "stale" : "fresh");
  }

  CC.register({
    key: "forecasts", code: "FCST", title: "Prévisions", icon: "✦", refreshMs: 60000,
    init: init,
    refresh: refresh,
    onShow: function () { highlight(); }
  });
})();
