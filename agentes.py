"""
agentes.py — Convierte números en veredictos.

Filtros de entrada (EMA200, EMA50, HH/HL) deciden quién es evaluable.
Tres señales (RSI, MACD, volumen) suman puntos: 3 = setup, 2 = vigilancia.
Además detecta confirmación sectorial (ETF + acciones con volumen alto).
"""

from collections import Counter


# --------------------------------------------------------------- filtro de entrada
def pasa_filtros_entrada(f, cfg):
    """Capa 1: zona de precio correcta + estructura de tendencia sana."""
    tec = cfg["tecnicos"]
    if f["dist_ema200"] is None or f["dist_ema50"] is None:
        return False
    if f["dist_ema200"] > tec["ema200_max_sobre"]:     # demasiado extendido arriba
        return False
    if f["dist_ema50"] < -tec["ema50_max_debajo"]:     # demasiado roto abajo
        return False
    if not f["hh_hl"]:                                  # sin máximos/mínimos crecientes
        return False
    return True


# --------------------------------------------------------------- señales
def senales(f, cfg):
    """Lista de señales activas: 'rsi', 'macd', 'volumen'."""
    vi = cfg["volumen_inusual"]
    s = []
    if f["rsi_dias"] is not None:                       # RSI <30 en los últimos N días
        s.append("rsi")
    if f["macd_dias"] is not None:                      # cruce alcista en los últimos N días
        s.append("macd")
    if f["vol_ratio"] >= vi["vigilancia"]:              # volumen >= 2x promedio
        s.append("volumen")
    return s


# --------------------------------------------------------------- confirmación sectorial
def confirmaciones_sector(filas, cfg):
    """Sector confirmado: algún ETF del sector con volumen >= etf_min
    Y al menos `acciones_min` acciones del sector también disparadas."""
    conf = cfg["volumen_inusual"]["confirmacion_sector"]
    etf_min, acc_min = conf["etf_min"], conf["acciones_min"]

    por_sector = {}
    for f in filas:
        clave = "etfs" if f["tipo"] == "etf" else "acciones"
        por_sector.setdefault(f["sector"], {"etfs": [], "acciones": []})[clave].append(f)

    resultado = []
    for sector, d in por_sector.items():
        etfs_on = [e for e in d["etfs"] if e["vol_ratio"] >= etf_min]
        if not etfs_on:
            continue
        accs_on = sorted([a for a in d["acciones"] if a["vol_ratio"] >= etf_min],
                         key=lambda a: -a["vol_ratio"])
        if len(accs_on) >= acc_min:
            resultado.append({"sector": sector, "etfs": etfs_on, "acciones": accs_on})
    return resultado


# --------------------------------------------------------------- veredicto
def evaluar(filas, cfg):
    """Aplica filtros y señales. Devuelve setups (🟢), vigilancia (🟡) y sectores."""
    niveles = cfg["niveles"]
    setups, vigilancia = [], []

    for f in filas:
        if not pasa_filtros_entrada(f, cfg):
            continue
        s = senales(f, cfg)
        f["senales"] = s
        if len(s) >= niveles["setup_completo"]:
            setups.append(f)
        elif len(s) >= niveles["vigilancia"]:
            vigilancia.append(f)

    setups.sort(key=lambda f: -f["vol_ratio"])
    vigilancia.sort(key=lambda f: -f["vol_ratio"])
    sectores = confirmaciones_sector(filas, cfg)
    return {"setups": setups, "vigilancia": vigilancia, "sectores": sectores}


# --------------------------------------------------------------- prueba
if __name__ == "__main__":
    from datos import cargar_config, preparar_datos

    cfg = cargar_config()
    filas = preparar_datos(cfg)
    res = evaluar(filas, cfg)

    # --- diagnóstico: cuántos pasan el filtro y cómo se reparten las señales
    pasan = [f for f in filas if pasa_filtros_entrada(f, cfg)]
    print(f"\nFiltro de entrada (EMA200/EMA50/HH-HL): {len(pasan)}/{len(filas)} activos")
    dist = Counter(len(senales(f, cfg)) for f in pasan)
    print(f"Señales por activo: {dict(sorted(dist.items()))}")

    print(f"\nSectores confirmados: {[s['sector'] for s in res['sectores']] or 'ninguno'}")

       print(f"\n🟢 SETUPS ({len(res['setups'])}):")
    for f in res["setups"]:
        print(f"  {f['ticker']:6} {f['sector']:22} USD {f['precio']:>9} "
              f"RSI {f['rsi']} (hace {f['rsi_dias']}d) · MACD (hace {f['macd_dias']}d) · "
              f"vol {f['vol_ratio']}x · VWAP2022 {f['pct_vwap']}%")

    print(f"\n🟡 VIGILANCIA ({len(res['vigilancia'])}):")
    for f in res["vigilancia"]:
        print(f"  {f['ticker']:6} {f['sector']:22} señales={f['senales']} "
              f"vol {f['vol_ratio']}x RSI {f['rsi']}")
