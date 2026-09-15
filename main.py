"""
main.py - Orquestador del desk: evalua, redacta y notifica.

- Setups (3 senales) y sectores confirmados -> ntfy inmediato.
- Vigilancias (2 senales) -> solo en el resumen semanal.
- Respaldo institucional (13F): si 2 o mas fondos tienen la accion,
  una vigilancia se promueve a setup.
- Insiders (Form 4): setups finales + escaneo de TODO el universo.
  Con insiders.solo_setups=false (default), cada corrida emite un bloque
  diario con las compras netas relevantes de cualquier accion del universo,
  haya pasado o no el filtro tecnico. Dedup propio de N dias (insiders.dedup_dias).
- Analisis IA via Groq, con el modelo leido del config (ia.modelo).
  Si no hay key, no hay modelo configurado o la API falla -> plantilla local.
- Si la corrida falla, avisa por ntfy (watchdog).
"""

import csv
import os
import time
from datetime import date, datetime, timedelta

import requests

from datos import cargar_config, preparar_datos
from agentes import evaluar, pasa_filtros_entrada
from sec_edgar import SecEdgar

ENVIADOS = "enviados.csv"
DIAS_SEMANA = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}


# --------------------------------------------------------------- csv de enviados
def crear_csv_si_falta(archivo, cabecera):
    if not os.path.exists(archivo):
        with open(archivo, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(cabecera)


def agregar_fila(archivo, fila):
    with open(archivo, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(fila)


def enviados_recientes(archivo, dias):
    """Claves enviadas en los ultimos N dias: {clave: fecha}."""
    recientes = {}
    if not os.path.exists(archivo):
        return recientes
    limite = date.today() - timedelta(days=dias)
    with open(archivo, newline="", encoding="utf-8") as f:
        for fila in list(csv.reader(f))[1:]:
            try:
                clave, fecha = fila[0], date.fromisoformat(fila[1][:10])
                if fecha >= limite:
                    recientes[clave] = fecha
            except (IndexError, ValueError):
                continue
    return recientes


# --------------------------------------------------------------- SEC EDGAR
def crear_edgar(cfg):
    """Construye el cliente SEC desde el config. None si falta la seccion."""
    sec = cfg.get("sec_edgar") or {}
    if not sec.get("email") or not sec.get("fondos"):
        return None
    ins = cfg.get("insiders") or {}
    return SecEdgar(
        email=sec["email"],
        dias_filings=sec.get("dias_filings", 21),
        fondos=sec.get("fondos", {}),
        insiders_dias=ins.get("dias", 15),
        insiders_max=ins.get("max_formularios", 8),
        insiders_solo_compras=ins.get("solo_compras", True),
        insiders_monto_min=ins.get("monto_min_usd", 100000),
        insiders_max_nombres=ins.get("max_nombres", 6),
    )


def promover_con_fondos(res, min_fondos=2):
    """Vigilancia (2 senales) con respaldo institucional se vuelve setup."""
    quedan = []
    for f in res["vigilancia"]:
        if f.get("n_fondos", 0) >= min_fondos:
            f["senales"] = list(f.get("senales", [])) + \
                [f"{f['n_fondos']} fondos institucionales"]
            res["setups"].append(f)
            print(f"  {f['ticker']}: promovida a setup por {f['n_fondos']} fondos")
        else:
            quedan.append(f)
    res["vigilancia"] = quedan
    return res


def enriquecer_insiders(setups, edgar):
    """Form 4 para los setups finales (el cache diario evita descargas repetidas)."""
    for f in setups:
        try:
            f["insiders"] = edgar.actividad_insiders(f["ticker"], f.get("cik"))
        except Exception as e:
            print(f"  insiders {f['ticker']}: {e}")
            f["insiders"] = None
    return setups


def texto_insiders(ins):
    """Linea opcional con la actividad de insiders del setup."""
    if not ins or not ins.get("formularios"):
        return None
    if ins["compras"] and ins["compras"] >= ins["ventas"]:
        signo = "+" if ins["neto_usd"] >= 0 else ""
        return (f"Insiders: {ins['compras']} compras vs {ins['ventas']} ventas "
                f"(neto {signo}${ins['neto_usd'] / 1e6:.1f}M)")
    return None


def texto_insiders_universo(hallazgos, dias):
    """Bloque diario: insiders con compras netas relevantes en TODO el universo,
    independiente de que el ticker pase o no los filtros tecnicos."""
    if not hallazgos:
        return None
    l = [f"INSIDERS COMPRANDO (ultimos {dias} dias)"]
    for h in hallazgos:
        linea = (f"- {h['ticker']}: {h['compras']} compras / {h['ventas']} ventas, "
                 f"neto USD {h['neto_usd'] / 1e6:+.1f}M")
        compras = [d for d in h.get("detalle", []) if d.get("codigo") == "P"]
        if compras:
            mayor = max(compras, key=lambda d: d.get("acciones", 0) * d.get("precio", 0))
            importe = mayor.get("acciones", 0) * mayor.get("precio", 0) / 1e6
            nombre = str(mayor.get("propietario") or "?").title()
            cargo = mayor.get("cargo")
            linea += f" - mayor: {nombre}"
            if cargo:
                linea += f" ({cargo})"
            linea += f" USD {importe:.1f}M"
        l.append(linea)
    return "\n".join(l)


# --------------------------------------------------------------- analisis IA
def plantilla_analisis(f):
    """Respaldo local: redacta con reglas si Groq no esta disponible."""
    partes = []
    if f["rsi_dias"] is not None:
        partes.append(f"RSI {f['rsi']:.0f} con suelo hace {f['rsi_dias']} dia(s)")
    if f["macd_dias"] is not None:
        partes.append(f"MACD cruzo al alza hace {f['macd_dias']} dia(s)")
    vd = f.get("vol_dias")
    if vd == 0:
        partes.append(f"volumen {f['vol_ratio']}x su promedio de 20 dias (disparo hoy)")
    elif vd is not None:
        partes.append(f"confirmacion de volumen en ventana (disparo hace {vd} dia(s), "
                      f"hoy {f['vol_ratio']}x)")
    else:
        partes.append(f"volumen {f['vol_ratio']}x su promedio de 20 dias")
    if f.get("n_fondos"):
        partes.append(f"{f['n_fondos']} fondos institucionales posicionados")
    if f["dist_ema200"] is not None and f["dist_ema200"] < 0:
        riesgo = "Riesgo: sigue bajo la EMA200, rebote temprano y fragil."
    else:
        riesgo = "Riesgo: si pierde la EMA50, el setup queda invalidado."
    return " - ".join(partes) + ". " + riesgo


def _recortar_a_dos_lineas(texto):
    """El prompt pide maximo 2 lineas; si el modelo se pasa, se recorta local."""
    lineas = [l.strip() for l in (texto or "").strip().splitlines() if l.strip()]
    return "\n".join(lineas[:2])


def analisis_groq(f, cfg):
    """Pide el analisis a Groq con el modelo declarado en config (ia.modelo).
    Devuelve None (y cae en plantilla local) si falta la key, falta el modelo
    o la API falla. NOTAS:
    - Los modelos de Groq cambian de disponibilidad: si aparece
      'Groq fallo (404...)', el modelo de config ya no esta disponible para
      el plan de la cuenta (ej: paso a Enterprise). Ver modelos vigentes en
      console.groq.com/docs/models y cambiar SOLO config.json.
    - Los modelos gpt-oss razonan antes de responder: se pide reasoning_effort
      'low' y tope alto de tokens para que quede presupuesto para la respuesta.
    """
    key = os.environ.get("GROQ_API_KEY")
    modelo = (cfg.get("ia") or {}).get("modelo")
    if not key or not modelo:
        return None
    datos = (
        f"{f['ticker']} ({f['sector']}) - precio USD {f['precio']}, "
        f"{f['dist_ema200'] * 100:+.1f}% vs EMA200, {f['dist_ema50'] * 100:+.1f}% vs EMA50, "
        f"RSI {f['rsi']} (minimo reciente hace {f['rsi_dias']} dias), "
        f"cruce alcista del MACD hace {f['macd_dias']} dias, "
        f"volumen {f['vol_ratio']}x el promedio de 20 dias"
    )
    vd = f.get("vol_dias")
    if vd is not None and vd > 0:
        datos += f" (el disparo de volumen fue hace {vd} dias)"
    datos += (f", tendencia de maximos y minimos crecientes 6 meses: "
              f"{'si' if f['hh_hl'] else 'no'}, "
              f"{f['pct_vwap']:+.1f}% sobre el VWAP anclado al minimo de 2022.")
    if f.get("n_fondos"):
        fondos = ", ".join(f["fondos_institucionales"])
        datos += (f" Posicion institucional: {f['n_fondos']} fondos "
                  f"({fondos}) por ${f['valor_fondos_usd'] / 1e6:.0f}M.")
    ins = f.get("insiders")
    if ins and ins.get("formularios"):
        datos += (f" Insiders ultimos dias: {ins['compras']} compras vs "
                  f"{ins['ventas']} ventas, neto ${ins['neto_usd'] / 1e6:+.1f}M.")
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": modelo,
                "messages": [
                    {"role": "system",
                     "content": "Sos un analista tecnico bursatil esceptico. "
                                "Escribi en espanol, maximo 2 lineas: interpreta el setup "
                                "y senala SIEMPRE un riesgo concreto. Sin saludos ni relleno."},
                    {"role": "user", "content": datos},
                ],
                "reasoning_effort": "low",
                "max_tokens": 500,
                "temperature": 0.4,
            },
            timeout=30,
        )
        r.raise_for_status()
        contenido = r.json()["choices"][0]["message"]["content"].strip()
        if not contenido:
            return None
        return _recortar_a_dos_lineas(contenido)
    except Exception as e:
        print(f"  Groq fallo ({e}) - uso plantilla local")
        return None


