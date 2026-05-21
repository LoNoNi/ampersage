"use strict";

// ─── État global ──────────────────────────────────────────────────────────────

var translations = {};
var currentLang  = "FR";
var _cache       = { statuts: null, results: null };
var _tariffIndex    = {};  // { offre_id: { "2024-04-19T00:00": {pk, h} } }
var _pendingRefreshDetails = false; // flag : rafraîchir le tableau détails après rechargement HC
var _customScriptPending = {}; // { offreId → {data, o} } — custom_scripts en attente de _consoRecords
var _hourKeyCache   = {};  // cache ts → clé heure Paris (évite recalcul Intl)
var _dataReady   = false;
var _contenuPages = {};   // contenu posé par les étapes, préservé à la navigation
var _detailsConfig   = null; // {acCols, e2mCols, offres} — rempli par _construireTableauDetails
var _acGroups        = null; // { year: { month: { day: [records] } } } — données groupées
var _acGroupsCfg     = null; // { cfg, ligneTpl } — config pour rendu à la demande
var _settingsListenerPret = false;
var _masquesCache      = null; // masques.json mis en cache pour re-rendu
var _modulesCompCache  = {};   // modules_complementaires découverts via /api/expose
var _paramsGlobaux     = null; // { puissance_souscrite, plage_hc } depuis params.json
var _inlinePanels    = {};   // { slug → html } — panels injectés sans fetch (offres generique)
var _panelsPrecharge = {};   // { nom → {html, params} | null } — pré-chargé à l'étape 3
var _masquesGenerique = {};  // masques.generique — templates HTML des offres
var _customMeta      = {};  // { offreId → {abonnement_mensuel_ht, abonnement_mensuel_ttc, puissance} }
var _consoRecords    = null; // [{ ts, kwh, pmax }] — records bruts chargés avec api_conso
var _e2mCreneaux     = null; // { ts → créneau enrichi } depuis eco2mix.json
var _solaireData     = null; // { sources:[{id,nom,coeff}], creneaux:{ts:[kwh|null,...]} }
var _solModConfig    = null; // config solaire : { tarifs, sources, panneau }
var _offresConfig    = null; // params.offres[] — barème abonnements par offre
var _rapportConfig   = null; // { puissance_principale, periodes... }
var _modePrix        = "ttc"; // params.params.mode_prix
var _modeDebug       = false; // true seulement sur /debug

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
    if (window.location.pathname === "/debug") {
      _activerModeDebug();
    } else {
      _debugEtapeSuivante(); // démarrage automatique hors mode debug
    }
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

function _locale() {
  /* Retourne le tag BCP47 correspondant à currentLang pour Intl / toLocaleDateString. */
  return currentLang === "EN" ? "en-GB" : "fr-FR";
}

function _tpl(cle, vars) {
  /* Retourne la traduction de `cle` avec substitution des variables {key}. */
  var s = t(cle);
  Object.keys(vars).forEach(function(k) {
    s = s.replace(new RegExp("\\{" + k + "\\}", "g"), vars[k]);
  });
  return s;
}

function _nomModule(nom) {
  /* Retourne le nom affiché d'un module, traduit selon currentLang. */
  if (nom === "api_conso")      return t("section_api_conso_label");
  if (nom === "eco2mix")        return t("section_eco2mix_label");
  if (nom === "rapport_config") return t("section_rapports_config");
  return nom.replace(/_/g, "\u00a0");
}

function appliquerLangue(lang) {
  currentLang = lang;
  document.querySelectorAll("[data-i18n]").forEach(function (el) {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll(".lang-btn").forEach(function (btn) {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
  // Re-rendre les pages dynamiques avec la nouvelle langue
  if (_masquesCache) {
    _rendreRapports();
    _appliquerMasquesParametres(_masquesCache);
  }
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

var _PAGE_CONTENEUR = { home: "main-content", details: "details-content", settings: "settings-content", modules: "modules-content" };

function chargerPage(page) {
  if (page === "home")     chargerPageAccueil();
  if (page === "details")  chargerPageDetails();
  if (page === "settings") chargerPageParametres();
  if (page === "modules")  chargerPageModules();
}

// ─── Chargement initial ───────────────────────────────────────────────────────

var _SPINNER_HTML = '<div class="main-spinner"><span class="main-spinner-anim"></span></div>';
var _SPINNER_DEBUG = '<span class="debug-spinner"></span>';

function _afficherSpinnersPartout() {
  ["main-content", "details-content", "settings-content", "modules-content"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = _SPINNER_HTML;
  });
}


// ─── Calcul et rendu des rapports ─────────────────────────────────────────────

function _comboKey(periode, puissance) {
  /* Clé unique identifiant une combinaison période/puissance. */
  var p = periode || {};
  var pk = p.type || "totale";
  if (p.type === "annee")  pk += ":" + (p.annee || "");
  if (p.type === "perso")  pk += ":" + (p.debut || "") + "/" + (p.fin || "");
  return pk + "|" + (puissance || "");
}

function _rendreRapports() {
  /* Déclenché après chaque chargement de données. Calcule les sous-rapports
     et met à jour la page d'accueil uniquement quand toutes les données sont prêtes. */
  if (!_consoRecords || !_offresConfig || !_masquesGenerique) return;
  if (!_rapportConfig || !_rapportConfig.periode_principale || !_rapportConfig.puissance_principale) {
    var el0 = document.getElementById("main-content");
    var html0 = _rendreControles() +
      '<p class="debug-vide">' + t("aucun_rapport_configure") + '</p>';
    _contenuPages["home"] = html0;
    if (el0) { el0.innerHTML = html0; _initControlesHome(el0); }
    return;
  }

  var offreIds = Object.keys(_masquesGenerique);
  if (!offreIds.length) return;

  // Vérifier que tous les tariff index sont chargés
  for (var i = 0; i < offreIds.length; i++) {
    if (!_tariffIndex[offreIds[i]]) return; // pas encore prêt
  }

  var rc = _rapportConfig;

  // Construire les listes dédupliquées de périodes (lignes) et puissances (colonnes)
  var periodes = [rc.periode_principale];
  var periKeys = [_comboKey(rc.periode_principale, "")];
  (rc.periodes_secondaires || []).forEach(function(p) {
    var k = _comboKey(p, "");
    if (periKeys.indexOf(k) === -1) { periodes.push(p); periKeys.push(k); }
  });

  var puissances = [rc.puissance_principale];
  (rc.puissances_secondaires || []).forEach(function(p) {
    if (puissances.indexOf(p) === -1) puissances.push(p);
  });

  // Calculer la matrice par offre : matrix[periIdx][puissIdx]
  var offresRendues = offreIds.map(function(offreId) {
    var masqueOffre = _masquesGenerique[offreId];
    var offreConfig = null;
    for (var j = 0; j < _offresConfig.length; j++) {
      if (_offresConfig[j].id === offreId) { offreConfig = _offresConfig[j]; break; }
    }
    var baremeAbos = offreConfig ? ((offreConfig.config || {}).abonnements || []) : [];
    var baremeKvas = baremeAbos.map(function(a) { return a.kva; }).sort(function(a, b) { return a - b; });
    // Offres custom : barème complet depuis _customMeta (résolution puissance conseillée/perso + warnings)
    if (!baremeAbos.length && masqueOffre.custom && _customMeta[offreId]) {
      var cm = _customMeta[offreId];
      baremeAbos = cm.abonnements.length
        ? cm.abonnements
        : [{ kva: cm.puissance, ht: cm.abonnement_mensuel_ht, ttc: cm.abonnement_mensuel_ttc }];
      baremeKvas = baremeAbos.map(function(a) { return a.kva; }).sort(function(a, b) { return a - b; });
    }
    var tariffIdx  = _tariffIndex[offreId];

    var matrix = periodes.map(function(peri) {
      return puissances.map(function(puis) {
        return _calculerSousRapport({ periode: peri, puissance: puis }, baremeAbos, baremeKvas, tariffIdx);
      });
    });

    var coutPrincipal = (matrix[0][0] && matrix[0][0].cout_mois_moyen != null)
      ? matrix[0][0].cout_mois_moyen : Infinity;

    return { id: offreId, masque: masqueOffre, matrix: matrix, coutPrincipal: coutPrincipal };
  });

  // Trier par coût du rapport principal
  offresRendues.sort(function(a, b) { return a.coutPrincipal - b.coutPrincipal; });

  // Barre de contrôles (puissance souscrite + plage HC)
  var ctrlsHtml = _rendreControles();

  // Construire le HTML final
  var cardsHtml = offresRendues.map(function(item) {
    var cardHtml  = item.masque.rapport || "";
    var grilleHtml = _rendreGrilleRapport(item.masque, item.matrix, periodes, puissances);
    return cardHtml.replace(
      '<div class="gen-ri-sous-rapports"></div>',
      '<div class="gen-ri-sous-rapports">' + grilleHtml + '</div>'
    );
  }).join("");

  var htmlHome = ctrlsHtml + (cardsHtml || '<p class="debug-vide">' + t("aucun_rapport") + '</p>');
  _contenuPages["home"] = htmlHome;
  var el = document.getElementById("main-content");
  if (el) {
    el.innerHTML = htmlHome;
    _initControlesHome(el);
  }

  // Rafraîchir le tableau de détails si demandé (après rechargement HC)
  if (_pendingRefreshDetails) {
    _pendingRefreshDetails = false;
    _rafraichirTableauDetails();
  }
}

function _rendreGrilleRapport(masque, matrix, periodes, puissances) {
  /* Affiche les sous-rapports en grille : périodes sur les lignes, puissances en colonnes. */
  var srTpl = masque.rapport_sous_rapport || "";

  // Cas simple : une seule cellule
  if (periodes.length === 1 && puissances.length === 1) {
    return _rendreUnSousRapport(srTpl, matrix[0][0], true);
  }

  var html = '<table class="gen-ri-grille">';

  // En-tête : coin vide + colonnes puissances
  html += '<thead><tr><th class="gen-ri-grille-coin"></th>';
  puissances.forEach(function(puis, pIdx) {
    var cls = "gen-ri-grille-th-puissance" + (pIdx === 0 ? " principale" : "");
    html += '<th class="' + cls + '">' + _labelPuissanceEntete(puis) + '</th>';
  });
  html += '</tr></thead>';

  // Corps : une ligne par période
  html += '<tbody>';
  periodes.forEach(function(peri, periIdx) {
    html += '<tr>';
    var clsRow = "gen-ri-grille-th-periode" + (periIdx === 0 ? " principale" : "");
    html += '<th class="' + clsRow + '">' + _labelPeriodeCourt(peri) + '</th>';
    puissances.forEach(function(puis, puissIdx) {
      var sr         = matrix[periIdx][puissIdx];
      var estPrinc   = (periIdx === 0 && puissIdx === 0);
      var clsCell    = "gen-ri-grille-cell" + (estPrinc ? " principale" : "");
      html += '<td class="' + clsCell + '">';
      html += _rendreUnSousRapport(srTpl, sr, estPrinc);
      html += '</td>';
    });
    html += '</tr>';
  });
  html += '</tbody></table>';

  return html;
}

function _rendreControles() {
  /* Barre de contrôles rapides affichée au-dessus des offres. */
  var ps = (_paramsGlobaux || {}).puissance_souscrite || "";
  var hc = (_paramsGlobaux || {}).plage_hc || "";
  return '<div class="gen-controles" id="gen-controles">' +
    '<div class="gen-ctrl-item">' +
      '<label class="gen-ctrl-label">' + t("ctrl_puissance_label") + '</label>' +
      '<input class="gen-ctrl-input" type="number" id="ctrl-puissance-souscrite" ' +
             'value="' + _escHtml(ps) + '" min="1" max="36" step="0.5" placeholder="kVA">' +
      '<button class="gen-ctrl-btn" id="ctrl-puissance-btn">' + t("btn_recalculer") + '</button>' +
    '</div>' +
    '<div class="gen-ctrl-item">' +
      '<label class="gen-ctrl-label">' + t("ctrl_plage_label") + '</label>' +
      '<input class="gen-ctrl-input" type="text" id="ctrl-plage-hc" ' +
             'value="' + _escHtml(hc) + '" placeholder="ex\u00a0: 22h00\u219206h00">' +
      '<button class="gen-ctrl-btn" id="ctrl-plage-hc-btn">' + t("btn_appliquer") + '</button>' +
    '</div>' +
  '</div>';
}

function _initControlesHome(el) {
  /* Attache les événements aux contrôles de la barre d'accueil. */
  var inpPs = el.querySelector("#ctrl-puissance-souscrite");
  var btnPs = el.querySelector("#ctrl-puissance-btn");
  if (inpPs && btnPs) {
    btnPs.addEventListener("click", function() {
      var val = inpPs.value.trim();
      if (!val) return;
      if (!_paramsGlobaux) _paramsGlobaux = {};
      _paramsGlobaux.puissance_souscrite = val;
      // Recalcul immédiat côté client
      _rendreRapports();
      // Sauvegarde côté serveur (generique SET_PARAM) puis reload masques+params
      btnPs.disabled    = true;
      btnPs.textContent = t("enregistrement_cours");
      fetch("/api/save_params", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ groupe: "generique", params: { puissance_souscrite: parseFloat(val) } }),
      })
      .then(function(r) { return r.json(); })
      .then(function(rep) {
        btnPs.disabled    = false;
        btnPs.textContent = t("btn_recalculer");
        if (rep.status) _rechargerMasquesParams();
      })
      .catch(function() {
        btnPs.disabled    = false;
        btnPs.textContent = t("btn_recalculer");
      });
    });
  }

  var inpHc = el.querySelector("#ctrl-plage-hc");
  var btnHc = el.querySelector("#ctrl-plage-hc-btn");
  if (inpHc && btnHc) {
    btnHc.addEventListener("click", function() {
      var plage = inpHc.value.trim();
      if (!plage) return;
      btnHc.disabled    = true;
      btnHc.textContent = t("enregistrement_cours");
      fetch("/api/save_params", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ groupe: "generique", params: { plage_hc_globale: plage } }),
      })
      .then(function(r) { return r.json(); })
      .then(function(rep) {
        btnHc.disabled    = false;
        btnHc.textContent = t("btn_appliquer");
        if (rep.status) _rechargerApresHC(true);
      })
      .catch(function() {
        btnHc.disabled    = false;
        btnHc.textContent = t("btn_appliquer");
      });
    });
  }
}

