"""Connecteur KoboToolbox : recupere les soumissions via l'API v2."""
from __future__ import annotations

import os
from typing import Any

import pandas as pd
import requests

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
