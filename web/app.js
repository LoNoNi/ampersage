/**
 * app.js - Logique cliente de l'IHM.
 * Gestion de la navigation, des statuts, des langues et des paramètres.
 */

"use strict";

// ─── État global client ───────────────────────────────────────────────────────

let translations = {};
let currentLang = "FR";
let statusInterval = null;

// ─── Initialisation ───────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  initDisclaimer();
  await chargerTraductions();
  appliquerLangue(currentLang);
  initNavigation();
  initLangButtons();
  initParamsPage();
  initDebugPanel();
  await chargerPageAccueil();
  demarrerRafraichissementStatuts();
});

// ─── Disclaimer ───────────────────────────────────────────────────────────────

function initDisclaimer() {
  var overlay  = document.getElementById("disclaimer-overlay");
  var btnOk    = document.getElementById("disclaimer-accepter");
  var btnNon   = document.getElementById("disclaimer-refuser");
  var btnClose = document.getElementById("disclaimer-close");
  var chk      = document.getElementById("disclaimer-ne-plus-montrer");
  if (!overlay) return;

  // Vérifier si accepté il y a moins de 7 jours
  var ts = parseInt(localStorage.getItem("ampersage_disclaimer_ts") || "0", 10);
  var SEPT_JOURS = 7 * 24 * 3600 * 1000;
  if (ts && (Date.now() - ts) < SEPT_JOURS) {
    overlay.classList.add("hidden");
    return;
  }

  function _refuser() {
    window.location.href = "https://www.google.fr";
  }

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

async function chargerTraductions() {
  try {
    const res = await fetch("/static/languages.json");
    translations = await res.json();
  } catch (e) {
    console.error("Impossible de charger languages.json", e);
    translations = { FR: {}, EN: {} };
  }
}

function t(cle) {
  return (translations[currentLang] || {})[cle] || cle;
}

function appliquerLangue(lang) {
  currentLang = lang;
  document.querySelectorAll("[data-i18n]").forEach(el => {
    const cle = el.dataset.i18n;
    el.textContent = t(cle);
  });
  // Mettre à jour les boutons de langue
  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
}

function initLangButtons() {
  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const lang = btn.dataset.lang;
      appliquerLangue(lang);
      // Persister la langue via l'API
      await fetch("/api/save_params", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ groupe: "global", params: { langue: lang } }),
      });
      // Recharger l'accueil pour appliquer la langue aux données générées côté serveur
      await chargerPageAccueil();
    });
  });
}

// ─── Navigation ───────────────────────────────────────────────────────────────

function initNavigation() {
  document.querySelectorAll(".nav-btn[data-page]").forEach(btn => {
    btn.addEventListener("click", () => {
      const page = btn.dataset.page;
      // Activer le bouton
      document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      // Afficher la page
      document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
      const target = document.getElementById(`page-${page}`);
      if (target) target.classList.add("active");
      // Charger les données de la page
      if (page === "settings") chargerPageParametres();
    });
  });
}

// ─── Page Accueil ─────────────────────────────────────────────────────────────

async function chargerPageAccueil() {
  try {
    const res = await fetch("/api/main");
    const data = await res.json();
    document.getElementById("main-content").innerHTML = data.html || "<p>Erreur de chargement</p>";
    initialiserConsoCompact();
  } catch (e) {
    document.getElementById("main-content").innerHTML = "<p>Impossible de joindre le serveur.</p>";
  }
}

// Alias utilisé depuis les panels injectés (ex : panel CSV)
var chargerContenuPrincipal = chargerPageAccueil;

function initialiserConsoCompact() {
  // Masquer toutes les lignes qui ont un parent (tout sauf les totaux annuels)
  // Les boutons ont déjà ▸ et data-collapsed non défini → le toggle le gère
  document.querySelectorAll("tr[data-parent]").forEach(function (row) {
    row.style.display = "none";
  });
  document.querySelectorAll(".conso-toggle").forEach(function (btn) {
    btn.dataset.collapsed = "1";
  });
}

// ─── Barre de statuts ─────────────────────────────────────────────────────────