function _rafraichirTableauDetails() {
  /* Vide le tbody de détails et réinjecte les groupes. */
  var tbody = document.getElementById("details-tbody");
  if (!tbody || !_acGroups) return;
  while (tbody.firstChild) tbody.removeChild(tbody.firstChild);
  _injecterGroupesAC(tbody);
}

function _labelPuissanceEntete(puis) {
  if (puis === "souscrite")  return t("puis_souscrite");
  if (puis === "conseillee") return t("puis_conseillee");
  if (typeof puis === "string" && puis.slice(0, 6) === "perso:") {
    return puis.slice(6) + "\u00a0" + t("puis_kva_perso");
  }
  return puis || "";
}

function _labelPeriodeCourt(peri) {
  var type = (peri || {}).type || "totale";
  if (type === "totale")         return t("periode_totale");
  if (type === "glissante_1an")  return t("periode_glissante_1an");
  if (type === "glissantes_nan") return t("periode_glissantes_nan");
  if (type === "annee")          return t("periode_annee") + "\u00a0" + ((peri && peri.annee) || "");
  if (type === "perso")          return ((peri && peri.debut) || "") + "\u00a0\u2192\u00a0" + ((peri && peri.fin) || "");
  return type;
}

function _escHtml(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function _calculerSousRapport(combo, baremeAbos, baremeKvas, tariffIdx) {
  var records = _filtrerRecordsPeriode(_consoRecords, combo.periode);
  if (!records.length) return { erreur: t("aucune_donnee_periode"), periode: combo.periode, puissance: combo.puissance };

  // pmax observé sur la période
  var pmax = 0;
  for (var i = 0; i < records.length; i++) {
    if ((records[i].pmax || 0) > pmax) pmax = records[i].pmax;
  }

  // kVA applicable
  var kvaResult = _resoudreKva(combo.puissance, pmax, baremeKvas);
  var kva = kvaResult.kva;

  // Hint ballasteur : pmax juste au-dessus du palier inférieur ?
  var hintBallasteur = null;
  var palierIdx = baremeKvas.indexOf(kva);
  if (palierIdx > 0) {
    var palierInf = baremeKvas[palierIdx - 1];
    var SEUIL_KW  = 1.0; // kW — si pmax dépasse le palier inf de moins de 1 kW
    if (pmax > palierInf && (pmax - palierInf) <= SEUIL_KW) {
      hintBallasteur = _tpl("hint_ballasteur", { pmax: pmax.toFixed(2), kva: palierInf, seuil: SEUIL_KW });
    }
  }

  // Abonnement
  var aboEntry = null;
  for (var j = 0; j < baremeAbos.length; j++) {
    if (baremeAbos[j].kva === kva) { aboEntry = baremeAbos[j]; break; }
  }
  var aboMois = aboEntry ? (aboEntry[_modePrix] || 0) : 0;
  var aboAn   = Math.round(aboMois * 12 * 100) / 100;

  // Agrégats conso × tarif
  var totalKwh = 0, totalEur = 0;
  var dateSet = {};
  for (var k = 0; k < records.length; k++) {
    var r       = records[k];
    var hourKey = _tariffHourKey(r.ts);
    var tarif   = tariffIdx[hourKey];
    if (tarif) {
      totalKwh += r.kwh;
      totalEur += r.kwh * tarif.pk;
    }
    dateSet[r.ts.slice(0, 10)] = 1;
  }

  var nbJours = Object.keys(dateSet).length || 1;
  var facteur = 365.0 / nbJours;
  var consoKwhAn  = Math.round(totalKwh * facteur);
  var coutElecAn  = Math.round(totalEur * facteur * 100) / 100;
  var coutMoisMoy = Math.round((coutElecAn + aboAn) / 12 * 100) / 100;

  return {
    kva: kva, abonnement_mois: aboMois, abonnement_an: aboAn,
    conso_kwh_an: consoKwhAn, cout_elec_an: coutElecAn, cout_mois_moyen: coutMoisMoy,
    warning: kvaResult.warning, hint_ballasteur: hintBallasteur,
    periode: combo.periode, puissance: combo.puissance,
  };
}

function _resoudreKva(puissance, pmax, baremeKvas) {
  /* Retourne { kva, warning }. Palier >= kvaVoulu dans le barème. */
  var kvaVoulu = 0;
  if (puissance === "conseillee") {
    kvaVoulu = pmax;
  } else if (puissance === "souscrite") {
    kvaVoulu = parseFloat((_paramsGlobaux || {}).puissance_souscrite) || 0;
  } else if (typeof puissance === "string" && puissance.slice(0, 6) === "perso:") {
    kvaVoulu = parseFloat(puissance.slice(6)) || 0;
  }

  // Plus petit palier >= kvaVoulu
  var kva = baremeKvas[baremeKvas.length - 1] || kvaVoulu;
  for (var i = 0; i < baremeKvas.length; i++) {
    if (baremeKvas[i] >= kvaVoulu) { kva = baremeKvas[i]; break; }
  }

  // Puissance conseillée (pmax → palier barème)
  var kvaConseille = baremeKvas[baremeKvas.length - 1] || pmax;
  for (var j = 0; j < baremeKvas.length; j++) {
    if (baremeKvas[j] >= pmax) { kvaConseille = baremeKvas[j]; break; }
  }

  var warning = (puissance !== "conseillee" && kva < kvaConseille)
    ? _tpl("avertissement_palier_inferieur", { kva: kva, conseille: kvaConseille, pmax: pmax.toFixed(2) })
    : null;

  return { kva: kva, warning: warning };
}

function _filtrerRecordsPeriode(records, periode) {
  if (!records || !records.length) return [];
  var type = (periode || {}).type || "totale";
  if (type === "totale") return records;

  if (type === "glissante_1an") {
    var tsLast1  = new Date(records[records.length - 1].ts);
    var limite1  = new Date(tsLast1.getTime() - 365 * 24 * 3600 * 1000);
    return records.filter(function(r) { return new Date(r.ts) >= limite1; });
  }

  if (type === "glissantes_nan") {
    var tsFirst = new Date(records[0].ts);
    var tsLast  = new Date(records[records.length - 1].ts);
    var moisTotal = (tsLast.getFullYear() - tsFirst.getFullYear()) * 12 +
                    (tsLast.getMonth() - tsFirst.getMonth());
    var n = Math.floor(moisTotal / 12);
    if (n === 0) return records;
    var limiteN = new Date(tsLast);
    limiteN.setMonth(limiteN.getMonth() - n * 12);
    return records.filter(function(r) { return new Date(r.ts) >= limiteN; });
  }

  if (type === "annee") {
    var annee = String(periode.annee || "");
    return records.filter(function(r) { return r.ts.slice(0, 4) === annee; });
  }

  if (type === "perso" && periode.debut && periode.fin) {
    var debut = new Date(periode.debut + "T00:00:00Z");
    var fin   = new Date(periode.fin   + "T23:59:59Z");
    return records.filter(function(r) { var d = new Date(r.ts); return d >= debut && d <= fin; });
  }

  return records;
}

function _rendreUnSousRapport(tplHtml, sr, estPrincipal) {
  if (!sr || !tplHtml) return "";
  var wrap = document.createElement("div");
  wrap.innerHTML = tplHtml;
  var node = wrap.firstElementChild;
  if (!node) return tplHtml;

  node.setAttribute("data-kva", sr.kva || "");
  node.setAttribute("data-principal", estPrincipal ? "1" : "0");
  if (estPrincipal) node.classList.add("gen-ri-sr-principal");

  var titreEl = node.querySelector(".gen-ri-sr-titre");
  if (titreEl) titreEl.textContent = _labelSousRapport(sr.periode, sr.puissance, sr.kva);

  var warnEl = node.querySelector(".gen-ri-sr-warning");
  if (warnEl) {
    if (sr.warning) { warnEl.textContent = "\u26a0 " + sr.warning; warnEl.classList.remove("hidden"); }
    else            { warnEl.classList.add("hidden"); }
  }

  var hintEl = node.querySelector(".gen-ri-sr-hint");
  if (hintEl) {
    if (sr.hint_ballasteur) { hintEl.textContent = "\ud83d\udca1 " + sr.hint_ballasteur; hintEl.classList.remove("hidden"); }
    else                    { hintEl.classList.add("hidden"); }
  }

  if (sr.erreur) {
    node.querySelector(".gen-ri-rapports").innerHTML =
      '<p class="gen-ri-sr-erreur">' + sr.erreur + '</p>';
  } else {
    _remplirSlot(node, "kva",             sr.kva + "\u00a0kVA");
    _remplirSlot(node, "abonnement_mois", _fmtEur(sr.abonnement_mois) + "\u00a0€/mois");
    _remplirSlot(node, "conso_kwh_an",    _fmtKwh(sr.conso_kwh_an) + "\u00a0kWh");
    _remplirSlot(node, "cout_elec_an",    _fmtEur(sr.cout_elec_an) + "\u00a0€");
    _remplirSlot(node, "abonnement_an",   _fmtEur(sr.abonnement_an) + "\u00a0€");
    _remplirSlot(node, "cout_mois_moyen", _fmtEur(sr.cout_mois_moyen) + "\u00a0€/mois");
  }

  return wrap.innerHTML;
}

function _remplirSlot(container, slot, valeur) {
  var el = container.querySelector('[data-slot="' + slot + '"]');
  if (el) el.textContent = valeur;
}

function _fmtEur(v) {
  if (v == null || isNaN(v)) return "\u2014";
  return v.toFixed(2).replace(".", ",");
}

function _fmtKwh(v) {
  if (v == null || isNaN(v)) return "\u2014";
  return String(Math.round(v)).replace(/\B(?=(\d{3})+(?!\d))/g, "\u202f");
}

function _labelSousRapport(periode, puissance, kva) {
  var type = (periode || {}).type || "totale";
  var lblP;
  if (type === "totale")         lblP = t("sr_totale");
  else if (type === "glissante_1an")  lblP = t("sr_glissante_1an");
  else if (type === "glissantes_nan") lblP = t("sr_glissantes_nan");
  else if (type === "annee")     lblP = t("periode_annee") + " " + ((periode && periode.annee) || "");
  else if (type === "perso")     lblP = ((periode && periode.debut) || "") + " \u2192 " + ((periode && periode.fin) || "");
  else lblP = type;
  var lblKva = kva ? (" \u00b7 " + kva + "\u00a0kVA") : "";
  if (puissance === "souscrite")       lblKva += " " + t("sr_souscrite");
  else if (puissance === "conseillee") lblKva += " " + t("sr_conseillee");
  return lblP + lblKva;
}

// ─── Application des masques ──────────────────────────────────────────────────

function _appliquerMasques(masques) {
  _masquesCache     = masques;
  _masquesGenerique = masques.generique || {};
  // ── Accueil : rapports ────────────────────────────────────────────────────
  var elHome = document.getElementById("main-content");
  if (elHome) {
    var generique = masques.generique || {};
    var rapports  = Object.values(generique).map(function (o) { return o.rapport || ""; }).join("");
    var htmlHome  = rapports || '<p class="debug-vide">' + t("aucun_rapport") + '</p>';
    _contenuPages["home"] = htmlHome;
    elHome.innerHTML = htmlHome;
  }

  // ── Détails : tableau unifié ──────────────────────────────────────────────
  _construireTableauDetails(masques);

  // ── Paramètres ───────────────────────────────────────────────────────────
  _appliquerMasquesParametres(masques);
}

function _appliquerMasquesParametres(masques) {
  _inlinePanels = {}; // reset à chaque re-rendu

  // ── Carte configuration générale (toujours visible) ──────────────────────
  var pg = _paramsGlobaux || {};
  var puissanceVal = pg.puissance_souscrite || "";
  var plageVal     = pg.plage_hc || "";
  var cardGlobaux  = (masques.params_globaux || "")
    .replace(/\{\{puissance_souscrite_val\}\}/g, _escHtml(puissanceVal))
    .replace(/\{\{plage_hc\}\}/g, _escHtml(plageVal));

  // ── Accordéon rapport_config (toujours présent) ───────────────────────────
  var cardRapport = _htmlCard("rapport_config", null, {});

  // ── Accordéon api_conso ───────────────────────────────────────────────────
  var cardApiConso = "";
  if ((masques.api_conso || {}).params !== undefined) {
    cardApiConso = _htmlCard("api_conso", null, {});
  }

  // ── Accordéon eco2mix ─────────────────────────────────────────────────────
  var cardEco2mix = "";
  if ((masques.eco2mix || {}).params !== undefined) {
    cardEco2mix = _htmlCard("eco2mix", null, {});
  }

  // ── Accordéons modules complémentaires (ex: solaire) ─────────────────────
  var modsComp = _modulesCompCache;
  var cardsModsComp = Object.keys(modsComp).filter(function(n) { return n !== "eco2mix" && n !== "rapport_config"; }).map(function(nomMod) {
    return _htmlCard(nomMod, null, {});
  }).join("");

  // ── Accordéons offres génériques (inline, lecture seule) ─────────────────
  var generique = masques.generique || {};
  var cardsOffres = Object.keys(generique).map(function(offreId) {
    var m    = generique[offreId];
    var slug = _genSlug(offreId);
    var parts = offreId.split("__"); // ["Fournisseur", "Nom", "type", "idx"]
    var nom   = (parts[0] || offreId) + " — " + (parts[1] || "") +
                (parts[2] ? " \u00b7 " + parts[2] : "");
    _inlinePanels[slug] = m.params || "";
    return m.save_groupe
      ? _htmlCardInlineSaveable(slug, nom, m.save_groupe)
      : _htmlCardInline(slug, nom);
  }).join("");

  var html = '<div class="settings-page">' +
    cardGlobaux +
    '<div class="settings-section"><h2 class="settings-section-titre">' + t("section_rapports") + '</h2>' + cardRapport + '</div>' +
    (cardApiConso || cardEco2mix || cardsModsComp || cardsOffres
      ? (cardApiConso   ? '<div class="settings-section"><h2 class="settings-section-titre">' + t("section_consommation") + '</h2>' + cardApiConso   + '</div>' : '') +
        (cardEco2mix    ? '<div class="settings-section"><h2 class="settings-section-titre">' + t("section_donnees")      + '</h2>' + cardEco2mix    + '</div>' : '') +
        (cardsModsComp  ? '<div class="settings-section"><h2 class="settings-section-titre">' + t("section_modules")     + '</h2>' + cardsModsComp  + '</div>' : '') +
        (cardsOffres    ? '<div class="settings-section"><h2 class="settings-section-titre">' + t("section_offres")       + '</h2>' + cardsOffres    + '</div>' : '')
      : '') +
    '</div>';

  _contenuPages["settings"] = html;
  var el = document.getElementById("settings-content");
  if (el) el.innerHTML = html;
}

function _appliquerParamsGlobaux(paramsData) {
  /* Appelé quand params.json est chargé — met à jour la carte globaux. */
  if (!paramsData) return;
  var p = paramsData.params || {};
  _paramsGlobaux = {
    puissance_souscrite: p.puissance_souscrite != null ? String(p.puissance_souscrite) : "",
    plage_hc: p.plage_hc_globale || ""
  };
  _offresConfig  = paramsData.offres || [];
  _rapportConfig = p.rapport_config || null;
  _modePrix      = p.mode_prix || "ttc";
  // Re-rendre la page paramètres avec les vraies valeurs
  if (_masquesCache) _appliquerMasquesParametres(_masquesCache);
  // Pré-charger les panels modules dès que params sont disponibles
  fetch("/api/expose").then(function(r) { return r.ok ? r.json() : {}; }).catch(function() { return {}; }).then(function(expose) {
    _modulesCompCache = expose.modules_complementaires || {};
    var nomsBase = ["rapport_config", "api_conso", "eco2mix"];
    var nomsComp = Object.keys(_modulesCompCache).filter(function(n) { return n !== "eco2mix" && n !== "rapport_config"; });
    _prechargerPanels(nomsBase.concat(nomsComp));
    // Re-rendre les paramètres maintenant qu'on connaît les modules
    if (_masquesCache) _appliquerMasquesParametres(_masquesCache);
  });
}

function _prechargerPanels(noms) {
  /* Pré-charge HTML panel + params expose pour chaque module en parallèle.
     Résultat stocké dans _panelsPrecharge[nom] = {html, params} | null. */
  fetch("/api/expose").then(function(r) {
    return r.ok ? r.json() : {};
  }).catch(function() { return {}; }).then(function(expose) {
    noms.forEach(function(nom) {
      fetch("/api/panel/" + nom).then(function(r) {
        if (!r.ok) throw new Error("introuvable");
        return r.text();
      }).then(function(html) {
        var params = ((expose.fournisseurs || {})[nom] || {}).parametres
                  || ((expose.modules_complementaires || {})[nom] || {}).parametres
                  || {};
        _panelsPrecharge[nom] = { html: html, params: params };
        // Si l'accordéon était déjà ouvert avant la fin du pré-chargement, injecter maintenant
        var header = document.querySelector("#settings-card-" + nom + " .settings-card-header");
        if (header && header.dataset.panelCharge === "attente") {
          header.dataset.panelCharge = "1";
          _injecterPanelPrecharge(nom);
        }
      }).catch(function() {
        _panelsPrecharge[nom] = null;
      });
    });
  });
}

function _injecterPanelPrecharge(nom) {
  var cache = _panelsPrecharge[nom];
  var conteneur = document.getElementById("panel-container-" + nom);
  if (!conteneur) return;
  if (!cache) {
    conteneur.innerHTML = '<p class="settings-no-panel">' + t("aucun_panel") + '</p>';
    return;
  }
  conteneur.innerHTML = cache.html;
  _execScripts(conteneur);
  var setter = window["setParams_" + nom];
  if (typeof setter === "function") setter(cache.params);
  else _setParamsFallback(conteneur, cache.params);
}

var _BATCH_AC = 1000; // lignes JSON parsées par tick (aucun DOM intermédiaire)

/* _MOIS_FR supprimé — _acNomMois utilise Intl pour respecter currentLang */

// ─── Helpers groupement ───────────────────────────────────────────────────────

var _acDateFmt = new Intl.DateTimeFormat("fr-FR", {
  timeZone: "Europe/Paris",
  year: "numeric", month: "2-digit", day: "2-digit"
});

var _acHourFmt = new Intl.DateTimeFormat("fr-FR", {
  timeZone: "Europe/Paris",
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", hourCycle: "h23"
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

function _tariffHourKey(ts) {
  /* Retourne "YYYY-MM-DDTHH:00" en heure de Paris — clé de lookup dans _tariffIndex. */
  if (!ts) return "";
  if (_hourKeyCache[ts]) return _hourKeyCache[ts];
  var parts = _acHourFmt.formatToParts(new Date(ts));
  var y = "", m = "", d = "", h = "";
  parts.forEach(function(p) {
    if (p.type === "year")  y = p.value;
    if (p.type === "month") m = p.value;
    if (p.type === "day")   d = p.value;
    if (p.type === "hour")  h = p.value;
  });
  return (_hourKeyCache[ts] = y + "-" + m + "-" + d + "T" + h + ":00");
}

function _genSlug(s) {
  /* Miroir JS de _slugifier Python : slug ASCII minuscule. */
  return s.normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function _chargerTariffNdjson(offreId, url) {
  /* Charge le NDJSON tarif et l'indexe par clé heure Paris. */
  fetch(url)
    .then(function(r) { return r.ok ? r.text() : null; })
    .then(function(texte) {
      if (!texte) return;
      var idx = {};
      texte.split("\n").forEach(function(ligne) {
        var l = ligne.trim();
        if (!l) return;
        try {
          var rec = JSON.parse(l);
          if (rec.t) idx[rec.t] = { pk: rec.pk || 0, h: rec.h || null };
        } catch(e) {}
      });
      _tariffIndex[offreId] = idx;
      console.log("[tariff] chargé:", offreId, "→", Object.keys(idx).length, "créneaux. Exemple clé:", Object.keys(idx)[0]);
      _rendreRapports();
    })
    .catch(function(e) { console.warn("[tariff] erreur chargement", offreId, e); });
}

function _chargerTariffCustom(offreId) {
  /* Charge les créneaux d'une offre custom via l'API serveur et peuple _tariffIndex.
     Le serveur calcule à la demande et met en cache jusqu'au prochain pipeline.
     Chaque créneau contient prix_kwh (€/kWh, stocké en pk) + champs extra pour affichage. */
  fetch("/api/tarif/custom/" + encodeURIComponent(offreId))
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
      if (!data || !Array.isArray(data.creneaux)) return;
      _customMeta[offreId] = {
        abonnement_mensuel_ht:  data.abonnement_mensuel_ht  || 0,
        abonnement_mensuel_ttc: data.abonnement_mensuel_ttc || 0,
        puissance:              data.puissance || 0,
        abonnements:            data.abonnements || [],
      };
      var idx = {};
      data.creneaux.forEach(function(c) {
        var hourKey = _tariffHourKey(c.ts);
        if (!hourKey) return;
        var entry = { pk: c.prix_kwh || 0 };
        // Stocker tous les champs extra (affichage, moyennes pondérées)
        Object.keys(c).forEach(function(key) {
          if (key === "ts" || key === "kwh" || key === "prix_kwh") return;
          entry[key] = c[key];
        });
        idx[hourKey] = entry;
      });
      _tariffIndex[offreId] = idx;
      _rendreRapports();
      _rafraichirTableauDetails();
    })
    .catch(function(e) { console.warn("[tariff custom] erreur chargement", offreId, e); });
}

function _execCustomScript(offreId) {
  /* Exécute le custom_script en attente pour offreId, si _consoRecords est dispo. */
  var pending = _customScriptPending[offreId];
  if (!pending || !_consoRecords) return;
  delete _customScriptPending[offreId];
  try {
    var fn = new Function("data", "offreId", pending.o.custom_script);
    fn(pending.data, offreId);
  } catch(e) {
    console.warn("[tariff custom_script] erreur exécution:", offreId, e);
  }
}

function _chargerTariffCustomScript(o) {
  /* Charge les données GET_CUSTOM (calendrier + tarifs).
     Le custom_script est exécuté seulement quand _consoRecords est aussi disponible
     (les deux peuvent arriver dans n'importe quel ordre). */
  fetch("/api/tarif/custom/" + encodeURIComponent(o.id))
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(data) {
      if (!data) return;
      _customScriptPending[o.id] = { data: data, o: o };
      // Exécuter immédiatement si _consoRecords est déjà chargé
      if (_consoRecords) _execCustomScript(o.id);
    })
    .catch(function(e) { console.warn("[tariff custom_script] fetch erreur:", o.id, e); });
}

var _tariffDebugDone = false;
function _tariffCellsHtml(offre, r, hourKey) {
  /* Retourne les <td>s de l'offre pour un record (sans le wrapper <tr>). */
  var td = _tariffIndex[offre.id] && _tariffIndex[offre.id][hourKey];
  if (!_tariffDebugDone) {
    _tariffDebugDone = true;
    console.log("[tariff] premier lookup — offre:", offre.id, "| hourKey:", hourKey, "| index dispo:", !!_tariffIndex[offre.id], "| entrée trouvée:", !!td);
    if (_tariffIndex[offre.id]) {
      var keys = Object.keys(_tariffIndex[offre.id]);
      console.log("[tariff] exemple clé index:", keys[0], "| total:", keys.length);
    }
  }
  if (!td || !offre.detail_ligne) {
    return '<td colspan="' + offre.cols + '"></td>';
  }
  var pk      = td.pk || 0;
  var kwh     = r.kwh || 0;
  var prixEur = (kwh * pk).toFixed(4);
  var typeHc  = td.h === "C" ? "HC" : (td.h === "H" ? "HP" : "");
  var typeCss = td.h === "C" ? "hc" : (td.h === "H" ? "hp" : "");
  // Tooltip décomposition du prix (offres custom Sobry)
  var prixKwhTip = pk.toFixed(6);
  if (offre.custom && td._base !== undefined) {
    var tipLines = [];
    if (td._spot !== undefined) tipLines.push(t("tip_spot_epex") + " : " + Number(td._spot).toFixed(4) + " c\u20ac/kWh");
    tipLines.push(t("tip_base_ttc") + " : " + Number(td._base).toFixed(4) + " c\u20ac/kWh" + (td._cap ? "  " + t("tip_plafond") : ""));
    tipLines.push(t("tip_marge_sobry") + " : " + Number(td._marge).toFixed(4) + " c\u20ac/kWh");
    if (Number(td._prime) > 0) tipLines.push(t("tip_prime") + " : " + Number(td._prime).toFixed(4) + " c\u20ac/kWh");
    tipLines.push("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500");
    tipLines.push(t("tip_prix_final") + " : " + (pk * 100).toFixed(4) + " c\u20ac/kWh");
    prixKwhTip = '<span class="detail-tip" data-tip="' + tipLines.join("\n") + '">' + pk.toFixed(6) + '</span>';
  }
  var html = offre.detail_ligne
    .replace(/\{\{type_hc\}\}/g,      typeHc)
    .replace(/\{\{type_css\}\}/g,     typeCss)
    .replace(/\{\{prix_kwh\}\}/g,     pk.toFixed(6))
    .replace(/\{\{prix_kwh_tip\}\}/g, prixKwhTip)
    .replace(/\{\{prix_eur\}\}/g,     prixEur);
  // Offres multi-colonnes (ex. Tempo) : colonne active reçoit prixEur, autres restent vides
  if (td.col) {
    var _TEMPO_COLS = ["bleu_hc","bleu_hp","blanc_hc","blanc_hp","rouge_hc","rouge_hp"];
    _TEMPO_COLS.forEach(function(c) {
      html = html.replace(new RegExp("\\{\\{" + c + "_eur\\}\\}", "g"), c === td.col ? prixEur : "");
    });
  }
  // Substitution générique des champs extra stockés dans l'index (offres custom)
  Object.keys(td).forEach(function(key) {
    if (key === "pk" || key === "h" || key === "col") return;
    html = html.replace(new RegExp("\\{\\{" + key + "\\}\\}", "g"), td[key] != null ? String(td[key]) : "");
  });
  // Extraire les <td>s en retirant le wrapper <tr ...>...</tr>
  return html.replace(/^\s*<tr[^>]*>\s*/i, "").replace(/\s*<\/tr>\s*$/i, "");
}

function _acNomMois(monthKey) {
  // "2024-01" → "Janvier 2024" / "January 2024" selon currentLang
  var parts = monthKey.split("-");
  var d   = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, 1);
  var nom = d.toLocaleDateString(_locale(), { month: "long" });
  return nom.charAt(0).toUpperCase() + nom.slice(1) + " " + parts[0];
}

