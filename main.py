"""
main.py - Orquestador del desk: evalua, redacta y notifica.
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


def crear_csv_si_falta(archivo, cabecera):
    if not os.path.exists(archivo):
        with open(archivo, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(cabecera)


def agregar_fila(archivo, fila):
    with open(archivo, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow(fila)


def enviados_recientes(archivo, dias):
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


def crear_edgar(cfg):
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
    )


def promover_con_fondos(res, min_fondos=2):
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
    for f in setups:
        try:
            f["insiders"] = edgar.actividad_insiders(f["ticker"], f.get("cik"))
        except Exception as e:
            print(f"  insiders {f['ticker']}: {e}")
            f["insiders"] = None
    return setups


def texto_insiders(ins):
    if not ins or not ins.get("formularios"):
        return None
    if ins["compras"] and ins["compras"] >= ins["ventas"]:
        signo = "+" if ins["neto_usd"] >= 0 else ""
        return (f"Insiders: {ins['compras']} compras vs {ins['ventas']} ventas "
                f"(neto {signo}${ins['neto_usd'] / 1e6:.1f}M)")
    return None


def plantilla_analisis(f):
    partes = []
    if f["rsi_dias"] is not None:
        partes.append(f"RSI {f['rsi']:.0f} con suelo hace {f['rsi_dias']} dia(s)")
    if f["macd_dias"] is not None:
        partes.append(f"MACD cruzo al alza hace {f['macd_dias']} dia(s)")
    partes.append(f"volumen {f['vol_ratio']}x su promedio de 20 dias")
    if f.get("n_fondos"):
        partes.append(f"{f['n_fondos']} fondos institucionales posicionados")
    if f["dist_ema200"] is not None and f["dist_ema200"] < 0:
        riesgo = "Riesgo: sigue bajo la EMA200, rebote temprano y fragil."
    else:
        riesgo = "Riesgo: si pierde la EMA50, el setup queda invalidado."
    return " - ".join(partes) + ". " + riesgo


def analisis_groq(f):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return None
    datos = (
        f"{f['ticker']} ({f['sector']}) - precio USD {f['precio']}, "
        f"{f['dist_ema200'] * 100:+.1f}% vs EMA200, {f['dist_ema50'] * 100:+.1f}% vs EMA50, "
        f"RSI {f['rsi']} (suelo bajo 30 hace {f['rsi_dias']} dias), "
        f"cruce alcista del MACD hace {f['macd_dias']} dias, "
        f"volumen {f['vol_ratio']}x el promedio de 20 dias, "
        f"tendencia de maximos y minimos crecientes 6 meses: {'si' if f['hh_hl'] else 'no'}, "
        f"{f['pct_vwap']:+.1f}% sobre el VWAP anclado al minimo de 2022."
    )
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
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system",
                     "content": "Sos un analista tecnico bursatil esceptico. "
                                "Escribi en espanol, maximo 2 lineas: interpreta el setup "
                                "y senala SIEMPRE un riesgo concreto. Sin saludos ni relleno."},
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
        print(f"  Groq fallo ({e}) - uso plantilla local")
        return None


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
    l.append(f"Volumen: {f['vol_ratio']}x promedio "
             f"({f['vol_hoy'] / 1e6:.1f}M vs {f['vol_prom'] / 1e6:.1f}M)")
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
    l.append
