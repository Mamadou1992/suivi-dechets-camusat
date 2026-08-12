"""
Suivi des dechets tries - CAMUSAT / SONAGED
============================================
Tableau de bord Streamlit alimente par KoboToolbox.

Fichier unique organise en 5 sections :
  1. Configuration et connexion KoboToolbox
  2. Normalisation des donnees (Kobo + fiches Excel historiques)
  3. Indicateurs et agregations
  4. Controle d'acces
  5. Interface Streamlit

Lancement :  streamlit run app.py
Documentation : README.md et DEPLOIEMENT.md
"""
from __future__ import annotations

import io
import os
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# =========================================================================== #
# 1. Connexion KoboToolbox
# =========================================================================== #

DEFAULT_BASE_URL = "https://kf.kobotoolbox.org"
TIMEOUT = 60


class KoboError(RuntimeError):
    pass


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Token {token}", "Accept": "application/json"}


def get_config() -> dict[str, str]:
    """Lit la configuration depuis st.secrets puis les variables d'environnement."""
    cfg = {"base_url": DEFAULT_BASE_URL, "token": "", "asset_uid": ""}
    try:
        import streamlit as st

        for key in cfg:
            if key in st.secrets:
                cfg[key] = str(st.secrets[key])
    except Exception:
        pass
    for key in cfg:
        env = os.getenv("KOBO_" + key.upper())
        if env:
            cfg[key] = env
    cfg["base_url"] = cfg["base_url"].rstrip("/")
    return cfg


def list_assets(base_url: str, token: str) -> list[dict[str, Any]]:
    url = f"{base_url}/api/v2/assets.json?limit=200"
    r = requests.get(url, headers=_headers(token), timeout=TIMEOUT)
    if r.status_code == 401:
        raise KoboError("Token invalide ou expire (401).")
    r.raise_for_status()
    return [a for a in r.json().get("results", []) if a.get("asset_type") == "survey"]


def fetch_submissions(base_url: str, token: str, asset_uid: str) -> pd.DataFrame:
    """Recupere toutes les soumissions du formulaire (pagination incluse)."""
    rows: list[dict[str, Any]] = []
    url = f"{base_url}/api/v2/assets/{asset_uid}/data.json?limit=1000"
    while url:
        r = requests.get(url, headers=_headers(token), timeout=TIMEOUT)
        if r.status_code == 401:
            raise KoboError("Token invalide ou expire (401).")
        if r.status_code == 404:
            raise KoboError(f"Formulaire introuvable : {asset_uid}")
        r.raise_for_status()
        payload = r.json()
        rows.extend(payload.get("results", []))
        url = payload.get("next")
    return pd.DataFrame(rows)

# =========================================================================== #
# 2 & 3. Normalisation, indicateurs et agregations
# =========================================================================== #

FLUX = ["Plastiques", "Cartons", "Autres"]
VALORISABLES = ["Plastiques", "Cartons"]

# Schema cible commun a toutes les sources
COLONNES = [
    "date_collecte", "mois", "semaine", "semaine_iso", "client", "site",
    "responsable_sonaged", "contact_client", "tel_contact",
    "bacs_plastiques", "poids_plastiques",
    "bacs_cartons", "poids_cartons",
    "bacs_autres", "poids_autres",
    "total_bacs", "total_poids", "poids_valorisable",
    "destination_dechets", "site_tampon_nom", "num_bon",
    "levee_mensuelle", "num_certificat", "observations", "source",
]

LIBELLES_DESTINATION = {
    "site_tampon": "Site tampon",
    "ciments_sahel": "Ciments du Sahel",
    "decharge": "Decharge",
    "autre": "Autre",
}
LIBELLES_CLIENT = {"camusat": "CAMUSAT", "miya": "MIYA", "autre": "Autre"}
LIBELLES_SITE = {"thies": "Camusat Thies", "dakar": "Camusat Dakar", "autre": "Autre site"}


