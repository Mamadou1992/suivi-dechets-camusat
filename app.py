"""Suivi des dechets tries CAMUSAT / SONAGED - tableau de bord Streamlit."""
from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import data_utils as du
from kobo_client import KoboError, fetch_submissions, get_config, list_assets

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
        st.caption("CAMUSAT / SONAGED — acces reserve aux equipes autorisees.")

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
        st.caption("Mot de passe oublie : contacter Mamadou DIOP (SONAGED).")
    st.stop()


controle_acces()


# --------------------------------------------------------------------------- #
# Chargement des donnees
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=600, show_spinner="Recuperation des donnees Kobo...")
def charger_kobo(base_url: str, token: str, uid: str) -> pd.DataFrame:
    return du.normaliser_kobo(fetch_submissions(base_url, token, uid))


@st.cache_data(ttl=600)
def charger_assets(base_url: str, token: str) -> list[dict]:
    return list_assets(base_url, token)


@st.cache_data(ttl=600)
def charger_fichiers_locaux() -> pd.DataFrame:
    """Reprend les fiches Excel historiques placees dans ./donnees."""
    if not DOSSIER_DONNEES.exists():
        return pd.DataFrame(columns=du.COLONNES)
    blocs = []
    for f in sorted(DOSSIER_DONNEES.glob("*.xlsx")):
        try:
            blocs.append(du.normaliser_fiche_excel(f))
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Fichier ignore ({f.name}) : {exc}")
    return pd.concat(blocs, ignore_index=True) if blocs else pd.DataFrame(columns=du.COLONNES)


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

df = pd.concat([b for b in blocs if not b.empty], ignore_index=True) if blocs else pd.DataFrame()
if df.empty:
    df = pd.DataFrame(columns=du.COLONNES)
else:
    df = df.sort_values("date_collecte").reset_index(drop=True)

entete = st.columns([1, 9])
if LOGO:
    entete[0].image(LOGO, width=80)
entete[1].title("Suivi des dechets tries — CAMUSAT / SONAGED")

if erreur_kobo:
    st.warning(f"Kobo : {erreur_kobo}")
if df.empty:
    st.info("Aucune donnee. Renseignez le token et l'UID du formulaire dans le menu de gauche, "
            "ou deposez les fiches Excel dans le dossier `donnees/`.")
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
flux_sel = reste[1].multiselect("Flux", du.FLUX, default=du.FLUX)

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

long = du.format_long(dfa)
long = long[long["flux"].isin(flux_sel)]

# --------------------------------------------------------------------------- #
# KPI
# --------------------------------------------------------------------------- #
k = du.kpis(dfa)
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
    evo = du.evolution_mensuelle(dfa)
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
    stock = du.calculer_stock_tampon(dfa)
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
            col.metric(f"{row['site']} — stock actuel", f"{row['stock']:,.0f} kg".replace(",", " "),
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
            du.evolution_mensuelle(donnees).to_excel(writer, sheet_name="Synthese mensuelle",
                                                     index=False)
            (du.format_long(donnees).groupby(["mois", "flux"], as_index=False)["poids"].sum()
             .to_excel(writer, sheet_name="Par flux", index=False))
            st_tampon = du.calculer_stock_tampon(donnees)
            if not st_tampon.empty:
                st_tampon.to_excel(writer, sheet_name="Stock tampon", index=False)
        return buffer.getvalue()

    st.download_button("Telecharger le rapport Excel", data=construire_export(dfa),
                       file_name=f"suivi_dechets_{date.today():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.download_button("Telecharger les donnees (CSV)",
                       data=dfa.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"suivi_dechets_{date.today():%Y%m%d}.csv", mime="text/csv")
