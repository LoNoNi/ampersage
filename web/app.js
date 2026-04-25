"use strict";

// ─── État global ──────────────────────────────────────────────────────────────

var translations = {};
var currentLang  = "FR";
var _cache       = { statuts: null, results: null };
var _dataReady   = false;
var _contenuPages = {};   // contenu posé par les étapes, préservé à la navigation
var _detailsConfig   = null; // {acCols, e2mCols, offres} — rempli par _construireTableauDetails
var _acGroups        = null; // { year: { month: { day: [records] } } } — données groupées
var _acGroupsCfg     = null; // { cfg, ligneTpl } — config pour rendu à la demande
var _settingsListenerPret = false;

// ─── Initialisation ───────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", function () {
  initDisclaimer();
  chargerTraductions().then(function () {
    appliquerLangue(currentLang);
    initNavigation();
    initLangButtons();
    initDebugPanel();
    initSettingsPage();
    _initPopupPlageHC();
    verifierVersion();
    _afficherSpinnersPartout();
    if (window.location.pathname === "/debug") _activerModeDebug();
  });
});

// ─── Disclaimer ───────────────────────────────────────────────────────────────

function initDisclaimer() {
  var overlay  = document.getElementById("disclaimer-overlay");
  var btnOk    = document.getElementById("disclaimer-accepter");
  var btnNon   = document.getElementById("disclaimer-refuser");
  var btnClose = document.getElementById("disclaimer-close");
  var chk      = document.getElementById("disclaimer-ne-plus-montrer");
  if (!overlay) return;

  var ts = parseInt(localStorage.getItem("ampersage_disclaimer_ts") || "0", 10);
  var SEPT_JOURS = 7 * 24 * 3600 * 1000;
  if (ts && (Date.now() - ts) < SEPT_JOURS) {
    overlay.classList.add("hidden");
    return;
  }

  function _refuser() { window.location.href = "https://www.google.fr"; }
  function _accepter() {
    if (chk.checked) {
      localStorage.setItem("ampersage_disclaimer_ts", Date.now().toString());
    } else {
      localStorage.removeItem("ampersage_disclaimer_ts");
    }
    overlay.classList.add("hidden");
  }

  btnOk.addEventListener("click", _accepter);
  btnNon.addEventListener("click", _refuser);
  btnClose.addEventListener("click", _refuser);
}

// ─── Traductions ──────────────────────────────────────────────────────────────

function chargerTraductions() {
  return fetch("/static/languages.json")
    .then(function (res) { return res.json(); })
    .then(function (data) { translations = data; })
    .catch(function () { translations = { FR: {}, EN: {} }; });
}

function t(cle) {
  return (translations[currentLang] || {})[cle] || cle;
}

function appliquerLangue(lang) {
  currentLang = lang;
  document.querySelectorAll("[data-i18n]").forEach(function (el) {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll(".lang-btn").forEach(function (btn) {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
}

function initLangButtons() {
  document.querySelectorAll(".lang-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      appliquerLangue(btn.dataset.lang);
    });
  });
}

// ─── Navigation ───────────────────────────────────────────────────────────────

function initNavigation() {
  document.querySelectorAll(".nav-btn[data-page]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var page = btn.dataset.page;
      document.querySelectorAll(".nav-btn").forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      document.querySelectorAll(".page").forEach(function (p) { p.classList.remove("active"); });
      var target = document.getElementById("page-" + page);
      if (target) target.classList.add("active");
      chargerPage(page);
    });
  });
}

var _PAGE_CONTENEUR = { home: "main-content", details: "details-content", settings: "settings-content" };

function chargerPage(page) {
  if (page === "home")     chargerPageAccueil();
  if (page === "details")  chargerPageDetails();
  if (page === "settings") chargerPageParametres();
}

// ─── Chargement initial ───────────────────────────────────────────────────────

var _SPINNER_HTML = '<div class="main-spinner"><span class="main-spinner-anim"></span></div>';
var _SPINNER_DEBUG = '<span class="debug-spinner"></span>';

function _afficherSpinnersPartout() {
  ["main-content", "details-content", "settings-content"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = _SPINNER_HTML;
  });
}


// ─── Application des masques ──────────────────────────────────────────────────

function _appliquerMasques(masques) {
  // ── Accueil : rapports ────────────────────────────────────────────────────
  var elHome = document.getElementById("main-content");
  if (elHome) {
    var generique = masques.generique || {};
    var rapports  = Object.values(generique).map(function (o) { return o.rapport || ""; }).join("");
    var htmlHome  = rapports || '<p class="debug-vide">Aucun rapport disponible</p>';
    _contenuPages["home"] = htmlHome;
    elHome.innerHTML = htmlHome;
  }

  // ── Détails : tableau unifié ──────────────────────────────────────────────
  _construireTableauDetails(masques);

  // ── Paramètres ───────────────────────────────────────────────────────────
  _appliquerMasquesParametres(masques);
}

function _appliquerMasquesParametres(masques) {
  var generique = masques.generique || {};

  var cartes = Object.values(generique).map(function (m) {
    return m.params || "";
  }).join("");

  var html = '<div class="settings-page">' +
    (cartes || '<p class="debug-vide">Aucun paramètre disponible</p>') +
    '</div>';

  _contenuPages["settings"] = html;
  var el = document.getElementById("settings-content");
  if (el) el.innerHTML = html;
}

var _BATCH_AC = 1000; // lignes JSON parsées par tick (aucun DOM intermédiaire)

var _MOIS_FR = ["Janvier","Février","Mars","Avril","Mai","Juin",
                "Juillet","Août","Septembre","Octobre","Novembre","Décembre"];

// ─── Helpers groupement ───────────────────────────────────────────────────────

var _acDateFmt = new Intl.DateTimeFormat("fr-FR", {
  timeZone: "Europe/Paris",
  year: "numeric", month: "2-digit", day: "2-digit"
});

function _acGroupeKey(ts) {
  /* Extrait year/month/day en heure de Paris depuis un timestamp ISO.
     Évite le décalage UTC : un relevé à 23h30 UTC = lendemain à Paris. */
  if (!ts) return { year: "", month: "", day: "" };
  var parts = _acDateFmt.formatToParts(new Date(ts));
  var y = "", m = "", d = "";
  parts.forEach(function(p) {
    if (p.type === "year")  y = p.value;
    if (p.type === "month") m = p.value;
    if (p.type === "day")   d = p.value;
  });
  return { year: y, month: y + "-" + m, day: y + "-" + m + "-" + d };
}