# --------------------------------------------------------------- redaccion
def texto_setup(f, analisis):
    l = [f"SETUP COMPRA - {f['ticker']} ({f['sector']})"]
    l.append(f"Precio: USD {f['precio']} - {f['dist_ema200'] * 100:+.1f}% vs EMA200 - "
             f"EMA50: {f['dist_ema50'] * 100:+.1f}% OK")
    rsi_txt = f"RSI {f['rsi']}"
    if f["rsi_dias"] is not None:
        rsi_txt += f" (suelo hace {f['rsi_dias']}d)"
    macd_txt = "MACD cruzo al alza"
    if f["macd_dias"] is not None:
        macd_txt += f" hace {f['macd_dias']}d"
    l.append(f"{rsi_txt} - {macd_txt}")
    vol_linea = (f"Volumen: {f['vol_ratio']}x promedio "
                 f"({f['vol_hoy'] / 1e6:.1f}M vs {f['vol_prom'] / 1e6:.1f}M)")
    vd = f.get("vol_dias")
    if vd is not None and vd > 0:
        vol_linea += f" - disparo hace {vd}d"
    l.append(vol_linea)
    vwap = f"{f['pct_vwap']:+.1f}%" if f["pct_vwap"] is not None else "n/d"
    hh = "si" if f["hh_hl"] else "no"
    l.append(f"Tendencia HH/HL 6m: {hh} - (VWAP-2022 {vwap})")
    if f.get("n_fondos"):
        fondos = ", ".join(f["fondos_institucionales"])
        l.append(f"Fondos institucionales: {fondos} (${f['valor_fondos_usd'] / 1e6:.0f}M)")
    ins_txt = texto_insiders(f.get("insiders"))
    if ins_txt:
        l.append(ins_txt)
    l.append(f"[Ver en TradingView](https://www.tradingview.com/chart/?symbol={f['ticker']})")
    l.append("")
    l.append(f"Analisis: {analisis}")
    return "\n".join(l)