async function rafraichirStatuts() {
  try {
    const res = await fetch("/api/status");
    const data = await res.json();
    afficherStatut("val-global", data.global);
    afficherStatut("val-api-conso", data.api_conso);
    const enedisActif = data.api_conso === "actif";
    const scripts = data.scripts || {};
    ["sobry", "trv_base", "trv_hchp", "trv_tempo"].forEach(nom => {
      afficherStatut(`val-${nom}`, scripts[nom] || "—");
      const item = document.getElementById(`status-${nom}`);
      if (item) item.classList.toggle("disabled", !enedisActif);
    });
  } catch (e) {
    console.warn("Statuts non disponibles", e);
  }
}

function afficherStatut(elementId, valeur) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = valeur;
  el.className = "status-value";
  if (valeur === "actif" || valeur === "ok") el.classList.add("ok");
  else if (valeur === "erreur") el.classList.add("erreur");
  else if (valeur === "configuré" || valeur === "initialisation") el.classList.add("initialisation");
}

function demarrerRafraichissementStatuts() {
  rafraichirStatuts();
  statusInterval = setInterval(() => {
    rafraichirStatuts();
    if (!document.getElementById("debug-panel").classList.contains("hidden")) {
      rafraichirDebug();
    }
  }, 5000);
}

// ─── Page Paramètres ──────────────────────────────────────────────────────────

function initParamsPage() {
  // Boutons Sauvegarder
  document.querySelectorAll(".btn-save").forEach(btn => {
    btn.addEventListener("click", () => sauvegarderGroupe(btn.dataset.groupe));
  });

  // Boutons Init
  document.querySelectorAll(".btn-init").forEach(btn => {
    btn.addEventListener("click", () => initialiserScript(btn.dataset.script));
  });

  // Bouton Rechercher historique
  const btnHistory = document.querySelector(".btn-history");
  if (btnHistory) {
    btnHistory.addEventListener("click", rechercherHistorique);
  }

  // Synchronisation select langue avec l'état courant
  const selectLangue = document.getElementById("param-langue");
  if (selectLangue) {
    selectLangue.value = currentLang;
    selectLangue.addEventListener("change", () => {
      appliquerLangue(selectLangue.value);
    });
  }
}

// Scripts disposant d'un panel HTML
const SCRIPTS_AVEC_PANEL = ["api_conso", "sobry", "trv_base", "trv_hchp", "trv_tempo"];

async function chargerPageParametres() {
  // Charger chaque panel puis remplir ses champs
  for (const script of SCRIPTS_AVEC_PANEL) {
    await chargerPanel(script);
    await chargerParamsScript(script);
  }
}

async function chargerPanel(script) {
  const conteneur = document.getElementById(`panel-container-${script}`);
  if (!conteneur) return;
  try {
    const res = await fetch(`/api/panel/${script}`);
    if (res.ok) {
      conteneur.innerHTML = await res.text();
      // Les <script> injectés via innerHTML ne s'exécutent pas automatiquement
      // On les recrée pour forcer leur exécution
      conteneur.querySelectorAll("script").forEach(ancien => {
        const nouveau = document.createElement("script");
        nouveau.textContent = ancien.textContent;
        document.body.appendChild(nouveau);
        document.body.removeChild(nouveau);
      });
    } else {
      conteneur.innerHTML = `<p class="placeholder">Panel non disponible (${script})</p>`;
    }
  } catch (e) {
    conteneur.innerHTML = `<p class="placeholder">Erreur de chargement du panel</p>`;
    console.warn(`Impossible de charger le panel ${script}`, e);
  }
}

async function chargerParamsScript(script) {
  const conteneur = document.getElementById(`panel-container-${script}`);
  if (!conteneur) return;
  try {
    const res = await fetch("/api/script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ script, mode: "GET_PARAM", params: {} }),
    });
    const data = await res.json();
    if (data.status && data.data) {
      // Setter personnalisé défini dans le panel (ex: tableau trv_base)
      const setter = window[`setParams_${script}`];
      if (setter) {
        setter(data.data);
      } else {
        // Setter générique via [data-param]
        conteneur.querySelectorAll("[data-param]").forEach(el => {
          const key = el.dataset.param;
          if (data.data[key] !== undefined && data.data[key] !== null) {
            el.value = data.data[key];
          }
        });
      }
    }
  } catch (e) {
    console.warn(`Impossible de charger les paramètres ${script}`, e);
  }
}

function setVal(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val;
}

function getVal(id) {
  const el = document.getElementById(id);
  return el ? el.value : "";
}