function _acNomMois(monthKey) {
  // "2024-01" → "Janvier 2024"
  var parts = monthKey.split("-");
  return _MOIS_FR[parseInt(parts[1], 10) - 1] + " " + parts[0];
}

function _acNomJour(dayKey) {
  // "2024-01-15" → "15/01/2024"
  var p = dayKey.split("-");
  return p[2] + "/" + p[1] + "/" + p[0];
}

function _acCellulesVides(cfg) {
  var s = '<td colspan="' + cfg.e2mCols + '"></td>';
  cfg.offres.forEach(function(o) { s += '<td colspan="' + o.cols + '"></td>'; });
  return s;
}

function _acStats(records) {
  /* Calcule { count, kwh, pmax } sur un tableau de records. */
  var count = 0, kwh = 0, pmax = 0;
  records.forEach(function(r) {
    count++;
    kwh  += r.kwh  || 0;
    if ((r.pmax || 0) > pmax) pmax = r.pmax;
  });
  return { count: count, kwh: kwh, pmax: pmax };
}

function _acLigneGroupe(ligneTpl, cfg, classes, acId, acDay, labelHtml, stats) {
  /* Construit une ligne de groupe en réutilisant la structure de colonnes de ligneTpl.
     {{ts}}   → labelHtml (toggle + nom du groupe + nb relevés)
     {{kwh}}  → somme kWh
     {{pmax}} → pmax max */
  var tr = '<tr class="ac-group ' + classes + '" data-ac-id="' + acId + '"' +
           (acDay ? ' data-ac-day="' + acDay + '"' : '') + '>';
  tr += ligneTpl
    .replace(/\{\{ts\}\}/g,   labelHtml)
    .replace(/\{\{kwh\}\}/g,  stats.kwh.toFixed(1))
    .replace(/\{\{pmax\}\}/g, stats.pmax.toFixed(1));
  tr += _acCellulesVides(cfg) + '</tr>';
  return tr;
}

function _acHtmlGroupe(cfg, ligneTpl) {
  /* Construit les lignes de regroupement (année/mois/jour).
     Années et mois ouverts par défaut. Jours fermés. Détails lazy.
     Les valeurs kWh/pmax sont alignées sur les colonnes du tableau. */
  var html = "";
  var years = Object.keys(_acGroups).sort();

  years.forEach(function(year) {
    var yearData = _acGroups[year];
    var months   = Object.keys(yearData).sort();

    var allYearRecs = [];
    months.forEach(function(m) {
      Object.keys(yearData[m]).forEach(function(d) {
        allYearRecs = allYearRecs.concat(yearData[m][d]);
      });
    });
    var yStats = _acStats(allYearRecs);
    var yLabel = '<button class="ac-toggle" data-ac-open="1">▼</button>' +
                 ' <strong>' + year + '</strong>' +
                 ' <span class="ac-count">' + yStats.count + '\u00a0relevés</span>';

    html += _acLigneGroupe(ligneTpl, cfg, 'ac-year', 'y-' + year, null, yLabel, yStats);

    months.forEach(function(month) {
      var monthData = yearData[month];
      var days      = Object.keys(monthData).sort();

      var allMonthRecs = [];
      days.forEach(function(d) { allMonthRecs = allMonthRecs.concat(monthData[d]); });
      var mStats = _acStats(allMonthRecs);
      var mLabel = '<button class="ac-toggle" data-ac-open="1">▼</button>' +
                   ' <strong>' + _acNomMois(month) + '</strong>' +
                   ' <span class="ac-count">' + mStats.count + '\u00a0relevés</span>';

      html += _acLigneGroupe(ligneTpl, cfg,
        'ac-month ac-child-y-' + year, 'm-' + month, null, mLabel, mStats);

      days.forEach(function(day) {
        var recs   = monthData[day];
        var dStats = _acStats(recs);
        var dLabel = '<button class="ac-toggle" data-ac-open="0">▶</button>' +
                     ' ' + _acNomJour(day) +
                     ' <span class="ac-count">' + dStats.count + '\u00a0relevés</span>';

        html += _acLigneGroupe(ligneTpl, cfg,
          'ac-day ac-child-m-' + month + ' hidden', 'd-' + day, day, dLabel, dStats);
      });
    });
  });
  return html;
}

function _acRendreDetailJour(tbody, dayRow, day) {
  /* Crée les lignes de détail pour un jour donné et les insère après dayRow. */
  if (!_acGroupsCfg || !_acGroups) return;
  var cfg      = _acGroupsCfg.cfg;
  var ligneTpl = _acGroupsCfg.ligneTpl;
  var parts    = day.split("-"); // ["2024","01","15"]
  var recs     = ((_acGroups[parts[0]] || {})[parts[0]+"-"+parts[1]] || {})[day] || [];

  var html = "";
  recs.forEach(function(r) {
    html += '<tr class="ac-detail ac-child-d-' + day + '">' +
      ligneTpl
        .replace(/\{\{ts\}\}/g,   _formaterTs(r.ts))
        .replace(/\{\{kwh\}\}/g,  r.kwh  !== undefined ? r.kwh.toFixed(3)  : "")
        .replace(/\{\{pmax\}\}/g, r.pmax !== undefined ? r.pmax.toFixed(1) : "") +
      _acCellulesVides(cfg) + '</tr>';
  });

  var tmp  = document.createElement("table");
  tmp.innerHTML = "<tbody>" + html + "</tbody>";
  var rows = tmp.querySelector("tbody");
  var frag = document.createDocumentFragment();
  while (rows.firstChild) frag.appendChild(rows.firstChild);
  dayRow.parentNode.insertBefore(frag, dayRow.nextSibling);
  dayRow.dataset.acRendered = "1";
}

function _acAttacherEvenements(tbody) {
  /* Délégation d'événements sur le tbody pour expand/collapse. */
  tbody.addEventListener("click", function(e) {
    var btn = e.target.closest(".ac-toggle");
    if (!btn) return;
    var row    = btn.closest("tr");
    var acId   = row.dataset.acId;
    var isOpen = btn.dataset.acOpen === "1";

    if (isOpen) {
      // Fermer : masquer tous les descendants (récursif via classe)
      _acMasquerDescendants(tbody, acId);
      btn.textContent    = "▶";
      btn.dataset.acOpen = "0";
    } else {
      // Ouvrir : afficher enfants directs seulement
      var day = row.dataset.acDay;
      if (day && !row.dataset.acRendered) {
        _acRendreDetailJour(tbody, row, day);
      }
      tbody.querySelectorAll(".ac-child-" + acId).forEach(function(c) {
        c.classList.remove("hidden");
      });
      btn.textContent    = "▼";
      btn.dataset.acOpen = "1";
    }
  });
}

