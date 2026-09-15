"""
main.py — Orquestador del desk: evalúa, redacta y notifica.

- Setups (3 señales) y sectores confirmados → ntfy inmediato (sin repetir
  el mismo aviso en 7 días).
- Vigilancias (2 señales) → solo aparecen en el resumen semanal.
- Análisis con Groq (gratis) y respaldo de plantilla local.
- Si la corrida falla, avisa por ntfy (watchdog).
"""

import csv
import os
import time
from datetime import date, datetime, timedelta

import requests

from datos import cargar_config, preparar_datos
from agentes import evaluar, pasa_filtros_entrada

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
    """Claves enviadas en los últimos N días: {clave: fecha}."""
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


# --------------------------------------------------------------- análisis IA
def plantilla_analisis(f):
    """Respaldo local: redacta con reglas si Groq no está disponible."""
    partes = []
    if f["rsi_dias"] is not None:
        partes.append(f"RSI {f['rsi']:.0f} con suelo hace {f['rsi_dias']} día(s)")
    if f["macd_dias"] is not None:
        partes.append(f"MACD cruzó al alza hace {f['macd_dias']} día(s)")
    partes.append(f"volumen {f['vol_ratio']}x su promedio de 20 días")
    if f["dist_ema200"] is not None and f["dist_ema200"] < 0:
        riesgo = "Riesgo: sigue bajo la EMA200, rebote temprano y frágil."
    else:
        riesgo = "Riesgo: si pierde la EMA50, el setup queda invalidado."
    return " · ".join(partes) + ". " + riesgo


def analisis_groq(f):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    datos = (
        f"{f['ticker']} ({f['sector']}) — precio USD {f['precio']}, "
        f"{f['dist_ema200'] * 100:+.1f}% vs EMA200, {f['dist_ema50'] * 100:+.1f}% vs EMA50, "
        f"RSI {f['rsi']} (suelo bajo 30 hace {f['rsi_dias']} días), "
        f"cruce alcista del MACD hace {f['macd_dias']} días, "
        f"volumen {f['vol_ratio']}x el promedio de 20 días, "
        f"tendencia de máximos y mínimos crecientes 6 meses: {'sí' if f['hh_hl'] else 'no'}, "
        f"{f['pct_vwap']:+.1f}% sobre el VWAP anclado al mínimo de 2022."
    )
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system",
                     "content": "Sos un analista técnico bursátil escéptico. "
                                "Escribí en español, máximo 2 líneas: interpretá el setup "
                                "y señalá SIEMPRE un riesgo concreto. Sin saludos ni relleno."},
                    {"role": "user", "content": datos},
                ],
                "max_tokens": 150,
                "temperature": 0.4,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  Groq falló ({e}) — uso plantilla local")
        return None


# --------------------------------------------------------------- redacción
def texto_setup(f, analisis):
    l = [f"🟢 **SETUP COMPRA — {f['ticker']} ({f['sector']})**"]
    l.append(f"Precio: USD {f['precio']} · {f['dist_ema200'] * 100:+.1f}% vs EMA200 · "
             f"EMA50: {f['dist_ema50'] * 100:+.1f}% ✓")
    rsi_txt = f"RSI {f['rsi']}"
    if f["rsi_dias"] is not None:
        rsi_txt += f" (suelo hace {f['rsi_dias']}d)"
    macd_txt = "MACD cruzó al alza"
    if f["macd_dias"] is not None:
        macd_txt += f" hace {f['macd_dias']}d"
    l.append(f"{rsi_txt} · {macd_txt}")
    l.append(f"Volumen: {f['vol_ratio']}x promedio "
             f"({f['vol_hoy'] / 1e6:.1f}M vs {f['vol_prom'] / 1e6:.1f}M)")
    vwap = f"{f['pct_vwap']:+.1f}%" if f["pct_vwap"] is not None else "n/d"
    hh = "✓" if f["hh_hl"] else "✗"
    l.append(f"Tendencia HH/HL 6m: {hh} · (VWAP-2022 {vwap})")
    l.append(f"[Ver en TradingView](https://www.tradingview.com/chart/?symbol={f['ticker']})")
    l.append("")
    l.append(f"🤖 **Análisis**: {analisis}")
    return "\n".join(l)


