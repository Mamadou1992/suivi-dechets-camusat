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

import base64
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
    "levee_mensuelle", "num_certificat", "observations",
    "nature_autres", "fonction_responsable",
    "latitude", "longitude", "photo_dechets", "photo_bon", "signature_client",
    "date_saisie", "id_soumission", "source",
]

# Champs du questionnaire suivis en completude documentaire
CHAMPS_QUALITE = {
    "num_bon": "Bon de pesee",
    "photo_dechets": "Photo des dechets",
    "photo_bon": "Photo du bon",
    "signature_client": "Signature client",
    "latitude": "Position GPS",
    "observations": "Observations",
}

LIBELLES_DESTINATION = {
    "site_tampon": "Site tampon",
    "ciments_sahel": "Ciments du Sahel",
    "decharge": "Decharge",
    "autre": "Autre",
}
LIBELLES_CLIENT = {"camusat": "CAMUSAT", "miya": "MIYA", "autre": "Autre"}
LIBELLES_SITE = {"thies": "Camusat Thies", "dakar": "Camusat Dakar", "autre": "Autre site"}


def _index_attachments(valeur: Any) -> dict[str, str]:
    """Associe le nom de fichier d'une piece jointe Kobo a son URL de telechargement."""
    if not isinstance(valeur, list):
        return {}
    index = {}
    for piece in valeur:
        if not isinstance(piece, dict):
            continue
        url = piece.get("download_url") or piece.get("download_medium_url") or ""
        for cle in ("filename", "media_file_basename", "question_xpath"):
            nom = str(piece.get(cle, "")).split("/")[-1]
            if nom:
                index[nom] = url
                index[nom.replace(" ", "_")] = url
    return index


def _url_media(index: dict[str, str], nom_fichier: str) -> str:
    if not nom_fichier:
        return ""
    return index.get(nom_fichier, index.get(nom_fichier.replace(" ", "_"), ""))


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
                  "site_tampon_nom", "num_bon", "num_certificat", "observations",
                  "nature_autres", "fonction_responsable"]:
        out[champ] = df.get(champ, pd.Series("", index=df.index)).astype(str).replace("nan", "")

    # Position GPS : "latitude longitude altitude precision"
    gps = df.get("gps", pd.Series("", index=df.index)).astype(str)
    coords = gps.str.strip().str.split(r"\s+", expand=True) if len(gps) else pd.DataFrame()
    out["latitude"] = (pd.to_numeric(coords[0], errors="coerce")
                       if coords.shape[1] > 0 else np.nan)
    out["longitude"] = (pd.to_numeric(coords[1], errors="coerce")
                        if coords.shape[1] > 1 else np.nan)

    # Pieces jointes : on associe chaque nom de fichier a son URL de telechargement
    liens = df["_attachments"].apply(_index_attachments) if "_attachments" in df \
        else pd.Series([{}] * len(df), index=df.index)
    for champ in ["photo_dechets", "photo_bon", "signature_client"]:
        noms = df.get(champ, pd.Series("", index=df.index)).astype(str).replace("nan", "")
        out[champ] = [_url_media(lien, nom) for lien, nom in zip(liens, noms)]

    out["date_saisie"] = pd.to_datetime(df.get("_submission_time"), errors="coerce")
    out["id_soumission"] = df.get("_id", pd.Series("", index=df.index)).astype(str)

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
            "site": _normaliser_site(str(info.get("site", "CAMUSAT"))),
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


