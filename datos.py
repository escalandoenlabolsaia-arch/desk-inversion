"""
datos.py — Arma el universo de activos y calcula todos los indicadores.

Universo: S&P 500 (Wikipedia o dataset espejo en GitHub) con screener de
Yahoo como último recurso. Solo obtiene datos; las decisiones las toman
agentes.py y main.py.
"""

import io
import json
import math
import re
import time
from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf

CONFIG = "config.json"

# Nombres de sector del config -> nombres GICS de las fuentes del S&P 500
MAPA_GICS = {
    "Technology": "Information Technology",
    "Communication Services": "Communication Services",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Energy": "Energy",
    "Financial Services": "Financials",
    "Healthcare": "Health Care",
    "Industrials": "Industrials",
    "Basic Materials": "Materials",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
}


# --------------------------------------------------------------- config
def cargar_config(ruta=CONFIG):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------- universo
CABECERAS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def _parsear_fila_sp500(sym, sec):
    sym = str(sym).strip().upper().replace(".", "-")
    sec = str(sec).strip()
    if sym and sym != "NAN":
        return {"symbol": sym, "sector_gics": sec}
    return None


def _sp500_wikipedia():
    """Fuente 1: tabla del S&P 500 en Wikipedia, descargada como navegador
    (Wikipedia rechaza clientes sin User-Agent válido con error 403)."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    ultimo_error = None
    for intento in range(3):
        try:
            r = requests.get(url, headers=CABECERAS, timeout=30)
            r.raise_for_status()
            tablas = pd.read_html(io.StringIO(r.text))
            tabla = tablas[0]
            tabla.columns = [str(c).strip() for c in tabla.columns]
            col_sym = next(c for c in tabla.columns if c.lower() == "symbol")
            col_sec = next(c for c in tabla.columns if "gics sector" in c.lower())
            salida = []
            for _, f in tabla.iterrows():
                fila = _parsear_fila_sp500(f[col_sym], f[col_sec])
                if fila:
                    salida.append(fila)
            if len(salida) >= 400:
                print(f"S&P 500 desde Wikipedia: {len(salida)} empresas")
                return salida
            ultimo_error = RuntimeError(f"tabla con pocas filas ({len(salida)})")
        except Exception as e:
            ultimo_error = e
            print(f"  Wikipedia intento {intento + 1} falló: {e}")
            time.sleep(4)
    raise RuntimeError(f"Wikipedia agotó intentos ({ultimo_error})")


def _sp500_dataset():
    """Fuente 2: dataset espejo del S&P 500 en GitHub (constituents.csv).
    raw.githubusercontent.com es siempre accesible desde los runners."""
    url = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
           "main/data/constituents.csv")
    r = requests.get(url, headers=CABECERAS, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [str(c).strip() for c in df.columns]
    col_sym = next(c for c in df.columns if c.lower() == "symbol")
    col_sec = next(c for c in df.columns if "sector" in c.lower())
    salida = []
    for _, f in df.iterrows():
        fila = _parsear_fila_sp500(f[col_sym], f[col_sec])
        if fila:
            salida.append(fila)
    if len(salida) < 400:
        raise RuntimeError(f"dataset con pocas filas ({len(salida)})")
    print(f"S&P 500 desde dataset GitHub: {len(salida)} empresas")
    return salida


def _cargar_sp500():
    """Devuelve la lista del S&P 500 (símbolo + sector GICS) probando fuentes."""
    errores = []
    for fuente in (_sp500_wikipedia, _sp500_dataset):
        try:
            return fuente()
        except Exception as e:
            print(f"  fuente {fuente.__name__} falló: {e}")
            errores.append(str(e))
    raise RuntimeError("; ".join(errores))


def _consultar_screener(query, size):
    """(Último recurso) Consulta el screener de Yahoo probando varios órdenes."""
    intentos = ("intradaymarketcap", "eodmarketcap", None)
    for orden in intentos:
        try:
            from yfinance import Screener
            if orden:
                resp = Screener(query=query, size=size,
                                sortField=orden, sortAsc=False).get()
            else:
                resp = Screener(query=query, size=size, sortAsc=False).get()
        except (ImportError, TypeError):
            try:
                if orden:
                    resp = yf.screen(query, size=size, sortField=orden, sortAsc=False)
                else:
                    resp = yf.screen(query, size=size, sortAsc=False)
            except Exception as e:
                print(f"  screener orden={orden}: {e}")
                continue
        except Exception as e:
            print(f"  screener orden={orden}: {e}")
            continue
        filas = resp.get("quotes", []) if isinstance(resp, dict) else []
        if filas:
            return filas
    return []


def _dato(fila, *claves):
    """Primer valor no vacío entre varios nombres posibles de campo."""
    for k in claves:
        v = fila.get(k)
        if v:
            return v
    return 0


def _screen_sector(sector, cant, filtros):
    """(Último recurso) Acciones de EE.UU. del sector vía screener de Yahoo."""
    try:
        from yfinance import EquityQuery as EQ
        q = EQ("eq", ["sector", sector])
    except ImportError:
        print(f"  {sector}: esta versión de yfinance no tiene screener")
        return []

    filas = _consultar_screener(q, size=250)
    solo_us = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)?$")
    excluir = {t.upper() for t in filtros.get("excluir", [])}
    precio_min = filtros.get("precio_min_usd", 0)
    vol_min = filtros.get("volumen_dolares_min", 0)
    cap_min = filtros.get("market_cap_min_usd", 0)

    candidatas = []
    for f in filas:
        sym = (f.get("symbol") or f.get("ticker") or "").upper()
        if not sym or sym in excluir or not solo_us.match(sym):
            continue
        cap = _dato(f, "intradaymarketcap", "intradayMarketCap",
                    "eodmarketcap", "marketCap", "marketcap")
        precio = _dato(f, "eodprice", "eodPrice", "regularMarketPrice")
        volumen = _dato(f, "eodvolume", "eodVolume", "regularMarketVolume")
        if precio_min and precio and precio < precio_min:
            continue
        vol_usd = precio * volumen if precio and volumen else 0
        if vol_min and vol_usd and vol_usd < vol_min:
            continue
        if cap_min and cap and cap < cap_min:
            continue
        candidatas.append((sym, cap))

    candidatas.sort(key=lambda x: -x[1])
    return [s for s, _ in candidatas[:cant]]


def armar_universo(cfg):
    """Devuelve ({ticker: sector} acciones candidatas, {ticker: sector} etfs).
    Se baja TODO el S&P 500; el top de cada sector se elige en preparar_datos
    tras medir la liquidez real de cada empresa."""
    etfs = {}
    for sector, lista in cfg["etfs"].items():
        for t in lista:
            etfs[t] = sector

    excluir = {t.upper().replace(".", "-") for t in cfg["filtros"].get("excluir", [])}
    acciones = {}

    try:
        sp = _cargar_sp500()
        por_gics = {}
        for a in sp:
            if a["symbol"] in excluir:
                continue
            por_gics.setdefault(a["sector_gics"], []).append(a["symbol"])
        for sector_cfg, cant in cfg["sectores"].items():
            gics = MAPA_GICS.get(sector_cfg, sector_cfg)
            lista = por_gics.get(gics, [])
            for t in lista:
                acciones[t] = sector_cfg
            print(f"  {sector_cfg}: {len(lista)} candidatas (S&P)")
    except Exception as e:
        print(f"Fuentes S&P 500 fallaron ({e}) — uso screener de Yahoo")
        for sector_cfg, cant in cfg["sectores"].items():
            try:
                lista = _screen_sector(sector_cfg, cant, cfg["filtros"])
            except Exception as err:
                print(f"  {sector_cfg}: error en respaldo ({err})")
                time.sleep(2)
                continue
            for t in lista:
                acciones[t] = sector_cfg
            print(f"  {sector_cfg}: {len(lista)} candidatas (Yahoo)")
            time.sleep(2)

    print(f"Universo pre-descarga: {len(acciones)} acciones + {len(etfs)} ETFs")
    return acciones, etfs


# --------------------------------------------------------------- indicadores
def _dias_desde(serie_bool, ventana):
    """Días desde la última vez que la condición fue verdadera (0=hoy). None si no ocurrió."""
    rec = serie_bool.iloc[-ventana:]
    pos = [i for i, v in enumerate(rec) if v]
    return (len(rec) - 1 - pos[-1]) if pos else None


def indicadores(sub, cfg):
    """Calcula todos los indicadores de UN ticker. Devuelve dict o None si no hay datos suficientes."""
    sub = sub.dropna(subset=["Close"])
    if len(sub) < 250:
        return None

    tec = cfg["tecnicos"]
    c, v = sub["Close"], sub["Volume"]
    precio = float(c.iloc[-1])

    # EMAs
    ema200 = float(c.ewm(span=200, adjust=False).mean().iloc[-1])
    ema50 = float(c.ewm(span=50, adjust=False).mean().iloc[-1])

    # RSI (método Wilder)
    delta = c.diff()
    ganancia = delta.clip(lower=0).ewm(alpha=1 / tec["rsi"]["periodo"], adjust=False).mean()
    perdida = (-delta.clip(upper=0)).ewm(alpha=1 / tec["rsi"]["periodo"], adjust=False).mean()
    rsi_serie = 100 - 100 / (1 + ganancia / perdida)
    rsi_hoy = float(rsi_serie.iloc[-1])
    if not math.isfinite(rsi_hoy):
        rsi_hoy = None
    rsi_dias = _dias_desde(rsi_serie < tec["rsi"]["sobrevendido"], tec["rsi"]["ventana_dias"])

    # MACD y último cruce alcista
    macd = c.ewm(span=tec["macd"]["rapida"], adjust=False).mean() - c.ewm(
        span=tec["macd"]["lenta"], adjust=False).mean()
    senal = macd.ewm(span=tec["macd"]["senal"], adjust=False).mean()
    dif = macd - senal
    cruce = (dif.shift(1) < 0) & (dif >= 0)
    macd_dias = _dias_desde(cruce, tec["macd"]["ventana_dias"])

    # Volumen: ratio de cada día vs promedio de los 20 días previos.
    # vol_ratio = el de HOY (reporte + confirmación sectorial, evento del día).
    # vol_dias  = días desde el último día con volumen >= umbral de vigilancia
    #             dentro de la ventana (señal "hubo volumen en los últimos N días").
    vi = cfg["volumen_inusual"]
    n = vi["dias_promedio"]
    ventana_vol = int(vi.get("ventana_dias", 5))
    vol_hoy = float(v.iloc[-1])
    vol_prom = float(v.iloc[-(n + 1):-1].mean())
    vol_ratio = round(vol_hoy / vol_prom, 2) if vol_prom > 0 else 0.0
    vol_usd_prom = float((c * v).iloc[-(n + 1):-1].mean())
    prom_rodante = v.rolling(n).mean().shift(1)          # promedio previo, excluye el día propio
    ratio_dia = v / prom_rodante.where(prom_rodante > 0)  # NaN si promedio 0 -> señal False
    vol_dias = _dias_desde(ratio_dia >= vi["vigilancia"], ventana_vol)

    # Tendencia HH/HL: máximos y mínimos crecientes por tramos
    dias = tec["tendencia"]["meses"] * 21
    tramos = tec["tendencia"]["tramos"]
    ventana = c.iloc[-dias:]
    k = len(ventana) // tramos
    maxs = [float(ventana.iloc[i * k:(i + 1) * k].max()) for i in range(tramos)]
    mins = [float(ventana.iloc[i * k:(i + 1) * k].min()) for i in range(tramos)]
    hh_hl = all(maxs[i + 1] > maxs[i] for i in range(tramos - 1)) and \
            all(mins[i + 1] > mins[i] for i in range(tramos - 1))

    # VWAP anclado (faro de largo plazo)
    ancla = pd.Timestamp(cfg["vwap_ancla"])
    desde = sub.loc[ancla:]
    tp = (desde["High"] + desde["Low"] + desde["Close"]) / 3
    vol = desde["Volume"]
    vwap = float((tp * vol).sum() / vol.sum()) if float(vol.sum()) > 0 else None
    pct_vwap = round((precio / vwap - 1) * 100, 1) if vwap else None

    return {
        "precio": round(precio, 2),
        "ema200": round(ema200, 2), "dist_ema200": round(precio / ema200 - 1, 4),
        "ema50": round(ema50, 2), "dist_ema50": round(precio / ema50 - 1, 4),
        "rsi": round(rsi_hoy, 1) if rsi_hoy is not None else None, "rsi_dias": rsi_dias,
        "macd_dias": macd_dias,
        "vol_ratio": vol_ratio, "vol_dias": vol_dias,
        "vol_hoy": int(vol_hoy), "vol_prom": int(vol_prom),
        "vol_usd_prom": int(vol_usd_prom),
        "hh_hl": hh_hl,
        "pct_vwap": pct_vwap,
    }


# --------------------------------------------------------------- orquestación
def preparar_datos(cfg):
    """Universo completo con indicadores. Descarga todo el S&P 500, mide
    liquidez y selecciona el top de cada sector. Devuelve lista de dicts."""
    acciones, etfs = armar_universo(cfg)
    todos = {**acciones, **etfs}
    if not todos:
        raise RuntimeError("Universo vacío: no se obtuvo ningún activo.")

    inicio = (date.fromisoformat(cfg["vwap_ancla"]) - timedelta(days=45)).isoformat()
    print(f"Descargando {len(todos)} tickers desde {inicio}...")
    data = yf.download(list(todos), start=inicio, auto_adjust=True,
                       progress=False, group_by="ticker")

    resultados = []
    for t, sector in todos.items():
        try:
            sub = data[t]
        except KeyError:
            continue
        try:
            ind = indicadores(sub, cfg)
        except Exception:
            continue
        if ind is None:
            continue
        ind["ticker"] = t
        ind["sector"] = sector
        ind["tipo"] = "etf" if t in etfs else "accion"
        resultados.append(ind)

    con_datos = sum(1 for r in resultados if r["tipo"] == "accion")
    print(f"Acciones con indicadores: {con_datos}/{len(acciones)} · ETFs: {len(etfs)}")

    # Selección final: top de cada sector por liquidez media en dólares
    precio_min = cfg["filtros"].get("precio_min_usd", 0)
    vol_min = cfg["filtros"].get("volumen_dolares_min", 0)
    por_sector = {}
    finales = []
    for r in resultados:
        if r["tipo"] == "etf":
            finales.append(r)
        else:
            por_sector.setdefault(r["sector"], []).append(r)

    for sector, cant in cfg["sectores"].items():
        filas = [f for f in por_sector.get(sector, [])
                 if f["precio"] >= precio_min and f["vol_usd_prom"] >= vol_min]
        filas.sort(key=lambda r: -r["vol_usd_prom"])
        elegidos = filas[:cant]
        finales.extend(elegidos)
        nombres = " ".join(r["ticker"] for r in elegidos)
        print(f"  {sector}: top {len(elegidos)} → {nombres}")

    n_acc = sum(1 for f in finales if f["tipo"] == "accion")
    print(f"Universo final: {len(finales)} activos ({n_acc} acciones + {len(etfs)} ETFs)")
    return finales


# --------------------------------------------------------------- prueba
if __name__ == "__main__":
    cfg = cargar_config()
    filas = preparar_datos(cfg)
    for f in filas[:15]:
        print(f"{f['ticker']:6} {f['sector']:22} USD {f['precio']:>9} "
              f"RSI {f['rsi']} vol {f['vol_ratio']}x (vol hace {f['vol_dias']}d) "
              f"liq {f['vol_usd_prom'] / 1e6:.0f}M VWAP2022 {f['pct_vwap']}%")
