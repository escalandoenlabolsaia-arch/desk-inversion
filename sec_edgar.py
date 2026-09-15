# -*- coding: utf-8 -*-
"""
sec_edgar.py — Pasarela SEC EDGAR para el screener.

Cubre dos secciones del config.json:
  1. sec_edgar  -> 13F-HR de fondos institucionales (Berkshire, Pershing, Scion...)
  2. insiders   -> Form 4 (compras/ventas de insiders) por empresa,
                   tanto de los setups como de TODO el universo

Uso desde el resto del sistema:

    from sec_edgar import SecEdgar

    edgar = SecEdgar.desde_config("config.json")
    resumen    = edgar.resumen_fondos(min_fondos=2)      # consenso de fondos
    candidatos = edgar.marcar_fondos(candidatos)         # añade n_fondos, lista_fondos...
    candidatos = edgar.marcar_insiders(candidatos)       # añade actividad_insiders (setups)
    hallazgos  = edgar.escanear_insiders_universo(tickers)  # compras en todo el universo

Uso directo (test):
    python sec_edgar.py config.json
    python sec_edgar.py config.json --universo AAPL MSFT XOM

Dependencias: requests
"""

from __future__ import annotations

import json
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests

DATA_SEC = "https://data.sec.gov"
WWW_SEC = "https://www.sec.gov"
OPENFIGI_URL = "https://api.openfigi.com/v1/mapping"
TICKERS_URL = f"{WWW_SEC}/files/company_tickers.json"

FORMS_13F = {"13F-HR", "13F-HR/A"}
COD_COMPRA = {"P"}   # compra en mercado abierto
COD_VENTA = {"S"}    # venta en mercado abierto


# --------------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------------- #

