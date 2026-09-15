"""
agentes.py — Convierte números en veredictos.

Filtros de entrada (EMA200, EMA50, HH/HL) deciden quién es evaluable.
Tres señales (RSI, MACD, volumen) suman puntos: 3 = setup, 2 = vigilancia.
Secuencia de rebote: cuando hay suelo de RSI, el cruce MACD debe llegar
DESPUÉS (o el mismo día) que ese suelo. El volumen cuenta si apareció en
la ventana reciente (vol_dias), no solo hoy.
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
    """Lista de señales activas: 'rsi', 'macd', 'volumen'.

    Secuencia lógica del rebote: suelo de RSI -> giro del MACD -> volumen.
    - Si hay suelo de RSI, el cruce MACD debe ser posterior o igual día
      (macd_dias <= rsi_dias; menos días = más reciente). Un cruce ANTERIOR
      al suelo queda invalidado: el precio volvió a caer después.
    - Si NO hay suelo de RSI en la ventana, el MACD cuenta solo (setup de
      momentum en lugar de rebote).
    - El volumen cuenta si hubo un día >= umbral de vigilancia dentro de la
      ventana (vol_dias, calculado en datos.py), no únicamente hoy.
    Todo con .get(): si falta un campo (datos.py viejo), la señal simplemente
    no aparece en vez de romper la corrida.
    """
    rsi_dias = f.get("rsi_dias")
    macd_dias = f.get("macd_dias")
    vol_dias = f.get("vol_dias")

    s = []
    if rsi_dias is not None:
        s.append("rsi")
    if macd_dias is not None and (rsi_dias is None or macd_dias <= rsi_dias):
        s.append("macd")
    if vol_dias is not None:
        s.append("volumen")
    return s


# --------------------------------------------------------------- confirmación sectorial
def confirmaciones_sector(filas, cfg):
    """Sector confirmado: algún ETF del sector con volumen >= etf_min
    Y al menos `acciones_min` acciones del sector también disparadas.
    (Evento del día: usa vol_ratio de HOY, no la ventana.)"""
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
    """Aplica filtros y señales. Devuelve setups (🟢), vigilancia (🟡) y sectores.
    Imprime el embudo de diagnóstico para el log de Actions."""
    niveles = cfg["niveles"]
    setups, vigilancia = [], []

    n_entrada = n_rsi = n_rsi_macd = n_vol = 0

    for f in filas:
        if not pasa_filtros_entrada(f, cfg):
            continue
        n_entrada += 1
        s = senales(f, cfg)
        f["senales"] = s
        if "rsi" in s:
            n_rsi += 1
            if "macd" in s:
                n_rsi_macd += 1
        if "volumen" in s:
            n_vol += 1
        if len(s) >= niveles["setup_completo"]:
            setups.append(f)
        elif len(s) >= niveles["vigilancia"]:
            vigilancia.append(f)

    # Embudo: dónde se atasca el sistema. Sale en el log de cada corrida.
    n_acc = sum(1 for f in filas if f.get("tipo", "accion") == "accion")
    print(f"EMBUDO: entrada {n_entrada}/{n_acc} | rsi {n_rsi} | "
          f"rsi+macd {n_rsi_macd} | +volumen {n_vol} | "
          f"setups {len(setups)} | vigilancia {len(vigilancia)}")

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

    print(f"\nSectores confirmados: {[s['sector'] for s in res['sectores']] or 'ninguno'}")

    print(f"\n🟢 SETUPS ({len(res['setups'])}):")
    for f in res["setups"]:
        print(f"  {f['ticker']:6} {f['sector']:22} USD {f['precio']:>9} "
              f"RSI {f['rsi']} (hace {f['rsi_dias']}d) · MACD (hace {f['macd_dias']}d) · "
              f"vol hoy {f['vol_ratio']}x (ventana hace {f['vol_dias']}d) · "
              f"VWAP2022 {f['pct_vwap']}%")

    print(f"\n🟡 VIGILANCIA ({len(res['vigilancia'])}):")
    for f in res["vigilancia"]:
        print(f"  {f['ticker']:6} {f['sector']:22} señales={f['senales']} "
              f"vol {f['vol_ratio']}x RSI {f['rsi']}")
