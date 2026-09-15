"""
fundamentos.py — Agente de fundamentales (Fase A).

Cuando un setup técnico dispara 🟢, mira la salud de la empresa:
valuación (P/E, P/B, P/S), deuda/EBITDA, rentabilidad (ROE, margen
operativo) y crecimiento de ventas. Devuelve un veredicto:

  🟢 sólido  ·  🟡 mixto  ·  🟠 débil  ·  ⚪ sin datos

Regla del desk: si los datos no están, el veredicto es "sin datos" y
el aviso sale igual. Este agente enriquece, nunca bloquea.
Todos los límites están en config.json → "fundamentos".
"""

import sys

import yfinance as yf

from datos import cargar_config


# --------------------------------------------------------------- datos
def _info(ticker):
    """Ficha de la empresa vía yfinance, probando ambas interfaces."""
    t = yf.Ticker(ticker)
    try:
        info = t.info
        if isinstance(info, dict) and info:
            return info
    except Exception:
        pass
    try:
        info = t.get_info()
        if isinstance(info, dict) and info:
            return info
    except Exception:
        pass
    return None


def _num(v):
    """Convierte a float si es un número usable; si no, None."""
    try:
        f = float(v)
        return f if f == f else None   # descarta NaN
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- veredicto
def evaluar_fundamentos(ticker, cfg=None):
    """Evalúa los fundamentales de UN ticker. Nunca lanza excepciones:
    ante datos faltantes degrada a 'sin_datos' y el aviso sale igual."""
    if cfg is None:
        cfg = cargar_config()
    p = cfg.get("fundamentos", {})

    vacio = {"ticker": ticker, "nombre": ticker, "sector_fund": "",
             "veredicto": "sin_datos", "emoji": "⚪", "puntaje": None,
             "ok_total": None, "texto": "Sin datos fundamentalistas disponibles"}

    info = _info(ticker)
    if not info:
        print(f"  fundamentos {ticker}: yfinance no devolvió datos")
        return vacio

    pe = _num(info.get("trailingPE"))
    pb = _num(info.get("priceToBook"))
    ps = _num(info.get("priceToSalesTrailing12Months"))
    roe = _num(info.get("returnOnEquity"))
    margen = _num(info.get("operatingMargins"))
    crecimiento = _num(info.get("revenueGrowth"))
    deuda = _num(info.get("totalDebt"))
    ebitda = _num(info.get("ebitda"))
    de = deuda / ebitda if deuda is not None and ebitda is not None and ebitda > 0 else None

    lineas = []    # texto plano: "P/E 18.2 ✓"
    evaluados = [] # True/False por cada métrica con dato

    def agregar(etiqueta, valor, limite, es_techo, formato):
        """es_techo=True → valor <= límite es bueno. False → valor >= límite es bueno."""
        if valor is None:
            lineas.append(f"{etiqueta} n/d")
            return
        ok = (valor <= limite) if es_techo else (valor >= limite)
        lineas.append(f"{etiqueta} {formato.format(valor)} {'✓' if ok else '✗'}")
        evaluados.append(ok)

    agregar("P/E", pe, p.get("pe_max", 40), True, "{:.1f}")
    agregar("P/B", pb, p.get("pb_max", 10), True, "{:.1f}")
    agregar("P/S", ps, p.get("ps_max", 8), True, "{:.1f}")
    agregar("Deuda/EBITDA", de, p.get("deuda_ebitda_max", 4.0), True, "{:.1f}")
    agregar("ROE", roe * 100 if roe is not None else None,
            p.get("roe_min", 8), False, "{:.1f}%")
    agregar("Margen op.", margen * 100 if margen is not None else None,
            p.get("margen_op_min", 5), False, "{:.1f}%")
    agregar("Crecim. ventas", crecimiento * 100 if crecimiento is not None else None,
            p.get("crecimiento_min", 0), False, "{:+.1f}%")

    if not evaluados:
        print(f"  fundamentos {ticker}: sin métricas utilizables")
        return vacio

    ok_n = sum(1 for x in evaluados if x)
    total = len(evaluados)
    puntaje = round(ok_n / total * 100)

    if puntaje >= p.get("aprobado_pct", 70):
        veredicto, emoji = "solido", "🟢"
    elif puntaje >= p.get("debil_pct", 40):
        veredicto, emoji = "mixto", "🟡"
    else:
        veredicto, emoji = "debil", "🟠"

    print(f"  fundamentos {ticker}: {emoji} {veredicto} ({ok_n}/{total})")
    return {
        "ticker": ticker,
        "nombre": info.get("shortName") or info.get("longName") or ticker,
        "sector_fund": info.get("sector") or "",
        "veredicto": veredicto, "emoji": emoji,
        "puntaje": puntaje, "ok_total": f"{ok_n} de {total}",
        "texto": " · ".join(lineas),
    }


# --------------------------------------------------------------- prueba
if __name__ == "__main__":
    lista = sys.argv[1:] or ["AAPL", "JPM"]
    cfg = cargar_config()
    for t in lista:
        r = evaluar_fundamentos(t, cfg)
        print(f"\n{r['emoji']} {r['ticker']} ({r['nombre']}) — {r['veredicto']} "
              f"[{r['ok_total']}] puntaje {r['puntaje']}%")
        print(f"  {r['texto']}")