def _normaliser_site(libelle: str) -> str:
    """Aligne les libelles de site issus des fiches Excel sur ceux du formulaire."""
    ref = _sans_accents(libelle).strip().lower()
    if ref in ("camusat", "camusat thies", "thies", ""):
        return "Camusat Thies"
    if ref in ("camusat dakar", "dakar"):
        return "Camusat Dakar"
    return libelle.strip()


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
    # les coordonnees doivent rester numeriques meme quand elles sont absentes
    for col in ("latitude", "longitude"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date_saisie"] = pd.to_datetime(df["date_saisie"], errors="coerce")
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


def _logo_base64() -> str:
    if not LOGO:
        return ""
    try:
        return base64.b64encode(Path(LOGO).read_bytes()).decode()
    except Exception:  # noqa: BLE001
        return ""


LOGO_B64 = _logo_base64()

st.set_page_config(page_title="Suivi dechets CAMUSAT / SONAGED",
                   page_icon=LOGO or "♻️", layout="wide")

# --- Charte graphique SONAGED ---
VERT = "#1F6F43"          # vert principal du logo
VERT_CLAIR = "#8CC63F"    # vert des feuilles
VERT_PALE = "#EFF5F0"     # fonds
ENCRE = "#1B2B22"         # texte
GRIS = "#6B7B72"          # texte secondaire
BORDURE = "#DCE6DF"
PALETTE = {"Plastiques": "#2E86C1", "Cartons": "#CA6F1E", "Autres": "#8A9A91"}
POLICE = ("system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', "
          "Arial, sans-serif")

st.markdown(f"""
<style>
  .block-container {{padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1500px;}}
  h1, h2, h3 {{color: {VERT}; letter-spacing: -0.01em;}}
  h1 {{font-size: 1.9rem; font-weight: 700;}}
  h2 {{font-size: 1.25rem; margin-top: 0.6rem;}}
  h3 {{font-size: 1.05rem; color: {ENCRE};}}

  /* Bandeau d'en-tete */
  .bandeau {{
      background: linear-gradient(100deg, {VERT} 0%, #2C8A57 55%, {VERT_CLAIR} 160%);
      color: #FFFFFF; border-radius: 14px; padding: 1.1rem 1.4rem;
      display: flex; align-items: center; gap: 1.1rem; margin-bottom: 1.1rem;
  }}
  .bandeau h1 {{color: #FFFFFF; margin: 0; font-size: 1.55rem;}}
  .bandeau p {{margin: .15rem 0 0; opacity: .88; font-size: .86rem;}}

  /* Indicateurs en cartes */
  div[data-testid="stMetric"] {{
      background: #FFFFFF; border: 1px solid {BORDURE}; border-radius: 12px;
      padding: .85rem 1rem; box-shadow: 0 1px 2px rgba(27,43,34,.05);
  }}
  div[data-testid="stMetric"]:hover {{border-color: {VERT_CLAIR};}}
  div[data-testid="stMetricLabel"] p {{
      font-size: .76rem; color: {GRIS}; text-transform: uppercase;
      letter-spacing: .04em; font-weight: 600;
  }}
  div[data-testid="stMetricValue"] {{
      font-size: 1.55rem; font-weight: 700; color: {ENCRE};
  }}
  div[data-testid="stMetricDelta"] {{font-size: .78rem;}}

  /* Onglets */
  .stTabs [data-baseweb="tab-list"] {{
      gap: .15rem; border-bottom: 1px solid {BORDURE};
  }}
  .stTabs [data-baseweb="tab"] {{
      padding: .55rem .95rem; border-radius: 8px 8px 0 0;
      font-size: .9rem; font-weight: 500; color: {GRIS};
  }}
  .stTabs [aria-selected="true"] {{
      background: {VERT_PALE}; color: {VERT} !important; font-weight: 600;
  }}

  /* Conteneurs encadres (blocs en attente) */
  div[data-testid="stVerticalBlockBorderWrapper"] {{border-radius: 12px;}}

  /* Barre laterale */
  section[data-testid="stSidebar"] {{background: {VERT_PALE};}}
  section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 {{
      color: {VERT}; font-size: .95rem;
  }}

  /* Boutons */
  .stButton button {{border-radius: 8px; font-weight: 500;}}

  /* Tableaux */
  div[data-testid="stDataFrame"] {{border-radius: 10px;}}

  /* Pied de page */
  .pied {{
      border-top: 1px solid {BORDURE}; margin-top: 2.2rem; padding-top: .9rem;
      color: {GRIS}; font-size: .78rem;
  }}
</style>
""", unsafe_allow_html=True)

MODELE_GRAPHIQUE = dict(
    font=dict(family=POLICE, size=12, color=ENCRE),
    title=dict(font=dict(size=14, color=VERT), x=0, xanchor="left"),
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    margin=dict(t=48, b=40, l=10, r=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                title_text="", font=dict(size=11)),
    hoverlabel=dict(font_size=12, font_family=POLICE),
    colorway=[VERT, VERT_CLAIR, "#2E86C1", "#CA6F1E", "#8A9A91"],
)


def styliser(fig, hauteur: int = 340):
    """Applique la charte graphique a une figure Plotly."""
    fig.update_layout(height=hauteur, **MODELE_GRAPHIQUE)
    fig.update_xaxes(showgrid=False, linecolor=BORDURE, ticks="outside",
                     tickcolor=BORDURE, title_font_size=11)
    fig.update_yaxes(gridcolor=BORDURE, zeroline=False, title_font_size=11)
    return fig


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
    gauche, centre, droite = st.columns([1, 1.5, 1])
    with centre:
        st.markdown("<div style='height:6vh'></div>", unsafe_allow_html=True)
        if LOGO:
            l1, l2, l3 = st.columns([1, 2, 1])
            l2.image(LOGO, width="stretch")
        st.markdown(
            f"<h1 style='text-align:center;margin-bottom:.1rem'>Suivi des dechets tries</h1>"
            f"<p style='text-align:center;color:{GRIS};font-size:.9rem;margin-top:0'>"
            "CAMUSAT / SONAGED &nbsp;·&nbsp; acces reserve aux equipes autorisees</p>",
            unsafe_allow_html=True)

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


# Historique de reference integre au code : fiche de collecte CAMUSAT de mars 2026.
# Permet d'afficher des indicateurs avant l'arrivee des premieres soumissions Kobo,
# sans publier de fichier de donnees sur le depot.
HISTORIQUE_REFERENCE = [
    # date,       bacs et poids : plastiques, cartons, autres
    ("2026-03-04", 5, 68.44, 2, 28.50, 1, 48.81),
    ("2026-03-09", 6, 97.38, 4, 68.11, 1, 17.06),
    ("2026-03-18", 6, 185.46, 4, 43.75, 1, 55.90),
    ("2026-03-25", 6, 53.75, 1, 14.32, 1, 46.75),
]


@st.cache_data
def charger_historique_reference() -> pd.DataFrame:
    """Reconstitue les collectes de mars 2026 (source : fiche de collecte CAMUSAT)."""
    lignes = []
    for d, bp, pp, bc, pc, ba, pa in HISTORIQUE_REFERENCE:
        lignes.append({
            "date_collecte": pd.Timestamp(d),
            "client": "CAMUSAT", "site": "Camusat Thies",
            "responsable_sonaged": "Fatoumata DEME",
            "contact_client": "Bassirou Gning", "tel_contact": "77 605 56 93",
            "bacs_plastiques": bp, "poids_plastiques": pp,
            "bacs_cartons": bc, "poids_cartons": pc,
            "bacs_autres": ba, "poids_autres": pa,
            "destination_dechets": "Non precise", "site_tampon_nom": "",
            "num_bon": "", "levee_mensuelle": False, "num_certificat": "",
            "observations": "", "source": "Historique mars 2026",
        })
    return _finaliser(pd.DataFrame(lignes))


def bloc_attente(titre: str, description: str, exemple: str = "") -> None:
    """Encadre gris decrivant un champ du questionnaire pas encore renseigne."""
    with st.container(border=True):
        st.markdown(f"**{titre}** · en attente")
        st.caption(description + (f"  \n_{exemple}_" if exemple else ""))


@st.cache_data(ttl=1800, show_spinner=False)
def charger_media(url: str, token: str) -> bytes | None:
    """Telecharge une piece jointe Kobo (necessite le token)."""
    if not url:
        return None
    try:
        r = requests.get(url, headers=_headers(token) if token else {}, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception:  # noqa: BLE001
        return None


def taux_completude(donnees: pd.DataFrame, champ: str) -> float:
    """Part des collectes pour lesquelles le champ est renseigne (en %)."""
    if donnees.empty or champ not in donnees:
        return 0.0
    col = donnees[champ]
    if col.dtype.kind in "fc":
        rempli = col.notna()
    else:
        rempli = col.astype(str).str.strip().replace("nan", "").ne("")
    return float(rempli.mean() * 100)


cfg = get_config()

with st.sidebar:
    if LOGO:
        st.image(LOGO, width=130)

    base_url, token, uid = cfg["base_url"], cfg["token"], cfg["asset_uid"]
    connecte = bool(token and uid)
    st.caption("Connecte a KoboToolbox" if connecte else "KoboToolbox non configure")

    st.subheader("Sources de donnees")
    inclure_historique = st.checkbox("Fiches Excel du dossier donnees/", value=True)
    inclure_reference = st.checkbox(
        "Historique de reference (mars 2026)", value=True,
        help="Collectes de mars 2026 integrees a l'application. Permet de visualiser "
             "les indicateurs avant les premieres saisies Kobo.")

    if st.button("Actualiser les donnees", width="stretch"):
        st.cache_data.clear()
        st.rerun()

    with st.expander("Parametres avances"):
        st.caption("Valeurs issues des secrets. Une modification ici ne vaut que "
                   "pour la session en cours.")
        base_url = st.text_input("Serveur Kobo", value=base_url)
        token = st.text_input("Token API", value=token, type="password",
                              help="Compte Kobo > Parametres > Securite > Cle API")
        if token:
            try:
                options = {a["name"]: a["uid"] for a in charger_assets(base_url, token)}
                if options:
                    noms = list(options)
                    defaut = next((n for n, u in options.items() if u == uid), noms[0])
                    uid = options[st.selectbox("Formulaire", noms,
                                               index=noms.index(defaut))]
            except Exception as exc:  # noqa: BLE001
                st.error(f"Liste des formulaires indisponible : {exc}")
        uid = st.text_input("UID du formulaire", value=uid)

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
if inclure_reference:
    blocs.append(charger_historique_reference())

blocs_valides = [b for b in blocs if b is not None and not b.empty]
if blocs_valides:
    df = pd.concat(blocs_valides, ignore_index=True)
    # une meme collecte peut venir d'une fiche Excel et de l'historique integre :
    # la version Kobo (ou la premiere source listee) est conservee
    df["_cle"] = df["total_poids"].round(2)
    df = (df.drop_duplicates(subset=["date_collecte", "_cle"], keep="first")
            .drop(columns="_cle")
            .sort_values("date_collecte").reset_index(drop=True))
else:
    df = pd.DataFrame(columns=COLONNES)

sources_actives = sorted(df["source"].dropna().unique()) if not df.empty else []
attente_kobo = "Kobo" not in sources_actives

st.markdown(f"""
<div class="bandeau">
  {f'<img src="data:image/jpeg;base64,{LOGO_B64}" width="58" '
   'style="border-radius:10px;background:#fff;padding:5px;">' if LOGO_B64 else ''}
  <div>
    <h1>Suivi des dechets tries</h1>
    <p>CAMUSAT / SONAGED &nbsp;·&nbsp; collecte, stockage tampon et valorisation</p>
  </div>
</div>
""", unsafe_allow_html=True)

if erreur_kobo:
    st.warning(f"Kobo : {erreur_kobo}")
if attente_kobo and not df.empty:
    st.info(
        "**En attente des premieres saisies Kobo.** Les indicateurs ci-dessous "
        "reposent sur l'historique de reference (mars 2026). Ils seront remplaces "
        "automatiquement par les donnees du formulaire des la premiere soumission.",
        icon=":material/hourglass_top:")
if df.empty:
    if token and uid and not erreur_kobo:
        st.success("Connexion a KoboToolbox etablie.")
        st.info(
            "Le formulaire ne contient encore aucune soumission. Les indicateurs "
            "se rempliront des la premiere collecte enregistree dans KoboCollect."
        )
    else:
        st.info(
            "Aucune source de donnees active.\n\n"
            "- Renseigner le token et l'UID du formulaire (secrets ou parametres avances).\n"
            "- Ou reactiver l'historique de reference dans le menu de gauche."
        )

    # Le tableau de bord reste visible : chaque bloc annonce ce qu'il affichera.
    z1, z2, z3, z4, z5 = st.columns(5)
    for col, libelle in zip((z1, z2, z3, z4, z5),
                            ("Tonnage total", "Valorisable", "Part valorisable",
                             "Bacs collectes", "Collectes enregistrees")):
        col.metric(libelle, "-")

    apercu = st.tabs(["Vue d'ensemble", "Evolution", "Stock tampon",
                      "Comparaison sites", "Fiches de collecte", "Tracabilite",
                      "Intervenants", "Donnees & export"])
    contenus = [
        ("Repartition par flux",
         "Part des plastiques, cartons et autres dechets dans le tonnage collecte, "
         "tonnage par destination et par passage.",
         "Alimente par : nombre de bacs et poids de chaque flux"),
        ("Evolution mensuelle et hebdomadaire",
         "Tonnage par mois et par flux, part valorisable, profil des semaines 1 a 5.",
         "Alimente par : date de collecte et poids"),
        ("Stock au site tampon",
         "Cumul des entrees au site tampon diminue des levees, avec seuil d'alerte "
         "declenchant la levee mensuelle.",
         "Alimente par : destination des dechets et levee mensuelle"),
        ("Comparaison entre sites",
         "Classement des sites par tonnage, croisement site par mois, moyennes par "
         "collecte.",
         "Alimente par : site de collecte"),
        ("Fiches de collecte",
         "Detail de chaque passage : interlocuteurs, quantites, photos des dechets et "
         "du bon de pesee, position GPS, signature du client, observations.",
         "Alimente par : l'ensemble du questionnaire Kobo"),
        ("Tracabilite documentaire",
         "Bons de pesee, levees mensuelles, certificats de traitement obtenus et "
         "manquants, completude de la saisie champ par champ.",
         "Alimente par : n° de bon, levee mensuelle, n° de certificat"),
        ("Intervenants",
         "Responsables SONAGED et contacts Camusat, nombre de collectes par agent, "
         "regularite des passages.",
         "Alimente par : responsable SONAGED et contact chez le client"),
        ("Donnees et export",
         "Table detaillee de toutes les collectes, export Excel multi-onglets et CSV.",
         "Alimente par : toutes les collectes enregistrees"),
    ]
    for onglet, (titre, description, exemple) in zip(apercu, contenus):
        with onglet:
            bloc_attente(titre, description, exemple)
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
m3.metric("Part valorisable", f"{k['taux']:.1f} %",
          help="Plastiques + cartons rapportes au tonnage total collecte. Ce ratio "
               "mesure la qualite du tri, pas le devenir reel des dechets : la "
               "valorisation effective est suivie dans l'onglet Tracabilite.")
m4.metric("Bacs collectes", f"{k['bacs']:,.0f}".replace(",", " "))
m5.metric("Collectes enregistrees", k["collectes"])

# --- Etat du dispositif : indicateurs de pilotage, utiles des le demarrage ---
with st.expander("Etat du dispositif", expanded=attente_kobo):
    derniere = pd.to_datetime(dfa["date_collecte"]).max()
    anciennete = (pd.Timestamp(date.today()) - derniere).days
    prochaine_levee = (derniere + pd.offsets.MonthEnd(0)).date()
    certificats_manquants = int(
        (dfa["levee_mensuelle"].astype(bool)
         & dfa["num_certificat"].astype(str).str.strip().eq("")).sum())
    tracabilite = dfa["num_bon"].astype(str).str.strip().ne("").mean() * 100

    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Derniere collecte", derniere.strftime("%d/%m/%Y"),
              delta=f"il y a {anciennete} j", delta_color="off")
    e2.metric("Moyenne par collecte", f"{k['total'] / max(k['collectes'], 1):.0f} kg")
    e3.metric("Bons de pesee renseignes", f"{tracabilite:.0f} %")
    e4.metric("Certificats manquants", certificats_manquants,
              delta="a reclamer" if certificats_manquants else "a jour",
              delta_color="inverse" if certificats_manquants else "normal")

    st.caption(
        f"Sources actives : {', '.join(sources_actives) or 'aucune'} · "
        f"Periode couverte : du {pd.to_datetime(dfa['date_collecte']).min():%d/%m/%Y} "
        f"au {derniere:%d/%m/%Y} · "
        f"Fin de mois de la derniere collecte : {prochaine_levee:%d/%m/%Y}")

onglets = st.tabs(["Vue d'ensemble", "Evolution", "Stock tampon",
                   "Comparaison sites", "Fiches de collecte", "Tracabilite",
                   "Intervenants", "Donnees & export"])

# --------------------------------------------------------------------------- #
with onglets[0]:
    g1, g2 = st.columns(2)
    par_flux = long.groupby("flux", as_index=False)["poids"].sum()
    fig = px.pie(par_flux, names="flux", values="poids", hole=0.5,
                 color="flux", color_discrete_map=PALETTE,
                 title="Repartition du tonnage par flux")
    fig.update_traces(texttemplate="%{label}<br>%{value:.0f} kg (%{percent})")
    g1.plotly_chart(styliser(fig), width="stretch")

    par_dest = dfa.groupby("destination_dechets", as_index=False)["total_poids"].sum()
    fig2 = px.bar(par_dest, x="destination_dechets", y="total_poids",
                  title="Tonnage par destination", labels={"destination_dechets": "",
                                                           "total_poids": "kg"})
    fig2.update_traces(marker_color=VERT)
    g2.plotly_chart(styliser(fig2), width="stretch")

    hebdo = long.groupby(["date_collecte", "flux"], as_index=False)["poids"].sum()
    fig3 = px.bar(hebdo, x="date_collecte", y="poids", color="flux",
                  color_discrete_map=PALETTE, barmode="stack",
                  title="Tonnage par collecte", labels={"date_collecte": "", "poids": "kg"})
    st.plotly_chart(styliser(fig3, hauteur=380), width="stretch")

# --------------------------------------------------------------------------- #
with onglets[1]:
    evo = evolution_mensuelle(dfa)
    e1, e2 = st.columns([3, 2])
    mensuel = long.groupby(["mois", "flux"], as_index=False)["poids"].sum()
    fige = px.bar(mensuel, x="mois", y="poids", color="flux", barmode="group",
                  color_discrete_map=PALETTE, title="Tonnage mensuel par flux",
                  labels={"mois": "", "poids": "kg"})
    e1.plotly_chart(styliser(fige), width="stretch")

    figt = px.line(evo, x="mois", y="taux", markers=True,
                   title="Part valorisable (%)", labels={"mois": "", "taux": "%"})
    figt.update_traces(line_color=VERT)
    figt.update_yaxes(range=[0, 100])
    e2.plotly_chart(styliser(figt), width="stretch")

    st.subheader("Profil hebdomadaire")
    hebdo = (long.groupby(["semaine", "flux"], as_index=False)["poids"].sum()
             .sort_values("semaine"))
    figs = px.bar(hebdo, x="semaine", y="poids", color="flux", barmode="stack",
                  color_discrete_map=PALETTE,
                  title="Tonnage cumule par semaine du mois",
                  labels={"semaine": "", "poids": "kg"})
    st.plotly_chart(styliser(figs), width="stretch")

    st.subheader("Detail mensuel")
    tab = evo.rename(columns={"mois": "Mois", "total_poids": "Total (kg)",
                              "poids_valorisable": "Valorisable (kg)",
                              "total_bacs": "Bacs", "taux": "Part valorisable (%)"})
    tab = tab.round({"Total (kg)": 2, "Valorisable (kg)": 2, "Part valorisable (%)": 1})
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
        st.plotly_chart(styliser(figs), width="stretch")

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
    st.plotly_chart(styliser(figc), width="stretch")

    figh = px.density_heatmap(long, x="mois", y="site", z="poids", histfunc="sum",
                              color_continuous_scale="Greens",
                              title="Tonnage par site et par mois (kg)",
                              labels={"mois": "", "site": ""})
    st.plotly_chart(styliser(figh), width="stretch")

    st.dataframe(par_site.rename(columns={
        "site": "Site", "total": "Total (kg)", "valorisable": "Valorisable (kg)",
        "bacs": "Bacs", "collectes": "Collectes", "taux": "Part valorisable (%)",
        "moyenne_par_collecte": "Moyenne / collecte (kg)"}),
        width="stretch", hide_index=True)

# --------------------------------------------------------------------------- #
with onglets[4]:
    st.caption("Detail de chaque passage, tel que saisi dans KoboCollect.")
    fiches = dfa.sort_values("date_collecte", ascending=False)
    etiquettes = [
        f"{r.date_collecte:%d/%m/%Y} · {r.site} · {r.total_poids:.0f} kg"
        for r in fiches.itertuples()]
    choix = st.selectbox("Collecte", range(len(etiquettes)),
                         format_func=lambda i: etiquettes[i])
    f = fiches.iloc[choix]

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Identification**")
        st.write(f"Date : {f['date_collecte']:%d/%m/%Y}")
        st.write(f"Semaine : {f['semaine']} (ISO {f['semaine_iso']})")
        st.write(f"Client / site : {f['client']} - {f['site']}")
        st.caption(f"Source : {f['source']}"
                   + (f" · soumission n°{f['id_soumission']}" if f["id_soumission"] else ""))
    with c2:
        st.markdown("**Interlocuteurs**")
        st.write(f"Responsable SONAGED : {f['responsable_sonaged'] or 'non renseigne'}")
        st.write(f"Fonction : {f['fonction_responsable'] or 'non renseignee'}")
        st.write(f"Contact client : {f['contact_client'] or 'non renseigne'}")
        st.write(f"Telephone : {f['tel_contact'] or 'non renseigne'}")
    with c3:
        st.markdown("**Destination**")
        st.write(f"Destination : {f['destination_dechets']}")
        st.write(f"Site tampon : {f['site_tampon_nom'] or 'non precise'}")
        st.write(f"N° de bon : {f['num_bon'] or 'non renseigne'}")
        st.write(f"Levee mensuelle : {'oui' if f['levee_mensuelle'] else 'non'}")
        if f["levee_mensuelle"]:
            st.write(f"N° de certificat : {f['num_certificat'] or 'non renseigne'}")

    st.markdown("**Quantites**")
    detail = pd.DataFrame({
        "Flux": FLUX,
        "Bacs": [f["bacs_plastiques"], f["bacs_cartons"], f["bacs_autres"]],
        "Poids (kg)": [f["poids_plastiques"], f["poids_cartons"], f["poids_autres"]],
    })
    q1, q2 = st.columns([2, 3])
    q1.dataframe(detail, hide_index=True, width="stretch")
    figf = px.bar(detail, x="Flux", y="Poids (kg)", color="Flux",
                  color_discrete_map=PALETTE, text_auto=".1f")
    q2.plotly_chart(styliser(figf, hauteur=260), width="stretch")
    figf.update_layout(showlegend=False)
    if f["nature_autres"]:
        st.caption(f"Nature des autres dechets : {f['nature_autres']}")

    st.markdown("**Preuves**")
    p1, p2, p3 = st.columns(3)
    for col, champ, titre, aide in (
            (p1, "photo_dechets", "Photo des dechets",
             "Prise lors du passage, elle atteste de la qualite du tri."),
            (p2, "photo_bon", "Photo du bon de pesee",
             "Justificatif du tonnage declare."),
            (p3, "signature_client", "Signature du client",
             "Validation du contact Camusat a la fin de la collecte.")):
        with col:
            url = str(f[champ]) if f[champ] else ""
            contenu = charger_media(url, token) if url else None
            if contenu:
                st.image(contenu, caption=titre, width="stretch")
            elif url:
                with st.container(border=True):
                    st.markdown(f"**{titre}** · indisponible")
                    st.caption("La piece jointe existe dans Kobo mais n'a pas pu etre "
                               "chargee. Verifier le token dans les secrets.")
                    st.link_button("Ouvrir dans Kobo", url, width="stretch")
            else:
                bloc_attente(titre, aide)

    g1, g2 = st.columns(2)
    with g1:
        if pd.notna(f["latitude"]) and pd.notna(f["longitude"]):
            st.map(pd.DataFrame({"lat": [f["latitude"]], "lon": [f["longitude"]]}), zoom=13)
            st.caption(f"Position : {f['latitude']:.5f}, {f['longitude']:.5f}")
        else:
            bloc_attente("Position GPS",
                         "Relevee automatiquement par KoboCollect au moment de la saisie.",
                         "Exemple : 14.79100, -16.92600")
    with g2:
        if str(f["observations"]).strip():
            st.markdown("**Observations**")
            st.info(f["observations"])
        else:
            bloc_attente("Observations",
                         "Anomalies, refus de tri, incidents signales par l'agent.")


# --------------------------------------------------------------------------- #
with onglets[5]:
    st.caption("Suivi documentaire : bons de pesee, levees mensuelles et certificats "
               "de traitement delivres par le repreneur.")

    levees = dfa[dfa["levee_mensuelle"].astype(bool)]
    sans_certificat = int((levees["num_certificat"].astype(str).str.strip() == "").sum())
    certifiees = levees[levees["num_certificat"].astype(str).str.strip() != ""]
    poids_certifie = float(certifiees["poids_valorisable"].sum())
    taux_reel = (poids_certifie / k["total"] * 100) if k["total"] else 0.0

    v1, v2 = st.columns(2)
    v1.metric("Part valorisable (au tri)", f"{k['taux']:.1f} %",
              help="Plastiques + cartons rapportes au tonnage collecte.")
    v2.metric("Valorisation certifiee", f"{taux_reel:.1f} %",
              help="Quantites effectivement remises au repreneur et couvertes par un "
                   "certificat de traitement, rapportees au tonnage collecte.")
    if taux_reel == 0:
        st.caption("Aucune levee certifiee a ce jour : la valorisation certifiee reste "
                   "a 0 % tant qu'un certificat de traitement n'a pas ete enregistre.")
    st.divider()

    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Collectes tracees", f"{taux_completude(dfa, 'num_bon'):.0f} %",
              help="Part des collectes avec un numero de bon de pesee")
    t2.metric("Levees mensuelles", len(levees))
    t3.metric("Certificats obtenus", len(levees) - sans_certificat)
    t4.metric("Certificats manquants", sans_certificat,
              delta="a reclamer" if sans_certificat else "a jour",
              delta_color="inverse" if sans_certificat else "normal")

    st.subheader("Completude de la saisie")
    completude = pd.DataFrame({
        "Champ": list(CHAMPS_QUALITE.values()),
        "Taux (%)": [round(taux_completude(dfa, c), 1) for c in CHAMPS_QUALITE],
    }).sort_values("Taux (%)")
    figc = px.bar(completude, x="Taux (%)", y="Champ", orientation="h",
                  range_x=[0, 100], text_auto=".0f",
                  title="Part des collectes pour lesquelles le champ est renseigne")
    figc.update_traces(marker_color=VERT)
    st.plotly_chart(styliser(figc), width="stretch")
    st.caption("Un taux faible signale un champ a rappeler aux agents lors du brief.")

    st.subheader("Levees mensuelles et certificats")
    if levees.empty:
        bloc_attente(
            "Levees vers Ciments du Sahel",
            "Ce tableau se remplira des qu'une collecte sera declaree comme levee "
            "mensuelle dans le formulaire, avec son numero de certificat.",
            "Colonnes attendues : date, site, quantite, n° de bon, n° de certificat")
    else:
        vue = levees[["date_collecte", "site", "poids_valorisable",
                      "destination_dechets", "num_bon", "num_certificat"]].rename(
            columns={"date_collecte": "Date", "site": "Site",
                     "poids_valorisable": "Quantite (kg)",
                     "destination_dechets": "Destination",
                     "num_bon": "N. bon", "num_certificat": "N. certificat"})
        st.dataframe(vue, width="stretch", hide_index=True)

    st.subheader("Collectes sans bon de pesee")
    sans_bon = dfa[dfa["num_bon"].astype(str).str.strip() == ""]
    if sans_bon.empty:
        st.success("Toutes les collectes de la periode disposent d'un bon de pesee.")
    else:
        st.warning(f"{len(sans_bon)} collecte(s) sans numero de bon.")
        st.dataframe(
            sans_bon[["date_collecte", "site", "total_poids", "responsable_sonaged"]].rename(
                columns={"date_collecte": "Date", "site": "Site",
                         "total_poids": "Poids (kg)",
                         "responsable_sonaged": "Responsable"}),
            width="stretch", hide_index=True)