function _acMasquerDescendants(tbody, acId) {
  /* Masque récursivement TOUS les descendants d'un groupe (sans condition d'état). */
  tbody.querySelectorAll(".ac-child-" + acId).forEach(function(c) {
    c.classList.add("hidden");
    var childBtn = c.querySelector(".ac-toggle");
    if (childBtn) {
      childBtn.textContent    = "▶";
      childBtn.dataset.acOpen = "0";
    }
    var childId = c.dataset.acId;
    if (childId) _acMasquerDescendants(tbody, childId);
  });
}

function _injecterGroupesAC(tbody) {
  /* Injecte les lignes de regroupement dans tbody (avant la ligne spinner).
     Appelé après chargement et à chaque navigation vers Détails. */
  if (!_acGroups || !_acGroupsCfg) return;
  var spinnerRow = tbody.lastChild;
  var tmp = document.createElement("table");
  tmp.innerHTML = "<tbody>" + _acHtmlGroupe(_acGroupsCfg.cfg, _acGroupsCfg.ligneTpl) + "</tbody>";
  var rows = tmp.querySelector("tbody");
  var frag = document.createDocumentFragment();
  while (rows.firstChild) frag.appendChild(rows.firstChild);
  tbody.insertBefore(frag, spinnerRow);
  _acAttacherEvenements(tbody);
}

// ─── Chargement api_conso ─────────────────────────────────────────────────────

function _appliquerApiConso(meta, masques, onAffiché) {
  /* Étape 4 : groupe les records en mémoire, insère uniquement les lignes
     de regroupement (année/mois/jour). Détail rendu à la demande.
     onAffiché(nbRelevés) est appelé une fois le DOM peuplé. */
  if (!_detailsConfig) return;

  var total    = (meta || {}).count || 0;
  var ligneTpl = ((masques || {}).api_conso || {}).tableau_ligne || "";
  var cfg      = _detailsConfig;

  var banner = document.getElementById("loading-banner");
  var fill   = document.getElementById("loading-banner-fill");
  var lbl    = document.getElementById("loading-banner-lbl");

  function _majBarre(pct) {
    if (fill) fill.style.width = pct + "%";
    if (lbl)  lbl.textContent  = pct + "\u00a0%";
  }
  function _cacherBanner() {
    if (banner) banner.classList.add("hidden");
  }

  if (total === 0) {
    var tbodyVide = document.getElementById("details-tbody");
    if (tbodyVide) tbodyVide.innerHTML = '<tr><td colspan="99" class="debug-vide">Aucune donnée</td></tr>';
    return;
  }

  if (banner) banner.classList.remove("hidden");
  _majBarre(0);

  // Groupement en mémoire
  var groupes = {};
  var nbRecu  = 0;

  function _grouperLigne(r) {
    var k = _acGroupeKey(r.ts);
    if (!groupes[k.year])              groupes[k.year] = {};
    if (!groupes[k.year][k.month])     groupes[k.year][k.month] = {};
    if (!groupes[k.year][k.month][k.day]) groupes[k.year][k.month][k.day] = [];
    groupes[k.year][k.month][k.day].push(r);
    nbRecu++;
  }

  function _insererDansDOM() {
    _majBarre(100);
    _acGroups    = groupes;
    _acGroupsCfg = { cfg: cfg, ligneTpl: ligneTpl };
    setTimeout(function() {
      var tbody = document.getElementById("details-tbody");
      if (tbody) {
        _injecterGroupesAC(tbody);
        // Effacer le spinner de la colonne horodateur (1ère cellule du spinner row)
        var spinnerRow = tbody.lastChild;
        if (spinnerRow) {
          var firstCell = spinnerRow.querySelector("td");
          if (firstCell) firstCell.innerHTML = "";
        }
      }
      _cacherBanner();
      if (typeof onAffiché === "function") onAffiché(nbRecu);
    }, 50);
  }

  fetch("/data/api_conso_records.ndjson")
    .then(function(response) { return response.text(); })
    .then(function(texte) {
      var lignes = texte.split("\n");
      var idx = 0;
      function _batch() {
        var fin = Math.min(idx + _BATCH_AC, lignes.length);
        for (var i = idx; i < fin; i++) {
          var l = lignes[i].trim();
          if (l) try { _grouperLigne(JSON.parse(l)); } catch(e) {}
        }
        idx = fin;
        _majBarre(Math.round(nbRecu / total * 100));
        if (idx < lignes.length) {
          setTimeout(_batch, 0);
        } else {
          _insererDansDOM();
        }
      }
      setTimeout(_batch, 0);
    })
    .catch(function(err) {
      console.error("[api_conso] Erreur fetch NDJSON :", err);
      _cacherBanner();
    });
}

function _formaterTs(ts) {
  if (!ts) return "";
  try {
    var d = new Date(ts);
    return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit", timeZone: "Europe/Paris" }) +
           " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" });
  } catch (e) { return ts; }
}

function _parseBandeau(html) {
  /* Parse toutes les <tr> d'un fragment detail_bandeau.
     Retourne un tableau de { innerHtml, cols } par ligne. */
  var div = document.createElement("div");
  div.innerHTML = "<table><tbody>" + html + "</tbody></table>";
  var rows = div.querySelectorAll("tr");
  return Array.prototype.map.call(rows, function (tr) {
    return { innerHtml: tr.innerHTML, cols: tr.querySelectorAll("th").length };
  });
}

function _compterTh(html) {
  var div = document.createElement("div");
  div.innerHTML = "<table><tr>" + html + "</tr></table>";
  return div.querySelectorAll("th").length;
}

function _avecRowspan(thsHtml, rowspan) {
  /* Ajoute rowspan="N" sur chaque <th> d'un fragment HTML. */
  if (rowspan <= 1) return thsHtml;
  return thsHtml.replace(/<th(\s|>)/gi, '<th rowspan="' + rowspan + '"$1');
}

