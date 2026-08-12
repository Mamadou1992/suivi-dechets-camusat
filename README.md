# Suivi des déchets triés — CAMUSAT / SONAGED

Dispositif de suivi en deux parties :

1. **`kobo_suivi_dechets_camusat.xlsx`** — questionnaire XLSForm à importer dans KoboToolbox (saisie terrain sur mobile, hors ligne possible).
2. **Application Streamlit** — tableau de bord connecté à l'API Kobo (KPI, évolution, stock tampon, comparaison multi-sites, export).

---

## 1. Déployer le questionnaire Kobo

1. Se connecter sur https://kf.kobotoolbox.org (ou le serveur OCHA `https://kobo.humanitarianresponse.info`).
2. **Nouveau projet → Importer un fichier XLSForm** → déposer `kobo_suivi_dechets_camusat.xlsx`.
3. **Déployer** le projet.
4. Onglet **PARTAGER** : ajouter les comptes SONAGED et Camusat.
   - agents de collecte → droit *Ajouter des soumissions*
   - référents / QHSE → droit *Voir les soumissions*
5. Saisie terrain : application **KoboCollect** (Android) ou **Enketo** (lien web), fonctionne hors connexion puis synchronise.

### Contenu du formulaire

| Section | Champs |
|---|---|
| 1. Identification | date de collecte, site (le client CAMUSAT est pré-rempli en champ caché ; la semaine du mois est déduite de la date par l'application) |
| 2. Interlocuteurs | responsable SONAGED, fonction, contact client, téléphone |
| 3. Quantités | nombre de bacs et poids (kg) pour plastiques, cartons, autres + nature des « autres » ; totaux calculés automatiquement |
| 4. Destination | site tampon / Ciments du Sahel / décharge, nom du site tampon, n° de bon, levée mensuelle, n° de certificat de traitement |
| 5. Preuves | photo des déchets, photo du bon de pesée, GPS, observations, signature du client |

Contrôles de saisie intégrés : date non future, poids entre 0 et 5000 kg, bacs entre 0 et 100, téléphone au bon format, n° de certificat obligatoire dès qu'une levée mensuelle est déclarée.

**Modifier le formulaire** : éditer le fichier `build_xlsform.py` puis `python build_xlsform.py`, ou modifier directement le XLSForm dans Excel (onglets `survey`, `choices`, `settings`). Penser à incrémenter `version` dans `settings` avant de redéployer.

---

## 2. Lancer l'application Streamlit

```bash
cd app
pip install -r requirements.txt
streamlit run app.py
```

L'app s'ouvre sur http://localhost:8501.

### Mot de passe d'accès

L'application est protégée par mot de passe. Définir `app_password` dans `.streamlit/secrets.toml` (ou la variable d'environnement `APP_PASSWORD`) avant le premier lancement — sans cette valeur, l'app affiche un message de configuration et n'expose aucune donnée. Le bouton **Se déconnecter** du menu latéral referme la session.

### Connexion à Kobo

Récupérer le **token API** : Kobo → menu compte → *Paramètres* → *Sécurité* → **Clé API**.

Deux options :

- **Dans l'interface** : coller le token dans le menu latéral, puis choisir le formulaire dans la liste déroulante.
- **En configuration** (recommandé pour un déploiement partagé) : renommer `.streamlit/secrets.toml.exemple` en `.streamlit/secrets.toml` et compléter :

```toml
base_url = "https://kf.kobotoolbox.org"
token = "votre_token"
asset_uid = "aXXXXXXXXXXXXXXXXX"
```

L'`asset_uid` se lit dans l'URL du projet Kobo : `.../forms/aXXXXXXXXXXXXXXXXX/...`

Variables d'environnement acceptées également : `KOBO_BASE_URL`, `KOBO_TOKEN`, `KOBO_ASSET_UID`.

### Données historiques

Les fiches Excel existantes (format « Fiche de collecte », un onglet par semaine) placées dans `app/donnees/` sont lues automatiquement et fusionnées avec les données Kobo. La fiche de mars 2026 est déjà incluse.

### Contenu du tableau de bord

- **KPI** : tonnage total, tonnage valorisable, taux de valorisation, bacs, nombre de collectes.
- **Vue d'ensemble** : répartition par flux, tonnage par destination, tonnage par collecte.
- **Évolution** : tonnage mensuel par flux, courbe du taux de valorisation, profil hebdomadaire (semaines 1 à 5), tableau de synthèse mensuelle.
- **Stock tampon** : cumul entrées (vers site tampon) – sorties (levées), seuil d'alerte paramétrable, liste des levées et alerte sur les certificats manquants.
- **Comparaison sites** : classement par site, heatmap site × mois, tableau comparatif.
- **Données & export** : table détaillée, export Excel multi-onglets et CSV.

Filtres disponibles : période, site, flux, semaine du mois. Le filtre client n'apparaît que si plusieurs clients sont présents dans les données. Bouton **Actualiser les données** pour vider le cache (données rafraîchies automatiquement toutes les 10 minutes).

---

## 3. Mise en ligne (optionnel)

Pour un accès partagé Camusat / SONAGED sans installation :

- **Streamlit Community Cloud** (gratuit) : voir le guide détaillé `DEPLOIEMENT.md`.
- **Serveur interne SONAGED** : `streamlit run app.py --server.port 8501 --server.address 0.0.0.0` derrière un reverse proxy.

Ne jamais committer `secrets.toml` dans un dépôt public.

Le logo SONAGED est lu depuis `logo_sonaged/logo_sonaged.jpg` (page de connexion, en-tête, menu latéral et favicon). L'app fonctionne sans, avec une icône par défaut.

---

## Organisation proposée

| Point de l'ordre du jour | Traduction opérationnelle |
|---|---|
| Référent collecte et suivi | Champs *Responsable SONAGED* et *Contact chez le client* obligatoires à chaque saisie |
| Suivi hebdomadaire | Une soumission par passage ; la semaine du mois et la semaine ISO sont calculées à partir de la date, sans saisie supplémentaire |
| Partage des quantités | Le tableau de bord remplace le fichier Excel partagé ; export Excel disponible pour diffusion |
| Site tampon | Champ *Destination* + onglet **Stock tampon** avec seuil d'alerte déclenchant la levée mensuelle |
| Traçabilité et certificats | Champs *N° de bon* et *N° de certificat de traitement*, photo du bon de pesée, GPS et signature client |


---

## Notes de version

**v2026081202** — Simplification du formulaire terrain : la question *Client* devient un champ caché (valeur `camusat`) et la question *Semaine du mois* est supprimée, la semaine étant déduite de la date de collecte côté application (semaines 1 à 5 + semaine ISO). Pour réactiver le choix du client, remettre une ligne `select_one client` dans l'onglet `survey` et la liste correspondante dans `choices`.