def _strip_group(col: str) -> str:
    """'quantites/g_plastiques/poids_plastiques' -> 'poids_plastiques'."""
    return col.split("/")[-1]


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def normaliser_kobo(df_brut: pd.DataFrame) -> pd.DataFrame:
    """Transforme les soumissions Kobo brutes en table analytique."""
    if df_brut is None or df_brut.empty:
        return pd.DataFrame(columns=COLONNES)

    df = df_brut.copy()
    df.columns = [_strip_group(c) for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]

    out = pd.DataFrame(index=df.index)
    out["date_collecte"] = pd.to_datetime(df.get("date_collecte"), errors="coerce")
    if out["date_collecte"].isna().all() and "_submission_time" in df:
        out["date_collecte"] = pd.to_datetime(df["_submission_time"], errors="coerce")

    client = df.get("client", pd.Series("camusat", index=df.index)).astype(str)
    out["client"] = client.map(LIBELLES_CLIENT).fillna(client).replace(
        {"": "CAMUSAT", "nan": "CAMUSAT"})

    site = df.get("site", pd.Series("", index=df.index)).astype(str)
    site_autre = df.get("site_autre", pd.Series("", index=df.index)).astype(str)
    out["site"] = np.where(site_autre.ne("") & site_autre.ne("nan"),
                           site_autre, site.map(LIBELLES_SITE).fillna(site))
    out["site"] = out["site"].replace({"": "Non precise", "nan": "Non precise"})

    for champ in ["responsable_sonaged", "contact_client", "tel_contact",
                  "site_tampon_nom", "num_bon", "num_certificat", "observations"]:
        out[champ] = df.get(champ, pd.Series("", index=df.index)).astype(str).replace("nan", "")

    for flux in ["plastiques", "cartons", "autres"]:
        out[f"bacs_{flux}"] = _num(df.get(f"bacs_{flux}", pd.Series(0, index=df.index)))
        out[f"poids_{flux}"] = _num(df.get(f"poids_{flux}", pd.Series(0, index=df.index)))

    dest = df.get("destination_dechets", pd.Series("", index=df.index)).astype(str)
    out["destination_dechets"] = dest.map(LIBELLES_DESTINATION).fillna(dest).replace(
        {"": "Non precise", "nan": "Non precise"})
    levee = df.get("levee_mensuelle", pd.Series("non", index=df.index)).astype(str)
    out["levee_mensuelle"] = levee.str.lower().eq("oui")
    out["source"] = "Kobo"
    return _finaliser(out)


def normaliser_fiche_excel(chemin) -> pd.DataFrame:
    """Lit une 'Fiche de collecte' historique (un onglet par semaine)."""
    feuilles = pd.read_excel(chemin, sheet_name=None, header=None)
    lignes = []
    for nom_feuille, ws in feuilles.items():
        info: dict[str, object] = {"semaine": nom_feuille.strip().capitalize()}
        poids = {"Plastiques": 0.0, "Cartons": 0.0, "Autres": 0.0}
        bacs = {"Plastiques": 0, "Cartons": 0, "Autres": 0}
        for _, row in ws.iterrows():
            cells = [c for c in row.tolist()]
            texte = [str(c).strip() if pd.notna(c) else "" for c in cells]
            cle = _sans_accents(texte[0]).lower() if texte else ""
            if cle.startswith("date"):
                info["date_collecte"] = _parse_date(cells[1] if len(cells) > 1 else None)
                info["contact_client"] = _premier_texte(texte[3:])
            elif cle.startswith("lieu"):
                info["site"] = _premier_texte(texte[1:2]) or "CAMUSAT"
            elif cle.startswith("responsable"):
                info["responsable_sonaged"] = _premier_texte(texte[1:2])
            elif cle in ("plastiques", "cartons", "autres"):
                libelle = cle.capitalize()
                nums = [c for c in cells[1:] if isinstance(c, (int, float)) and pd.notna(c)]
                if nums:
                    bacs[libelle] = nums[0]
                    poids[libelle] = nums[1] if len(nums) > 1 else 0.0
        if sum(poids.values()) == 0:
            continue
        ligne = {
            "date_collecte": info.get("date_collecte"),
            "semaine": info.get("semaine", ""),
            "client": "CAMUSAT",
            "site": str(info.get("site", "CAMUSAT")),
            "responsable_sonaged": str(info.get("responsable_sonaged", "")),
            "contact_client": str(info.get("contact_client", "")),
            "tel_contact": "", "site_tampon_nom": "", "num_bon": "",
            "num_certificat": "", "observations": "",
            "destination_dechets": "Non precise", "levee_mensuelle": False,
            "source": "Historique Excel",
        }
        for libelle, champ in zip(FLUX, ["plastiques", "cartons", "autres"]):
            ligne[f"bacs_{champ}"] = bacs[libelle]
            ligne[f"poids_{champ}"] = poids[libelle]
        lignes.append(ligne)
    return _finaliser(pd.DataFrame(lignes))