# --------------------------------------------------------------------------- #
with onglets[6]:
    st.caption("Referents de la collecte cote SONAGED et cote client.")

    agents = dfa[dfa["responsable_sonaged"].astype(str).str.strip() != ""]
    if agents.empty:
        bloc_attente(
            "Responsables SONAGED",
            "Le champ 'Responsable SONAGED' du formulaire alimente ce tableau : "
            "nombre de collectes, tonnage et regularite par agent.",
            "Point 1 de la note : designer un referent pour la collecte et le suivi")
    else:
        recap = agents.groupby("responsable_sonaged", as_index=False).agg(
            collectes=("total_poids", "size"), tonnage=("total_poids", "sum"),
            derniere=("date_collecte", "max"), premiere=("date_collecte", "min"))
        recap["tonnage"] = recap["tonnage"].round(1)
        recap["derniere"] = recap["derniere"].dt.strftime("%d/%m/%Y")
        recap["premiere"] = recap["premiere"].dt.strftime("%d/%m/%Y")
        st.dataframe(recap.rename(columns={
            "responsable_sonaged": "Responsable SONAGED", "collectes": "Collectes",
            "tonnage": "Tonnage (kg)", "premiere": "Premiere collecte",
            "derniere": "Derniere collecte"}), width="stretch", hide_index=True)

        figa = px.bar(recap.sort_values("tonnage"), x="tonnage", y="responsable_sonaged",
                      orientation="h", text_auto=".0f",
                      labels={"tonnage": "kg", "responsable_sonaged": ""},
                      title="Tonnage collecte par responsable")
        figa.update_traces(marker_color=VERT)
        st.plotly_chart(styliser(figa), width="stretch")

    st.subheader("Contacts chez le client")
    contacts = dfa[dfa["contact_client"].astype(str).str.strip() != ""]
    if contacts.empty:
        bloc_attente("Contacts Camusat",
                     "Nom et telephone du contact present lors de chaque passage.")
    else:
        vue = (contacts.groupby(["site", "contact_client"], as_index=False)
               .agg(telephone=("tel_contact", "last"),
                    collectes=("total_poids", "size"),
                    derniere=("date_collecte", "max")))
        vue["derniere"] = vue["derniere"].dt.strftime("%d/%m/%Y")
        st.dataframe(vue.rename(columns={
            "site": "Site", "contact_client": "Contact", "telephone": "Telephone",
            "collectes": "Collectes", "derniere": "Dernier passage"}),
            width="stretch", hide_index=True)

    st.subheader("Regularite des passages")
    jours = dfa.sort_values("date_collecte")["date_collecte"].diff().dt.days.dropna()
    if len(jours) < 2:
        bloc_attente("Intervalle entre passages",
                     "Calcule automatiquement des que trois collectes au moins "
                     "auront ete enregistrees.")
    else:
        r1, r2, r3 = st.columns(3)
        r1.metric("Intervalle moyen", f"{jours.mean():.0f} j")
        r2.metric("Intervalle le plus court", f"{jours.min():.0f} j")
        r3.metric("Intervalle le plus long", f"{jours.max():.0f} j")


# --------------------------------------------------------------------------- #
with onglets[7]:
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

# --------------------------------------------------------------------------- #
st.markdown(
    f"<div class='pied'>SONAGED · Suivi des dechets tries CAMUSAT · "
    f"donnees issues de KoboToolbox · consulte le {date.today():%d/%m/%Y}</div>",
    unsafe_allow_html=True)