function _normaliserColspan(innerHtml, cols) {
  /* Remplace tous les colspan par la valeur réelle de colonnes de l'offre.
     Les lignes de groupe du bandeau peuvent avoir des colspan incorrects. */
  return innerHtml.replace(/colspan="[^"]*"/gi, 'colspan="' + cols + '"');
}

function _construireTableauDetails(masques) {
  var spinner = '<div class="main-spinner"><span class="main-spinner-anim"></span></div>';

  // api_conso
  var acTitre = (masques.api_conso || {}).tableau_titre || "";
  var acCols  = _compterTh(acTitre) || 3;

  // eco2mix
  var e2mTitre = (masques.eco2mix || {}).tableau_titre || "";
  var e2mCols  = _compterTh(e2mTitre) || 2;

  // generique : toutes les lignes du detail_bandeau, placeholders substitués
  var _SPINNER_INLINE = '<span class="details-inline-spinner"></span>';
  var generique = masques.generique || {};
  var offres    = Object.entries(generique).map(function (kv) {
    var id     = kv[0]; var m = kv[1];
    var lignes = _parseBandeau(m.detail_bandeau || "");
    var cols   = lignes.length ? lignes[lignes.length - 1].cols : 2;
    return { id: id, cols: cols, lignes: lignes };
  });

  // Nombre de lignes de bandeau (même nombre pour toutes les offres)
  var nbLignesBandeau = offres.reduce(function (max, o) {
    return Math.max(max, o.lignes.length);
  }, 0);

  // ── thead ─────────────────────────────────────────────────────────────────
  // api_conso et eco2mix utilisent rowspan sur toutes les lignes de groupe
  // → une seule cellule quelle que soit la hauteur du bandeau le plus long.
  // Les offres s'alignent par le bas : padding en haut avec rowspan si nécessaire.
  var theadHtml = "";

  if (nbLignesBandeau <= 1) {
    // Cas simple : une seule ligne
    theadHtml += "<tr>" + acTitre + e2mTitre;
    offres.forEach(function (o) {
      var derniere = o.lignes[o.lignes.length - 1];
      theadHtml += derniere
        ? derniere.innerHtml
        : '<th colspan="' + o.cols + '">' + o.id + "</th>";
    });
    theadHtml += "</tr>";
  } else {
    // Ligne 0 : ac + e2m avec rowspan, puis première ligne utile de chaque offre
    theadHtml += "<tr>";
    theadHtml += _avecRowspan(acTitre,  nbLignesBandeau);
    theadHtml += _avecRowspan(e2mTitre, nbLignesBandeau);
    offres.forEach(function (o) {
      var offset = nbLignesBandeau - o.lignes.length;  // lignes vides en haut
      if (offset > 0) {
        // Cellule vide qui s'étend sur toutes les lignes de padding
        theadHtml += '<th colspan="' + o.cols + '" rowspan="' + offset +
                     '" class="details-th-vide"></th>';
        // Puis première ligne réelle de l'offre (hors dernière)
        if (o.lignes.length > 1) theadHtml += _normaliserColspan(o.lignes[0].innerHtml, o.cols);
      } else {
        if (o.lignes.length > 1) theadHtml += _normaliserColspan(o.lignes[0].innerHtml, o.cols);
      }
    });
    theadHtml += "</tr>";

    // Lignes intermédiaires : uniquement le contenu des offres (ac/e2m déjà couverts)
    for (var i = 1; i < nbLignesBandeau - 1; i++) {
      theadHtml += "<tr>";
      offres.forEach(function (o) {
        var offset   = nbLignesBandeau - o.lignes.length;
        var ligneIdx = i - offset;
        if (ligneIdx > 0 && ligneIdx < o.lignes.length - 1) {
          theadHtml += _normaliserColspan(o.lignes[ligneIdx].innerHtml, o.cols);
        }
        // Sinon : couvert par le rowspan vide posé à la ligne 0
      });
      theadHtml += "</tr>";
    }

    // Dernière ligne : colonnes réelles de chaque offre (ac/e2m couverts par rowspan)
    theadHtml += "<tr>";
    offres.forEach(function (o) {
      var derniere = o.lignes[o.lignes.length - 1];
      theadHtml += derniere
        ? derniere.innerHtml
        : '<th colspan="' + o.cols + '">' + o.id + "</th>";
    });
    theadHtml += "</tr>";
  }

  // Stocker la config pour les étapes suivantes
  _detailsConfig = { acCols: acCols, e2mCols: e2mCols, offres: offres };

  // ── tbody : une ligne de spinners ─────────────────────────────────────────
  var tbodyHtml = "<tr>";
  tbodyHtml += '<td colspan="' + acCols  + '" class="details-spinner-cell">' + spinner + "</td>";
  tbodyHtml += '<td colspan="' + e2mCols + '" class="details-spinner-cell">' + spinner + "</td>";
  offres.forEach(function (o) {
    tbodyHtml += '<td colspan="' + o.cols + '" class="details-spinner-cell">' + spinner + "</td>";
  });
  tbodyHtml += "</tr>";

  var html = '<div class="details-scroll">' +
    '<table class="details-table">' +
      "<thead>" + theadHtml + "</thead>" +
      "<tbody id=\"details-tbody\">" + tbodyHtml + "</tbody>" +
    "</table></div>";

  _contenuPages["details"] = html;
  var elDetails = document.getElementById("details-content");
  if (elDetails) elDetails.innerHTML = html;
}


// ─── Pages ────────────────────────────────────────────────────────────────────

function chargerPageAccueil() {
  var el = document.getElementById("main-content");
  if (!el) return;
  el.innerHTML = _contenuPages["home"] !== undefined ? _contenuPages["home"] : _SPINNER_HTML;
}

function chargerPageDetails() {
  var el = document.getElementById("details-content");
  if (!el) return;
  // Restaurer la structure de base (thead + spinner row, sans données)
  el.innerHTML = _contenuPages["details"] !== undefined ? _contenuPages["details"] : _SPINNER_HTML;
  // Si les groupes sont prêts, les réinjecter
  if (_acGroups) {
    var tbody = document.getElementById("details-tbody");
    if (tbody) _injecterGroupesAC(tbody);
  }
}

// ─── Page Paramètres ──────────────────────────────────────────────────────────

function initSettingsPage() {
  if (_settingsListenerPret) return;
  var el = document.getElementById("settings-content");
  if (!el) return;
  el.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-save]");
    if (btn) { sauvegarderParams(btn.dataset.save); return; }
    var header = e.target.closest(".settings-card-header[data-toggle]");
    if (header) _toggleCard(header.dataset.toggle);
  });
  _settingsListenerPret = true;
}