function _acNomJour(dayKey) {
  // "2024-01-15" → "15/01/2024"
  var p = dayKey.split("-");
  return p[2] + "/" + p[1] + "/" + p[0];
}

function _formaterCo2Total(gCO2) {
  /* Adapte l'unité : g → kg → t selon la magnitude. */
  if (gCO2 == null || isNaN(gCO2)) return "—";
  if (gCO2 >= 1000000) return (gCO2 / 1000000).toFixed(2) + "\u00a0t";
  if (gCO2 >= 1000)    return (gCO2 / 1000).toFixed(2) + "\u00a0kg";
  return gCO2.toFixed(0) + "\u00a0g";
}

function _e2mStatsGroupe(recs) {
  /* Retourne { co2m_kwh, co2m_total_g, co2mg_kwh, co2mg_total_g } pour un groupe.
     co2m_kwh / co2mg_kwh : moyenne pondérée par kWh (gCO₂/kWh).
     co2m_total_g / co2mg_total_g : somme des émissions (g). */
  if (!_e2mCreneaux || !recs || !recs.length) return null;
  var sumKwh = 0, sumCo2mW = 0, sumCo2mgW = 0, valid = false;
  recs.forEach(function(r) {
    var slot = _e2mCreneaux[r.ts];
    if (!slot) return;
    var kwh  = r.kwh || 0;
    var co2m  = (slot.co2_moyen  || {}).taux_gco2_kwh;
    var co2mg = (slot.co2_marginal || {}).taux_gco2_kwh;
    if (co2m == null && co2mg == null) return;
    valid = true;
    sumKwh += kwh;
    if (co2m  != null) sumCo2mW  += co2m  * kwh;
    if (co2mg != null) sumCo2mgW += co2mg * kwh;
  });
  if (!valid) return null;
  return {
    co2m_kwh:    sumKwh > 0 ? sumCo2mW  / sumKwh : null,
    co2m_total:  sumCo2mW,
    co2mg_kwh:   sumKwh > 0 ? sumCo2mgW / sumKwh : null,
    co2mg_total: sumCo2mgW,
  };
}