function _collecterParams(groupe) {
  // Groupe global : champs fixes dans le HTML principal
  if (groupe === "global") {
    return {
      port: parseInt(getVal("param-port"), 10) || 8080,
      langue: getVal("param-langue"),
    };
  }
  // Collecteur personnalisé défini dans le panel (ex: tableau trv_base)
  const collecteur = window[`getParams_${groupe}`];
  if (collecteur) return collecteur();
  // Collecteur générique via [data-param]
  const conteneur = document.getElementById(`panel-container-${groupe}`);
  if (!conteneur) return {};
  const params = {};
  conteneur.querySelectorAll("[data-param]:not([readonly])").forEach(el => {
    params[el.dataset.param] = el.value || null;
  });
  return params;
}

async function sauvegarderGroupe(groupe) {
  const params = _collecterParams(groupe);

  try {
    const res = await fetch("/api/save_params", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ groupe, params }),
    });
    const data = await res.json();
    if (!data.status) {
      afficherMessageErreur(`${groupe}-msg`, data.error || "Erreur inconnue");
    } else {
      afficherMessageSucces(`${groupe}-msg`, t("btn_save") + " ✓");
    }
  } catch (e) {
    afficherMessageErreur(`${groupe}-msg`, "Erreur réseau");
  }
}

async function initialiserScript(script) {
  try {
    const res = await fetch("/api/script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ script, mode: "INIT_PARAM", params: {} }),
    });
    const data = await res.json();
    if (!data.status) {
      const msgKey = data.error && data.error.includes("API conso")
        ? "error_api_conso_required"
        : null;
      afficherMessageErreur(`${script}-msg`, msgKey ? t(msgKey) : (data.error || "Erreur"));
    } else {
      afficherMessageErreur(`${script}-msg`, "");
      if (SCRIPTS_AVEC_PANEL.includes(script)) chargerParamsScript(script);
    }
  } catch (e) {
    afficherMessageErreur(`${script}-msg`, "Erreur réseau");
  }
}

async function rechercherHistorique() {
  try {
    const res = await fetch("/api/script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ script: "api_conso", mode: "GET_HISTORY_START", params: {} }),
    });
    const data = await res.json();
    if (data.status && data.data) {
      afficherMessageSucces("api_conso-msg", "Historique disponible depuis : " + data.data.max_history_start);
    } else {
      afficherMessageErreur("api_conso-msg", data.error || "Impossible de récupérer l'historique");
    }
  } catch (e) {
    afficherMessageErreur("api_conso-msg", "Erreur réseau");
  }
}

// ─── Panneau de débogage ──────────────────────────────────────────────────────

function initDebugPanel() {
  document.getElementById("debug-toggle").addEventListener("click", () => {
    const panel = document.getElementById("debug-panel");
    const btn = document.getElementById("debug-toggle");
    const hidden = panel.classList.toggle("hidden");
    btn.classList.toggle("active", !hidden);
    if (!hidden) rafraichirDebug();
  });
  document.getElementById("debug-close").addEventListener("click", () => {
    document.getElementById("debug-panel").classList.add("hidden");
    document.getElementById("debug-toggle").classList.remove("active");
  });
  document.getElementById("debug-refresh").addEventListener("click", rafraichirDebug);
}

async function rafraichirDebug() {
  const [resStatus, resResults] = await Promise.all([
    fetch("/api/status").then(r => r.json()).catch(() => null),
    fetch("/api/results").then(r => r.json()).catch(() => null),
  ]);

  document.getElementById("debug-ts").textContent =
    "Mis à jour : " + new Date().toLocaleTimeString("fr-FR");

  if (resStatus) _debugAfficherStatuts(resStatus);
  if (resResults) {
    _debugAfficherEnedis(resResults.api_conso || null);
    _debugAfficherTarifs(resResults);
  }
}

function _debugAfficherStatuts(statuts) {
  const el = document.getElementById("debug-statuts");
  const lignes = [
    { k: "Global", v: statuts.global },
    { k: "API Conso", v: statuts.api_conso },
    ...Object.entries(statuts.scripts || {}).map(([k, v]) => ({ k, v })),
  ];
  el.innerHTML = '<div class="debug-kv">' +
    lignes.map(({ k, v }) => {
      const cls = v === "actif" || v === "ok" ? "ok" : v === "erreur" ? "err" : "warn";
      return `<div class="debug-kv-row">
        <span class="debug-key">${k}</span>
        <span class="debug-val ${cls}">${v}</span>
      </div>`;
    }).join("") +
    '</div>';
}

