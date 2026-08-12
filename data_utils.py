"""Normalisation des donnees de collecte (Kobo, Excel historique, CSV)."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

import numpy as np
import pandas as pd

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