function chargerPageParametres() {
  var el = document.getElementById("settings-content");
  if (!el) return;
  if (_contenuPages["settings"]) {
    el.innerHTML = _contenuPages["settings"];
    return;
  }
  el.innerHTML = _SPINNER_HTML;
}

function _afficherPageParametres(expose) {
  var el = document.getElementById("settings-content");
  if (!el) return;

  var fournisseurs = expose.fournisseurs || {};
  var modules      = expose.modules_complementaires || {};

  // Séparer api_conso des tarifs
  var conso   = {};
  var tarifs  = {};
  Object.keys(fournisseurs).forEach(function (nom) {
    if (nom === "api_conso") { conso[nom]  = fournisseurs[nom]; }
    else                     { tarifs[nom] = fournisseurs[nom]; }
  });

  var html = '<div class="settings-page">';
  if (Object.keys(conso).length) {
    html += _htmlSection("Consommation", conso);
  }
  if (Object.keys(modules).length) {
    html += _htmlSection("Modules complémentaires", modules);
  }
  if (Object.keys(tarifs).length) {
    html += _htmlSection("Fournisseurs", tarifs);
  }
  html += "</div>";
  el.innerHTML = html;

  // Les panels sont chargés à la demande (au premier clic sur la carte)
}

function _htmlSection(titre, modules) {
  var html = '<div class="settings-section">'
    + '<h2 class="settings-section-titre">' + titre + '</h2>';
  Object.keys(modules).forEach(function (nom) {
    html += _htmlCard(nom, modules[nom].statut, modules[nom].parametres || {});
  });
  return html + '</div>';
}

function _htmlCard(nom, statut, params) {
  var cls = (statut === "actif" || statut === "mock") ? "ok"
          : (statut === "erreur") ? "err" : "warn";
  // Les params sont encodés en JSON dans un attribut data pour le chargement paresseux
  var paramsJson = JSON.stringify(params).replace(/'/g, "&#39;");
  return '<div class="settings-card" id="settings-card-' + nom + '">'
    + '<div class="settings-card-header" data-toggle="' + nom + '" data-params=\'' + paramsJson + '\'>'
    +   '<span class="settings-card-arrow">&#9654;</span>'
    +   '<span class="settings-card-nom">' + nom.replace(/_/g, "\u00a0") + '</span>'
    +   '<span class="settings-card-statut ' + cls + '">' + (statut || "\u2014") + '</span>'
    + '</div>'
    + '<div class="settings-card-body hidden" id="settings-body-' + nom + '">'
    +   '<div id="panel-container-' + nom + '" class="settings-panel-container">'
    +   '</div>'
    +   '<div class="settings-card-footer">'
    +     '<span class="settings-save-msg" id="settings-msg-' + nom + '"></span>'
    +     '<button class="settings-save-btn" data-save="' + nom + '">Sauvegarder</button>'
    +   '</div>'
    + '</div>'
    + '</div>';
}

function _toggleCard(nom) {
  var body   = document.getElementById("settings-body-" + nom);
  var card   = document.getElementById("settings-card-" + nom);
  var header = card && card.querySelector(".settings-card-header");
  if (!body) return;

  var hidden = body.classList.toggle("hidden");
  var arrow  = card && card.querySelector(".settings-card-arrow");
  if (arrow) arrow.textContent = hidden ? "\u25BA" : "\u25BC";

  // Chargement paresseux : on charge le panel uniquement à la première ouverture
  if (!hidden && header && !header.dataset.panelCharge) {
    header.dataset.panelCharge = "1";
    var params = {};
    try { params = JSON.parse(header.dataset.params || "{}"); } catch (e) {}
    var conteneur = document.getElementById("panel-container-" + nom);
    if (conteneur) conteneur.innerHTML = '<span class="debug-spinner"></span>';
    _chargerPanel(nom, params);
  }
}

function _chargerPanel(nom, paramsActuels) {
  fetch("/api/panel/" + nom)
    .then(function (r) {
      if (!r.ok) throw new Error("introuvable");
      return r.text();
    })
    .then(function (html) {
      var conteneur = document.getElementById("panel-container-" + nom);
      if (!conteneur) return;
      conteneur.innerHTML = html;
      _execScripts(conteneur);
      // Peupler les champs avec les valeurs actuelles
      var setter = window["setParams_" + nom];
      if (typeof setter === "function") {
        setter(paramsActuels);
      } else {
        _setParamsFallback(conteneur, paramsActuels);
      }
    })
    .catch(function () {
      var conteneur = document.getElementById("panel-container-" + nom);
      if (conteneur) {
        conteneur.innerHTML = '<p class="settings-no-panel">Aucun panel disponible pour ce module.</p>';
      }
    });
}

function _execScripts(conteneur) {
  // Les scripts injectés via innerHTML ne s'exécutent pas — on les recrée manuellement
  conteneur.querySelectorAll("script").forEach(function (ancien) {
    var nouveau = document.createElement("script");
    Array.from(ancien.attributes).forEach(function (attr) {
      nouveau.setAttribute(attr.name, attr.value);
    });
    nouveau.textContent = ancien.textContent;
    ancien.parentNode.replaceChild(nouveau, ancien);
  });
}

function _setParamsFallback(conteneur, params) {
  conteneur.querySelectorAll("[data-param]").forEach(function (el) {
    var v = params[el.dataset.param];
    if (v === undefined || v === null) return;
    if (el.type === "checkbox") {
      el.checked = (v === true || v === "true");
    } else {
      el.value = v;
    }
  });
}

// Appelée par les boutons Sauvegarder (via délégation) et potentiellement par les panels
function sauvegarderParams(nom) {
  var getter = window["getParams_" + nom];
  var params;
  if (typeof getter === "function") {
    params = getter();
  } else {
    params = {};
    var conteneur = document.getElementById("panel-container-" + nom);
    if (conteneur) {
      conteneur.querySelectorAll("[data-param]:not([readonly])").forEach(function (el) {
        params[el.dataset.param] = (el.type === "checkbox") ? el.checked : (el.value || null);
      });
    }
  }

  var msgEl = document.getElementById("settings-msg-" + nom);
  if (msgEl) { msgEl.textContent = "Sauvegarde\u2026"; msgEl.className = "settings-save-msg"; }

  fetch("/api/save_params", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupe: nom, params: params }),
  })
    .then(function (r) { return r.json(); })
    .then(function (rep) {
      if (!msgEl) return;
      msgEl.textContent = rep.status ? "Sauvegard\xe9\u00a0\u2713" : ("Erreur\u00a0: " + rep.error);
      msgEl.className   = "settings-save-msg " + (rep.status ? "ok" : "err");
      setTimeout(function () {
        if (msgEl) { msgEl.textContent = ""; msgEl.className = "settings-save-msg"; }
      }, 3000);
    })
    .catch(function () {
      if (msgEl) { msgEl.textContent = "Erreur r\xe9seau"; msgEl.className = "settings-save-msg err"; }
    });
}