def texto_sector(s):
    l = [f"DINERO ENTRANDO A {s['sector'].upper()}"]
    etfs = ", ".join(f"{e['ticker']} ({e['vol_ratio']}x)" for e in s["etfs"])
    l.append(f"ETFs disparados: {etfs}")
    accs = ", ".join(f"{a['ticker']} ({a['vol_ratio']}x)" for a in s["acciones"][:8])
    extra = "..." if len(s["acciones"]) > 8 else ""
    l.append(f"Acciones con volumen alto: {accs}{extra}")
    return "\n".join(l)


def texto_resumen(cfg, filas, res, edgar=None):
    pasan = [f for f in filas if pasa_filtros_entrada(f, cfg)]
    l = ["RESUMEN SEMANAL - desk de inversion", "",
         f"Universo: {len(filas)} activos - {len(pasan)} en zona evaluable "
         "(cerca de EMA200, con tendencia sana)",
         f"Setups hoy: {len(res['setups'])} - En vigilancia: {len(res['vigilancia'])}", ""]
    if res["setups"]:
        l.append("**Setups:**")
        for f in res["setups"]:
            l.append(f"- {f['ticker']} ({f['sector']}) USD {f['precio']} - vol {f['vol_ratio']}x")
        l.append("")
    if res["vigilancia"]:
        l.append("**En vigilancia (2 de 3 senales):**")
        for f in res["vigilancia"]:
            l.append(f"- {f['ticker']} ({f['sector']}) - senales: {', '.join(f['senales'])} - "
                     f"vol {f['vol_ratio']}x")
        l.append("")
    if res["sectores"]:
        nombres = ", ".join(s["sector"] for s in res["sectores"])
        l.append(f"**Sectores confirmados:** {nombres}")
        l.append("")
    if edgar:
        try:
            consenso = edgar.resumen_fondos(min_fondos=2)
        except Exception:
            consenso = []
        if consenso:
            l.append("**Consenso institucional (ultimos 13F):**")
            for p in consenso[:5]:
                l.append(f"- {p['ticker']} - {p['n_fondos']} fondos - "
                         f"${p['valor_total'] / 1e6:.0f}M")
            l.append("")
    con_vwap = [f for f in filas if f.get("pct_vwap") is not None]
    if con_vwap:
        con_vwap.sort(key=lambda f: -f["pct_vwap"])
        top = con_vwap[0]
        l.append(f"Mas extendido sobre VWAP-2022: {top['ticker']} {top['pct_vwap']:+.1f}% "
                 "(faro de euforia del ciclo)")
    l.append("")
    l.append("Silencio durante la semana = sin setups bajo tus criterios.")
    return "\n".join(l)


