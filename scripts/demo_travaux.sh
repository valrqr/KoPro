#!/usr/bin/env bash
# Démonstration du module travaux sur la copro fictive « Les Mimosas » :
#   1. crée un dossier « porte du garage »
#   2. dépose les 3 devis fictifs de exemples/devis/
#   3. vérifie les entreprises (registre des entreprises, BODACC, assurance décennale)
#   4. demande à Mistral un résumé des différences (brouillon)
#   5. propose de le valider, puis télécharge le comparatif PDF
#
# Prérequis : le moteur tourne (docker compose up -d), MOTEUR_TOKEN et MISTRAL_API_KEY sont dans .env
# Lancement, depuis la racine du projet :   bash scripts/demo_travaux.sh

set -euo pipefail
cd "$(dirname "$0")/.."

API="${API:-http://localhost:8000}"
TOKEN="$(grep '^MOTEUR_TOKEN=' .env | cut -d= -f2-)"
AU="2027-06-01"     # date de référence de la démo (le devis de Menuiserie du Port sera expiré)
[ -n "$TOKEN" ] || { echo "❌ MOTEUR_TOKEN absent de .env"; exit 1; }

appel() { curl -sS -H "X-API-Key: $TOKEN" "$@"; }
champ() { python3 -c "import sys, json; print(json.load(sys.stdin)$1)"; }

echo "▶ Le moteur répond-il ?"
appel "$API/sante" >/dev/null || { echo "❌ Moteur injoignable sur $API : lance 'docker compose up -d'"; exit 1; }

echo "▶ Création du dossier de travaux"
ID=$(appel -X POST "$API/copros/mimosas/travaux" -H "Content-Type: application/json" \
  -d '{"titre": "Remplacement de la porte du garage", "nature": "reparation",
       "description": "La porte basculante du parking ne se ferme plus correctement. Remplacement par une porte sectionnelle motorisée."}' \
  | champ '["id"]')
echo "  dossier : $ID"

depot() {  # entreprise, HT, délai, validité, fichier
  appel -X POST "$API/copros/mimosas/travaux/$ID/devis" \
    -F "entreprise=$1" -F "montant_ht=$2" -F "taux_tva=10" -F "delai_jours=$3" \
    -F "valide_jusqu_au=$4" -F "fichier=@exemples/devis/$5;type=application/pdf" | champ '["id"]'
}
echo "▶ Dépôt des devis"
echo "  $(depot 'Portes Azur' 3200 15 2027-07-15 devis_portes_azur.pdf) : Portes Azur"
echo "  $(depot 'Fermetures Niçoises' 2780 21 2027-06-30 devis_fermetures_nicoises.pdf) : Fermetures Niçoises"
echo "  $(depot 'Menuiserie du Port' 4150 10 2027-05-20 devis_menuiserie_du_port.pdf) : Menuiserie du Port"

echo "▶ Vérification des entreprises (registre des entreprises + BODACC)"
appel -X POST "$API/copros/mimosas/travaux/$ID/verifications" -H "Content-Type: application/json" -d '{}' \
  | python3 -c "
import sys, json
for f in json.load(sys.stdin):
    etat = f['verifications']
    print(f\"  {f['devis']} {f['entreprise_devis']} : registre {etat['registre']}, BODACC {etat['bodacc']}\")
    for a in f['alertes']: print('     -', a['niveau'], ':', a['message'])
"
echo "  (les SIRET des devis d'exemple sont volontairement fictifs : c'est normal qu'ils soient signalés)"

echo "▶ Résumé par Mistral (quelques secondes)…"
REPONSE=$(appel -X POST "$API/copros/mimosas/travaux/$ID/resume?au=$AU" -w '\n%{http_code}')
CODE=$(echo "$REPONSE" | tail -n1); CORPS=$(echo "$REPONSE" | sed '$d')
if [ "$CODE" != "201" ]; then
  echo "❌ Échec ($CODE) : $(echo "$CORPS" | champ '["detail"]' 2>/dev/null || echo "$CORPS")"
  exit 1
fi
echo "$CORPS" | python3 -c "
import sys, json
r = json.load(sys.stdin)
print('\n  RÉSUMÉ (brouillon, modèle ' + r['modele'] + ')\n  ' + r['resume'] + '\n')
for p in r['points']: print('  •', p['critere'], ':', p['constat'])
for m in r['manquants']: print('  ? ', m)
print('\n  Contrôles :', ' / '.join(r['controles']) if r['controles'] else 'aucun problème détecté ✅')
for i in r['devis_illisibles']: print('  ⚠ devis illisible :', i)
"

read -r -p $'\n▶ Valider ce résumé tel quel ? (o/n) ' OK
if [ "$OK" = "o" ]; then
  REPONSE=$(appel -X PUT "$API/copros/mimosas/travaux/$ID/resume" -H "Content-Type: application/json" -d '{}' -w '\n%{http_code}')
  CODE=$(echo "$REPONSE" | tail -n1); CORPS=$(echo "$REPONSE" | sed '$d')
  if [ "$CODE" = "200" ]; then
    echo "  ✅ Résumé validé : il figurera dans le comparatif PDF"
  else
    echo "  ⛔ Validation refusée ($CODE) : $(echo "$CORPS" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("detail"))' 2>/dev/null || echo "$CORPS")"
    echo "     Corrige le texte puis valide-le avec :"
    echo "     curl -X PUT \"$API/copros/mimosas/travaux/$ID/resume\" -H \"X-API-Key: \$TOKEN\" \\"
    echo "          -H \"Content-Type: application/json\" -d '{\"texte\": \"ton résumé corrigé\"}'"
  fi
fi

mkdir -p out
appel "$API/copros/mimosas/travaux/$ID/comparatif.pdf?au=$AU" -o "out/comparatif_$ID.pdf"
echo "▶ Comparatif PDF : out/comparatif_$ID.pdf"
open "out/comparatif_$ID.pdf" 2>/dev/null || true