def texto_sector(s):
    l = [f"💰 **DINERO ENTRANDO A {s['sector'].upper()}**"]
    etfs = ", ".join(f"{e['ticker']} ({e['vol_ratio']}x)" for e in s["etfs"])
    l.append(f"ETFs disparados: {etfs}")
    accs = ", ".join(f"{a['ticker']} ({a['vol_ratio']}x)" for a in s["acciones"][:8])
    extra = "…" if len(s["acciones"]) > 8 else ""
    l.append(f"Acciones con volumen alto: {accs}{extra}")
    return "\n".join(l)


def texto_resumen(cfg, filas, res):
    pasan = [f for f in filas if pasa_filtros_entrada(f, cfg)]
    l = ["📋 **RESUMEN SEMANAL — desk de inversión**", "",
         f"Universo: {len(filas)} activos · {len(pasan)} en zona evaluable "
         "(cerca de EMA200, con tendencia sana)",
         f"🟢 Setups hoy: {len(res['setups'])} · 🟡 En vigilancia: {len(res['vigilancia'])}", ""]
    if res["setups"]:
        l.append("**Setups:**")
        for f in res["setups"]:
            l.append(f"· {f['ticker']} ({f['sector']}) USD {f['precio']} · vol {f['vol_ratio']}x")
        l.append("")
    if res["vigilancia"]:
        l.append("**En vigilancia (2 de 3 señales):**")
        for f in res["vigilancia"]:
            l.append(f"· {f['ticker']} ({f['sector']}) · señales: {', '.join(f['senales'])} · "
                     f"vol {f['vol_ratio']}x")
        l.append("")
    if res["sectores"]:
        nombres = ", ".join(s["sector"] for s in res["sectores"])
        l.append(f"**Sectores confirmados:** {nombres}")
        l.append("")
    con_vwap = [f for f in filas if f.get("pct_vwap") is not None]
    if con_vwap:
        con_vwap.sort(key=lambda f: -f["pct_vwap"])
        top = con_vwap[0]
        l.append(f"🌡 Más extendido sobre VWAP-2022: {top['ticker']} {top['pct_vwap']:+.1f}% "
                 "(faro de euforia del ciclo)")
    l.append("")
    l.append("Silencio durante la semana = sin setups bajo tus criterios.")
    return "\n".join(l)


# --------------------------------------------------------------- ntfy
def enviar_ntfy(topic, texto, titulo="Desk de inversión"):
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
                print(f"  intento {intento + 1} falló: {e}")
                time.sleep(5)
        print(f"Parte {i}/{len(partes)}: {'enviada ✅' if ok else 'FALLÓ ❌'}")
        if not ok:
            raise RuntimeError(f"No se pudo enviar la parte {i}")
        time.sleep(2)


# --------------------------------------------------------------- orquestación
def main():
    cfg = cargar_config()
    filas = preparar_datos(cfg)
    res = evaluar(filas, cfg)
    hoy = date.today()

    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        raise RuntimeError("Falta el secret NTFY_TOPIC")

    crear_csv_si_falta(ENVIADOS, ["clave", "fecha"])
    recientes = enviados_recientes(ENVIADOS, dias=7)

    mensajes = []

    # Setups que no se hayan avisado en los últimos 7 días
    for f in res["setups"]:
        clave = f"setup-{f['ticker']}"
        if clave in recientes:
            print(f"  {f['ticker']}: ya avisado esta semana, se omite")
            continue
        analisis = analisis_groq(f) or plantilla_analisis(f)
        mensajes.append(texto_setup(f, analisis))
        agregar_fila(ENVIADOS, [clave, hoy.isoformat()])

    # Sectores confirmados (también con deduplicación semanal)
    for s in res["sectores"]:
        clave = f"sector-{s['sector']}"
        if clave in recientes:
            continue
        mensajes.append(texto_sector(s))
        agregar_fila(ENVIADOS, [clave, hoy.isoformat()])

    # Resumen semanal (el día configurado, se manda siempre)
    dia_resumen = DIAS_SEMANA.get(cfg.get("resumen_semanal_dia", "sunday"), 6)
    if hoy.weekday() == dia_resumen:
        mensajes.append(texto_resumen(cfg, filas, res))

    if mensajes:
        enviar_ntfy(topic, "\n\n---\n\n".join(mensajes))
        print(f"Enviado: {len(mensajes)} bloque(s) ✅")
    else:
        print(f"Sin setups ni sectores nuevos. "
              f"Silencio (vigilancias: {len(res['vigilancia'])}).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        topic = os.environ.get("NTFY_TOPIC")
        if topic:
            try:
                enviar_ntfy(topic, f"⚠️ **Desk de inversión**: la corrida falló.\n`{e}`",
                            titulo="Desk — ERROR")
            except Exception:
                pass
        raise