# --------------------------------------------------------------- ntfy
def enviar_ntfy(topic, texto, titulo="Desk de inversion"):
    MAX = 3800
    partes, resto = [], texto
    while len(resto) > MAX:
        corte = resto.rfind("\n", 0, MAX)
        if corte == -1:
            corte = MAX
        partes.append(resto[:corte])
        resto = resto[corte:].lstrip("\n")
    if resto:
        partes.append(resto)

    for i, parte in enumerate(partes, start=1):
        ok = False
        for intento in range(3):
            try:
                r = requests.post(
                    f"https://ntfy.sh/{topic}",
                    data=parte.encode("utf-8"),
                    headers={"Title": titulo, "Priority": "high",
                             "Tags": "chart", "Markdown": "yes"},
                    timeout=30,
                )
                r.raise_for_status()
                ok = True
                break
            except Exception as e:
                print(f"  intento {intento + 1} fallo: {e}")
                time.sleep(5)
        print(f"Parte {i}/{len(partes)}: {'enviada OK' if ok else 'FALLO'}")
        if not ok:
            raise RuntimeError(f"No se pudo enviar la parte {i}")
        time.sleep(2)


# --------------------------------------------------------------- orquestacion
def main():
    cfg = cargar_config()
    filas = preparar_datos(cfg)
    res = evaluar(filas, cfg)
    hoy = date.today()

    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        raise RuntimeError("Falta el secret NTFY_TOPIC")

    ins_cfg = cfg.get("insiders") or {}
    solo_setups = ins_cfg.get("solo_setups", False)
    dedup_ins_dias = int(ins_cfg.get("dedup_dias", 15))
    ventana_ins_dias = int(ins_cfg.get("dias", 15))

    # --- Capa SEC EDGAR: fondos institucionales + insiders de setups ---
    edgar = crear_edgar(cfg)
    if edgar:
        try:
            edgar.posiciones_todos_fondos(solo_recientes=False)
            filas = edgar.marcar_fondos(filas)
            res = promover_con_fondos(res, min_fondos=2)
            enriquecer_insiders(res["setups"], edgar)
        except Exception as e:
            print(f"  AVISO: EDGAR fallo, se sigue sin datos institucionales: {e}")
            edgar = None

    # --- Escaneo de insiders de TODO el universo (bloque diario) ---
    # try/except propio: si falla solo este paso, el resto de la corrida sigue.
    insiders_universo = []
    if edgar and not solo_setups:
        try:
            tickers_acciones = [f["ticker"] for f in filas if f.get("tipo") == "accion"]
            insiders_universo = edgar.escanear_insiders_universo(tickers_acciones)
        except Exception as e:
            print(f"  AVISO: escaneo de insiders fallo, se sigue sin bloque diario: {e}")

    crear_csv_si_falta(ENVIADOS, ["clave", "fecha"])
    recientes = enviados_recientes(ENVIADOS, dias=7)
    recientes_ins = enviados_recientes(ENVIADOS, dias=dedup_ins_dias)

    mensajes = []

    # Setups que no se hayan avisado en los ultimos 7 dias
    for f in res["setups"]:
        clave = f"setup-{f['ticker']}"
        if clave in recientes:
            print(f"  {f['ticker']}: ya avisado esta semana, se omite")
            continue
        analisis = analisis_groq(f, cfg) or plantilla_analisis(f)
        mensajes.append(texto_setup(f, analisis))
        agregar_fila(ENVIADOS, [clave, hoy.isoformat()])

    # Sectores confirmados (tambien con deduplicacion semanal)
    for s in res["sectores"]:
        clave = f"sector-{s['sector']}"
        if clave in recientes:
            continue
        mensajes.append(texto_sector(s))
        agregar_fila(ENVIADOS, [clave, hoy.isoformat()])

    # Bloque diario de insiders del universo (dedup propio de N dias)
    if insiders_universo:
        nuevos = [h for h in insiders_universo
                  if f"insider-{h['ticker']}" not in recientes_ins]
        for h in nuevos:
            agregar_fila(ENVIADOS, [f"insider-{h['ticker']}", hoy.isoformat()])
        if nuevos:
            mensajes.append(texto_insiders_universo(nuevos, ventana_ins_dias))
        else:
            print(f"  Insiders universo: {len(insiders_universo)} hallazgos, "
                  f"todos ya avisados en los ultimos {dedup_ins_dias} dias")

    # Resumen semanal (el dia configurado, se manda siempre)
    dia_resumen = DIAS_SEMANA.get(cfg.get("resumen_semanal_dia", "sunday"), 6)
    if hoy.weekday() == dia_resumen:
        mensajes.append(texto_resumen(cfg, filas, res, edgar))

    if mensajes:
        enviar_ntfy(topic, "\n\n---\n\n".join(mensajes))
        print(f"Enviado: {len(mensajes)} bloque(s) OK")
    else:
        print(f"Sin setups ni sectores nuevos. "
              f"Silencio (vigilancias: {len(res['vigilancia'])}).")


# --------------------------------------------------------------- arranque
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        topic = os.environ.get("NTFY_TOPIC")
        if topic:
            try:
                enviar_ntfy(topic, f"Desk de inversion: la corrida fallo. {e}",
                            titulo="Desk - ERROR")
            except Exception:
                pass
        raise