// ─── Colonnes solaires ────────────────────────────────────────────────────────

function _solaireVidesHtml() {
  /* Cellules solaires vides (—) basées sur _detailsConfig.solaireCols. */
  var nb = (_detailsConfig && _detailsConfig.solaireCols) || 0;
  if (!nb) return '';
  var html = '';
  for (var i = 0; i < nb; i++) html += '<td class="sol-td sol-td-nd">\u2014</td>';
  return html;
}

function _solaireCellulesDetail(ts) {
  /* Cellules solaires pour une ligne de détail : 2 cellules par source (lux moy + kWh). */
  if (!_solaireData || !_solaireData.creneaux) return _solaireVidesHtml();
  var vals = _solaireData.creneaux[ts];
  if (!vals) return _solaireVidesHtml();
  var html = '';
  vals.forEach(function(v) {
    if (!v || v.lux === null && v.kwh === null) {
      html += '<td class="sol-td sol-td-nd">\u2014</td><td class="sol-td sol-td-nd">\u2014</td>';
    } else {
      var lux = v.lux !== null && v.lux !== undefined ? Math.round(v.lux).toString() : '\u2014';
      var kwh = v.kwh !== null && v.kwh !== undefined ? v.kwh.toFixed(3) : '\u2014';
      html += '<td class="sol-td">' + lux + '</td><td class="sol-td">' + kwh + '</td>';
    }
  });
  return html;
}

function _solaireCellulesGroupe(recs) {
  /* Cellules solaires pour une ligne de groupe : 2 cellules par source.
     lux moy : non agrégeable → affiche toujours —
     kWh     : somme des créneaux */
  if (!_solaireData || !_solaireData.sources || !_solaireData.sources.length) return _solaireVidesHtml();
  var nb = _solaireData.sources.length;
  var kwhTotaux  = new Array(nb).fill(0);
  var kwhHasData = new Array(nb).fill(false);
  (recs || []).forEach(function(r) {
    var vals = (_solaireData.creneaux || {})[r.ts];
    if (!vals) return;
    for (var i = 0; i < nb; i++) {
      var v = vals[i];
      if (v && v.kwh !== null && v.kwh !== undefined) {
        kwhTotaux[i]  += v.kwh;
        kwhHasData[i]  = true;
      }
    }
  });
  var html = '';
  for (var i = 0; i < nb; i++) {
    html += '<td class="sol-td sol-td-nd">\u2014</td>';  // lux : pas de somme significative
    html += kwhHasData[i]
      ? '<td class="sol-td">' + kwhTotaux[i].toFixed(2) + '</td>'
      : '<td class="sol-td sol-td-nd">\u2014</td>';
  }
  return html;
}

function _e2mCellulesGroupe(recs) {
  /* 4 cellules eco2mix pour une ligne de groupe (agrégat). */
  var s = _e2mStatsGroupe(recs);
  if (!s) return '<td class="eco2mix-td" colspan="4"></td>';
  var co2mKwh  = s.co2m_kwh  != null ? Math.round(s.co2m_kwh).toString()  : "\u2014";
  var co2mgKwh = s.co2mg_kwh != null ? Math.round(s.co2mg_kwh).toString() : "\u2014";
  var co2mTot  = _formaterCo2Total(s.co2m_total);
  var co2mgTot = _formaterCo2Total(s.co2mg_total);
  return '<td class="eco2mix-td eco2mix-td-co2m">' + co2mKwh + '</td>' +
         '<td class="eco2mix-td eco2mix-td-co2m-total">' + co2mTot + '</td>' +
         '<td class="eco2mix-td eco2mix-td-co2mg">' + co2mgKwh + '</td>' +
         '<td class="eco2mix-td eco2mix-td-co2mg-total">' + co2mgTot + '</td>';
}

function _e2mBuildTipMix(slot) {
  /* Construit le contenu du tooltip mix de production pour CO₂ moyen. */
  var mix = slot.mix;
  if (!mix) return "";
  var LABELS = {
    nucleaire:        t("energie_nucleaire"),
    hydraulique:      t("energie_hydraulique"),
    eolien_terrestre: t("energie_eolien_terr"),
    eolien_offshore:  t("energie_eolien_off"),
    solaire:          t("energie_solaire"),
    gaz:              t("energie_gaz"),
    bioenergies:      t("energie_bioenergies"),
    fioul:            t("energie_fioul"),
    charbon:          t("energie_charbon"),
  };
  var total = 0;
  Object.keys(mix).forEach(function(k) { if (mix[k] > 0) total += mix[k]; });
  if (total === 0) return "";
  var sources = Object.keys(mix)
    .filter(function(k) { return mix[k] > 0; })
    .sort(function(a, b) { return mix[b] - mix[a]; });
  var lines = [t("mix_production")];
  sources.forEach(function(k) {
    var pct   = Math.round(mix[k] / total * 100);
    var mw    = Math.round(mix[k]);
    lines.push((LABELS[k] || k) + " : " + pct + "% (" + mw + " MW)");
  });
  var echanges = slot.echanges;
  if (echanges) {
    var solde = 0;
    Object.keys(echanges).forEach(function(k) { solde += echanges[k] || 0; });
    if (Math.abs(solde) > 1) {
      lines.push("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500");
      lines.push(t("solde_echanges") + (solde > 0 ? "+" : "") + Math.round(solde) + " MW");
    }
  }
  return lines.join("\n");
}

function _e2mBuildTipMarginal(slot) {
  /* Construit le contenu du tooltip pour CO₂ marginal avec filières retenues. */
  var mg = slot.co2_marginal;
  if (!mg) return "";
  var LABELS = {
    nucleaire:        t("energie_nucleaire"),
    hydraulique:      t("energie_hydraulique"),
    eolien_terrestre: t("energie_eolien_terr"),
    eolien_offshore:  t("energie_eolien_off"),
    solaire:          t("energie_solaire"),
    gaz:              t("energie_gaz"),
    bioenergies:      t("energie_bioenergies"),
    fioul:            t("energie_fioul"),
    charbon:          t("energie_charbon"),
  };
  var seuil = mg.seuil_pct != null ? ((mg.seuil_pct * 100).toFixed(0) + "%") : "—";
  var lines = [_tpl("co2_mix_marginal_titre", { seuil: seuil })];
  var filieres = mg.filieres_retenues;
  if (filieres && filieres.length) {
    filieres.forEach(function(f) {
      var label   = LABELS[f.filiere] || f.filiere;
      var facteur = f.facteur + " gCO\u2082/kWh";
      var mw      = f.partiel
        ? f.mw_retenus.toFixed(0) + "/" + f.mw_total.toFixed(0) + " MW (partiel)"
        : f.mw_retenus.toFixed(0) + " MW";
      lines.push(label + " : " + mw + " \u2014 " + facteur);
    });
    lines.push("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500");
    lines.push(t("co2_resultat") + " : " + mg.taux_gco2_kwh + " gCO\u2082/kWh");
  } else {
    lines.push(t("co2_donnees_indisponibles"));
  }
  return lines.join("\n");
}

function _e2mCellulesDetail(r) {
  /* 4 cellules eco2mix pour une ligne de détail (créneau individuel). */
  if (!_e2mCreneaux) return '<td class="eco2mix-td" colspan="4"></td>';
  var slot = _e2mCreneaux[r.ts];
  if (!slot) return '<td class="eco2mix-td" colspan="4"></td>';
  var co2m  = (slot.co2_moyen  || {}).taux_gco2_kwh;
  var co2mg = (slot.co2_marginal || {}).taux_gco2_kwh;
  var kwh   = r.kwh || 0;
  var co2mKwh  = co2m  != null ? slot.affichage_co2m  : "\u2014";
  var co2mgKwh = co2mg != null ? slot.affichage_co2mg : "\u2014";
  var co2mTot  = co2m  != null ? _formaterCo2Total(co2m  * kwh) : "\u2014";
  var co2mgTot = co2mg != null ? _formaterCo2Total(co2mg * kwh) : "\u2014";
  var clsM  = slot.cls_co2m  || "";
  var clsMg = slot.cls_co2mg || "";
  // Tooltips
  var tipMix = _e2mBuildTipMix(slot);
  var tipMg  = _e2mBuildTipMarginal(slot);
  var dispM  = tipMix ? '<span class="detail-tip" data-tip="' + tipMix + '">' + co2mKwh  + '</span>' : co2mKwh;
  var dispMg = tipMg  ? '<span class="detail-tip" data-tip="' + tipMg  + '">' + co2mgKwh + '</span>' : co2mgKwh;
  return '<td class="eco2mix-td eco2mix-td-co2m ' + clsM + '">' + dispM + '</td>' +
         '<td class="eco2mix-td eco2mix-td-co2m-total">' + co2mTot + '</td>' +
         '<td class="eco2mix-td eco2mix-td-co2mg ' + clsMg + '">' + dispMg + '</td>' +
         '<td class="eco2mix-td eco2mix-td-co2mg-total">' + co2mgTot + '</td>';
}

function _acCellulesVides(cfg) {
  var s = '<td colspan="' + cfg.e2mCols + '"></td>';
  cfg.offres.forEach(function(o) { s += '<td colspan="' + o.cols + '"></td>'; });
  return s;
}