def _normalizar_nombre(nombre: str) -> str:
    n = (nombre or "").upper()
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
    n = re.sub(r"\b(INC|INCORPORATED|CORP|CORPORATION|LTD|LIMITED|PLC|CO|COMPANY|"
               r"HLDGS|HOLDINGS|SA|NV|LP|LLC|ORD|SHS|SHARES|THE)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _etiqueta(elem: ET.Element) -> str:
    """Tag XML sin namespace."""
    return elem.tag.split("}")[-1]


# --------------------------------------------------------------------------- #
# Clase principal
# --------------------------------------------------------------------------- #

class SecEdgar:
    def __init__(self, email: str, dias_filings: int = 21, fondos: dict | None = None,
                 insiders_dias: int = 15, insiders_max: int = 8,
                 insiders_solo_compras: bool = True, insiders_monto_min: float = 100000,
                 insiders_max_nombres: int = 6,
                 cache_dir: str = ".cache_edgar", pausa: float = 0.15):
        if not email:
            raise ValueError("sec_edgar.email es obligatorio (la SEC exige User-Agent con email).")

        self.email = email
        self.dias_filings = int(dias_filings)
        self.fondos = dict(fondos or {})
        self.insiders_dias = int(insiders_dias)
        self.insiders_max = int(insiders_max)
        self.insiders_solo_compras = bool(insiders_solo_compras)
        self.insiders_monto_min = float(insiders_monto_min)
        self.insiders_max_nombres = int(insiders_max_nombres)
        self.pausa = pausa

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)

        self.sess = requests.Session()
        self.sess.headers.update({
            "User-Agent": f"EscalandoEnLaBolsa-IA {email}",
            "Accept-Encoding": "gzip, deflate",
        })

        self._subs: dict[str, dict] = {}          # cache en memoria de submissions
        self._posiciones: dict | None = None
        self._mapa_exacto: dict | None = None
        self._cik_por_ticker: dict | None = None
        self._hoy = datetime.utcnow().strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ #
    @classmethod
    def desde_config(cls, ruta: str = "config.json") -> "SecEdgar":
        cfg = json.loads(Path(ruta).read_text(encoding="utf-8"))
        sec = cfg.get("sec_edgar", {})
        ins = cfg.get("insiders", {})
        return cls(
            email=sec.get("email", ""),
            dias_filings=sec.get("dias_filings", 21),
            fondos=sec.get("fondos", {}),
            insiders_dias=ins.get("dias", 15),
            insiders_max=ins.get("max_formularios", 8),
            insiders_solo_compras=ins.get("solo_compras", True),
            insiders_monto_min=ins.get("monto_min_usd", 100000),
            insiders_max_nombres=ins.get("max_nombres", 6),
        )

    # ------------------------------------------------------------------ #
    # HTTP con pausa (máx ~10 req/s que exige la SEC) y reintentos
    # ------------------------------------------------------------------ #
    def _get(self, url: str, as_json: bool = False):
        ultimo_error = None
        for intento in range(3):
            try:
                r = self.sess.get(url, timeout=25)
                if r.status_code == 200:
                    time.sleep(self.pausa)
                    return r.json() if as_json else r.content
                if r.status_code in (403, 429):          # rate-limit / bloqueo
                    time.sleep(2 * (intento + 1))
                    continue
                r.raise_for_status()
            except requests.RequestException as e:
                ultimo_error = e
                time.sleep(1 + intento)
        raise RuntimeError(f"GET falló: {url} ({ultimo_error})")

    # ------------------------------------------------------------------ #
    # Cache en disco
    # ------------------------------------------------------------------ #
    def _leer_cache(self, nombre: str, solo_hoy: bool = False):
        ruta = self.cache_dir / f"{nombre}.json"
        if not ruta.exists():
            return None
        try:
            data = json.loads(ruta.read_text(encoding="utf-8"))
        except Exception:
            return None
        if solo_hoy and isinstance(data, dict) and data.get("_fecha") != self._hoy:
            return None
        return data.get("_data") if isinstance(data, dict) and "_data" in data else data

    def _guardar_cache(self, nombre: str, data):
        ruta = self.cache_dir / f"{nombre}.json"
        ruta.write_text(json.dumps({"_fecha": self._hoy, "_data": data},
                                   ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ #
    # Submissions (listado de filings por CIK)
    # ------------------------------------------------------------------ #
    def submissions(self, cik10: str) -> dict:
        cik10 = f"{int(cik10):010d}"
        if cik10 not in self._subs:
            self._subs[cik10] = self._get(f"{DATA_SEC}/submissions/CIK{cik10}.json", as_json=True)
        return self._subs[cik10]

    def _filings(self, cik10: str, formas: set[str], desde: str | None):
        """Lista de filings (más recientes primero) filtrada por formulario y fecha."""
        rec = self.submissions(cik10)["filings"]["recent"]
        claves = ("form", "accessionNumber", "filingDate", "reportDate", "primaryDocument")
        n = min(len(rec[k]) for k in claves)
        salida = []
        for i in range(n - 1, -1, -1):                    # más recientes primero
            f = rec["form"][i]
            if f not in formas:
                continue
            fdate = rec["filingDate"][i]
            if desde and fdate < desde:
                continue
            salida.append({
                "form": f,
                "accession": rec["accessionNumber"][i],
                "filing_date": fdate,
                "report_date": rec["reportDate"][i] or fdate,
                "primary_doc": rec["primaryDocument"][i],
            })
        return salida

    # ------------------------------------------------------------------ #
    # 13F — descarga y parseo del infotable
    # ------------------------------------------------------------------ #
    def _archivos_filing(self, cik10: str, accession: str) -> list[str]:
        acc = accession.replace("-", "")
        url = f"{WWW_SEC}/Archives/edgar/data/{int(cik10)}/{acc}/index.json"
        j = self._get(url, as_json=True)
        return [it["name"] for it in j["directory"]["item"]], acc

    def _descargar_infotable(self, cik10: str, filing: dict) -> bytes | None:
        nombres, acc = self._archivos_filing(cik10, filing["accession"])
        base = f"{WWW_SEC}/Archives/edgar/data/{int(cik10)}/{acc}/"
        primario = (filing["primary_doc"] or "").lower()

        candidatos = [n for n in nombres
                      if ("infotable" in n.lower() or "informationtable" in n.lower())
                      and n.lower() != primario]
        if not candidatos:  # fallback: cualquier XML que no sea el doc principal
            candidatos = [n for n in nombres if n.lower().endswith(".xml")
                          and n.lower() not in (primario, "primary_doc.xml")]
        if not candidatos:
            return None

        data = self._get(base + candidatos[0])
        if candidatos[0].lower().endswith(".zip"):
            with zipfile.ZipFile(BytesIO(data)) as z:
                xml_interno = next(n for n in z.namelist() if n.lower().endswith(".xml"))
                data = z.read(xml_interno)
        return data

    def _parsear_infotable(self, data: bytes, factor: int) -> list[dict]:
        root = ET.fromstring(data)
        filas = []
        for elem in root.iter():
            if _etiqueta(elem) != "infoTable":
                continue
            fila: dict = {}
            for hijo in elem:
                k = _etiqueta(hijo)
                if k in ("nameOfIssuer", "titleOfClass", "cusip", "putCall",
                         "investmentDiscretion"):
                    fila[k] = (hijo.text or "").strip()
                elif k == "value":
                    fila["valor_usd"] = int(float(hijo.text)) * factor
                elif k == "shrsOrPrnAmt":
                    for sub in hijo:
                        sk = _etiqueta(sub)
                        if sk == "sshPrnamt":
                            fila["acciones"] = int(float(sub.text))
                        elif sk == "sshPrnamtType":
                            fila["tipo"] = (sub.text or "").strip()
            filas.append(fila)
        return filas

    # ------------------------------------------------------------------ #
    # CUSIP -> ticker (OpenFIGI con cache + fallback por nombre en EDGAR)
    # ------------------------------------------------------------------ #
    def _cusip_a_ticker_openfigi(self, cusip: str) -> str | None:
        try:
            r = requests.post(OPENFIGI_URL,
                              json=[{"idType": "ID_CUSIP", "idValue": cusip}],
                              timeout=10)
            if r.status_code == 200:
                arr = r.json()
                datos = arr[0].get("data") if isinstance(arr, list) else None
                if datos:
                    return datos[0].get("ticker")
        except Exception:
            pass
        return None

    def _cargar_mapa_tickers(self):
        if self._mapa_exacto is None:
            j = self._get(TICKERS_URL, as_json=True)
            self._mapa_exacto = {_normalizar_nombre(v["title"]): v["ticker"]
                                 for v in j.values()}
            self._cik_por_ticker = {v["ticker"].upper(): str(v["cik_str"]).zfill(10)
                                    for v in j.values()}
        return self._mapa_exacto

    def _ticker_por_nombre(self, nombre: str) -> str | None:
        mapa = self._cargar_mapa_tickers()
        clave = _normalizar_nombre(nombre)
        if clave in mapa:
            return mapa[clave]
        for k, t in mapa.items():
            if clave and (k.startswith(clave) or (len(clave) >= 6 and len(k) >= 6
                          and clave.startswith(k))):
                return t
        return None

    def _resolver_ticker(self, cusip: str, nombre: str) -> str | None:
        cache = self._leer_cache("cusip_tickers") or {}
        if cusip in cache and cache[cusip]:
            return cache[cusip]
        t = self._cusip_a_ticker_openfigi(cusip) or ""
        if not t:
            t = self._ticker_por_nombre(nombre) or ""
        if t:
            cache[cusip] = t
            self._guardar_cache("cusip_tickers", cache)
        return t or None

    # ------------------------------------------------------------------ #
    # 13F — API pública
    # ------------------------------------------------------------------ #
    def holdings_fondo(self, nombre: str, cik: str, solo_recientes: bool = True) -> list[dict]:
        """Última presentación 13F del fondo (la más reciente; si hay enmienda, gana la enmienda)."""
        desde = (datetime.utcnow() - timedelta(days=self.dias_filings)).strftime("%Y-%m-%d")
        filings = self._filings(cik, FORMS_13F, desde if solo_recientes else None)
        if not filings:
            return []

        filing = filings[0]  # la más reciente (reversed en _filings)
        data = self._descargar_infotable(cik, filing)
        if data is None:
            return []

        # Desde el periodo 2023-01-01 el valor va en USD; antes iba en miles de USD
        factor = 1 if filing["report_date"] >= "2023-01-01" else 1000
        holdings = self._parsear_infotable(data, factor)
        for h in holdings:
            h["fondo"] = nombre
            h["fecha_reporte"] = filing["report_date"]
            h["filing_date"] = filing["filing_date"]
            h["ticker"] = self._resolver_ticker(h.get("cusip", ""), h.get("nameOfIssuer", ""))
        return holdings

    def posiciones_todos_fondos(self, solo_recientes: bool = True) -> dict:
        """Agregado por ticker: {ticker: {fondos: {fondo: valor}, n_fondos, valor_total, nombre}}"""
        if self._posiciones is not None:
            return self._posiciones

        agregado: dict[str, dict] = {}
        for nombre, cik in self.fondos.items():
            try:
                holdings = self.holdings_fondo(nombre, cik, solo_recientes)
            except Exception as e:
                print(f"[sec_edgar] AVISO: {nombre}: {e}")
                continue
            for h in holdings:
                if h.get("putCall"):          # puts/calls no cuentan como posición en acción
                    continue
                t = h.get("ticker")
                if not t:
                    continue
                e = agregado.setdefault(t, {"ticker": t, "nombre": h["nameOfIssuer"],
                                            "fondos": {}, "valor_total": 0})
                e["fondos"][nombre] = e["fondos"].get(nombre, 0) + h["valor_usd"]
                e["valor_total"] += h["valor_usd"]

        for e in agregado.values():
            e["n_fondos"] = len(e["fondos"])
            e["lista_fondos"] = sorted(e["fondos"])
        self._posiciones = agregado
        return agregado

    def resumen_fondos(self, min_fondos: int = 2, solo_recientes: bool = True) -> list[dict]:
        """Acciones con consenso de al menos `min_fondos`, ordenadas por fuerza."""
        pos = self.posiciones_todos_fondos(solo_recientes)
        filas = [p for p in pos.values() if p["n_fondos"] >= min_fondos]
        filas.sort(key=lambda p: (p["n_fondos"], p["valor_total"]), reverse=True)
        return filas

    def marcar_fondos(self, candidatos: list[dict]) -> list[dict]:
        """Añade a cada candidato: fondos_institucionales, n_fondos, valor_fondos_usd."""
        pos = self.posiciones_todos_fondos()
        for c in candidatos:
            p = pos.get(str(c.get("ticker", "")).upper())
            c["fondos_institucionales"] = p["lista_fondos"] if p else []
            c["n_fondos"] = p["n_fondos"] if p else 0
            c["valor_fondos_usd"] = p["valor_total"] if p else 0
        return candidatos

    # ------------------------------------------------------------------ #
    # Insiders (Form 4)
    # ------------------------------------------------------------------ #
    def _parsear_form4(self, data: bytes) -> dict:
        root = ET.fromstring(data)

        def txt(ruta: str) -> str | None:
            nodo = root.find(ruta)
            return nodo.text.strip() if nodo is not None and nodo.text else None

        transacciones = []
        for ndt in root.iter():
            if _etiqueta(ndt) != "nonDerivativeTransaction":
                continue

            def v(tag: str):
                nodo = ndt.find(f".//{tag}")
                return nodo.text.strip() if nodo is not None and nodo.text else None

            try:
                transacciones.append({
                    "fecha": v("transactionDate/value"),
                    "codigo": v("transactionCoding/transactionCode"),
                    "acciones": float(v("transactionShares/value") or 0),
                    "precio": float(v("transactionPricePerShare/value") or 0),
                    "adquisicion": (v("transactionAmounts/transactionAcquiredDisposedCode/value") or ""),
                })
            except (TypeError, ValueError):
                continue

        rel = root.find("reportingOwner/reportingOwnerRelationship")
        return {
            "propietario": txt("reportingOwner/reportingOwnerId/reportingOwnerName"),
            "es_director": (rel is not None and (rel.findtext("isDirector") or "").upper() == "1"),
            "es_oficial": (rel is not None and (rel.findtext("isOfficer") or "").upper() == "1"),
            "cargo": (rel.findtext("officerTitle") if rel is not None else None),
            "transacciones": transacciones,
        }

    def actividad_insiders(self, ticker: str, cik: str | None = None) -> dict | None:
        """Resumen de Form 4 de los últimos N días (máx `insiders_max` formularios).
        Cache diario en disco: pedir el mismo ticker dos veces en la misma corrida
        (o entre setup y escaneo del universo) no repite descargas."""
        clave_cache = f"insider_{ticker.upper()}"
        cache = self._leer_cache(clave_cache, solo_hoy=True)
        if cache is not None:
            return cache

        cik10 = cik or (self._cargar_mapa_tickers(), self._cik_por_ticker.get(ticker.upper()))[1]
        if not cik10:
            return None

        desde = (datetime.utcnow() - timedelta(days=self.insiders_dias)).strftime("%Y-%m-%d")
        filings = self._filings(cik10, {"4"}, desde)[: self.insiders_max]

        compras = ventas = 0
        compras_usd = 0.0
        neto_usd = 0.0
        detalle = []
        for f in filings:
            acc = f["accession"].replace("-", "")
            url = f"{WWW_SEC}/Archives/edgar/data/{int(cik10)}/{acc}/{f['primary_doc']}"
            try:
                form4 = self._parsear_form4(self._get(url))
            except Exception:
                continue
            for t in form4["transacciones"]:
                importe = t["acciones"] * t["precio"]
                if t["codigo"] in COD_COMPRA:
                    compras += 1
                    compras_usd += importe
                    neto_usd += importe
                elif t["codigo"] in COD_VENTA:
                    ventas += 1
                    neto_usd -= importe
                detalle.append({**t, "propietario": form4["propietario"],
                                "cargo": form4["cargo"],
                                "filing_date": f["filing_date"]})

        total = compras + ventas
        resumen = {
            "ticker": ticker.upper(),
            "cik": cik10,
            "formularios": len(filings),
            "compras": compras,
            "ventas": ventas,
            "compras_usd": round(compras_usd, 2),
            "neto_usd": round(neto_usd, 2),
            "ratio_compras": round(compras / total, 2) if total else None,
            "ultimo_filing": filings[0]["filing_date"] if filings else None,
            "detalle": detalle,
        }
        self._guardar_cache(clave_cache, resumen)
        return resumen

    def escanear_insiders_universo(self, tickers: list[str],
                                   solo_compras: bool | None = None,
                                   monto_min_usd: float | None = None,
                                   max_nombres: int | None = None) -> list[dict]:
        """Escanea Form 4 de TODA la lista de tickers (universo completo, no solo
        setups) y devuelve los que tienen actividad neta relevante, ordenados por
        fuerza de neto. Criterio: |neto_usd| >= monto_min_usd; con solo_compras=True
        (default del config) además se exige neto positivo (hay más compras que ventas).
        Cada hallazgo trae 'direccion': 'compra' o 'venta' para que main.py redacte.
        Reutiliza el cache diario de actividad_insiders."""
        solo_compras = self.insiders_solo_compras if solo_compras is None else solo_compras
        monto_min = self.insiders_monto_min if monto_min_usd is None else monto_min_usd
        max_nombres = self.insiders_max_nombres if max_nombres is None else max_nombres

        self._cargar_mapa_tickers()
        lista = sorted(set(t.upper() for t in tickers))
        hallazgos: list[dict] = []
        sin_cik = sin_datos = 0

        for i, t in enumerate(lista, start=1):
            if i % 40 == 0 or i == len(lista):
                print(f"[insiders-universo] progreso {i}/{len(lista)} · hallazgos {len(hallazgos)}")
            cik = self._cik_por_ticker.get(t)
            if not cik:
                sin_cik += 1
                continue
            try:
                ins = self.actividad_insiders(t, cik)
            except Exception as e:
                print(f"[insiders-universo] AVISO {t}: {e}")
                sin_datos += 1
                continue
            if not ins:
                sin_datos += 1
                continue
            neto = ins["neto_usd"]
            if abs(neto) < monto_min:
                continue
            if solo_compras and neto <= 0:
                continue
            ins["direccion"] = "compra" if neto > 0 else "venta"
            hallazgos.append(ins)

        hallazgos.sort(key=lambda h: -abs(h["neto_usd"]))
        print(f"[insiders-universo] {len(hallazgos)} tickers con |neto| >= ${monto_min:,.0f} "
              f"(escaneados {len(lista) - sin_cik}/{len(lista)}, sin CIK {sin_cik}, "
              f"sin datos {sin_datos})")
        return hallazgos[:max_nombres]

    def marcar_insiders(self, candidatos: list[dict]) -> list[dict]:
        """Añade a cada candidato el campo 'insiders' con el resumen Form 4."""
        for c in candidatos:
            c["insiders"] = self.actividad_insiders(c.get("ticker", ""), c.get("cik"))
        return candidatos


# --------------------------------------------------------------------------- #
# CLI de prueba
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Test de sec_edgar.py")
    parser.add_argument("config", nargs="?", default="config.json")
    parser.add_argument("--min-fondos", type=int, default=1)
    parser.add_argument("--universo", nargs="*", metavar="TICKER",
                        help="escanear insiders de estos tickers de ejemplo")
    args = parser.parse_args()

    edgar = SecEdgar.desde_config(args.config)
    filas = edgar.resumen_fondos(min_fondos=args.min_fondos)

    print(f"\n=== CONSENSO DE FONDOS (min {args.min_fondos}) ===")
    if not filas:
        print("Sin filings 13F en los últimos"
              f" {edgar.dias_filings} días (recuerda: son trimestrales).")
    for p in filas:
        fondos_str = ", ".join(p["lista_fondos"])
        print(f"{p['ticker']:<6} {p['n_fondos']} fondos | "
              f"${p['valor_total']/1e6:>10.1f}M | {p['nombre'][:28]:<28} | {fondos_str}")

    print("\n=== ACTIVIDAD INSIDERS (ejemplo AAPL) ===")
    ins = edgar.actividad_insiders("AAPL")
    if ins:
        print(f"compras={ins['compras']} ventas={ins['ventas']} "
              f"neto=${ins['neto_usd']/1e6:.2f}M ratio={ins['ratio_compras']}")

    if args.universo:
        print(f"\n=== ESCANEO UNIVERSO (muestra: {', '.join(args.universo)}) ===")
        for h in edgar.escanear_insiders_universo(args.universo):
            print(f"{h['ticker']:<6} {h['direccion']} neto=${h['neto_usd']/1e6:.2f}M "
                  f"(compras={h['compras']} ventas={h['ventas']})")
