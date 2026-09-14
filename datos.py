"""
datos.py — Arma el universo de activos y calcula todos los indicadores.

Solo obtiene datos y números. Las decisiones las toman agentes.py y main.py.
"""

import json
import math
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

CONFIG = "config.json"


# --------------------------------------------------------------- config
def cargar_config(ruta=CONFIG):
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------- universo
def _consultar_screener(query, size):
    """Consulta el screener probando varios criterios de orden hasta que uno
    funcione (los nombres cambian según la versión de yfinance)."""
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
    """Top `cant` acciones del sector por market cap. Se pide a Yahoo solo el
    sector (una condición: máxima compatibilidad); cap, precio y volumen se
    filtran acá en Python, con los nombres de campo que vengan."""
    try:
        from yfinance import EquityQuery as EQ
        q = EQ("eq", ["sector", sector])
    except ImportError:
        print(f"  {sector}: esta versión de yfinance no tiene screener")
        return []

    filas = _consultar_screener(q, size=250)

    excluir = {t.upper() for t in filtros.get("excluir", [])}
    cap_min = filtros.get("market_cap_min_usd", 0)
    precio_min = filtros.get("precio_min_usd", 0)
    vol_min = filtros.get("volumen_dolares_min", 0)

    candidatas = []
    for f in filas:
        sym = (f.get("symbol") or f.get("ticker") or "").upper()
        if not sym or sym in excluir:
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

    candidatas.sort(key=lambda x: -x[1])          # más grandes primero
    return [s for s, _ in candidatas[:cant]]


def armar_universo(cfg):
    """Devuelve ({ticker: sector} acciones, {ticker: sector} etfs)."""
    acciones, etfs = {}, {}
    for sector, cant in cfg["sectores"].items():
        try:
            lista = _screen_sector(sector, cant, cfg["filtros"])
        except Exception as e:
            print(f"Screener {sector}: error ({e}) — sin datos esta corrida")
            continue
        for t in lista:
            acciones[t] = sector
        print(f"  {sector}: {len(lista)} acciones")
    for sector, lista in cfg["etfs"].items():
        for t in lista:
            etfs[t] = sector
    print(f"Universo: {len(acciones)} acciones + {len(etfs)} ETFs")
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

    # Volumen: hoy vs promedio de los N días anteriores
    n = cfg["volumen_inusual"]["dias_promedio"]
    vol_hoy = float(v.iloc[-1])
    vol_prom = float(v.iloc[-(n + 1):-1].mean())
    vol_ratio = round(vol_hoy / vol_prom, 2) if vol_prom > 0 else 0.0

    # Tendencia HH/HL: últimos meses divididos en tramos con máx y min crecientes
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
        "vol_ratio": vol_ratio, "vol_hoy": int(vol_hoy), "vol_prom": int(vol_prom),
        "hh_hl": hh_hl,
        "pct_vwap": pct_vwap,
    }


# --------------------------------------------------------------- orquestación
def preparar_datos(cfg):
    """Universo completo con indicadores. Devuelve lista de dicts listos para los agentes."""
    acciones, etfs = armar_universo(cfg)
    todos = {**acciones, **etfs}
    if not todos:
        raise RuntimeError("Universo vacío: el screener no devolvió nada.")

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

    print(f"Con indicadores completos: {len(resultados)}/{len(todos)}")
    return resultados


# --------------------------------------------------------------- prueba
if __name__ == "__main__":
    cfg = cargar_config()
    filas = preparar_datos(cfg)
    for f in filas[:10]:
        print(f"{f['ticker']:6} {f['sector']:22} USD {f['precio']:>9} "
              f"RSI {f['rsi']} vol {f['vol_ratio']}x VWAP2022 {f['pct_vwap']}%")