function _acTariffStatsGroupe(records, offres) {
  /* Calcule pour chaque offre : coût total (€), kwh total, sommes pondérées
     des champs extra numériques (offres custom), et sommes par couleur×HC/HP (Tempo). */
  var stats = {};
  offres.forEach(function(o) { stats[o.id] = { prix_eur: 0, kwh: 0, extra: {}, colEur: {} }; });
  records.forEach(function(r) {
    var hourKey = _tariffHourKey(r.ts);
    var kwh     = r.kwh || 0;
    offres.forEach(function(o) {
      var td = _tariffIndex[o.id] && _tariffIndex[o.id][hourKey];
      if (td) {
        stats[o.id].prix_eur += kwh * (td.pk || 0);
        stats[o.id].kwh      += kwh;
        // Sommes pondérées des champs extra numériques (offres custom)
        Object.keys(td).forEach(function(key) {
          if (key === "pk" || key === "h" || key === "col") return;
          var val = parseFloat(td[key]);
          if (!isNaN(val)) {
            stats[o.id].extra[key] = (stats[o.id].extra[key] || 0) + kwh * val;
          }
        });
        // Somme du coût par couleur×HC/HP (Tempo)
        if (td.col) {
          var cEur = td.col + "_eur";
          stats[o.id].colEur[cEur] = (stats[o.id].colEur[cEur] || 0) + kwh * (td.pk || 0);
        }
      }
    });
  });
  return stats;
}