// Appelée par les panels après une action (ex: trigger update dans api_conso)
function chargerParamsScript(nom) {
  return fetch("/api/script", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ script: nom, mode: "GET_PARAM", params: {} }),
  })
    .then(function (r) { return r.json(); })
    .then(function (rep) {
      if (rep.status) {
        var setter = window["setParams_" + nom];
        if (typeof setter === "function") setter(rep.data);
        else {
          var c = document.getElementById("panel-container-" + nom);
          if (c) _setParamsFallback(c, rep.data);
        }
      }
      return rep;
    });
}

// Appelée par les panels après une mise à jour des données (import CSV, etc.)
function chargerContenuPrincipal() {
  if (!_dataReady) return;
  var btn = document.querySelector(".nav-btn.active");
  chargerPage(btn ? btn.dataset.page : "home");
}

// ─── Panneau de débogage ──────────────────────────────────────────────────────

function _activerModeDebug() {
  var panel = document.getElementById("debug-panel");
  var btn   = document.getElementById("debug-toggle");
  if (panel) panel.classList.remove("hidden");
  if (btn)   btn.classList.add("active");
}

function initDebugPanel() {
  document.getElementById("debug-toggle").addEventListener("click", function () {
    var panel = document.getElementById("debug-panel");
    var btn   = document.getElementById("debug-toggle");
    var hidden = panel.classList.toggle("hidden");
    btn.classList.toggle("active", !hidden);
    if (!hidden) _debugAfficherCache();
  });
  document.getElementById("debug-close").addEventListener("click", function () {
    document.getElementById("debug-panel").classList.add("hidden");
    document.getElementById("debug-toggle").classList.remove("active");
  });
  // Délégation unique pour le repliement des accordéons JSON
  document.getElementById("debug-json-accordeons").addEventListener("click", function (e) {
    var header = e.target.closest(".debug-module-header");
    if (!header) return;
    var body  = header.nextElementSibling;
    var arrow = header.querySelector(".debug-module-arrow");
    if (!body) return;
    var hidden = body.classList.toggle("hidden");
    if (arrow) arrow.textContent = hidden ? "▶" : "▼";
  });

  document.getElementById("debug-etape-btn").addEventListener("click", _debugEtapeSuivante);
  document.getElementById("debug-etape-reset").addEventListener("click", _debugEtapeReset);
  _debugEtapeReset();

  // ── Resize par drag ───────────────────────────────────────────────────────
  var handle = document.getElementById("debug-resize-handle");
  var panel  = document.getElementById("debug-panel");
  var startY, startH;

  handle.addEventListener("mousedown", function (e) {
    e.preventDefault();
    startY = e.clientY;
    startH = panel.getBoundingClientRect().height;
    handle.classList.add("dragging");
    document.addEventListener("mousemove", _onResizeMove);
    document.addEventListener("mouseup",   _onResizeUp);
  });

  function _onResizeMove(e) {
    var delta  = startY - e.clientY;   // vers le haut = agrandir
    var newH   = Math.min(Math.max(startH + delta, 120), window.innerHeight * 0.9);
    panel.style.height = newH + "px";
  }

  function _onResizeUp() {
    handle.classList.remove("dragging");
    document.removeEventListener("mousemove", _onResizeMove);
    document.removeEventListener("mouseup",   _onResizeUp);
  }
}

// ─── Mode pas à pas ───────────────────────────────────────────────────────────

var _ETAPES = [
  { id: "manifest",  label: "Manifest",  url: "/data/manifest.json"  },
  { id: "masques",   label: "Masques",   url: "/data/masques.json"   },
  { id: "params",    label: "Params",    url: "/data/params.json"    },
  { id: "api_conso", label: "API Conso", url: "/data/api_conso.json" },
  { id: "eco2mix",   label: "Eco2mix",   url: "/data/eco2mix.json"   },
];

// état : { idx, data, resultats: [{ok, ms, resume}|null] }
var _etape = { idx: 0, data: {}, resultats: [] };

function _debugCreerCase(id, label) {
  /* Crée une case accordéon vide (en attente) si elle n'existe pas encore. */
  if (document.getElementById("debug-accord-" + id)) return;
  var acc = document.getElementById("debug-json-accordeons");
  if (!acc) return;
  var div = document.createElement("div");
  div.id = "debug-accord-" + id;
  div.className = "debug-module";
  div.innerHTML =
    '<div class="debug-module-header">' +
      '<span class="debug-module-arrow">▶</span>' +
      '<span class="debug-module-nom">' + label + '</span>' +
      '<span class="debug-module-size debug-vide">en attente</span>' +
    '</div>' +
    '<div class="debug-module-body hidden">' +
      '<span class="debug-vide">Pas encore chargé</span>' +
    '</div>';
  acc.appendChild(div);
}

function _debugEtapeReset() {
  _etape         = { idx: 0, data: {}, resultats: new Array(_ETAPES.length).fill(null) };
  _detailsConfig = null;
  _acGroups      = null;
  _acGroupsCfg   = null;
  var acc = document.getElementById("debug-json-accordeons");
  if (acc) acc.innerHTML = "";
  // Seule la case manifest est connue d'avance
  _debugCreerCase("manifest", "Manifest");
  _debugEtapeRendreGantt();
  var btn = document.getElementById("debug-etape-btn");
  var cpt = document.getElementById("debug-etape-compteur");
  if (btn) { btn.disabled = false; btn.textContent = "▶ Étape suivante"; }
  if (cpt) cpt.textContent = "0 / " + _ETAPES.length;
}

