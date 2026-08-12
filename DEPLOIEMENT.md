# Déploiement — GitHub + Streamlit Community Cloud

Mise en ligne du tableau de bord à une adresse partageable, sans installation pour les équipes.
**Tout se fait dans le navigateur** : aucune commande, aucun logiciel à installer.

Durée : environ 15 minutes.

---

## Avant de commencer

| Prérequis | Où l'obtenir |
|---|---|
| Compte GitHub | https://github.com/signup |
| Compte Streamlit Cloud | https://share.streamlit.io — se connecter avec le compte GitHub |
| Token API Kobo | Kobo → menu compte → Paramètres → Sécurité → **Clé API** |
| UID du formulaire | Dans l'URL du projet Kobo : `.../forms/aXXXXXXXXXXXX/...` |

Le dossier **`A_ENVOYER_SUR_GITHUB`** contient exactement les fichiers à publier — ni token, ni données client. C'est le seul dossier à ouvrir pendant l'étape 2.

> **Mot de passe obligatoire** : l'application refuse d'afficher les données tant que le secret `app_password` n'est pas défini. Le dépôt peut donc être public sans exposer quoi que ce soit.

---

## Étape 1 — Créer le dépôt

1. Aller sur https://github.com/new
2. **Repository name** : `suivi-dechets-camusat`
3. **Description** (facultatif) : `Suivi des déchets triés Camusat / SONAGED`
4. Visibilité : **Public**
5. Ne cocher **aucune** case (ni README, ni .gitignore, ni licence).
6. **Create repository**.

La page suivante affiche « Quick setup ». Ne pas tenir compte des commandes proposées.

---

## Étape 2 — Envoyer les fichiers

1. Sur cette même page, cliquer le lien **uploading an existing file**.
   (Si la page a été quittée : onglet **Code** → bouton **Add file** → **Upload files**.)
2. Ouvrir l'Explorateur Windows sur le dossier :
   `Documents\DOP\suivi_camusat-sonaged\suivi_dechets\A_ENVOYER_SUR_GITHUB`
3. Sélectionner **tout le contenu** du dossier — `Ctrl + A` — et le glisser dans la zone de dépôt de GitHub.
   Le dossier `logo_sonaged` part avec le reste, GitHub conserve l'arborescence.
4. En bas de page, dans **Commit changes**, saisir : `Version initiale du tableau de bord`
5. Cliquer **Commit changes**.

**Vérification** — la page du dépôt doit afficher 8 fichiers et 1 dossier :

```
logo_sonaged/          app.py              build_xlsform.py
data_utils.py          kobo_client.py      requirements.txt
README.md              DEPLOIEMENT.md      kobo_suivi_dechets_camusat.xlsx
```

Si un fichier `secrets.toml` ou une fiche de collecte `.xlsx` apparaît : le supprimer immédiatement (ouvrir le fichier → icône corbeille → **Commit changes**), et régénérer le token Kobo.

### Facultatif — le thème vert SONAGED

Le fichier de thème vit dans un dossier masqué par Windows, il se crée donc directement sur GitHub :

1. **Add file** → **Create new file**
2. Dans le champ du nom, taper : `.streamlit/config.toml` — la barre oblique crée le dossier automatiquement.
3. Coller :

```toml
[theme]
primaryColor = "#1F6F43"
backgroundColor = "#FFFFFF"
secondaryBackgroundColor = "#F2F6F3"
textColor = "#1B2B22"
font = "sans serif"

[browser]
gatherUsageStats = false
```

4. **Commit changes**. Sans ce fichier, l'application fonctionne à l'identique avec le thème par défaut.

---

## Étape 3 — Déployer sur Streamlit Cloud

1. https://share.streamlit.io → **Sign in with GitHub** → **Authorize**.
2. **Create app** → **Deploy a public app from GitHub**.
3. Renseigner :
   - **Repository** : `<ton-compte>/suivi-dechets-camusat`
   - **Branch** : `main`
   - **Main file path** : `app.py`
   - **App URL** : `suivi-dechets-camusat` → adresse finale `https://suivi-dechets-camusat.streamlit.app`
4. **Advanced settings** → **Python version** : 3.12 → champ **Secrets**, coller :

```toml
base_url = "https://kf.kobotoolbox.org"
token = "votre_token_api_kobo"
asset_uid = "ajg8Ltu8fRuvypkCDE96hL"
app_password = "mot_de_passe_a_partager_aux_equipes"
```

5. **Save**, puis **Deploy**. La construction prend 2 à 5 minutes.

Le mot de passe se change à tout moment : **Manage app → Settings → Secrets**, modifier la valeur, puis **Reboot app**.

---

## Étape 4 — Vérifier et partager

- Ouvrir l'URL, saisir le mot de passe : les KPI doivent se remplir.
- « Aucune donnée » → vérifier `token` et `asset_uid` dans les secrets.
- Les logs de démarrage sont sous **Manage app**, en bas à droite de l'application.

Message type pour les équipes :

> Le suivi des déchets triés Camusat est consultable en ligne : https://suivi-dechets-camusat.streamlit.app
> Mot de passe : `<mot de passe>`
> La saisie terrain se fait dans KoboCollect ; les chiffres se mettent à jour automatiquement.

---

## Mettre à jour l'application

Toujours dans le navigateur :

- **Modifier un fichier** : l'ouvrir sur GitHub → icône crayon → éditer → **Commit changes**.
- **Remplacer un fichier** : **Add file → Upload files**, déposer la nouvelle version portant le même nom.

Streamlit Cloud redéploie automatiquement en une minute environ.

---

## Problèmes fréquents

| Symptôme | Cause probable | Solution |
|---|---|---|
| « Aucun mot de passe n'est configure » | Secret `app_password` absent | L'ajouter dans *Settings → Secrets*, puis **Reboot app** |
| « Token invalide ou expiré (401) » | Token erroné ou régénéré | Regénérer la clé API Kobo et corriger le secret `token` |
| « Formulaire introuvable » | Mauvais `asset_uid` | Recopier l'UID depuis l'URL du projet Kobo |
| `ModuleNotFoundError` au démarrage | `requirements.txt` absent du dépôt | Le réenvoyer, puis **Reboot app** |
| Données non actualisées | Cache de 10 minutes | Bouton **Actualiser les données** du menu latéral |
| « This app has gone to sleep » | Inactivité de 7 jours | Cliquer le bouton de réveil, une minute de redémarrage |
| Le logo ne s'affiche pas | Dossier `logo_sonaged` non envoyé | Le déposer via **Add file → Upload files** |

---

## Annexe — variante en ligne de commande

Pour information, si Git est installé un jour sur le poste :

```powershell
cd "$env:USERPROFILE\OneDrive - sonaged\Documents\DOP\suivi_camusat-sonaged\suivi_dechets"
git init -b main
git add .
git commit -m "Suivi des dechets tries CAMUSAT / SONAGED"
git remote add origin https://github.com/<ton-compte>/suivi-dechets-camusat.git
git push -u origin main
```

Le fichier `.gitignore` du dossier exclut alors automatiquement `secrets.toml` et les fiches de collecte.

---

## Alternative sans GitHub

Pour un usage strictement interne, l'application tourne sur un poste ou un serveur SONAGED :

```powershell
cd "$env:USERPROFILE\OneDrive - sonaged\Documents\DOP\suivi_camusat-sonaged\suivi_dechets"
pip install -r requirements.txt
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

Les autres postes du réseau y accèdent via `http://<ip-du-poste>:8501`.