function _acGroupCellsTarif(offre, tariffStats) {
  /* Cellules tarif pour une ligne de groupe : total coût + prix moyen pondéré. */
  var s = tariffStats && tariffStats[offre.id];
  if (!s || s.kwh === 0 || !offre.detail_ligne) {
    return '<td colspan="' + offre.cols + '"></td>';
  }
  var prixKwh = (s.prix_eur / s.kwh).toFixed(6);  // moyenne pondérée (exact pour base, agrégat pour HPHC)
  var prixEur = s.prix_eur.toFixed(2);
  var html = offre.detail_ligne
    .replace(/\{\{type_hc\}\}/g,      "")   // pas de type HC/HP pour un agrégat
    .replace(/\{\{type_css\}\}/g,     "")
    .replace(/\{\{prix_kwh\}\}/g,     prixKwh)
    .replace(/\{\{prix_kwh_tip\}\}/g, prixKwh)  // pas de tooltip sur les lignes agrégées
    .replace(/\{\{prix_eur\}\}/g,     prixEur);
  // Moyennes pondérées des champs extra numériques (offres custom)
  if (s.extra) {
    Object.keys(s.extra).forEach(function(key) {
      var moy = s.kwh > 0 ? (s.extra[key] / s.kwh).toFixed(4) : "0";
      html = html.replace(new RegExp("\\{\\{" + key + "\\}\\}", "g"), moy);
    });
  }
  // Sommes par couleur×HC/HP (Tempo)
  var TEMPO_COLS = ["bleu_hc","bleu_hp","blanc_hc","blanc_hp","rouge_hc","rouge_hp"];
  TEMPO_COLS.forEach(function(c) {
    var key = c + "_eur";
    var val = (s.colEur && s.colEur[key] !== undefined) ? s.colEur[key].toFixed(2) : "0.00";
    html = html.replace(new RegExp("\\{\\{" + key + "\\}\\}", "g"), val);
  });
  return html.replace(/^\s*<tr[^>]*>\s*/i, "").replace(/\s*<\/tr>\s*$/i, "");
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

function _acLigneGroupe(ligneTpl, cfg, classes, acId, acDay, labelHtml, stats, tariffStats, e2mCells, solCells) {
  /* Construit une ligne de groupe.
     tariffStats : résultat de _acTariffStatsGroupe (peut être null → cellules vides)
     e2mCells : HTML des 4 cellules eco2mix (pré-calculé)
     solCells  : HTML des cellules solaires (pré-calculé) */
  var tr = '<tr class="ac-group ' + classes + '" data-ac-id="' + acId + '"' +
           (acDay ? ' data-ac-day="' + acDay + '"' : '') + '>';
  tr += ligneTpl
    .replace(/\{\{ts\}\}/g,   labelHtml)
    .replace(/\{\{kwh\}\}/g,  stats.kwh.toFixed(1))
    .replace(/\{\{pmax\}\}/g, stats.pmax.toFixed(1));
  tr += e2mCells || ('<td colspan="' + cfg.e2mCols + '"></td>');
  cfg.offres.forEach(function(o) { tr += _acGroupCellsTarif(o, tariffStats); });
  tr += (solCells !== undefined ? solCells : _solaireVidesHtml());
  tr += '</tr>';
  return tr;
}

function _acHtmlGroupe(cfg, ligneTpl) {
  /* Construit les lignes de regroupement (année/mois/jour).
     Tri décroissant. Ligne TOTAL en tête. Détails lazy. */
  var html = "";
  var years = Object.keys(_acGroups).sort().reverse();

  // ── Ligne TOTAL ───────────────────────────────────────────────────────────
  var allRecs = [];
  years.forEach(function(y) {
    var yd = _acGroups[y];
    Object.keys(yd).forEach(function(mo) {
      Object.keys(yd[mo]).forEach(function(d) { allRecs = allRecs.concat(yd[mo][d]); });
    });
  });
  var tStats = _acStats(allRecs);
  var tTarif = _acTariffStatsGroupe(allRecs, cfg.offres);
  var tLabel = '<strong>' + t("detail_total") + '</strong>' +
               ' <span class="ac-count">' + tStats.count + '\u00a0' + t("releves") + '</span>';
  var trTotal = '<tr class="ac-group ac-total">';
  trTotal += ligneTpl
    .replace(/\{\{ts\}\}/g,   tLabel)
    .replace(/\{\{kwh\}\}/g,  tStats.kwh.toFixed(1))
    .replace(/\{\{pmax\}\}/g, tStats.pmax.toFixed(1));
  trTotal += _e2mCellulesGroupe(allRecs);
  cfg.offres.forEach(function(o) { trTotal += _acGroupCellsTarif(o, tTarif); });
  trTotal += _solaireCellulesGroupe(allRecs);
  trTotal += '</tr>';
  html += trTotal;

  years.forEach(function(year) {
    var yearData = _acGroups[year];
    var months   = Object.keys(yearData).sort().reverse();

    var allYearRecs = [];
    months.forEach(function(m) {
      Object.keys(yearData[m]).forEach(function(d) {
        allYearRecs = allYearRecs.concat(yearData[m][d]);
      });
    });
    var yStats      = _acStats(allYearRecs);
    var yTarif      = _acTariffStatsGroupe(allYearRecs, cfg.offres);
    var yLabel = '<button class="ac-toggle" data-ac-open="1">▼</button>' +
                 ' <strong>' + year + '</strong>' +
                 ' <span class="ac-count">' + yStats.count + '\u00a0' + t("releves") + '</span>';

    html += _acLigneGroupe(ligneTpl, cfg, 'ac-year', 'y-' + year, null, yLabel, yStats, yTarif,
      _e2mCellulesGroupe(allYearRecs), _solaireCellulesGroupe(allYearRecs));

    months.forEach(function(month) {
      var monthData = yearData[month];
      var days      = Object.keys(monthData).sort().reverse();

      var allMonthRecs = [];
      days.forEach(function(d) { allMonthRecs = allMonthRecs.concat(monthData[d]); });
      var mStats = _acStats(allMonthRecs);
      var mTarif = _acTariffStatsGroupe(allMonthRecs, cfg.offres);
      var mLabel = '<button class="ac-toggle" data-ac-open="0">▶</button>' +
                   ' <strong>' + _acNomMois(month) + '</strong>' +
                   ' <span class="ac-count">' + mStats.count + '\u00a0' + t("releves") + '</span>';

      html += _acLigneGroupe(ligneTpl, cfg,
        'ac-month ac-child-y-' + year, 'm-' + month, null, mLabel, mStats, mTarif,
        _e2mCellulesGroupe(allMonthRecs), _solaireCellulesGroupe(allMonthRecs));

      days.forEach(function(day) {
        var recs   = monthData[day];
        var dStats = _acStats(recs);
        var dTarif = _acTariffStatsGroupe(recs, cfg.offres);
        var dLabel = '<button class="ac-toggle" data-ac-open="0">▶</button>' +
                     ' ' + _acNomJour(day) +
                     ' <span class="ac-count">' + dStats.count + '\u00a0' + t("releves") + '</span>';

        html += _acLigneGroupe(ligneTpl, cfg,
          'ac-day ac-child-m-' + month + ' hidden', 'd-' + day, day, dLabel, dStats, dTarif,
          _e2mCellulesGroupe(recs), _solaireCellulesGroupe(recs));
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

  // Tri décroissant (le plus récent en haut)
  recs = recs.slice().sort(function(a, b) {
    return a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0;
  });

  var html = "";
  recs.forEach(function(r) {
    var hourKey    = _tariffHourKey(r.ts);
    var cellesExt  = _e2mCellulesDetail(r);
    cfg.offres.forEach(function(o) { cellesExt += _tariffCellsHtml(o, r, hourKey); });
    cellesExt += _solaireCellulesDetail(r.ts);
    html += '<tr class="ac-detail ac-child-d-' + day + '">' +
      ligneTpl
        .replace(/\{\{ts\}\}/g,   _formaterTs(r.ts))
        .replace(/\{\{kwh\}\}/g,  r.kwh  !== undefined ? r.kwh.toFixed(3)  : "")
        .replace(/\{\{pmax\}\}/g, r.pmax !== undefined ? r.pmax.toFixed(1) : "") +
      cellesExt + '</tr>';
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
  /* Injecte les lignes de regroupement dans tbody.
     Appelé après chargement et à chaque navigation vers Détails. */
  if (!_acGroups || !_acGroupsCfg) return;
  var tmp = document.createElement("table");
  tmp.innerHTML = "<tbody>" + _acHtmlGroupe(_acGroupsCfg.cfg, _acGroupsCfg.ligneTpl) + "</tbody>";
  var rows = tmp.querySelector("tbody");
  var frag = document.createDocumentFragment();
  while (rows.firstChild) frag.appendChild(rows.firstChild);
  tbody.appendChild(frag);
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
    if (tbodyVide) tbodyVide.innerHTML = '<tr><td colspan="99" class="debug-vide">' + t("aucune_donnee") + '</td></tr>';
    return;
  }

  if (banner) banner.classList.remove("hidden");
  _majBarre(0);

  // Groupement en mémoire
  var groupes = {};
  var rawRecords = [];
  var nbRecu  = 0;

  function _grouperLigne(r) {
    rawRecords.push(r);
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
      }
      _consoRecords = rawRecords;
      // Déclencher les custom_scripts qui attendaient _consoRecords
      Object.keys(_customScriptPending).forEach(function(id) { _execCustomScript(id); });
      _rendreRapports();
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
    return d.toLocaleDateString(_locale(), { day: "2-digit", month: "2-digit", timeZone: "Europe/Paris" }) +
           " " + d.toLocaleTimeString(_locale(), { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" });
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
  // api_conso
  var acTitre = (masques.api_conso || {}).tableau_titre || "";
  var acCols  = _compterTh(acTitre) || 3;

  // eco2mix — peut être multi-lignes (tableau_titre contient des <tr>)
  var e2mTitre  = (masques.eco2mix || {}).tableau_titre || "";
  var e2mLignes = _parseBandeau(e2mTitre);
  var e2mCols   = e2mLignes.length > 0
    ? e2mLignes[e2mLignes.length - 1].cols
    : (_compterTh(e2mTitre) || 2);

  // solaire — 2 lignes (groupe + noms sources), peut être absent
  var solaireTitre   = (masques.solaire || {}).tableau_titre || "";
  var solaireLignes  = _parseBandeau(solaireTitre);
  var solaireCols    = solaireLignes.length > 0
    ? solaireLignes[solaireLignes.length - 1].cols
    : 0;
  var solaireSources = (masques.solaire || {}).sources || [];

  // generique : toutes les lignes du detail_bandeau, placeholders substitués
  var _SPINNER_INLINE = '<span class="details-inline-spinner"></span>';
  var generique = masques.generique || {};
  var offres    = Object.entries(generique).map(function (kv) {
    var id     = kv[0]; var m = kv[1];
    var lignes = _parseBandeau(m.detail_bandeau || "");
    var cols   = lignes.length ? lignes[lignes.length - 1].cols : 2;
    return {
      id:           id,
      cols:         cols,
      lignes:       lignes,
      detail_ligne: m.detail_ligne  || "",
      custom:       m.custom === true,
      custom_script: m.custom_script || "",
      ndjson_url:   m.custom ? null : "/data/tarif_" + _genSlug(id) + ".ndjson",
    };
  });

  // Lancement du chargement des tarifs en arrière-plan
  offres.forEach(function(o) {
    if (o.custom && o.custom_script) _chargerTariffCustomScript(o);
    else if (o.custom)               _chargerTariffCustom(o.id);
    else                             _chargerTariffNdjson(o.id, o.ndjson_url);
  });

  // Nombre de lignes de bandeau : max entre eco2mix, solaire et les offres
  var nbLignesBandeau = offres.reduce(function (max, o) {
    return Math.max(max, o.lignes.length);
  }, Math.max(e2mLignes.length, solaireLignes.length));

  // ── thead ─────────────────────────────────────────────────────────────────
  // api_conso et eco2mix utilisent rowspan sur toutes les lignes de groupe
  // → une seule cellule quelle que soit la hauteur du bandeau le plus long.
  // Les offres s'alignent par le bas : padding en haut avec rowspan si nécessaire.
  var theadHtml = "";

  // Décalage eco2mix (bottom-aligned, comme les offres tarifs)
  var e2mOffset = e2mLignes.length > 0 ? nbLignesBandeau - e2mLignes.length : 0;

  // Décalage solaire (bottom-aligned comme eco2mix et les offres)
  var solOffset = solaireLignes.length > 0 ? nbLignesBandeau - solaireLignes.length : 0;

  if (nbLignesBandeau <= 1) {
    // Cas simple : une seule ligne
    theadHtml += "<tr>" + acTitre;
    if (e2mLignes.length === 1) {
      theadHtml += e2mLignes[0].innerHtml;
    } else if (e2mLignes.length === 0 && e2mTitre) {
      theadHtml += e2mTitre;
    }
    offres.forEach(function (o) {
      var derniere = o.lignes[o.lignes.length - 1];
      theadHtml += derniere
        ? derniere.innerHtml
        : '<th colspan="' + o.cols + '">' + o.id + "</th>";
    });
    // solaire
    if (solaireLignes.length === 1) {
      theadHtml += solaireLignes[0].innerHtml;
    }
    theadHtml += "</tr>";
  } else {
    // Ligne 0 : acTitre rowspan + eco2mix row 0 (ou cellule vide) + offres row 0 + solaire row 0
    theadHtml += "<tr>";
    theadHtml += _avecRowspan(acTitre, nbLignesBandeau);

    // eco2mix en ligne 0 : cellule vide de rembourrage si e2mOffset > 0
    if (e2mLignes.length > 0) {
      if (e2mOffset > 0) {
        theadHtml += '<th colspan="' + e2mCols + '" rowspan="' + e2mOffset +
                     '" class="details-th-vide"></th>';
        // eco2mix[0] ira dans la première ligne intermédiaire (e2mIdx=0)
      } else {
        // eco2mix commence en ligne 0
        if (e2mLignes.length > 1) {
          theadHtml += e2mLignes[0].innerHtml;  // première ligne (hors dernière)
        } else {
          // Une seule ligne eco2mix, offset=0 → couvre toutes les lignes avec rowspan
          theadHtml += _avecRowspan(e2mLignes[0].innerHtml, nbLignesBandeau);
        }
      }
    } else if (e2mTitre) {
      theadHtml += _avecRowspan(e2mTitre, nbLignesBandeau);
    }

    // offres en ligne 0
    offres.forEach(function (o) {
      var offset = nbLignesBandeau - o.lignes.length;
      if (offset > 0) {
        theadHtml += '<th colspan="' + o.cols + '" rowspan="' + offset +
                     '" class="details-th-vide"></th>';
        if (o.lignes.length > 1) theadHtml += _normaliserColspan(o.lignes[0].innerHtml, o.cols);
      } else {
        if (o.lignes.length > 1) theadHtml += _normaliserColspan(o.lignes[0].innerHtml, o.cols);
      }
    });

    // solaire en ligne 0
    if (solaireLignes.length > 0) {
      if (solOffset > 0) {
        theadHtml += '<th colspan="' + solaireCols + '" rowspan="' + solOffset +
                     '" class="details-th-vide"></th>';
        // solaireLignes[0] ira dans la première ligne intermédiaire via solIdx
      } else {
        if (solaireLignes.length > 1) theadHtml += solaireLignes[0].innerHtml;
        else theadHtml += _avecRowspan(solaireLignes[0].innerHtml, nbLignesBandeau);
      }
    }
    theadHtml += "</tr>";

    // Lignes intermédiaires
    for (var i = 1; i < nbLignesBandeau - 1; i++) {
      theadHtml += "<tr>";

      // eco2mix lignes intermédiaires (index ajusté par e2mOffset)
      if (e2mLignes.length > 1) {
        var e2mIdx = i - e2mOffset;
        if (e2mIdx >= 0 && e2mIdx <= e2mLignes.length - 2) {
          theadHtml += e2mLignes[e2mIdx].innerHtml;
        }
      }

      // offres lignes intermédiaires
      offres.forEach(function (o) {
        var offset   = nbLignesBandeau - o.lignes.length;
        var ligneIdx = i - offset;
        if (ligneIdx > 0 && ligneIdx < o.lignes.length - 1) {
          theadHtml += _normaliserColspan(o.lignes[ligneIdx].innerHtml, o.cols);
        }
      });

      // solaire lignes intermédiaires
      if (solaireLignes.length > 1) {
        var solIdx = i - solOffset;
        if (solIdx >= 0 && solIdx <= solaireLignes.length - 2) {
          theadHtml += solaireLignes[solIdx].innerHtml;
        }
      }
      theadHtml += "</tr>";
    }

    // Dernière ligne
    theadHtml += "<tr>";

    // eco2mix dernière ligne (sauf si single-row avec offset=0, déjà couverte par rowspan)
    if (e2mLignes.length > 1) {
      theadHtml += e2mLignes[e2mLignes.length - 1].innerHtml;
    } else if (e2mLignes.length === 1 && e2mOffset > 0) {
      // Single eco2mix row, alignée en bas
      theadHtml += e2mLignes[0].innerHtml;
    }

    // offres dernière ligne
    offres.forEach(function (o) {
      var derniere = o.lignes[o.lignes.length - 1];
      theadHtml += derniere
        ? derniere.innerHtml
        : '<th colspan="' + o.cols + '">' + o.id + "</th>";
    });

    // solaire dernière ligne
    if (solaireLignes.length > 1) {
      theadHtml += solaireLignes[solaireLignes.length - 1].innerHtml;
    } else if (solaireLignes.length === 1 && solOffset > 0) {
      theadHtml += solaireLignes[0].innerHtml;
    }
    theadHtml += "</tr>";
  }

  // Stocker la config pour les étapes suivantes
  _detailsConfig = { acCols: acCols, e2mCols: e2mCols, offres: offres,
                     solaireCols: solaireCols, solaireSources: solaireSources };

  var html = '<div class="details-scroll">' +
    '<table class="details-table">' +
      "<thead>" + theadHtml + "</thead>" +
      "<tbody id=\"details-tbody\"></tbody>" +
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
  _initControlesHome(el); // rattacher les listeners après chaque restauration HTML
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
    // Bouton Configuration générale
    if (e.target.id === "pg-save-btn") { _sauvegarderParamsGlobaux(); return; }
    var btn = e.target.closest("[data-save]");
    if (btn) { sauvegarderParams(btn.dataset.save); return; }
    var btnInline = e.target.closest("[data-save-inline]");
    if (btnInline) { _sauvegarderParamsInline(btnInline.dataset.saveInline, btnInline.dataset.saveGroupe); return; }
    var header = e.target.closest(".settings-card-header[data-toggle]");
    if (header) _toggleCard(header.dataset.toggle);
  });
  _settingsListenerPret = true;
}

function _sauvegarderParamsGlobaux() {
  var inpPs = document.getElementById("pg-puissance-input");
  var inpHc = document.getElementById("pg-hc-input");
  var msgEl = document.getElementById("pg-msg");
  var btnEl = document.getElementById("pg-save-btn");
  if (!inpPs || !inpHc) return;

  var nouvellePuissance = inpPs.value.trim() ? parseFloat(inpPs.value) : null;
  var nouvelleHc        = inpHc.value.trim();
  var hcChangee         = nouvelleHc !== ((_paramsGlobaux || {}).plage_hc || "");

  if (btnEl) { btnEl.disabled = true; btnEl.textContent = t("enregistrement_cours"); }
  if (msgEl) { msgEl.textContent = ""; msgEl.className = "params-globaux-msg"; }

  fetch("/api/save_params", {
    method:  "POST",
    headers: { "Content-Type": "application/json" },
    body:    JSON.stringify({
      groupe: "generique",
      params: { puissance_souscrite: nouvellePuissance, plage_hc_globale: nouvelleHc },
    }),
  })
  .then(function (r) { return r.json(); })
  .then(function (rep) {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = t("btn_save"); }
    if (rep.status) {
      if (msgEl) { msgEl.textContent = t("sauvegarde_ok"); msgEl.className = "params-globaux-msg ok"; }
      // Mettre à jour _paramsGlobaux en mémoire
      if (!_paramsGlobaux) _paramsGlobaux = {};
      _paramsGlobaux.puissance_souscrite = nouvellePuissance != null ? String(nouvellePuissance) : "";
      _paramsGlobaux.plage_hc            = nouvelleHc;
      // Rechargement : complet si HC a changé (NDJSON tarif impactés), léger sinon
      if (hcChangee) {
        _rechargerApresHC(true); // HC changé → NDJSON tarif rechargés + tableau de détails rafraîchi
      } else {
        _rechargerMasquesParams();
      }
      setTimeout(function () {
        if (msgEl) { msgEl.textContent = ""; msgEl.className = "params-globaux-msg"; }
      }, 3000);
    } else {
      if (msgEl) { msgEl.textContent = t("erreur_prefix") + (rep.error || "?"); msgEl.className = "params-globaux-msg err"; }
    }
  })
  .catch(function () {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = t("btn_save"); }
    if (msgEl) { msgEl.textContent = t("erreur_reseau"); msgEl.className = "params-globaux-msg err"; }
  });
}

function chargerPageParametres() {
  var el = document.getElementById("settings-content");
  if (!el) return;
  el.innerHTML = _contenuPages["settings"] !== undefined ? _contenuPages["settings"] : _SPINNER_HTML;
}

function chargerPageModules() {
  var el = document.getElementById("modules-content");
  if (!el) return;
  // Toujours reconstruire la page depuis l'API config
  el.innerHTML = _SPINNER_HTML;
  fetch("/api/solaire/config")
    .then(function (r) { return r.json(); })
    .then(function (rep) {
      if (!rep.status) { el.innerHTML = '<p class="debug-vide">Erreur chargement config solaire.</p>'; return; }
      _solModConfig = rep.data;
      el.innerHTML = _solModuleHtml(rep.data);
      _initModulesPage(el);
    })
    .catch(function () { el.innerHTML = '<p class="debug-vide">Erreur réseau.</p>'; });
}

function _solModuleHtml(cfg) {
  var tarifs  = cfg.tarifs  || {};
  var sources = cfg.sources || [];
  var panneau = cfg.panneau || {};

  // Selector tarif
  var tarOpts = Object.keys(tarifs).map(function (id) {
    var m = tarifs[id];
    return '<option value="' + _escHtml(id) + '">'
      + _escHtml((m.fournisseur || "") + " — " + (m.nom || id)
        + (m.type && m.type !== "base" ? " (" + m.type + ")" : ""))
      + "</option>";
  }).join("");

  // Selector source
  var srcOpts = sources.map(function (s) {
    return '<option value="' + _escHtml(s.id) + '">' + _escHtml(s.nom || s.id) + "</option>";
  }).join("");

  var noTarif  = !tarOpts  ? '<p class="sol-mod-warn">Aucun tarif disponible — lancez une mise à jour.</p>' : "";
  var noSource = !srcOpts  ? '<p class="sol-mod-warn">Aucune source solaire configurée — allez dans Paramètres → Modules complémentaires → Solaire.</p>' : "";

  var nbMaxLabel = "0–" + (panneau.nb_panneaux_max || 20);

  return '<div class="sol-mod-page">'
    + '<div class="sol-mod-titre">☀ Solaire — Analyse d\'optimisation</div>'
    + '<div class="sol-mod-config">'
      + noTarif + noSource
      + (tarOpts && srcOpts
        ? '<div class="sol-mod-row">'
            + '<div class="sol-mod-field">'
              + '<label class="sol-mod-label">Tarif de référence</label>'
              + '<select id="sol-mod-tarif" class="sol-mod-select">' + tarOpts + '</select>'
            + '</div>'
            + '<div class="sol-mod-field">'
              + '<label class="sol-mod-label">Source solaire</label>'
              + '<select id="sol-mod-source" class="sol-mod-select">' + srcOpts + '</select>'
            + '</div>'
            + '<div class="sol-mod-field sol-mod-field-btn">'
              + '<label class="sol-mod-label">&nbsp;</label>'
              + '<button class="sol-mod-btn-calc" id="sol-mod-calc-btn" onclick="_solModCalculer()">Calculer</button>'
            + '</div>'
          + '</div>'
          + '<div class="sol-mod-hint">Simule de ' + nbMaxLabel + ' panneaux · ' + (panneau.puissance_wc || 425) + ' Wc · PR ' + (panneau.performance_ratio || 80) + ' %</div>'
        : "")
    + '</div>'
    + '<div id="sol-mod-results"></div>'
  + '</div>';
}

function _initModulesPage(el) {
  // Listeners déjà posés via onclick inline — rien à ajouter
}

window._solModCalculer = function () {
  var tarifEl  = document.getElementById("sol-mod-tarif");
  var sourceEl = document.getElementById("sol-mod-source");
  var resEl    = document.getElementById("sol-mod-results");
  var btn      = document.getElementById("sol-mod-calc-btn");
  if (!tarifEl || !sourceEl || !resEl) return;

  var tariffId = tarifEl.value;
  var sourceId = sourceEl.value;
  if (!tariffId || !sourceId) return;

  if (!_consoRecords || !_solaireData) {
    resEl.innerHTML = '<p class="sol-mod-warn">Données non encore chargées — attendez la fin du chargement initial.</p>';
    return;
  }

  if (btn) { btn.disabled = true; btn.textContent = "Calcul…"; }
  resEl.innerHTML = _SPINNER_HTML;

  // Tarif déjà chargé → calcul immédiat
  if (_tariffIndex[tariffId]) {
    _solModDoCalc(tariffId, sourceId);
    if (btn) { btn.disabled = false; btn.textContent = "Calculer"; }
    return;
  }

  // Charger le tarif à la demande
  var cfg = (_solModConfig || {});
  var meta = (cfg.tarifs || {})[tariffId] || {};
  var isCustom = meta.source === "custom";

  function _onLoaded() {
    if (btn) { btn.disabled = false; btn.textContent = "Calculer"; }
    if (!_tariffIndex[tariffId]) {
      resEl.innerHTML = '<p class="sol-mod-warn">Impossible de charger les données du tarif.</p>';
      return;
    }
    _solModDoCalc(tariffId, sourceId);
  }

  if (isCustom) {
    fetch("/api/tarif/custom/" + encodeURIComponent(tariffId))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (data && Array.isArray(data.creneaux)) {
          var idx = {};
          data.creneaux.forEach(function (c) {
            var hk = _tariffHourKey(c.ts);
            if (hk) idx[hk] = { pk: c.prix_kwh || 0 };
          });
          _tariffIndex[tariffId] = idx;
        }
        _onLoaded();
      })
      .catch(function () { _onLoaded(); });
  } else {
    var slug = _genSlug(tariffId);
    fetch("/data/tarif_" + slug + ".ndjson")
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (texte) {
        if (texte) {
          var idx = {};
          texte.split("\n").forEach(function (ligne) {
            var l = ligne.trim();
            if (!l) return;
            try { var rec = JSON.parse(l); if (rec.t) idx[rec.t] = { pk: rec.pk || 0 }; } catch (e) {}
          });
          _tariffIndex[tariffId] = idx;
        }
        _onLoaded();
      })
      .catch(function () { _onLoaded(); });
  }
};

function _solModDoCalc(tariffId, sourceId) {
  var resEl = document.getElementById("sol-mod-results");
  if (!resEl) return;

  var cfg     = _solModConfig || {};
  var panneau = cfg.panneau   || {};
  var sources = (cfg.tarifs && cfg.tarifs[tariffId] ? (cfg.sources || []) : (_solaireData.sources || []));
  // Chercher dans les sources solaire chargées
  var srcList = _solaireData.sources || [];
  var srcIdx  = -1;
  var srcInfo = null;
  for (var i = 0; i < srcList.length; i++) {
    if (srcList[i].id === sourceId) { srcIdx = i; srcInfo = srcList[i]; break; }
  }
  if (srcIdx === -1) {
    resEl.innerHTML = '<p class="sol-mod-warn">Source introuvable dans les données solaires.</p>';
    return;
  }

  var prix    = _tariffIndex[tariffId] || {};
  var creneaux = _solaireData.creneaux || {};
  var puissanceWc = parseFloat(panneau.puissance_wc  || 425);
  var pr          = parseFloat(panneau.performance_ratio || 80) / 100.0;
  var nbMax       = parseInt(panneau.nb_panneaux_max || 20, 10);
  var coutPanneau = parseFloat(panneau.cout_panneau  || 900);
  var facteur1p   = (puissanceWc / 1000.0) * pr;

  // Intersection : conso × tarif × solaire → trié
  var tsListe = [];
  for (var ri = 0; ri < _consoRecords.length; ri++) {
    var r   = _consoRecords[ri];
    var hk  = _tariffHourKey(r.ts);
    var pkE = prix[hk];
    if (!pkE) continue;
    var solVals = creneaux[r.ts];
    if (!solVals || !solVals[srcIdx] || solVals[srcIdx].kwh == null) continue;
    tsListe.push(r.ts);
  }
  tsListe.sort();

  if (tsListe.length === 0) {
    resEl.innerHTML = '<p class="sol-mod-warn">Aucune période commune (solaire × tarif × consommation).</p>';
    return;
  }

  // Dernière année glissante
  var tsFin   = tsListe[tsListe.length - 1];
  var dtFin   = new Date(tsFin);
  var dtDeb   = new Date(dtFin);
  dtDeb.setFullYear(dtDeb.getFullYear() - 1);
  var tsDeb   = dtDeb.toISOString();
  var tsPeriode = tsListe.filter(function (ts) { return ts >= tsDeb; });

  if (tsPeriode.length < 48) {
    resEl.innerHTML = '<p class="sol-mod-warn">Données insuffisantes (' + tsPeriode.length + ' créneaux).</p>';
    return;
  }

  // Index rapide conso
  var consoIdx = {};
  for (var ci = 0; ci < _consoRecords.length; ci++) {
    consoIdx[_consoRecords[ci].ts] = _consoRecords[ci].kwh || 0;
  }

  // Facture sans panneaux (N=0)
  var consoTotale = 0, facture0 = 0;
  for (var ti = 0; ti < tsPeriode.length; ti++) {
    var ts  = tsPeriode[ti];
    var kwh = consoIdx[ts] || 0;
    var pk0 = (prix[_tariffHourKey(ts)] || {}).pk || 0;
    consoTotale += kwh;
    facture0    += kwh * pk0;
  }

  // Calcul pour N = 0..nbMax
  var resultats = [];
  for (var n = 0; n <= nbMax; n++) {
    if (n === 0) {
      resultats.push({ n: 0, production_kwh: 0, excedent_kwh: 0,
        conso_nette_kwh: Math.round(consoTotale * 10) / 10,
        facture_eur: Math.round(facture0 * 100) / 100,
        economie_eur: 0, cout_install: 0, rsi_ans: null });
      continue;
    }
    var facteurN = n * facteur1p;
    var prodTot = 0, excTot = 0, factureN = 0;
    for (var si = 0; si < tsPeriode.length; si++) {
      var ts2  = tsPeriode[si];
      var cw   = consoIdx[ts2] || 0;
      var sw   = ((creneaux[ts2][srcIdx] || {}).kwh || 0) * facteurN;
      var net  = cw - sw;
      if (net < 0) { excTot += -net; net = 0; }
      prodTot  += sw;
      factureN += net * ((prix[_tariffHourKey(ts2)] || {}).pk || 0);
    }
    var eco      = Math.round((facture0 - factureN) * 100) / 100;
    var coutInst = Math.round(n * coutPanneau);
    var rsi      = (eco > 0.1) ? Math.round(coutInst / eco * 10) / 10 : null;
    resultats.push({
      n: n,
      production_kwh:  Math.round(prodTot  * 10) / 10,
      excedent_kwh:    Math.round(excTot   * 10) / 10,
      conso_nette_kwh: Math.round((consoTotale - prodTot + excTot) * 10) / 10,
      facture_eur:     Math.round(factureN * 100) / 100,
      economie_eur:    eco,
      cout_install:    coutInst,
      rsi_ans:         rsi,
    });
  }

  // Nom du tarif
  var tarifMeta = (cfg.tarifs || {})[tariffId] || {};
  var tariffNom = (tarifMeta.fournisseur || "") + " — " + (tarifMeta.nom || tariffId)
    + (tarifMeta.type && tarifMeta.type !== "base" ? " (" + tarifMeta.type + ")" : "");

  resEl.innerHTML = _solModRapportHtml({
    source:           srcInfo,
    tariff_nom:       tariffNom,
    periode_debut:    tsPeriode[0].slice(0, 10),
    periode_fin:      tsPeriode[tsPeriode.length - 1].slice(0, 10),
    nb_slots:         tsPeriode.length,
    conso_totale_kwh: Math.round(consoTotale * 10) / 10,
    resultats:        resultats,
  });
}

function _solModRapportHtml(d) {
  var rows = d.resultats || [];
  var nbsp = "\u00a0";

  var lignes = rows.map(function (r) {
    var best = r.economie_eur > 0 && r.rsi_ans !== null && r.rsi_ans <= 15;
    var cls  = best ? " sol-mod-row-best" : "";
    return '<tr class="' + cls + '">'
      + '<td>' + r.n + '</td>'
      + '<td class="sol-mod-num">' + _fmtKwh(r.production_kwh) + '</td>'
      + '<td class="sol-mod-num">' + _fmtKwh(r.excedent_kwh)   + '</td>'
      + '<td class="sol-mod-num">' + _fmtKwh(r.conso_nette_kwh) + '</td>'
      + '<td class="sol-mod-num">' + _fmtEur(r.facture_eur)    + '</td>'
      + '<td class="sol-mod-num">' + (r.economie_eur > 0 ? _fmtEur(r.economie_eur) : "—") + '</td>'
      + '<td class="sol-mod-num">' + (r.cout_install  > 0 ? _fmtEur(r.cout_install) : "—") + '</td>'
      + '<td class="sol-mod-num">' + (r.rsi_ans !== null ? r.rsi_ans + nbsp + "ans" : "—") + '</td>'
      + '</tr>';
  }).join("");

  return '<div class="sol-mod-rapport">'
    + '<div class="sol-mod-rapport-header">'
      + '<span class="sol-mod-rapport-src">Source\u00a0: <strong>' + _escHtml(d.source.nom || d.source.id) + '</strong></span>'
      + '<span class="sol-mod-rapport-tarif">Tarif\u00a0: <strong>' + _escHtml(d.tariff_nom) + '</strong></span>'
      + '<span class="sol-mod-rapport-periode">' + d.periode_debut + ' → ' + d.periode_fin
        + ' (' + d.nb_slots + ' créneaux · ' + _fmtKwh(d.conso_totale_kwh) + ' kWh consommés)</span>'
    + '</div>'
    + '<div class="sol-mod-table-wrap">'
    + '<table class="sol-mod-table">'
      + '<thead><tr>'
        + '<th>Panneaux</th>'
        + '<th>Production<br>(kWh/an)</th>'
        + '<th>Excédent<br>(kWh/an)</th>'
        + '<th>Conso nette<br>(kWh/an)</th>'
        + '<th>Facture est.<br>(€/an)</th>'
        + '<th>Économie<br>(€/an)</th>'
        + '<th>Coût install.<br>(€)</th>'
        + '<th>Retour sur<br>invest.</th>'
      + '</tr></thead>'
      + '<tbody>' + lignes + '</tbody>'
    + '</table>'
    + '</div>'
    + '<div class="sol-mod-legende">Les lignes en vert indiquent un retour sur investissement ≤ 15 ans.</div>'
  + '</div>';
}

function _fmtEur(v) {
  if (v == null || isNaN(v)) return "\u2014";
  return Number(v).toLocaleString("fr-FR", { minimumFractionDigits: 0, maximumFractionDigits: 0 }) + "\u00a0€";
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
    html += _htmlSection(t("section_consommation"), conso);
  }
  if (Object.keys(modules).length) {
    html += _htmlSection(t("section_modules"), modules);
  }
  if (Object.keys(tarifs).length) {
    html += _htmlSection(t("section_fournisseurs"), tarifs);
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
  var affNom = _nomModule(nom);
  // Les params sont encodés en JSON dans un attribut data pour le chargement paresseux
  var paramsJson = JSON.stringify(params).replace(/'/g, "&#39;");
  return '<div class="settings-card" id="settings-card-' + nom + '">'
    + '<div class="settings-card-header" data-toggle="' + nom + '" data-params=\'' + paramsJson + '\'>'
    +   '<span class="settings-card-arrow">&#9654;</span>'
    +   '<span class="settings-card-nom">' + affNom + '</span>'
    +   (statut ? '<span class="settings-card-statut ' + cls + '">' + statut + '</span>' : '')
    + '</div>'
    + '<div class="settings-card-body hidden" id="settings-body-' + nom + '">'
    +   '<div id="panel-container-' + nom + '" class="settings-panel-container">'
    +   '</div>'
    +   '<div class="settings-card-footer">'
    +     '<span class="settings-save-msg" id="settings-msg-' + nom + '"></span>'
    +     '<button class="settings-save-btn" data-save="' + nom + '">' + t("btn_save") + '</button>'
    +   '</div>'
    + '</div>'
    + '</div>';
}

function _htmlCardInline(slug, nom) {
  /* Accordéon lecture seule — contenu déjà stocké dans _inlinePanels[slug]. */
  return '<div class="settings-card" id="settings-card-' + slug + '">'
    + '<div class="settings-card-header" data-toggle="' + slug + '">'
    +   '<span class="settings-card-arrow">&#9654;</span>'
    +   '<span class="settings-card-nom">' + nom + '</span>'
    + '</div>'
    + '<div class="settings-card-body hidden" id="settings-body-' + slug + '">'
    +   '<div id="panel-container-' + slug + '" class="settings-panel-container"></div>'
    + '</div>'
    + '</div>';
}

function _htmlCardInlineSaveable(slug, nom, saveGroupe) {
  /* Accordéon avec bouton Sauvegarder — params gérés par getParams_{saveGroupe}. */
  return '<div class="settings-card" id="settings-card-' + slug + '">'
    + '<div class="settings-card-header" data-toggle="' + slug + '">'
    +   '<span class="settings-card-arrow">&#9654;</span>'
    +   '<span class="settings-card-nom">' + nom + '</span>'
    + '</div>'
    + '<div class="settings-card-body hidden" id="settings-body-' + slug + '">'
    +   '<div id="panel-container-' + slug + '" class="settings-panel-container"></div>'
    +   '<div class="settings-card-footer">'
    +     '<span class="settings-save-msg" id="settings-msg-' + slug + '"></span>'
    +     '<button class="settings-save-btn"'
    +       ' data-save-inline="' + slug + '"'
    +       ' data-save-groupe="' + saveGroupe + '">'
    +       t("btn_save")
    +     '</button>'
    +   '</div>'
    + '</div>'
    + '</div>';
}

function _sauvegarderParamsInline(slug, saveGroupe) {
  /* Sauvegarde d'un panel inline saveable via getParams_{saveGroupe}. */
  var getter = window["getParams_" + saveGroupe];
  var params = typeof getter === "function" ? getter() : {};
  var msgEl  = document.getElementById("settings-msg-" + slug);
  if (msgEl) { msgEl.textContent = t("sauvegarde_cours"); msgEl.className = "settings-save-msg"; }
  fetch("/api/save_params", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupe: saveGroupe, params: params }),
  })
    .then(function(r) { return r.json(); })
    .then(function(rep) {
      if (!msgEl) return;
      msgEl.textContent = rep.status ? t("sauvegarde_ok") : (t("erreur_prefix") + (rep.error || t("erreur_inconnue")));
      msgEl.className   = "settings-save-msg " + (rep.status ? "ok" : "err");
      setTimeout(function() {
        if (msgEl) { msgEl.textContent = ""; msgEl.className = "settings-save-msg"; }
      }, 3000);
    })
    .catch(function() {
      if (msgEl) { msgEl.textContent = t("erreur_reseau"); msgEl.className = "settings-save-msg err"; }
    });
}

function _toggleCard(nom) {
  var body   = document.getElementById("settings-body-" + nom);
  var card   = document.getElementById("settings-card-" + nom);
  var header = card && card.querySelector(".settings-card-header");
  if (!body) return;

  var hidden = body.classList.toggle("hidden");
  var arrow  = card && card.querySelector(".settings-card-arrow");
  if (arrow) arrow.textContent = hidden ? "\u25BA" : "\u25BC";

  // Injection du panel uniquement à la première ouverture
  if (!hidden && header && !header.dataset.panelCharge) {
    var conteneur = document.getElementById("panel-container-" + nom);
    if (_inlinePanels[nom]) {
      // Panel inline (offres generique) — injection directe, pas de fetch
      header.dataset.panelCharge = "1";
      if (conteneur) {
        conteneur.innerHTML = _inlinePanels[nom];
        _execScripts(conteneur);  // exécute les <script> (getParams_*, setParams_*)
      }
    } else if (_panelsPrecharge[nom]) {
      // Pré-chargé à l'étape 3 — injection immédiate, sans spinner
      header.dataset.panelCharge = "1";
      _injecterPanelPrecharge(nom);
    } else if (_panelsPrecharge[nom] === null) {
      // Pré-chargement échoué
      header.dataset.panelCharge = "1";
      if (conteneur) conteneur.innerHTML = '<p class="settings-no-panel">' + t("aucun_panel") + '</p>';
    } else {
      // Pas encore pré-chargé — marquer en attente; quand le pré-chargement finit, il injectera
      header.dataset.panelCharge = "attente";
      if (conteneur) conteneur.innerHTML = '<span class="debug-spinner"></span>';
    }
  }
}

function _chargerPanel(nom, paramsActuels) {
  /* Charge le HTML du panel et les paramètres actuels depuis /api/expose en parallèle. */
  Promise.all([
    fetch("/api/panel/" + nom).then(function(r) {
      if (!r.ok) throw new Error("introuvable");
      return r.text();
    }),
    fetch("/api/expose").then(function(r) {
      return r.ok ? r.json() : {};
    }).catch(function() { return {}; })
  ]).then(function(res) {
    var html   = res[0];
    var expose = res[1];
    // Chercher les params dans fournisseurs puis modules_complementaires
    var params = ((expose.fournisseurs || {})[nom] || {}).parametres
              || ((expose.modules_complementaires || {})[nom] || {}).parametres
              || paramsActuels || {};

    var conteneur = document.getElementById("panel-container-" + nom);
    if (!conteneur) return;
    conteneur.innerHTML = html;
    _execScripts(conteneur);
    var setter = window["setParams_" + nom];
    if (typeof setter === "function") {
      setter(params);
    } else {
      _setParamsFallback(conteneur, params);
    }
  }).catch(function() {
    var conteneur = document.getElementById("panel-container-" + nom);
    if (conteneur) {
      conteneur.innerHTML = '<p class="settings-no-panel">' + t("aucun_panel") + '</p>';
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
  if (msgEl) { msgEl.textContent = t("sauvegarde_cours"); msgEl.className = "settings-save-msg"; }

  fetch("/api/save_params", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ groupe: nom, params: params }),
  })
    .then(function (r) { return r.json(); })
    .then(function (rep) {
      if (rep.status && nom === "rapport_config" && params.rapport_config) {
        _rapportConfig = params.rapport_config;
        // Mettre à jour le cache panel pour que la prochaine réouverture charge les bons params
        if (_panelsPrecharge["rapport_config"]) {
          _panelsPrecharge["rapport_config"].params = params;
        }
        _rendreRapports();
      }
      if (!msgEl) return;
      msgEl.textContent = rep.status ? t("sauvegarde_ok") : (t("erreur_prefix") + rep.error);
      msgEl.className   = "settings-save-msg " + (rep.status ? "ok" : "err");
      setTimeout(function () {
        if (msgEl) { msgEl.textContent = ""; msgEl.className = "settings-save-msg"; }
      }, 3000);
    })
    .catch(function () {
      if (msgEl) { msgEl.textContent = t("erreur_reseau"); msgEl.className = "settings-save-msg err"; }
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

// Appelée après un import CSV : recharge records conso + tarifs + détails
function rechargerApresImport() {
  if (!_dataReady) return;
  // Vider l'index tarif pour forcer le rechargement des NDJSON
  _tariffIndex = {};
  _customScriptPending = {};
  // Recharger masques + params + reconstruire le tableau détails (déclenche _chargerTariffNdjson)
  _rechargerApresHC(true);
  // Recharger les records conso depuis le nouveau ndjson
  fetch("/data/api_conso.json")
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(meta) {
      if (meta && _masquesCache) _appliquerApiConso(meta, _masquesCache, null);
    });
}

// ─── Panneau de débogage ──────────────────────────────────────────────────────

function _activerModeDebug() {
  _modeDebug = true;
  var panel      = document.getElementById("debug-panel");
  var toggleBtn  = document.getElementById("debug-toggle");
  if (panel)     panel.classList.remove("hidden");
  if (toggleBtn) { toggleBtn.classList.remove("hidden"); toggleBtn.classList.add("active"); }
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
  { id: "manifest",  label: "Manifest",  url: "/data/manifest.json"         },
  { id: "masques",   label: "Masques",   url: "/data/masques.json"          },
  { id: "params",    label: "Params",    url: "/data/params.json"           },
  { id: "solaire",   label: "Solaire",   url: "/data/solaire_creneaux.json" },
  { id: "api_conso", label: "API Conso", url: "/data/api_conso.json"        },
  { id: "eco2mix",   label: "Eco2mix",   url: "/data/eco2mix.json"          },
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
  _detailsConfig  = null;
  _acGroups       = null;
  _acGroupsCfg    = null;
  _hourKeyCache   = {};
  _tariffIndex         = {};
  _customScriptPending = {};
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
      if (etape.id === "params"  && ok) { verifierHcRequises(res.data); _appliquerParamsGlobaux(res.data); }
      if (etape.id === "solaire" && ok && res.data && res.data.sources && res.data.sources.length) {
        _solaireData = res.data;
      }
      if (etape.id === "eco2mix" && ok) {
        _e2mCreneaux = (res.data || {}).creneaux || {};
        // Rafraîchir les lignes de groupe rendues avant que eco2mix soit disponible
        _rafraichirTableauDetails();
      }
      if (etape.id === "api_conso" && ok) {
        // Marquer "affichage en cours" jusqu'à ce que le DOM soit peuplé
        var acIdx = _etape.idx - 1;
        _etape.resultats[acIdx].affichage = true;
        _etape.resultats[acIdx].resume    = "affichage en cours…";
        _debugEtapeRendreGantt(false);
        // Déclencher l'étape suivante ou activer le bouton
        if (_etape.idx < _ETAPES.length) {
          if (_modeDebug) { if (btn) btn.disabled = false; }
          else _debugEtapeSuivante();
        }
        _appliquerApiConso(res.data, _etape.data["masques"], function(nbAffiches) {
          _etape.resultats[acIdx].affichage = false;
          _etape.resultats[acIdx].resume    = nbAffiches + "\u00a0" + t("releves_affiches") +
                                              (res.data.mock ? " (mock)" : "");
          _debugEtapeRendreGantt(false);
        });
        return; // ne pas re-rendre le gantt une seconde fois ci-dessous
      }

      if (_etape.idx >= _ETAPES.length) {
        _dataReady = true;
        if (_modeDebug && btn) { btn.disabled = true; btn.textContent = "✓ Terminé"; }
      } else {
        if (_modeDebug) { if (btn) btn.disabled = false; }
        else _debugEtapeSuivante();
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
    if (ts) ts.textContent = "Manifest : " + new Date().toLocaleTimeString(_locale());
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
    elInfo.textContent = t("version_installee") + versionActuelle
      + t("version_disponible") + versionLatest;
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
      if (errEl) { errEl.textContent = t("erreur_plage_hc"); errEl.classList.remove("hidden"); }
      return;
    }
    if (errEl) errEl.classList.add("hidden");
    btnOk.disabled = true;
    btnOk.textContent = t("enregistrement_cours");

    fetch("/api/save_params", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ groupe: "generique", params: { plage_hc_globale: plage } }),
    })
      .then(function (r) { return r.json(); })
      .then(function (rep) {
        btnOk.disabled    = false;
        btnOk.textContent = t("btn_enregistrer");
        if (rep.status) {
          _fermer();
          // Le pipeline est relancé par save_params ; recharger masques + params quand dispo
          _rechargerApresHC();
        } else {
          if (errEl) { errEl.textContent = t("erreur_prefix") + (rep.error || "?"); errEl.classList.remove("hidden"); }
        }
      })
      .catch(function () {
        btnOk.disabled    = false;
        btnOk.textContent = t("btn_enregistrer");
        if (errEl) { errEl.textContent = t("erreur_reseau"); errEl.classList.remove("hidden"); }
      });
  }

  btnOk.addEventListener("click", _valider);
  if (btnAnn) btnAnn.addEventListener("click", _fermer);

  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") _valider();
    if (e.key === "Escape") _fermer();
  });
}