function _debugEtapeSuivante() {
  if (_etape.idx >= _ETAPES.length) return;
  var etape = _ETAPES[_etape.idx];
  var btn   = document.getElementById("debug-etape-btn");
  if (btn) btn.disabled = true;

  // Passe l'étape courante en état "en cours" dans le gantt
  _debugEtapeRendreGantt(true);

  var t0 = Date.now();
  fetch(etape.url)
    .then(function (r) {
      var status = r.status;
      return r.json().then(function (d) { return { status: status, data: d }; });
    })
    .catch(function (e) { return { status: 0, data: null }; })
    .then(function (res) {
      var ms = Date.now() - t0;
      var ok = res.status >= 200 && res.status < 300;
      _etape.data[etape.id]        = res.data;
      _etape.resultats[_etape.idx] = { ok: ok, ms: ms, resume: _debugEtapeResume(etape.id, res.data) };
      _etape.idx++;

      var cpt = document.getElementById("debug-etape-compteur");
      if (cpt) cpt.textContent = _etape.idx + " / " + _ETAPES.length;

      _debugEtapeRendreGantt(false);

      _debugAfficherJson(etape.id, etape.label, res.data, ok);
      if (etape.id === "masques" && ok) _appliquerMasques(res.data);
      if (etape.id === "params"  && ok) verifierHcRequises(res.data);
      if (etape.id === "api_conso" && ok) {
        // Marquer "affichage en cours" jusqu'à ce que le DOM soit peuplé
        var acIdx = _etape.idx - 1;
        _etape.resultats[acIdx].affichage = true;
        _etape.resultats[acIdx].resume    = "affichage en cours…";
        _debugEtapeRendreGantt(false);
        _appliquerApiConso(res.data, _etape.data["masques"], function(nbAffiches) {
          _etape.resultats[acIdx].affichage = false;
          _etape.resultats[acIdx].resume    = nbAffiches + "\u00a0relevés affichés" +
                                              (res.data.mock ? " (mock)" : "");
          _debugEtapeRendreGantt(false);
        });
        return; // ne pas re-rendre le gantt une seconde fois ci-dessous
      }

      if (_etape.idx >= _ETAPES.length) {
        if (btn) { btn.disabled = true; btn.textContent = "✓ Terminé"; }
      } else {
        if (btn) btn.disabled = false;
      }
    });
}

function _debugEtapeRendreGantt(enCours) {
  var gantt = document.getElementById("debug-etape-gantt");
  if (!gantt) return;

  // Calcule la durée max pour proportionner les barres
  var maxMs = _etape.resultats.reduce(function (m, r) {
    return r ? Math.max(m, r.ms) : m;
  }, 1);

  gantt.innerHTML = _ETAPES.map(function (e, i) {
    var r   = _etape.resultats[i];
    var etat;                        // "fait" | "encours" | "attente"
    if (r && r.affichage) etat = "encours";   // données reçues, affichage en cours
    else if (r !== null)  etat = "fait";
    else if (i === _etape.idx && enCours) etat = "encours";
    else                  etat = "attente";

    var barPct  = r ? Math.round(r.ms / maxMs * 100) : 0;
    var icon    = etat === "fait" ? (r.ok ? "✓" : "✗") : (etat === "encours" ? "…" : "·");
    var iconCls = etat === "fait" ? (r.ok ? "ok" : "err") : "";
    var ms      = r ? r.ms + "ms" : "";
    var resume  = r ? r.resume : "";

    return '<div class="debug-gantt-row debug-gantt-' + etat + '">' +
      '<div class="debug-gantt-header">' +
        '<span class="debug-gantt-icon ' + iconCls + '">' + icon + '</span>' +
        '<span class="debug-gantt-label">' + e.label + '</span>' +
        '<span class="debug-gantt-ms">' + ms + '</span>' +
      '</div>' +
      (etat !== "attente" ?
        '<div class="debug-gantt-bar-track">' +
          '<div class="debug-gantt-bar" style="width:' + (etat === "encours" ? 100 : barPct) + '%"></div>' +
        '</div>' : '') +
      (resume ? '<div class="debug-gantt-resume">' + resume + '</div>' : '') +
    '</div>';
  }).join("");
}

function _debugEtapeResume(id, data) {
  if (!data) return "réponse vide";
  if (id === "manifest") {
    return Object.keys(data).map(function (k) {
      return k + "\u00a0" + data[k].slice(0, 7) + "…";
    }).join("  ");
  }
  if (id === "masques")   return Object.keys(data).length + " modules";
  if (id === "params")    { var n = (data.offres||[]).length; return n + " offre" + (n>1?"s":""); }
  if (id === "api_conso") { return (data.count||0) + "\u00a0relevés récupérés" + (data.mock?" (mock)":""); }
  if (id === "eco2mix")   { var n = Object.keys(data.creneaux||{}).length; return n + " créneaux"; }
  return "";
}


function _debugAfficherJson(id, label, data, ok) {
  var conteneur = document.getElementById("debug-json-accordeons");
  if (!conteneur) return;

  // Construire le corps de l'accordéon
  var corps;
  if (!data) {
    corps = '<span class="debug-vide">Pas de données</span>';
  } else if (id === "manifest") {
    // Affichage spécial : hash par fichier
    var ts = document.getElementById("debug-ts");
    if (ts) ts.textContent = "Manifest : " + new Date().toLocaleTimeString("fr-FR");
    // Créer dynamiquement une case pour chaque fichier référencé dans le manifest
    var etapesIndex = {};
    _ETAPES.forEach(function (e) { etapesIndex[e.id] = e.label; });
    Object.keys(data).forEach(function (cle) {
      _debugCreerCase(cle, etapesIndex[cle] || cle);
    });
    var ordre = Object.keys(data);
    corps = '<div class="debug-kv">' +
      ordre.map(function (cle) {
        var hash = data[cle];
        if (!hash) return '';
        var court = hash.slice(0, 8);
        var cached = _cache._manifestPrev && _cache._manifestPrev[cle] === hash;
        var cls   = cached ? "warn" : "ok";
        var lbl   = cached ? "cache" : "nouveau";
        return '<div class="debug-kv-row">' +
          '<span class="debug-key">' + cle + '.json</span>' +
          '<span class="debug-val ' + cls + '" title="' + hash + '">' + court + '… <em>' + lbl + '</em></span>' +
        '</div>';
      }).join("") + '</div>';
  } else {
    // JSON brut
    corps = '<pre class="debug-json">' + _debugFormatJson(data) + '</pre>';
  }

  var taille = data ? _debugTaille(data) : "—";
  var errCls = ok === false ? ' debug-module-err' : '';

  // Créer ou mettre à jour l'élément accordéon
  var existant = document.getElementById("debug-accord-" + id);
  if (existant) {
    existant.querySelector(".debug-module-size").textContent = taille;
    existant.querySelector(".debug-module-body").innerHTML = corps;
  } else {
    var div = document.createElement("div");
    div.id = "debug-accord-" + id;
    div.className = "debug-module" + errCls;
    div.innerHTML =
      '<div class="debug-module-header">' +
        '<span class="debug-module-arrow">▶</span>' +
        '<span class="debug-module-nom">' + label + '</span>' +
        '<span class="debug-module-size">' + taille + '</span>' +
      '</div>' +
      '<div class="debug-module-body hidden">' + corps + '</div>';
    conteneur.appendChild(div);
  }
}

