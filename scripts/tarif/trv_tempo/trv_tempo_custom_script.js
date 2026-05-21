/* Script Tempo transmis via GET_MASKS, exécuté côté client avec :
   - data    : résultat GET_CUSTOM { calendrier, tarifs, plage_hc, heure_changement_jour,
                                     puissance, abonnement_mensuel_ht, abonnement_mensuel_ttc,
                                     abonnements }
   - offreId : identifiant de l'offre (string)
   Globals app.js utilisés : _consoRecords, _tariffIndex, _customMeta, _rendreRapports. */

var cal     = data.calendrier || {};
var tarifs  = data.tarifs     || {};
var plageHC = data.plage_hc              || "22h00\u21926h00";
var hcj     = data.heure_changement_jour || "6h00";

function _ph(s) {
    var p = s.trim().replace("h", ":").split(":");
    return parseInt(p[0], 10) * 60 + (parseInt(p[1], 10) || 0);
}

function _parsePlage(plage) {
    var cr = [];
    plage.split("+").forEach(function(seg) {
        seg = seg.trim();
        if (seg.indexOf("\u2192") < 0) return;
        var pt = seg.split("\u2192");
        cr.push([_ph(pt[0]), _ph(pt[1])]);
    });
    return cr;
}

function _estHC(hMin, cr) {
    for (var i = 0; i < cr.length; i++) {
        var d = cr[i][0], f = cr[i][1];
        if (d <= f ? (d <= hMin && hMin < f) : (hMin >= d || hMin < f)) return true;
    }
    return false;
}

/* Formatter heure de Paris — même approche que _acHourFmt dans app.js */
var _fmtP = new Intl.DateTimeFormat("fr-CA", {
    timeZone: "Europe/Paris",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false
});

function _parisParts(ts) {
    var parts = _fmtP.formatToParts(new Date(ts));
    var m = {};
    parts.forEach(function(p) { m[p.type] = p.value; });
    var h = parseInt(m.hour, 10), mn = parseInt(m.minute, 10);
    return {
        date:    m.year + "-" + m.month + "-" + m.day,
        hourKey: m.year + "-" + m.month + "-" + m.day + "T" + m.hour + ":00",
        hMin:    h * 60 + mn
    };
}

var crHC     = _parsePlage(plageHC);
var changMin = _ph(hcj);
var idx      = {};

(_consoRecords || []).forEach(function(r) {
    var p = _parisParts(r.ts);

    /* Date Tempo : si hMin < heure_changement_jour → appartient au jour J-1 */
    var dateTempo = p.date;
    if (p.hMin < changMin) {
        var d = new Date(p.date + "T12:00:00Z");
        d.setUTCDate(d.getUTCDate() - 1);
        dateTempo = d.toISOString().slice(0, 10);
    }

    var couleur = cal[dateTempo];
    if (!couleur) return; /* Couleur inconnue pour ce jour → créneau ignoré */

    var hc  = _estHC(p.hMin, crHC);
    var col = couleur.toLowerCase() + "_" + (hc ? "hc" : "hp");
    var pk  = (tarifs[col] || 0) / 100; /* c€/kWh → €/kWh */

    /* Un seul enregistrement par clé-heure suffit pour pk (couleur constante sur l'heure) */
    if (!idx[p.hourKey]) {
        idx[p.hourKey] = { pk: pk, h: hc ? "C" : "H", col: col };
    }
});

_tariffIndex[offreId] = idx;
_customMeta[offreId]  = {
    abonnement_mensuel_ht:  data.abonnement_mensuel_ht  || 0,
    abonnement_mensuel_ttc: data.abonnement_mensuel_ttc || 0,
    puissance:              data.puissance              || 0,
    abonnements:            data.abonnements            || []
};
_rendreRapports();