def derive_semaine(dates: pd.Series) -> pd.Series:
    """Semaine du mois deduite de la date : jours 1-7 -> Semaine 1, 8-14 -> Semaine 2, etc."""
    d = pd.to_datetime(dates, errors="coerce")
    num = ((d.dt.day - 1) // 7 + 1).clip(upper=5)
    return num.apply(lambda n: f"Semaine {int(n)}" if pd.notna(n) else "")


def _sans_accents(txt: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", txt) if unicodedata.category(c) != "Mn")


def _premier_texte(valeurs) -> str:
    for v in valeurs:
        if v and v not in ("nan", "None") and not re.fullmatch(r"[\d\s.]+", v):
            return v
    return ""


def _parse_date(val):
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return pd.NaT
    if isinstance(val, (int, float)):  # serial Excel
        return pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(val))
    return pd.to_datetime(val, errors="coerce", dayfirst=True)


def _finaliser(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=COLONNES)
    df = df.copy()
    df["date_collecte"] = pd.to_datetime(df["date_collecte"], errors="coerce")
    df["total_bacs"] = df[["bacs_plastiques", "bacs_cartons", "bacs_autres"]].sum(axis=1)
    df["total_poids"] = df[["poids_plastiques", "poids_cartons", "poids_autres"]].sum(axis=1)
    df["poids_valorisable"] = df[["poids_plastiques", "poids_cartons"]].sum(axis=1)
    df["mois"] = df["date_collecte"].dt.to_period("M").astype(str)
    semaine_calc = derive_semaine(df["date_collecte"])
    if "semaine" in df:
        vide = df["semaine"].astype(str).str.strip().isin(["", "nan", "None"])
        df["semaine"] = np.where(vide, semaine_calc, df["semaine"])
    else:
        df["semaine"] = semaine_calc
    df["semaine_iso"] = df["date_collecte"].dt.isocalendar().week.astype("Int64")
    for col in COLONNES:
        if col not in df:
            df[col] = ""
    return df[COLONNES].sort_values("date_collecte").reset_index(drop=True)


def format_long(df: pd.DataFrame) -> pd.DataFrame:
    """Passe en format long (une ligne par flux) pour les graphiques."""
    if df.empty:
        return pd.DataFrame(columns=["date_collecte", "mois", "site", "flux", "poids", "bacs"])
    morceaux = []
    for libelle, champ in zip(FLUX, ["plastiques", "cartons", "autres"]):
        bloc = df[["date_collecte", "mois", "semaine", "site", "client"]].copy()
        bloc["flux"] = libelle
        bloc["poids"] = df[f"poids_{champ}"].values
        bloc["bacs"] = df[f"bacs_{champ}"].values
        morceaux.append(bloc)
    return pd.concat(morceaux, ignore_index=True)


def calculer_stock_tampon(df: pd.DataFrame) -> pd.DataFrame:
    """Entrees (vers site tampon) - sorties (levees) par site, cumulees dans le temps."""
    if df.empty:
        return pd.DataFrame(columns=["date_collecte", "site", "entrees", "sorties", "stock"])
    d = df.copy()
    d["entrees"] = np.where(d["destination_dechets"].eq("Site tampon"), d["poids_valorisable"], 0.0)
    d["sorties"] = np.where(d["levee_mensuelle"].astype(bool), d["poids_valorisable"], 0.0)
    if (d["entrees"].sum() + d["sorties"].sum()) == 0:
        return pd.DataFrame(columns=["date_collecte", "site", "entrees", "sorties", "stock"])
    agg = (d.groupby(["site", "date_collecte"], as_index=False)[["entrees", "sorties"]].sum()
             .sort_values(["site", "date_collecte"]))
    agg["mouvement"] = agg["entrees"] - agg["sorties"]
    agg["stock"] = agg.groupby("site")["mouvement"].cumsum()
    return agg.drop(columns="mouvement")


def kpis(df: pd.DataFrame) -> dict[str, float]:
    if df.empty:
        return {"total": 0.0, "valorisable": 0.0, "taux": 0.0, "bacs": 0.0, "collectes": 0}
    total = float(df["total_poids"].sum())
    valo = float(df["poids_valorisable"].sum())
    return {
        "total": total,
        "valorisable": valo,
        "taux": (valo / total * 100) if total else 0.0,
        "bacs": float(df["total_bacs"].sum()),
        "collectes": int(len(df)),
    }


def evolution_mensuelle(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=["mois", "total_poids", "poids_valorisable", "taux"])
    g = df.groupby("mois", as_index=False)[["total_poids", "poids_valorisable", "total_bacs"]].sum()
    g["taux"] = np.where(g["total_poids"] > 0, g["poids_valorisable"] / g["total_poids"] * 100, 0)
    return g.sort_values("mois")

# =========================================================================== #
# 4 & 5. Controle d'acces et interface Streamlit
# =========================================================================== #

RACINE = Path(__file__).parent
DOSSIER_DONNEES = RACINE / "donnees"


def _trouver_logo() -> str | None:
    for chemin in (RACINE / "logo_sonaged" / "logo_sonaged.jpg",
                   RACINE / "logo_sonaged.jpg",
                   RACINE / "assets" / "logo_sonaged.jpg"):
        if chemin.exists():
            return str(chemin)
    return None


LOGO = _trouver_logo()

st.set_page_config(page_title="Suivi dechets CAMUSAT / SONAGED",
                   page_icon=LOGO or "♻️", layout="wide")

VERT = "#1F6F43"
PALETTE = {"Plastiques": "#2E86C1", "Cartons": "#CA6F1E", "Autres": "#7D8A95"}

st.markdown(f"""
<style>
  .block-container {{padding-top: 2rem;}}
  h1, h2, h3 {{color: {VERT};}}
  div[data-testid="stMetricValue"] {{font-size: 1.6rem;}}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# Controle d'acces
# --------------------------------------------------------------------------- #
def _mot_de_passe_attendu() -> str:
    try:
        valeur = str(st.secrets.get("app_password", "")).strip()
    except Exception:
        valeur = ""
    return valeur or os.getenv("APP_PASSWORD", "").strip()


def controle_acces() -> None:
    """Bloque l'acces tant que le mot de passe n'est pas saisi."""
    if st.session_state.get("acces_ok"):
        return

    attendu = _mot_de_passe_attendu()
    gauche, centre, droite = st.columns([1, 2, 1])
    with centre:
        if LOGO:
            st.image(LOGO, width=170)
        st.title("Suivi des dechets tries")
        st.caption("CAMUSAT / SONAGED - acces reserve aux equipes autorisees.")

        if not attendu:
            st.error(
                "Aucun mot de passe n'est configure. Definir `app_password` dans "
                "`.streamlit/secrets.toml` (poste local) ou dans *Manage app > "
                "Settings > Secrets* (Streamlit Cloud), puis recharger la page."
            )
            st.stop()

        with st.form("connexion"):
            saisie = st.text_input("Mot de passe", type="password",
                                   placeholder="Saisir le mot de passe")
            valider = st.form_submit_button("Se connecter", width="stretch")
        if valider:
            if saisie == attendu:
                st.session_state["acces_ok"] = True
                st.rerun()
            st.error("Mot de passe incorrect.")
    st.stop()


controle_acces()


# --------------------------------------------------------------------------- #
# Chargement des donnees
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=600, show_spinner="Recuperation des donnees Kobo...")
def charger_kobo(base_url: str, token: str, uid: str) -> pd.DataFrame:
    return normaliser_kobo(fetch_submissions(base_url, token, uid))


@st.cache_data(ttl=600)
def charger_assets(base_url: str, token: str) -> list[dict]:
    return list_assets(base_url, token)


@st.cache_data(ttl=600)
def charger_fichiers_locaux() -> pd.DataFrame:
    """Reprend les fiches Excel historiques placees dans ./donnees."""
    if not DOSSIER_DONNEES.exists():
        return pd.DataFrame(columns=COLONNES)
    blocs = []
    for f in sorted(DOSSIER_DONNEES.glob("*.xlsx")):
        try:
            blocs.append(normaliser_fiche_excel(f))
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Fichier ignore ({f.name}) : {exc}")
    return pd.concat(blocs, ignore_index=True) if blocs else pd.DataFrame(columns=COLONNES)


cfg = get_config()

with st.sidebar:
    if LOGO:
        st.image(LOGO, width=130)
    st.header("Connexion KoboToolbox")
    base_url = st.text_input("Serveur Kobo", value=cfg["base_url"])
    token = st.text_input("Token API", value=cfg["token"], type="password",
                          help="Compte Kobo > Parametres > Securite > Cle API")
    uid = cfg["asset_uid"]
    if token:
        try:
            assets = charger_assets(base_url, token)
            options = {a["name"]: a["uid"] for a in assets}
            if options:
                defaut = next((n for n, u in options.items() if u == uid), list(options)[0])
                choix = st.selectbox("Formulaire", list(options),
                                     index=list(options).index(defaut))
                uid = options[choix]
        except Exception as exc:  # noqa: BLE001
            st.error(f"Connexion impossible : {exc}")
    uid = st.text_input("UID du formulaire", value=uid)

    inclure_historique = st.checkbox("Inclure les fiches Excel historiques", value=True)
    if st.button("Actualiser les donnees", width="stretch"):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    if st.button("Se deconnecter", width="stretch"):
        st.session_state.clear()
        st.rerun()

blocs = []
erreur_kobo = None
if token and uid:
    try:
        blocs.append(charger_kobo(base_url, token, uid))
    except (KoboError, Exception) as exc:  # noqa: BLE001
        erreur_kobo = str(exc)
if inclure_historique:
    blocs.append(charger_fichiers_locaux())

blocs_valides = [b for b in blocs if b is not None and not b.empty]
if blocs_valides:
    df = (pd.concat(blocs_valides, ignore_index=True)
          .sort_values("date_collecte").reset_index(drop=True))
else:
    df = pd.DataFrame(columns=COLONNES)

entete = st.columns([1, 9])
if LOGO:
    entete[0].image(LOGO, width=80)
entete[1].title("Suivi des dechets tries - CAMUSAT / SONAGED")

if erreur_kobo:
    st.warning(f"Kobo : {erreur_kobo}")
if df.empty:
    if token and uid and not erreur_kobo:
        st.success("Connexion a KoboToolbox etablie.")
        st.info(
            "Le formulaire ne contient encore aucune soumission. Les indicateurs "
            "apparaitront des la premiere collecte enregistree dans KoboCollect.\n\n"
            "Verifier au passage que le formulaire selectionne est bien le bon "
            "(menu de gauche) et qu'il est **deploye** dans Kobo."
        )
    else:
        st.info(
            "Aucune donnee disponible.\n\n"
            "- Renseigner le token et l'UID du formulaire (menu de gauche ou secrets).\n"
            "- Ou deposer les fiches de collecte Excel dans le dossier `donnees/`."
        )
    st.stop()

# --------------------------------------------------------------------------- #
# Filtres
# --------------------------------------------------------------------------- #
dmin = pd.to_datetime(df["date_collecte"]).min().date()
dmax = pd.to_datetime(df["date_collecte"]).max().date()
tous_clients = sorted(df["client"].dropna().unique())
tous_sites = sorted(df["site"].dropna().unique())
toutes_semaines = sorted(df["semaine"].dropna().unique())

colonnes = st.columns([2, 2, 2, 2] if len(tous_clients) > 1 else [2, 2, 2])
periode = colonnes[0].date_input("Periode", value=(dmin, dmax),
                                 min_value=dmin, max_value=dmax)
if len(tous_clients) > 1:
    clients = colonnes[1].multiselect("Client", tous_clients, default=tous_clients)
    reste = colonnes[2:]
else:
    clients = tous_clients
    reste = colonnes[1:]
sites = reste[0].multiselect("Site", tous_sites, default=tous_sites)
flux_sel = reste[1].multiselect("Flux", FLUX, default=FLUX)

semaines = st.multiselect("Semaine du mois (deduite de la date de collecte)",
                          toutes_semaines, default=toutes_semaines)

d1, d2 = (periode if isinstance(periode, tuple) and len(periode) == 2 else (dmin, dmax))
masque = (df["date_collecte"].dt.date.between(d1, d2)
          & df["client"].isin(clients) & df["site"].isin(sites)
          & df["semaine"].isin(semaines))
dfa = df[masque].copy()
if dfa.empty:
    st.warning("Aucune collecte sur ce perimetre.")
    st.stop()

long = format_long(dfa)
long = long[long["flux"].isin(flux_sel)]

# --------------------------------------------------------------------------- #
# KPI
# --------------------------------------------------------------------------- #
k = kpis(dfa)
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Tonnage total", f"{k['total']/1000:,.2f} t".replace(",", " "))
m2.metric("Valorisable (plastiques + cartons)", f"{k['valorisable']/1000:,.2f} t".replace(",", " "))
m3.metric("Taux de valorisation", f"{k['taux']:.1f} %")
m4.metric("Bacs collectes", f"{k['bacs']:,.0f}".replace(",", " "))
m5.metric("Collectes enregistrees", k["collectes"])

onglets = st.tabs(["Vue d'ensemble", "Evolution", "Stock tampon",
                   "Comparaison sites", "Donnees & export"])

# --------------------------------------------------------------------------- #
with onglets[0]:
    g1, g2 = st.columns(2)
    par_flux = long.groupby("flux", as_index=False)["poids"].sum()
    fig = px.pie(par_flux, names="flux", values="poids", hole=0.5,
                 color="flux", color_discrete_map=PALETTE,
                 title="Repartition du tonnage par flux")
    fig.update_traces(texttemplate="%{label}<br>%{value:.0f} kg (%{percent})")
    g1.plotly_chart(fig, width="stretch")

    par_dest = dfa.groupby("destination_dechets", as_index=False)["total_poids"].sum()
    fig2 = px.bar(par_dest, x="destination_dechets", y="total_poids",
                  title="Tonnage par destination", labels={"destination_dechets": "",
                                                           "total_poids": "kg"})
    fig2.update_traces(marker_color=VERT)
    g2.plotly_chart(fig2, width="stretch")

    hebdo = long.groupby(["date_collecte", "flux"], as_index=False)["poids"].sum()
    fig3 = px.bar(hebdo, x="date_collecte", y="poids", color="flux",
                  color_discrete_map=PALETTE, barmode="stack",
                  title="Tonnage par collecte", labels={"date_collecte": "", "poids": "kg"})
    st.plotly_chart(fig3, width="stretch")

# --------------------------------------------------------------------------- #
with onglets[1]:
    evo = evolution_mensuelle(dfa)
    e1, e2 = st.columns([3, 2])
    mensuel = long.groupby(["mois", "flux"], as_index=False)["poids"].sum()
    fige = px.bar(mensuel, x="mois", y="poids", color="flux", barmode="group",
                  color_discrete_map=PALETTE, title="Tonnage mensuel par flux",
                  labels={"mois": "", "poids": "kg"})
    e1.plotly_chart(fige, width="stretch")

    figt = px.line(evo, x="mois", y="taux", markers=True,
                   title="Taux de valorisation (%)", labels={"mois": "", "taux": "%"})
    figt.update_traces(line_color=VERT)
    figt.update_yaxes(range=[0, 100])
    e2.plotly_chart(figt, width="stretch")

    st.subheader("Profil hebdomadaire")
    hebdo = (long.groupby(["semaine", "flux"], as_index=False)["poids"].sum()
             .sort_values("semaine"))
    figs = px.bar(hebdo, x="semaine", y="poids", color="flux", barmode="stack",
                  color_discrete_map=PALETTE,
                  title="Tonnage cumule par semaine du mois",
                  labels={"semaine": "", "poids": "kg"})
    st.plotly_chart(figs, width="stretch")

    st.subheader("Detail mensuel")
    tab = evo.rename(columns={"mois": "Mois", "total_poids": "Total (kg)",
                              "poids_valorisable": "Valorisable (kg)",
                              "total_bacs": "Bacs", "taux": "Taux (%)"})
    tab = tab.round({"Total (kg)": 2, "Valorisable (kg)": 2, "Taux (%)": 1})
    st.dataframe(tab, width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
with onglets[2]:
    st.caption("Le stock correspond au cumul des collectes dirigees vers le site tampon, "
               "diminue des quantites evacuees lors des levees mensuelles.")
    seuil = st.number_input("Seuil d'alerte de remplissage (kg)", min_value=100,
                            max_value=50000, value=1500, step=100)
    stock = calculer_stock_tampon(dfa)
    if stock.empty:
        st.info("Aucun mouvement vers un site tampon sur la periode.")
    else:
        figs = px.line(stock, x="date_collecte", y="stock", color="site", markers=True,
                       title="Stock cumule au site tampon (kg)",
                       labels={"date_collecte": "", "stock": "kg"})
        figs.add_hline(y=seuil, line_dash="dash", line_color="#C0392B",
                       annotation_text="Seuil d'alerte")
        st.plotly_chart(figs, width="stretch")

        dernier = stock.groupby("site").tail(1)
        cols = st.columns(max(len(dernier), 1))
        for col, (_, row) in zip(cols, dernier.iterrows()):
            depasse = row["stock"] >= seuil
            col.metric(f"{row['site']} - stock actuel", f"{row['stock']:,.0f} kg".replace(",", " "),
                       delta="Levee a programmer" if depasse else "Sous le seuil",
                       delta_color="inverse" if depasse else "normal")
        if (dernier["stock"] >= seuil).any():
            st.error("Seuil atteint sur au moins un site : programmer une levee vers "
                     "Ciments du Sahel.")

    st.subheader("Levees et certificats")
    levees = dfa[dfa["levee_mensuelle"].astype(bool)]
    if levees.empty:
        st.info("Aucune levee mensuelle enregistree sur la periode.")
    else:
        vue = levees[["date_collecte", "site", "poids_valorisable", "destination_dechets",
                      "num_bon", "num_certificat"]].rename(columns={
                          "date_collecte": "Date", "site": "Site",
                          "poids_valorisable": "Quantite (kg)",
                          "destination_dechets": "Destination",
                          "num_bon": "N. bon", "num_certificat": "N. certificat"})
        manquants = int((vue["N. certificat"].astype(str).str.strip() == "").sum())
        if manquants:
            st.warning(f"{manquants} levee(s) sans numero de certificat de traitement.")
        st.dataframe(vue, width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
with onglets[3]:
    par_site = dfa.groupby("site", as_index=False).agg(
        total=("total_poids", "sum"), valorisable=("poids_valorisable", "sum"),
        bacs=("total_bacs", "sum"), collectes=("total_poids", "size"))
    par_site["taux"] = (par_site["valorisable"] / par_site["total"] * 100).round(1)
    par_site["moyenne_par_collecte"] = (par_site["total"] / par_site["collectes"]).round(1)

    figc = px.bar(par_site.sort_values("total", ascending=False), x="site", y="total",
                  title="Tonnage total par site", labels={"site": "", "total": "kg"},
                  text_auto=".0f")
    figc.update_traces(marker_color=VERT)
    st.plotly_chart(figc, width="stretch")

    figh = px.density_heatmap(long, x="mois", y="site", z="poids", histfunc="sum",
                              color_continuous_scale="Greens",
                              title="Tonnage par site et par mois (kg)",
                              labels={"mois": "", "site": ""})
    st.plotly_chart(figh, width="stretch")

    st.dataframe(par_site.rename(columns={
        "site": "Site", "total": "Total (kg)", "valorisable": "Valorisable (kg)",
        "bacs": "Bacs", "collectes": "Collectes", "taux": "Taux (%)",
        "moyenne_par_collecte": "Moyenne / collecte (kg)"}),
        width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
with onglets[4]:
    st.subheader("Donnees detaillees")
    st.dataframe(dfa, width="stretch", hide_index=True)

    def construire_export(donnees: pd.DataFrame) -> bytes:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            donnees.to_excel(writer, sheet_name="Collectes", index=False)
            evolution_mensuelle(donnees).to_excel(writer, sheet_name="Synthese mensuelle",
                                                     index=False)
            (format_long(donnees).groupby(["mois", "flux"], as_index=False)["poids"].sum()
             .to_excel(writer, sheet_name="Par flux", index=False))
            st_tampon = calculer_stock_tampon(donnees)
            if not st_tampon.empty:
                st_tampon.to_excel(writer, sheet_name="Stock tampon", index=False)
        return buffer.getvalue()

    st.download_button("Telecharger le rapport Excel", data=construire_export(dfa),
                       file_name=f"suivi_dechets_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.download_button("Telecharger les donnees (CSV)",
                       data=dfa.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"suivi_dechets_{date.today():%Y%m%d}.csv", mime="text/csv")