function _debugTaille(val) {
  if (Array.isArray(val))             return val.length + " éléments";
  if (val && typeof val === "object") return Object.keys(val).length + " clés";
  return "";
}

function _debugFormatJson(val) {
  return JSON.stringify(val, null, 2)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ─── Version ──────────────────────────────────────────────────────────────────

var _updateChangelogUrl = "";

function verifierVersion() {
  fetch("/api/version").then(function (r) { return r.json(); }).then(function (data) {
    var elVer = document.getElementById("nav-version");
    if (elVer) elVer.textContent = "v" + data.current;
    if (data.update_available && data.latest) {
      _updateChangelogUrl = data.changelog_url || "";
      var notif    = document.getElementById("nav-update-notif");
      var elLatest = document.getElementById("nav-update-version");
      if (notif && elLatest) {
        elLatest.textContent = data.latest;
        notif.classList.remove("hidden");
        document.getElementById("nav-btn-changelog").onclick = function () {
          if (_updateChangelogUrl) window.open(_updateChangelogUrl, "_blank");
        };
        document.getElementById("nav-btn-aide-update").onclick = function () {
          _ouvrirAideMiseAJour(data.current, data.latest, data.server_path || "");
        };
      }
    }
  }).catch(function () {});
}

function _ouvrirAideMiseAJour(versionActuelle, versionLatest, serverPath) {
  var overlay = document.getElementById("update-aide-overlay");
  if (!overlay) return;

  var elInfo = document.getElementById("update-aide-version");
  if (elInfo) {
    elInfo.textContent = "Version installée : " + versionActuelle
      + " \u2192 Version disponible : " + versionLatest;
  }

  var elCode = document.getElementById("update-aide-code");
  if (elCode) {
    var dir = serverPath || "/home/alex/ampersage";
    elCode.textContent = "cd " + dir + "\ngit pull\nsudo systemctl restart ampersage";
  }

  document.getElementById("update-aide-close").onclick  = _fermerAideMiseAJour;
  document.getElementById("update-aide-ok").onclick     = _fermerAideMiseAJour;
  document.getElementById("update-aide-changelog").onclick = function () {
    if (_updateChangelogUrl) window.open(_updateChangelogUrl, "_blank");
  };

  overlay.classList.remove("hidden");
}

function _fermerAideMiseAJour() {
  var overlay = document.getElementById("update-aide-overlay");
  if (overlay) overlay.classList.add("hidden");
}

// ─── Popup plage heures creuses ───────────────────────────────────────────────

/**
 * Vérifie si params.json signale des offres HPHC sans plage HC configurée.
 * Si oui, ouvre le popup pour demander à l'utilisateur.
 * paramsData : objet issu de /data/params.json
 */
function verifierHcRequises(paramsData) {
  if (!paramsData) return;
  var hcReq = (paramsData.hc_requises || []);
  if (!hcReq.length) return;

  var overlay = document.getElementById("plage-hc-overlay");
  var input   = document.getElementById("plage-hc-input");
  if (!overlay || !input) return;

  // Préremplir avec la valeur actuelle si disponible
  var actuelle = (paramsData.params || {}).plage_hc_globale || "";
  input.value  = actuelle;

  overlay.classList.remove("hidden");
  setTimeout(function () { input.focus(); }, 100);
}

function _initPopupPlageHC() {
  var overlay  = document.getElementById("plage-hc-overlay");
  var input    = document.getElementById("plage-hc-input");
  var btnOk    = document.getElementById("plage-hc-valider");
  var btnAnn   = document.getElementById("plage-hc-annuler");
  var errEl    = document.getElementById("plage-hc-err");
  if (!overlay || !input || !btnOk) return;

  function _fermer() { overlay.classList.add("hidden"); }

  function _valider() {
    var plage = input.value.trim();
    if (!plage) {
      if (errEl) { errEl.textContent = "Veuillez saisir une plage horaire."; errEl.classList.remove("hidden"); }
      return;
    }
    if (errEl) errEl.classList.add("hidden");
    btnOk.disabled = true;
    btnOk.textContent = "Enregistrement…";

    fetch("/api/save_params", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ groupe: "generique", params: { plage_hc_globale: plage } }),
    })
      .then(function (r) { return r.json(); })
      .then(function (rep) {
        btnOk.disabled    = false;
        btnOk.textContent = "Enregistrer";
        if (rep.status) {
          _fermer();
          // Le pipeline est relancé par save_params ; recharger masques + params quand dispo
          _rechargerApresHC();
        } else {
          if (errEl) { errEl.textContent = "Erreur : " + (rep.error || "inconnue"); errEl.classList.remove("hidden"); }
        }
      })
      .catch(function () {
        btnOk.disabled    = false;
        btnOk.textContent = "Enregistrer";
        if (errEl) { errEl.textContent = "Erreur réseau."; errEl.classList.remove("hidden"); }
      });
  }

  btnOk.addEventListener("click", _valider);
  if (btnAnn) btnAnn.addEventListener("click", _fermer);

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") _valider();
    if (e.key === "Escape") _fermer();
  });
}

function _rechargerApresHC() {
  // Recharge masques.json puis params.json pour mettre à jour l'affichage
  Promise.all([
    fetch("/data/masques.json").then(function (r) { return r.ok ? r.json() : null; }),
    fetch("/data/params.json").then(function (r) { return r.ok ? r.json() : null; }),
  ]).then(function (res) {
    var masques = res[0];
    var params  = res[1];
    if (masques) _appliquerMasques(masques);
    // Ne pas ré-ouvrir le popup si des HC restent (évite boucle)
    // L'utilisateur peut relancer manuellement si nécessaire
  }).catch(function () {});
}