function _rechargerMasquesParams() {
  /* Recharge masques.json + params.json sans toucher _tariffIndex.
     Utilisé quand seule la puissance souscrite change (les NDJSON tarif restent valides). */
  Promise.all([
    fetch("/data/masques.json").then(function(r) { return r.ok ? r.json() : null; }),
    fetch("/data/params.json").then(function(r)  { return r.ok ? r.json() : null; }),
  ]).then(function(res) {
    if (res[0]) _appliquerMasques(res[0]);
    if (res[1]) _appliquerParamsGlobaux(res[1]);
  }).catch(function() {});
}

function _rechargerApresHC(rafraichirDetails) {
  // Recharge masques.json puis params.json pour mettre à jour l'affichage
  if (rafraichirDetails) _pendingRefreshDetails = true;
  Promise.all([
    fetch("/data/masques.json").then(function (r) { return r.ok ? r.json() : null; }),
    fetch("/data/params.json").then(function (r) { return r.ok ? r.json() : null; }),
  ]).then(function (res) {
    var masques = res[0];
    var params  = res[1];
    // Vider l'index tarif pour forcer le rechargement complet des NDJSON
    _tariffIndex         = {};
    _customScriptPending = {};
    if (masques) _appliquerMasques(masques);
    if (params)  _appliquerParamsGlobaux(params);
    // Ne pas ré-ouvrir le popup si des HC restent (évite boucle)
    // L'utilisateur peut relancer manuellement si nécessaire
  }).catch(function () {});
}
