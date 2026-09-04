/* FUTUR ▮ TERMINAL — core.js
   Script classique (pas de module, pas de bundler). Définit window.CC AVANT les vues.
   Tout ce qui est affiché est paper/shadow : capital virtuel, aucun ordre réel. */
(function () {
  "use strict";

  var CC = window.CC = window.CC || {};
  CC.version = "terminal-1";

  // ── tokens (miroir de terminal.css, pour echarts/SVG) ─────────────────────
  var T = CC.tokens = {
    bg: "#050608", bg2: "#0b0d12", bg3: "#12151c", line: "#23272f",
    ink: "#e6e6e6", ink2: "#a3a8b3", ink3: "#6b7280",
    amber: "#ffb000", amber2: "#ffcf5a", up: "#00d26a", dn: "#ff3b30", warn: "#ffb000", info: "#4cc2ff",
    mono: '"IBM Plex Mono","JetBrains Mono","SF Mono",ui-monospace,Menlo,monospace',
    palette: ["#ffb000", "#4cc2ff", "#00d26a", "#ff3b30", "#b48cff", "#ff8c42", "#a3a8b3", "#ffcf5a"]
  };

  CC.state = { user: null, role: "guest", view: null, status: null, visible: true, authKnown: false };

  // ── bus d'événements ───────────────────────────────────────────────────────
  var listeners = {};
  CC.on = function (name, fn) {
    (listeners[name] = listeners[name] || []).push(fn);
    return function () { var a = listeners[name] || []; var i = a.indexOf(fn); if (i >= 0) a.splice(i, 1); };
  };
  CC.emit = function (name, payload) {
    (listeners[name] || []).slice().forEach(function (fn) {
      try { fn(payload); } catch (e) { console.error("[CC] listener " + name, e); }
    });
  };

  // ── échappement HTML ───────────────────────────────────────────────────────
  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  CC.esc = function (s) { return s == null ? "" : String(s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); };
  var esc = CC.esc;

  // ── fetch JSON (401 → /login?next=…) ───────────────────────────────────────
  var redirecting = false;
  CC.loginUrl = function () {
    var next = location.pathname + location.search + location.hash;
    return "/login?next=" + encodeURIComponent(next || "/");
  };
  CC.j = async function (url, opts) {
    var r = await fetch(url, opts || {});
    if (r.status === 401) {
      if (!redirecting) { redirecting = true; location.assign(CC.loginUrl()); }
      throw new Error("401 non authentifié");
    }
    if (!r.ok) {
      var txt = "";
      try { txt = await r.text(); } catch (e) { txt = ""; }
      var err = new Error(txt || (r.status + " " + r.statusText));
      err.status = r.status;
      throw err;
    }
    return r.json();
  };

  // ── formatage fr-FR ────────────────────────────────────────────────────────
  var DASH = "—", MINUS = "−";
  function isNum(v) {
    if (typeof v === "number") return isFinite(v);
    if (typeof v === "string" && v.trim() !== "") return isFinite(Number(v));
    return false;
  }
  function toNum(v) { return typeof v === "number" ? v : Number(v); }
  function grp(s) { return s.replace(/\B(?=(\d{3})+(?!\d))/g, " "); }
  // fixed(v, décimales, signe explicite) → "1 234,50" / "−12,3" / "+0,8"
  function fixed(v, d, signed) {
    if (!isNum(v)) return DASH;
    v = toNum(v);
    if (d == null) d = 2;
    var s = Math.abs(v).toFixed(d);
    var zero = Number(s) === 0;
    var parts = s.split(".");
    var out = grp(parts[0]) + (parts[1] ? "," + parts[1] : "");
    var sign = zero ? "" : (v < 0 ? MINUS : (signed ? "+" : ""));
    return sign + out;
  }
  function adaptiveDigits(a, floor) {
    if (a >= 1000) return floor != null ? floor : 0;
    if (a >= 1) return 2;
    if (a === 0) return 2;
    var d = 3 - Math.floor(Math.log10(a));      // 4 chiffres significatifs
    return Math.max(2, Math.min(8, d));
  }
  function parseDate(v) {
    if (v == null || v === "") return null;
    if (v instanceof Date) return isNaN(v.getTime()) ? null : v;
    if (typeof v === "number") { var d0 = new Date(v < 1e12 ? v * 1000 : v); return isNaN(d0.getTime()) ? null : d0; }
    var s = String(v);
    // ISO sans fuseau → l'API est UTC partout
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) s += "Z";
    var d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }
  function p2(n) { return (n < 10 ? "0" : "") + n; }

  CC.fmt = {
    num: function (v, digits) {
      if (!isNum(v)) return DASH;
      var n = toNum(v);
      return fixed(n, digits == null ? adaptiveDigits(Math.abs(n)) : digits, false);
    },
    snum: function (v, digits) {
      if (!isNum(v)) return DASH;
      var n = toNum(v);
      return fixed(n, digits == null ? adaptiveDigits(Math.abs(n)) : digits, true);
    },
    int: function (v) { return fixed(v, 0, false); },
    eur0: function (v) { return isNum(v) ? fixed(v, 0, false) + " €" : DASH; },
    eur2: function (v) { return isNum(v) ? fixed(v, 2, false) + " €" : DASH; },
    seur0: function (v) { return isNum(v) ? fixed(v, 0, true) + " €" : DASH; },   // P&L : signe explicite
    seur2: function (v) { return isNum(v) ? fixed(v, 2, true) + " €" : DASH; },
    usdt0: function (v) { return isNum(v) ? fixed(v, 0, false) + " USDT" : DASH; },
    pct: function (v, digits) { return isNum(v) ? fixed(toNum(v) * 100, digits == null ? 2 : digits, true) + " %" : DASH; },
    pct0: function (v, digits) { return isNum(v) ? fixed(toNum(v) * 100, digits == null ? 2 : digits, false) + " %" : DASH; },
    bps: function (v, digits) { return isNum(v) ? fixed(v, digits == null ? 0 : digits, true) + " bps" : DASH; },
    sgn: function (v) { return isNum(v) ? (toNum(v) > 0 ? "+" : toNum(v) < 0 ? MINUS : "") : ""; },
    price: function (v) {
      if (!isNum(v)) return DASH;
      var n = toNum(v), a = Math.abs(n), d;
      if (a >= 1) d = 2; else if (a >= 0.1) d = 4; else d = adaptiveDigits(a);
      return fixed(n, d, false);
    },
    ratio: function (v, digits) { return isNum(v) ? fixed(v, digits == null ? 2 : digits, false) + " ×" : DASH; },
    h: function (v, digits) { return isNum(v) ? fixed(v, digits == null ? 0 : digits, false) + " h" : DASH; },
    min: function (v) { return isNum(v) ? fixed(v, 0, false) + " min" : DASH; },
    days: function (v, digits) { return isNum(v) ? fixed(v, digits == null ? 0 : digits, false) + " j" : DASH; },
    gb: function (v) { return isNum(v) ? fixed(v, 0, false) + " Go" : DASH; },
    dt: function (iso) { var d = parseDate(iso); return d ? p2(d.getDate()) + "/" + p2(d.getMonth() + 1) + " " + p2(d.getHours()) + ":" + p2(d.getMinutes()) : DASH; },
    date: function (iso) { var d = parseDate(iso); return d ? p2(d.getDate()) + "/" + p2(d.getMonth() + 1) + "/" + d.getFullYear() : DASH; },
    dtfull: function (iso) { var d = parseDate(iso); return d ? p2(d.getDate()) + "/" + p2(d.getMonth() + 1) + "/" + d.getFullYear() + " " + p2(d.getHours()) + ":" + p2(d.getMinutes()) + ":" + p2(d.getSeconds()) : DASH; },
    hhmm: function (iso) { var d = parseDate(iso); return d ? p2(d.getHours()) + ":" + p2(d.getMinutes()) : DASH; },
    hhmmss: function (iso) { var d = parseDate(iso); return d ? p2(d.getHours()) + ":" + p2(d.getMinutes()) + ":" + p2(d.getSeconds()) : DASH; },
    ago: function (iso) {
      var d = parseDate(iso); if (!d) return DASH;
      var s = Math.round((Date.now() - d.getTime()) / 1000);
      if (s < 0) s = 0;
      if (s < 60) return "à l'instant";
      var m = Math.round(s / 60);
      if (m < 60) return "il y a " + m + " min";
      var h = Math.floor(m / 60);
      if (h < 48) return "il y a " + h + " h" + (m % 60 ? " " + p2(m % 60) : "");
      return "il y a " + Math.floor(h / 24) + " j";
    },
    ageMin: function (min) {
      if (!isNum(min)) return DASH;
      var m = Math.round(toNum(min));
      if (m < 60) return m + " min";
      var h = Math.floor(m / 60);
      if (h < 48) return h + " h" + (m % 60 ? " " + p2(m % 60) : "");
      return Math.floor(h / 24) + " j";
    },
    parseDate: parseDate
  };
  CC.isNum = isNum;

  CC.cls = function (v) { if (!isNum(v)) return "flat"; var n = toNum(v); return n > 0 ? "up" : n < 0 ? "dn" : "flat"; };
  CC.badge = function (text, kind, title) {
    return '<span class="badge' + (kind ? " " + esc(kind) : "") + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(text) + "</span>";
  };
  CC.led = function (state, title) {
    return '<i class="led ' + esc(freshCls(state)) + '"' + (title ? ' title="' + esc(title) + '"' : "") + "></i>";
  };
  function freshCls(f) {
    if (f === true) return "fresh";
    if (f === false) return "stale";
    if (f === "fresh" || f === "stale" || f === "stopped" || f === "error") return f;
    return "unknown";
  }
  CC.freshCls = freshCls;

  // ── panneau standard ───────────────────────────────────────────────────────
  // {code, title, body, asOf, fresh, tools (HTML brut), id, cls}
  CC.panel = function (o) {
    o = o || {};
    var hasFresh = o.fresh !== undefined;
    var asOf = o.asOf ? CC.fmt.hhmmss(o.asOf) : "";
    return '<section class="panel' + (o.cls ? " " + esc(o.cls) : "") + '"' + (o.id ? ' id="' + esc(o.id) + '"' : "") + ">"
      + '<header class="panel-h">'
      + '<span class="code">' + esc(o.code || "") + "</span>"
      + '<span class="ttl" title="' + esc(o.title || "") + '">' + esc(o.title || "") + "</span>"
      + (o.tools ? '<span class="tools">' + o.tools + "</span>" : "")
      + '<span class="asof"' + (o.asOf ? ' data-iso="' + esc(o.asOf) + '" title="' + esc(CC.fmt.dtfull(o.asOf)) + '"' : "") + ">" + (asOf ? "as of " + asOf : "") + "</span>"
      + (hasFresh ? CC.led(o.fresh) : "")
      + "</header>"
      + '<div class="panel-b">' + (o.body || "") + "</div>"
      + "</section>";
  };
  CC.setAsOf = function (el, iso, fresh) {
    if (typeof el === "string") el = document.getElementById(el);
    if (!el) return;
    var a = el.classList && el.classList.contains("asof") ? el : el.querySelector(".asof");
    if (a) {
      var d = parseDate(iso);
      a.textContent = d ? "as of " + CC.fmt.hhmmss(d) : "";
      if (d) { a.setAttribute("data-iso", d.toISOString()); a.setAttribute("title", CC.fmt.dtfull(d)); }
    }
    if (fresh !== undefined) {
      var h = a ? a.parentNode : el.querySelector(".panel-h");
      var led = h ? h.querySelector(".led") : null;
      if (!led && h) { led = document.createElement("i"); h.appendChild(led); }
      if (led) led.className = "led " + freshCls(fresh);
    }
  };

  // ── tableau dense ──────────────────────────────────────────────────────────
  // cols: [{k (clé ou fn(row)), label, align 'l'|'r', fmt(v,row) → HTML, cls (str|fn(v,row)), title}]
  // rows, empty, maxH ("240px"), id, cls, rowCls(row), rowAttrs(row) → " data-x=…"
  CC.table = function (o) {
    o = o || {};
    var cols = o.cols || [], rows = o.rows || [];
    var th = cols.map(function (c) {
      return "<th" + (c.align === "r" ? ' class="num"' : "") + (c.title ? ' title="' + esc(c.title) + '"' : "") + ">" + esc(c.label == null ? c.k : c.label) + "</th>";
    }).join("");
    var body;
    if (!rows.length) {
      body = '<tr><td class="empty" colspan="' + (cols.length || 1) + '">' + esc(o.empty || "aucune donnée") + "</td></tr>";
    } else {
      body = rows.map(function (r) {
        var tds = cols.map(function (c) {
          var v = typeof c.k === "function" ? c.k(r) : (r == null ? undefined : r[c.k]);
          var txt = c.fmt ? c.fmt(v, r) : (v == null || v === "" ? DASH : esc(String(v)));
          var cl = [];
          if (c.align === "r") cl.push("num");
          if (c.cls) { var x = typeof c.cls === "function" ? c.cls(v, r) : c.cls; if (x) cl.push(x); }
          return "<td" + (cl.length ? ' class="' + cl.join(" ") + '"' : "") + ">" + txt + "</td>";
        }).join("");
        var rc = o.rowCls ? o.rowCls(r) : "";
        var ra = o.rowAttrs ? o.rowAttrs(r) : "";
        return "<tr" + (rc ? ' class="' + rc + '"' : "") + (ra ? " " + ra : "") + ">" + tds + "</tr>";
      }).join("");
    }
    return '<div class="tblwrap"' + (o.maxH ? ' style="max-height:' + esc(o.maxH) + '"' : "") + ">"
      + '<table class="tbl' + (o.cls ? " " + esc(o.cls) : "") + '"' + (o.id ? ' id="' + esc(o.id) + '"' : "") + ">"
      + "<thead><tr>" + th + "</tr></thead><tbody>" + body + "</tbody></table></div>";
  };

  // ── echarts ────────────────────────────────────────────────────────────────
  CC.echartsBase = function () {
    var axisLabel = { color: T.ink2, fontSize: 11, fontFamily: T.mono };
    return {
      backgroundColor: "transparent",
      animation: false,
      color: T.palette,
      textStyle: { fontFamily: T.mono, color: T.ink2 },
      tooltip: {
        backgroundColor: T.bg2, borderColor: T.line, borderWidth: 1, padding: [6, 8],
        textStyle: { color: T.ink, fontFamily: T.mono, fontSize: 12 },
        axisPointer: { lineStyle: { color: T.ink3 }, crossStyle: { color: T.ink3 } }
      },
      legend: { textStyle: { color: T.ink2, fontSize: 11, fontFamily: T.mono }, icon: "rect", itemWidth: 10, itemHeight: 3, top: 0 },
      grid: { left: 56, right: 16, top: 28, bottom: 32 },
      xAxis: { axisLine: { lineStyle: { color: T.line } }, axisTick: { show: false }, axisLabel: axisLabel, splitLine: { show: false } },
      yAxis: { axisLine: { show: false }, axisTick: { show: false }, axisLabel: axisLabel, splitLine: { lineStyle: { color: T.line, type: "dashed" } } },
      dataZoom: []
    };
  };
  function isObj(x) { return x && typeof x === "object" && !Array.isArray(x); }
  function merge(base, opt) {
    if (opt === undefined) return base;
    if (Array.isArray(opt)) {
      if (isObj(base)) return opt.map(function (item) { return isObj(item) ? merge(base, item) : item; });
      return opt;
    }
    if (isObj(opt) && isObj(base)) {
      var out = {}, k;
      for (k in base) out[k] = base[k];
      for (k in opt) out[k] = merge(base[k], opt[k]);
      return out;
    }
    return opt;
  }
  CC.mergeOption = function (opt) {
    var base = CC.echartsBase();
    var m = merge(base, opt || {});
    // dataZoom slider : style terminal
    if (Array.isArray(m.dataZoom)) {
      m.dataZoom = m.dataZoom.map(function (z) {
        if (z && z.type === "slider") {
          return merge({ height: 14, bottom: 6, borderColor: T.line, fillerColor: "rgba(255,176,0,.15)", handleStyle: { color: T.ink3 },
            textStyle: { color: T.ink3, fontSize: 10 }, dataBackground: { lineStyle: { color: T.ink3 }, areaStyle: { color: T.bg3 } } }, z);
        }
        return z;
      });
    }
    return m;
  };
  var charts = [];
  var resizeBound = false;
  function bindResize() {
    if (resizeBound) return; resizeBound = true;
    var t = null;
    window.addEventListener("resize", function () {
      clearTimeout(t);
      t = setTimeout(function () { charts.forEach(function (c) { try { c.resize(); } catch (e) { /* détaché */ } }); }, 80);
    });
  }
  CC.chart = function (el, option, opts) {
    opts = opts || {};
    if (typeof el === "string") el = document.getElementById(el);
    if (!el) return null;
    if (!window.echarts) {
      el.innerHTML = '<div class="chart-na">graphique indisponible (echarts non chargé)</div>';
      return null;
    }
    var inst = window.echarts.getInstanceByDom(el);
    if (!inst) {
      inst = window.echarts.init(el, null, { renderer: "svg" });
      charts.push(inst);
      bindResize();
      if (window.ResizeObserver) {
        try { new ResizeObserver(function () { try { inst.resize(); } catch (e) { /* détaché */ } }).observe(el); } catch (e) { /* ignore */ }
      }
    }
    inst.setOption(CC.mergeOption(option), { notMerge: opts.notMerge !== false, lazyUpdate: !!opts.lazyUpdate });
    return inst;
  };
  CC.resizeCharts = function (root) {
    charts.forEach(function (c) {
      try { var dom = c.getDom(); if (!root || root.contains(dom)) c.resize(); } catch (e) { /* détaché */ }
    });
  };
  // sparkline SVG (sans echarts). opts: {up, color, base, h, w}. Retourne le SVG ; remplit el si fourni.
  CC.spark = function (el, values, opts) {
    opts = opts || {};
    var vals = (values || []).filter(function (v) { return isNum(v); }).map(toNum);
    var w = opts.w || 120, h = opts.h || 28, svg;
    if (vals.length < 2) {
      svg = '<svg class="spark" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" aria-hidden="true"></svg>';
    } else {
      var mn = Math.min.apply(null, vals), mx = Math.max.apply(null, vals);
      if (isNum(opts.base)) { mn = Math.min(mn, toNum(opts.base)); mx = Math.max(mx, toNum(opts.base)); }
      var rg = (mx - mn) || 1;
      var y = function (v) { return (h - 2 - (v - mn) / rg * (h - 4)).toFixed(1); };
      var pts = vals.map(function (v, i) { return (i / (vals.length - 1) * w).toFixed(1) + "," + y(v); }).join(" ");
      var up = opts.up !== undefined ? !!opts.up : vals[vals.length - 1] >= (isNum(opts.base) ? toNum(opts.base) : vals[0]);
      var color = opts.color || (up ? T.up : T.dn);
      var base = isNum(opts.base) ? '<line x1="0" x2="' + w + '" y1="' + y(toNum(opts.base)) + '" y2="' + y(toNum(opts.base)) + '" stroke="' + T.ink3 + '" stroke-width="1" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>' : "";
      svg = '<svg class="spark" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" aria-hidden="true">' + base
        + '<polyline fill="none" stroke="' + color + '" stroke-width="1.5" vector-effect="non-scaling-stroke" points="' + pts + '"/></svg>';
    }
    if (el) { if (typeof el === "string") el = document.getElementById(el); if (el) el.innerHTML = svg; }
    return svg;
  };

  // ── registre des vues ──────────────────────────────────────────────────────
  var REG = [], BYKEY = {};
  var booted = false;
  CC.register = function (def) {
    if (!def || !def.key || !def.code) throw new Error("CC.register: key et code requis");
    if (BYKEY[def.key]) { console.warn("[CC] vue déjà enregistrée : " + def.key + " (remplacée)"); REG = REG.filter(function (d) { return d.key !== def.key; }); }
    def._inited = false; def._timer = null; def._busy = false;
    BYKEY[def.key] = def; REG.push(def);
    if (booted) buildNav();
    return def;
  };
  CC.views = function () { return REG.slice(); };
  CC.view = function (key) { return BYKEY[key] || null; };
  function resolveKey(k) {
    if (!k) return null;
    var s = String(k).trim().toLowerCase();
    if (BYKEY[s]) return s;
    for (var i = 0; i < REG.length; i++) if (REG[i].code.toLowerCase() === s) return REG[i].key;
    return null;
  }

  // ── analyseur de commande ──────────────────────────────────────────────────
  var ALIASES = {
    portfolio: ["port", "portefeuille", "portfolio", "pf", "porte"],
    lab: ["lab", "live alpha lab", "alpha", "alphalab", "labo", "shadow"],
    tournament: ["tour", "tournoi", "tournament", "alpha20", "alpha_20"],
    cryptos: ["cryp", "crypto", "cryptos", "coins", "prix"],
    forecasts: ["fcst", "prévisions", "previsions", "prevision", "prévision", "forecast", "forecasts", "fc"],
    world: ["wld", "world", "monde", "worldmon", "world monitor"],
    edgelab: ["edge", "edge lab", "edgelab", "lab edge", "cross", "croiser"]
  };
  function norm(s) { return String(s || "").trim().toLowerCase().replace(/\s+/g, " "); }
  CC.parse = function (text) {
    var s = norm(text);
    if (!s) return { action: "none" };
    if (s === "?" || s === "aide" || s === "help" || s === "h") return { action: "help" };
    if (s === "logout" || s === "déconnexion" || s === "deconnexion" || s === "quit" || s === "exit") return { action: "logout" };
    if (s === "refresh" || s === "maj" || s === "r" || s === "reload") return { action: "refresh" };
    if (s === "clear" || s === "cls") return { action: "clear" };
    var parts = s.split(" "), head = parts[0], args = parts.slice(1);
    var key = resolveKey(head), k;
    if (!key) {
      for (k in ALIASES) {
        if (ALIASES[k].indexOf(s) >= 0) return { action: "show", key: k, args: [] };
        if (ALIASES[k].indexOf(head) >= 0) { key = k; break; }
      }
    }
    if (!key) {
      // deux mots ("live alpha", "edge lab")
      for (k in ALIASES) {
        for (var i = 0; i < ALIASES[k].length; i++) {
          var a = ALIASES[k][i];
          if (a.indexOf(" ") > 0 && s.indexOf(a) === 0) return { action: "show", key: k, args: norm(s.slice(a.length)).split(" ").filter(Boolean) };
        }
      }
    }
    if (!key) return { action: "unknown", text: s };
    return { action: "show", key: key, args: args.map(function (x) { return x.toUpperCase(); }) };
  };
  CC.exec = function (text) {
    var c = CC.parse(text);
    switch (c.action) {
      case "none": return c;
      case "help": CC.help(); return c;
      case "logout": location.assign("/logout"); return c;
      case "refresh": CC.refresh(); CC.toast("actualisation…", "muted", 1200); return c;
      case "clear": CC.closeOverlay(); return c;
      case "show":
        CC.show(c.key);
        if (c.args && c.args.length) CC.emit("cmd", { key: c.key, args: c.args });
        return c;
      default:
        CC.toast("commande inconnue : " + c.text + " — tape ? pour l'aide", "warn", 3000);
        return c;
    }
  };

  // ── routeur / affichage ────────────────────────────────────────────────────
  var LS_VIEW = "cc.view";
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* privé */ } }
  function sectionOf(def) { return document.getElementById("v-" + def.key); }
  var settingHash = false;

  CC.show = function (key) {
    var k = resolveKey(key);
    if (!k) return false;
    var def = BYKEY[k];
    var prev = CC.state.view ? BYKEY[CC.state.view] : null;
    if (prev && prev !== def) {
      stopTimer(prev);
      if (prev.onHide) { try { prev.onHide(); } catch (e) { console.error("[CC] onHide " + prev.key, e); } }
    }
    CC.state.view = k;
    var secs = document.querySelectorAll("#views .view");
    for (var i = 0; i < secs.length; i++) secs[i].classList.toggle("active", secs[i].id === "v-" + k);
    var tabs = document.querySelectorAll("#nav .tab");
    for (var j = 0; j < tabs.length; j++) {
      var on = tabs[j].getAttribute("data-key") === k;
      tabs[j].classList.toggle("active", on);
      tabs[j].setAttribute("aria-current", on ? "page" : "false");
    }
    var hash = "#" + def.code;
    if (location.hash !== hash) { settingHash = true; try { location.hash = hash; } catch (e) { /* ignore */ } settingHash = false; }
    lsSet(LS_VIEW, k);
    document.title = "FUTUR ▮ " + def.code + " · " + def.title;
    ensureInit(def);
    if (def.onShow) { try { def.onShow(); } catch (e) { console.error("[CC] onShow " + def.key, e); } }
    startTimer(def);
    CC.emit("show", k);
    setTimeout(function () { var s = sectionOf(def); if (s) CC.resizeCharts(s); }, 60);
    return true;
  };
  function ensureInit(def) {
    if (def._inited) return;
    var sec = sectionOf(def);
    if (!sec) { console.error("[CC] section #v-" + def.key + " absente"); return; }
    def._inited = true;
    try { if (def.init) def.init(sec); }
    catch (e) {
      console.error("[CC] init " + def.key, e);
      sec.innerHTML = CC.panel({ code: def.code, title: def.title, fresh: "error", body: '<div class="muted">erreur d\'initialisation : ' + esc(e && e.message) + "</div>" });
    }
  }
  async function runRefresh(def) {
    if (!def || def._busy || !def.refresh) return;
    def._busy = true;
    try { await def.refresh(); CC.emit("refreshed", def.key); }
    catch (e) {
      if (!(e && /401/.test(e.message))) console.error("[CC] refresh " + def.key, e);
      CC.emit("viewerror", { key: def.key, error: e });
    }
    finally { def._busy = false; }
  }
  function startTimer(def) {
    stopTimer(def);
    if (!def._inited) return;
    runRefresh(def);
    if (def.refreshMs > 0 && document.visibilityState !== "hidden") {
      def._timer = setInterval(function () {
        if (document.visibilityState === "hidden" || CC.state.view !== def.key) return;
        runRefresh(def);
      }, def.refreshMs);
    }
  }
  function stopTimer(def) { if (def && def._timer) { clearInterval(def._timer); def._timer = null; } }
  CC.refresh = function (key) {
    var def = key ? BYKEY[resolveKey(key)] : (CC.state.view ? BYKEY[CC.state.view] : null);
    if (def && def._inited) return runRefresh(def);
  };
  CC.isAdmin = function () { return CC.state.role === "admin"; };

  function keyFromHash() {
    var h = (location.hash || "").replace(/^#/, "");
    if (!h) return null;
    return resolveKey(h.split("/")[0]);
  }

  // ── chrome : cmdbar / nav / footer ─────────────────────────────────────────
  function renderCmdbar() {
    var el = document.getElementById("cmdbar"); if (!el) return;
    el.innerHTML =
      '<div class="cmd-left">'
      + '<div class="wordmark">FUTUR <b>▮</b> TERMINAL</div>'
      + '<div class="cmdwrap"><span class="prompt" aria-hidden="true">&gt;</span>'
      + '<input id="cmd" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" list="cmdlist" '
      + 'placeholder="commande… PORT LAB TOUR CRYP FCST WLD EDGE  ? aide" aria-label="commande" title="Entrée : exécuter · / : focus · Esc : quitter">'
      + '<datalist id="cmdlist">' + REG.map(function (d) { return '<option value="' + esc(d.code) + '">' + esc(d.title) + "</option>"; }).join("")
      + '<option value="?">aide</option><option value="REFRESH">actualiser</option><option value="LOGOUT">déconnexion</option></datalist>'
      + "</div></div>"
      + '<div class="ticker" id="ticker" aria-label="bandeau prix"><div class="ticker-track"><span class="ticker-half"><span class="t muted">prix… chargement</span></span></div></div>'
      + '<div class="cmd-right">'
      + '<span class="clock utc" title="temps universel"><span class="lbl">UTC</span><span id="clk-utc">--:--:--</span></span>'
      + '<span class="clock paris" title="heure de Paris"><span class="lbl">PAR</span><span id="clk-par">--:--:--</span></span>'
      + '<span class="user" id="user"></span>'
      + '<span class="leds" id="leds" title="fraîcheur des données (statut)"></span>'
      + "</div>";
    var cmd = document.getElementById("cmd");
    cmd.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); var v = cmd.value; cmd.value = ""; CC.exec(v); if (!e.shiftKey) cmd.blur(); }
      else if (e.key === "Escape") { cmd.value = ""; cmd.blur(); }
      e.stopPropagation();
    });
    cmd.addEventListener("keyup", function (e) { e.stopPropagation(); });
    cmd.addEventListener("keypress", function (e) { e.stopPropagation(); });
  }
  function renderUser() {
    var el = document.getElementById("user"); if (!el) return;
    var admin = CC.isAdmin();
    var name = CC.state.user || "—";
    el.innerHTML = '<span class="uname" title="session">' + esc(name) + "</span>"
      + '<span class="badge role ' + (admin ? "admin amber" : "guest") + '" title="' + (admin ? "administrateur : peut modifier les prévisions" : "invité : lecture seule") + '">' + (admin ? "ADMIN" : "INVITÉ") + "</span>"
      + (CC.state.authKnown ? '<a href="/logout" title="fermer la session">déconnexion</a>' : "");
  }
  function buildNav() {
    var nav = document.getElementById("nav"); if (!nav) return;
    nav.innerHTML = REG.map(function (d, i) {
      return '<a class="tab' + (CC.state.view === d.key ? " active" : "") + '" href="#' + esc(d.code) + '" data-key="' + esc(d.key) + '" title="' + esc(d.title) + " — F" + (i + 1) + " ou " + (i + 1) + '">'
        + '<span class="ico" aria-hidden="true">' + esc(d.icon || "▪") + "</span>"
        + '<span class="fk">F' + (i + 1) + "</span>"
        + '<span class="tcode">' + esc(d.code) + "</span>"
        + '<span class="ttl">' + esc(d.title) + "</span></a>";
    }).join("");
    var tabs = nav.querySelectorAll(".tab");
    for (var j = 0; j < tabs.length; j++) {
      tabs[j].addEventListener("click", function (e) { e.preventDefault(); CC.show(this.getAttribute("data-key")); });
    }
    if (document.getElementById("cmdlist")) {
      document.getElementById("cmdlist").innerHTML = REG.map(function (d) { return '<option value="' + esc(d.code) + '">' + esc(d.title) + "</option>"; }).join("")
        + '<option value="?">aide</option><option value="REFRESH">actualiser</option><option value="LOGOUT">déconnexion</option>';
    }
  }

  // ── horloges ───────────────────────────────────────────────────────────────
  var parisFmt = null;
  try { parisFmt = new Intl.DateTimeFormat("fr-FR", { timeZone: "Europe/Paris", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }); } catch (e) { parisFmt = null; }
  function tickClock() {
    var d = new Date();
    var u = document.getElementById("clk-utc"), p = document.getElementById("clk-par");
    if (u) u.textContent = p2(d.getUTCHours()) + ":" + p2(d.getUTCMinutes()) + ":" + p2(d.getUTCSeconds());
    if (p) p.textContent = parisFmt ? parisFmt.format(d).replace(/ | /g, "") : CC.fmt.hhmmss(d);
    CC.emit("tick", d);
  }

  // ── statut (/api/status → LEDs + footer) ───────────────────────────────────
  var statusFailed = false;
  function stateOf(sv) { return sv && sv.state ? sv.state : "unknown"; }
  function svcTitle(sv) {
    var parts = [];
    parts.push(sv.label || sv.key);
    parts.push("état : " + (sv.state || "inconnu") + (sv.expected === "stopped" ? " (arrêt attendu)" : ""));
    if (sv.age_min != null) parts.push("âge : " + CC.fmt.ageMin(sv.age_min));
    if (sv.artefact) parts.push("artefact : " + sv.artefact);
    return parts.join(" · ");
  }
  function renderStatus(s) {
    var foot = document.getElementById("statusbar"), leds = document.getElementById("leds");
    if (!s) {
      if (foot) foot.innerHTML = '<span class="k">statut</span> <span class="v">indisponible</span><span class="sep">·</span>' + sessionHtml();
      if (leds) leds.innerHTML = CC.led("unknown", "statut indisponible");
      return;
    }
    var lab = s.lab || {};
    var labState = lab.live ? "fresh" : (lab.status === "OK" || lab.status === "ok") ? "stale" : (lab.status ? "error" : "unknown");
    var labTitle = "Live Alpha Lab — dernier cycle " + CC.fmt.dtfull(lab.finished_at) + (lab.age_min != null ? " (âge " + CC.fmt.ageMin(lab.age_min) + ")" : "")
      + (lab.status ? " · statut " + lab.status : "") + (lab.producers_failed ? " · " + lab.producers_failed + " producteur(s) en échec" : "") + " · capital virtuel, aucun ordre réel";
    var svcs = Array.isArray(s.services) ? s.services : [];
    var svcHtml = svcs.map(function (sv) {
      return '<span class="svc" title="' + esc(svcTitle(sv)) + '">' + esc(sv.label || sv.key) + " " + CC.led(stateOf(sv)) + "</span>";
    }).join(" ");
    var disk = s.disk || {};
    var diskFree = disk.free_gb;
    var diskCls = isNum(diskFree) ? (toNum(diskFree) < 10 ? "dn" : toNum(diskFree) < 25 ? "warn" : "v") : "v";
    if (foot) {
      foot.innerHTML =
        '<span class="svc" title="' + esc(labTitle) + '">' + CC.led(labState) + ' <span class="k">cycle Live Alpha Lab</span> <span class="v">' + esc(CC.fmt.hhmm(lab.finished_at)) + "</span>"
        + ' <span class="v">(' + esc(lab.producers_ok != null ? lab.producers_ok : "?") + "/" + esc(lab.producers_run != null ? lab.producers_run : "?") + " producteurs)</span></span>"
        + '<span class="sep">·</span><span class="k">données :</span> ' + (svcHtml || '<span class="muted">aucun service déclaré</span>')
        + '<span class="sep">·</span><span class="k">disque</span> <span class="' + diskCls + '" title="' + esc((disk.path || "") + (isNum(disk.total_gb) ? " · total " + CC.fmt.gb(disk.total_gb) : "")) + '">' + esc(CC.fmt.gb(diskFree)) + " libres</span>"
        + '<span class="sep">·</span>' + sessionHtml();
    }
    if (leds) {
      leds.innerHTML = CC.led(labState, labTitle) + svcs.map(function (sv) { return CC.led(stateOf(sv), svcTitle(sv)); }).join("");
    }
  }
  function sessionHtml() {
    return '<span class="k">session</span> <span class="v">' + esc(CC.state.user || "—") + "</span>";
  }
  async function pollStatus() {
    try {
      var s = await CC.j("/api/status");
      CC.state.status = s; statusFailed = false;
      renderStatus(s);
      CC.emit("status", s);
    } catch (e) {
      if (!statusFailed) { statusFailed = true; console.warn("[CC] /api/status indisponible : " + (e && e.message || e).toString().slice(0, 120)); }
      CC.state.status = null;
      renderStatus(null);
      CC.emit("status", null);
    }
  }

  // ── bandeau prix (/api/universe pour BTC ETH SOL 24h ; /api/lab/marks si dispo) ──
  var TICK_SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
  var marksMissing = false;
  async function pollTicker() {
    var el = document.getElementById("ticker"); if (!el) return;
    var items = [];
    try {
      var uni = await CC.j("/api/universe");
      var m = {}; (uni.assets || []).forEach(function (a) { m[a.symbol] = a; });
      TICK_SYMS.forEach(function (s) {
        var a = m[s]; if (!a) return;
        var c = a.chg24;   // en %, ex. 4.562
        items.push('<span class="t"><span class="sym">' + esc(s.replace("USDT", "")) + "</span> <b>" + esc(CC.fmt.price(a.price)) + '</b> <span class="' + CC.cls(c) + '">' + esc(isNum(c) ? fixed(c, 2, true) + " %" : DASH) + "</span> <span class=\"muted\">24h</span></span>");
      });
      items.push('<span class="t muted" title="' + esc(uni.live ? "prix temps réel" : "dernier close") + '">' + (uni.live ? "prix live" : "dernier close") + " " + esc(CC.fmt.hhmm(uni.ts)) + "</span>");
    } catch (e) {
      items.push('<span class="t muted">prix indisponibles</span>');
    }
    if (!marksMissing) {
      try {
        var mk = await CC.j("/api/lab/marks");
        var marks = (mk && mk.marks) || {};
        var keys = Object.keys(marks).slice(0, 8);
        keys.forEach(function (k) {
          var x = marks[k] || {};
          items.push('<span class="t mark" title="mark Live Alpha Lab ' + esc(CC.fmt.dtfull(x.ts)) + '"><span class="sym">' + esc(String(k).replace("USDT", "")) + "</span> <b>" + esc(CC.fmt.price(x.price)) + '</b> <span class="muted">mark</span></span>');
        });
        if (keys.length) items.push('<span class="t muted">marks as of ' + esc(CC.fmt.hhmm(mk.as_of)) + "</span>");
      } catch (e) { if (e && e.status === 404) marksMissing = true; }
    }
    items.push('<span class="t muted">capital virtuel · aucun ordre réel</span>');
    var half = '<span class="ticker-half">' + items.join("") + "</span>";
    el.innerHTML = '<div class="ticker-track">' + half + half + "</div>";
  }

  // ── overlay aide / toasts ──────────────────────────────────────────────────
  CC.overlay = function (html) {
    var o = document.getElementById("overlay"); if (!o) return;
    o.innerHTML = '<div class="ovl" role="dialog" aria-modal="true">' + html + "</div>";
    o.hidden = false;
    o.onclick = function (e) { if (e.target === o) CC.closeOverlay(); };
    var x = o.querySelector("[data-close]"); if (x) x.onclick = CC.closeOverlay;
  };
  CC.closeOverlay = function () { var o = document.getElementById("overlay"); if (o) { o.hidden = true; o.innerHTML = ""; } };
  CC.help = function () {
    var rows = REG.map(function (d, i) {
      return "<dt><span class=\"kbd\">F" + (i + 1) + "</span> <span class=\"kbd\">" + (i + 1) + '</span> <span class="amber">' + esc(d.code) + "</span></dt><dd class=\"left\">" + esc(d.title) + "</dd>";
    }).join("");
    CC.overlay(
      '<header class="panel-h"><span class="code">?</span><span class="ttl">aide — commandes et raccourcis</span><span class="tools"><button class="btn-sm" data-close>Esc fermer</button></span></header>'
      + '<div class="panel-b">'
      + "<h3>vues</h3><dl class=\"kv\" style=\"grid-template-columns:auto 1fr\">" + rows + "</dl>"
      + "<h3>commandes (barre en haut, insensible à la casse : code ou mot entier)</h3>"
      + '<dl class="kv" style="grid-template-columns:auto 1fr">'
      + '<dt><span class="amber">PORT</span> · portefeuille</dt><dd class="left">Portefeuille (Live Alpha Lab, shadow)</dd>'
      + '<dt><span class="amber">LAB</span> · live alpha lab</dt><dd class="left">producteurs, cycles, portefeuilles</dd>'
      + '<dt><span class="amber">TOUR</span> · tournoi</dt><dd class="left">tournoi ALPHA_20 — arrêté le 03/09/2026, lecture seule</dd>'
      + '<dt><span class="amber">CRYP</span> [SYMBOLE] · cryptos</dt><dd class="left">univers ; ex. <span class="kbd">CRYP BTC</span></dd>'
      + '<dt><span class="amber">FCST</span> · prévisions</dt><dd class="left">mes prévisions (admin : édition)</dd>'
      + '<dt><span class="amber">WLD</span> · world</dt><dd class="left">world monitor</dd>'
      + '<dt><span class="amber">EDGE</span> · edge lab</dt><dd class="left">croiser des séries</dd>'
      + '<dt><span class="amber">REFRESH</span> · maj</dt><dd class="left">actualiser la vue courante</dd>'
      + '<dt><span class="amber">LOGOUT</span> · déconnexion</dt><dd class="left">fermer la session</dd>'
      + '<dt><span class="amber">?</span> · aide</dt><dd class="left">cette fenêtre</dd>'
      + "</dl>"
      + "<h3>raccourcis</h3>"
      + '<dl class="kv" style="grid-template-columns:auto 1fr">'
      + '<dt><span class="kbd">/</span></dt><dd class="left">focus sur la commande</dd>'
      + '<dt><span class="kbd">Esc</span></dt><dd class="left">fermer / quitter le champ</dd>'
      + '<dt><span class="kbd">?</span></dt><dd class="left">aide</dd>'
      + '<dt><span class="kbd">F1</span>…<span class="kbd">F7</span> ou <span class="kbd">1</span>…<span class="kbd">7</span></dt><dd class="left">changer de vue (hors champ de saisie)</dd>'
      + "</dl>"
      + "<h3>LEDs</h3>"
      + '<div class="row small">' + CC.led("fresh") + " frais&nbsp;&nbsp;" + CC.led("stale") + " en retard&nbsp;&nbsp;" + CC.led("stopped") + " arrêté (attendu)&nbsp;&nbsp;" + CC.led("error") + " erreur&nbsp;&nbsp;" + CC.led("unknown") + " inconnu</div>"
      + '<div class="fn">Tout est paper/shadow : capital virtuel, aucun ordre réel. Session ' + esc(CC.state.user || "—") + " (" + (CC.isAdmin() ? "admin" : "invité, lecture seule") + ").</div>"
      + "</div>"
    );
  };
  CC.toast = function (text, kind, ms) {
    var host = document.getElementById("toasts");
    if (!host) { host = document.createElement("div"); host.id = "toasts"; document.body.appendChild(host); }
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " " + kind : "");
    el.textContent = text;
    host.appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, ms || 4000);
    return el;
  };

  // ── clavier ────────────────────────────────────────────────────────────────
  function focusCmd() { var c = document.getElementById("cmd"); if (c) { c.focus(); c.select(); } }
  CC.focusCmd = focusCmd;
  function onKey(e) {
    var t = e.target, tag = (t && t.tagName || "").toLowerCase();
    var inField = tag === "input" || tag === "textarea" || tag === "select" || (t && t.isContentEditable);
    if (e.key === "Escape") {
      var o = document.getElementById("overlay");
      if (o && !o.hidden) { CC.closeOverlay(); e.preventDefault(); return; }
      if (inField) t.blur();
      return;
    }
    if (inField || e.ctrlKey || e.metaKey || e.altKey) return;
    var m = /^F([1-7])$/.exec(e.key), idx;
    if (m) { idx = +m[1] - 1; if (REG[idx]) { e.preventDefault(); CC.show(REG[idx].key); } return; }
    if (/^[1-7]$/.test(e.key)) { idx = +e.key - 1; if (REG[idx]) { e.preventDefault(); CC.show(REG[idx].key); } return; }
    if (e.key === "/") { e.preventDefault(); focusCmd(); return; }
    if (e.key === "?") { e.preventDefault(); CC.help(); return; }
  }

  // ── démarrage ──────────────────────────────────────────────────────────────
  var clockTimer = null, statusTimer = null, tickerTimer = null;
  async function boot() {
    if (booted) return; booted = true;
    renderCmdbar();
    buildNav();
    renderStatus(null);
    // identité (401 → redirection vers /login gérée par CC.j)
    try {
      var me = await CC.j("/api/me");
      CC.state.user = me && me.user || "—";
      CC.state.role = me && me.role === "admin" ? "admin" : "guest";
      CC.state.authKnown = true;
    } catch (e) {
      if (redirecting) return;
      // backend sans /api/me : rôle invité par défaut (le serveur tranche de toute façon)
      CC.state.user = "—"; CC.state.role = "guest"; CC.state.authKnown = false;
    }
    renderUser();
    document.addEventListener("keydown", onKey);
    tickClock(); clockTimer = setInterval(tickClock, 1000);
    pollStatus(); statusTimer = setInterval(function () { if (document.visibilityState !== "hidden") pollStatus(); }, 20000);
    pollTicker(); tickerTimer = setInterval(function () { if (document.visibilityState !== "hidden") pollTicker(); }, 15000);
    window.addEventListener("hashchange", function () {
      if (settingHash) return;
      var k = keyFromHash();
      if (k && k !== CC.state.view) CC.show(k);
    });
    document.addEventListener("visibilitychange", function () {
      var vis = document.visibilityState !== "hidden";
      CC.state.visible = vis;
      var cur = CC.state.view ? BYKEY[CC.state.view] : null;
      if (!cur) return;
      if (vis) { startTimer(cur); pollStatus(); pollTicker(); } else stopTimer(cur);
    });
    var initial = keyFromHash() || resolveKey(lsGet(LS_VIEW)) || (REG[0] && REG[0].key);
    if (initial) CC.show(initial);
    CC.emit("boot", CC.state);
  }
  CC.boot = boot;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { boot(); });
  else setTimeout(boot, 0);
})();