function _debugAfficherEnedis(data) {
  const el = document.getElementById("debug-api-conso");
  const badge = document.getElementById("debug-api-conso-count");
  if (!data || !data.records || data.records.length === 0) {
    el.innerHTML = '<span style="color:#6c7086">Aucune donnée</span>';
    badge.textContent = "0";
    return;
  }
  const records = data.records;
  badge.textContent = records.length + " points";

  // Valeur max pour les barres proportionnelles
  const maxKwh = Math.max(...records.map(r => r.kwh));

  const lignes = records.map(r => {
    const dt = new Date(r.ts);
    const dateStr = dt.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
    const heureStr = dt.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
    const pct = maxKwh > 0 ? Math.round((r.kwh / maxKwh) * 100) : 0;
    const typeCls = r.type === "reel" ? "td-type-reel" : "td-type-estimation";
    return `<tr>
      <td>${dateStr}</td>
      <td>${heureStr}</td>
      <td class="td-kwh">
        ${r.kwh.toFixed(3)}
        <span class="kwh-bar-wrap"><span class="kwh-bar" style="width:${pct}%"></span></span>
      </td>
      <td class="${typeCls}">${r.type}</td>
    </tr>`;
  }).join("");

  el.innerHTML = `<div class="debug-table-wrap">
    <table class="debug-table">
      <thead><tr><th>Date</th><th>Heure</th><th>kWh</th><th>Type</th></tr></thead>
      <tbody>${lignes}</tbody>
    </table>
  </div>`;
}

function _debugAfficherTarifs(results) {
  const el = document.getElementById("debug-tarifs");
  const noms = ["sobry", "trv_base", "trv_hchp", "trv_tempo"];
  let html = '<div class="debug-kv">';
  let auMoinsUn = false;

  noms.forEach(nom => {
    const data = results[nom];
    if (!data) return;
    auMoinsUn = true;
    html += `<div class="debug-subsection">${nom}</div>`;
    Object.entries(data).forEach(([k, v]) => {
      const affVal = typeof v === "object" ? JSON.stringify(v) : v;
      html += `<div class="debug-kv-row">
        <span class="debug-key">${k}</span>
        <span class="debug-val">${affVal}</span>
      </div>`;
    });
  });

  if (!auMoinsUn) {
    html += '<span style="color:#6c7086">Aucune donnée tarif</span>';
  }
  html += '</div>';
  el.innerHTML = html;
}

// ─── Tableau de consommation : expansion/contraction ─────────────────────────

function _collapserRecursivement(btn) {
  if (!btn || btn.dataset.collapsed === "1") return;
  var key = btn.dataset.key;
  document.querySelectorAll("tr[data-parent='" + key + "']").forEach(function (row) {
    row.style.display = "none";
    var childBtn = row.querySelector(".conso-toggle");
    if (childBtn) _collapserRecursivement(childBtn);
  });
  btn.dataset.collapsed = "1";
  btn.textContent = "\u25b8";
  btn.title = "Développer";
}

document.addEventListener("click", function (e) {
  var btn = e.target.closest && e.target.closest(".conso-toggle");
  if (!btn) return;
  var key       = btn.dataset.key;
  var collapsed = btn.dataset.collapsed === "1";
  var rows      = document.querySelectorAll("tr[data-parent='" + key + "']");
  if (collapsed) {
    // Développer : afficher les enfants directs uniquement
    rows.forEach(function (row) { row.style.display = ""; });
    btn.dataset.collapsed = "0";
    btn.textContent = "\u25be";   // ▾
    btn.title = "Réduire";
  } else {
    // Contracter : masquer enfants et replier récursivement leurs descendants
    rows.forEach(function (row) {
      row.style.display = "none";
      var childBtn = row.querySelector(".conso-toggle");
      if (childBtn) _collapserRecursivement(childBtn);
    });
    btn.dataset.collapsed = "1";
    btn.textContent = "\u25b8";   // ▸
    btn.title = "Développer";
  }
});

function afficherMessageErreur(elementId, message) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = message;
  el.style.color = message ? "#d32f2f" : "";
}

function afficherMessageSucces(elementId, message) {
  const el = document.getElementById(elementId);
  if (!el) return;
  el.textContent = message;
  el.style.color = "#2e7d32";
  setTimeout(() => { el.textContent = ""; el.style.color = ""; }, 3000);
}
