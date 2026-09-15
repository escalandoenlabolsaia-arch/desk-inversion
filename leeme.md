Exacto, esa es la idea. Y te aclaro el matiz importante: **en un chat nuevo yo no tengo memoria de esta conversación**. Entonces el flujo futuro sería: abre chat nuevo → me pegás el README (o me decís "leé el README del repo") → pegás el log del error → y tengo todo el contexto para arreglarlo sin volver a empezar de cero. El README es la memoria del sistema, tuya y mía.

Acá va completo. Repo → **Add file** → **Create new file** → nombre: `README.md` → pegá todo esto → **Commit changes**:

````markdown
# Desk de Inversión 📊

Sistema automático de screener bursátil con agentes técnicos que analiza
177 activos diarios y avisa por ntfy solo cuando aparece un setup que cumple
todas las reglas. Corre gratis en GitHub Actions. Silencio = todo bien.

---

## Flujo completo

```
1. UNIVERSO      S&P 500 (Wikipedia, respaldo: dataset GitHub, último recurso:
                 screener Yahoo) → top 15 por sector por LIQUIDEZ real (20 días)
                 + 22 ETFs sectoriales fijos = 177 activos
2. INDICADORES   EMA200, EMA50, RSI(14), MACD(12,26,9), volumen vs 20 días,
                 tendencia HH/HL (máx/mín crecientes en 6 meses, 3 tramos),
                 VWAP anclado al 13/10/2022 (faro de ciclo)
3. AGENTES       Filtro de entrada → 3 señales votan → veredicto
4. NOTIFICACIÓN  ntfy solo con setup completo o sector confirmado,
                 deduplicado 7 días. Resumen semanal los domingos.
5. WATCHDOG      Si la corrida falla, aviso ⚠️ por ntfy con el error.
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `config.json` | **Panel de control**: todos los parámetros. Editar acá, no en el código. |
| `datos.py` | Arma el universo y calcula indicadores. Solo números, no decide. |
| `agentes.py` | Aplica filtros y señales → setups / vigilancia / sectores. |
| `main.py` | Orquestador: redacta mensajes, Groq, ntfy, deduplicación, resumen. |
| `requirements.txt` | Librerías (yfinance, pandas, lxml, requests). |
| `.github/workflows/desk.yml` | El que corre todo: cron + secrets + commit del historial. |
| `enviados.csv` | Historial de avisos (se commitea solo para deduplicar). |

## La estrategia (reglas exactas)

**Filtro de entrada** (quién es evaluable):
- Precio ≤ EMA200 +10% (no demasiado extendido arriba)
- Precio ≥ EMA50 −5% (no roto por debajo)
- Tendencia HH/HL 6 meses: máximos y mínimos de 3 tramos de 2 meses, crecientes

**Las 3 señales** (sobre los que pasan el filtro):
1. RSI < 30 en los últimos 10 días (suelo reciente)
2. Cruce alcista del MACD en los últimos 5 días
3. Volumen ≥ 2× el promedio de 20 días

**Veredictos:**
- 🟢 **Setup** = las 3 señales → alerta ntfy inmediata con análisis IA
- 🟡 **Vigilancia** = 2 señales → solo aparece en el resumen semanal
- 💰 **Sector confirmado** = algún ETF ≥2× volumen + ≥3 acciones del sector ≥2×
- Menos de 2 señales = silencio

**Alerta de ejemplo:**
```
🟢 SETUP COMPRA — NUE (Energy)
Precio: USD 82.30 · −3.0% vs EMA200 · EMA50: −1.2% ✓
RSI 28 (suelo hace 4 días) · MACD cruzó al alza hace 2 días
Volumen: 3.1× promedio (2.1M vs 680K)
Tendencia HH/HL 6m: ✓ · (VWAP-2022 +18%)
[Ver en TradingView](link)
🤖 Análisis: (Groq llama-3.3-70b, tono escéptico, o plantilla local si falla)
```

## Parámetros (config.json)

| Clave | Valor actual | Para qué |
|---|---|---|
| `sectores` | 11 sectores: 15 c/u, Real Estate 5 | Cuántos tickers por sector |
| `etfs` | 2 por sector (XL*, V*) | Doble confirmación sectorial |
| `filtros.market_cap_min_usd` | 10.000M (documental con S&P) | Descarta empresas chicas |
| `filtros.precio_min_usd` | 10 | Anti penny stocks |
| `filtros.volumen_dolares_min` | 50M/día | Liquidez mínima |
| `filtros.excluir` | [] | Tickers prohibidos, ej: ["TSLA"] |
| `volumen_inusual.vigilancia` | 2.0 | Volumen 2× = señal |
| `volumen_inusual.alerta` | 3.0 | Volumen 3× = fuerte |
| `tecnicos.ema200_max_sobre` | 0.10 | Máx +10% sobre EMA200 |
| `tecnicos.ema50_max_debajo` | 0.05 | Máx −5% bajo EMA50 |
| `tecnicos.rsi.ventana_dias` | 10 | Ventana del suelo de RSI |
| `tecnicos.macd.ventana_dias` | 5 | Ventana del cruce MACD |
| `vwap_ancla` | 2022-10-13 | Faro del mínimo del bear market |
| `niveles` | setup 3 / vigilancia 2 | Señales necesarias |
| `resumen_semanal_dia` | sunday | Día del resumen (en inglés) |

**Calibración**: si tras ~1 mes es demasiado silencioso, aflojar por este orden:
1. `rsi.ventana_dias` 10→15 · 2. `macd.ventana_dias` 5→8 · 3. `vigilancia` 2.0→1.8

## Horarios y costos

- Cron: `0 23 * * 0-5` = lunes a viernes + domingo, 23:00 UTC (**20:00 hs Argentina**),
  después del cierre de Wall Street.
- Consumo: ~4-6 min/corrida ≈ USD 1-2/mes del pozo gratis de USD 16 (Linux ×1).
  Ver en GitHub → Settings → Billing → Usage. "Amount due" debe ser $0.00.
- Secrets: `NTFY_TOPIC` y `GROQ_API_KEY` (Settings → Secrets and variables → Actions).

## Si algo falla (diagnóstico en orden)

1. **Me llegó ⚠️ "la corrida falló"** → ir a Actions → abrir el run rojo → leer
   el log del paso "Ejecutar desk". La última línea suele nombrar el problema.
2. **Líneas clave que deben existir en un log sano:**
   - `S&P 500 desde Wikipedia: 503 empresas` (o `dataset GitHub: 50X`)
   - `Universo final: 177 activos (155 acciones + 22 ETFs)`
   - `Silencio...` o `Enviado: X bloque(s) ✅`
3. Si dice `Fuente S&P falló... uso screener de Yahoo` → funcionó con plan B;
   si el universo queda chico (<100 acciones), revisar fuentes.
4. Pegar el log completo en el chat junto con este README.

## Problemas ya resueltos (no volver a pisarlos)

- **Screener Yahoo "Invalid field marketcap"**: los campos internos cambian por
  versión. Filtros siempre en Python, no en la query.
- **`EquityQuery` no soporta `&`**: una sola condición por query, máximo.
- **stockanalysis.com devolvía 404**: su API cambió. Descartado como fuente.
- **Tickers extranjeros (.BA CEDARs, .KS coreanas)**: regex solo-EE.UU.
  `^[A-Z0-9]+(-[A-Z0-9]+)?$` en respaldos; el S&P 500 ya viene limpio.
- **Wikipedia 403**: exige User-Agent de navegador. `pd.read_html` directo falla;
  descargar con `requests` + headers y parsear del texto.
- **YAML del workflow roto**: `run:` debe estar alineado con `env:` (8 espacios).
  Al editar, reemplazar el archivo completo, nunca líneas sueltas.
- **Regla general aprendida**: ante API externa frágil → fuente principal +
  respaldo en cascada + try/except que degrada sin morir.

## Cómo usar este README con Claude (chat nuevo)

1. Abrir chat nuevo y pegar: *"Te pego el README de mi repo desk-inversion"* + este archivo.
2. Pegar el log de Actions del run fallido.
3. Claude tiene el contexto completo: arquitectura, reglas, errores históricos.

## Recordatorio importante

Las señales 🟢 son setups que cumplen criterios técnicos definidos, **no
predicciones ni garantías**. El sistema dice dónde mirar y por qué; la decisión,
el tamaño de posición y el riesgo son del operador. Sin dinero real conectado.
````

Commitealo y quedó todo: **el sistema corriendo solo + el manual vivo dentro del repo**. La próxima vez que algo falle, abrís chat nuevo, me pegás el README y el log, y arrancamos desde el punto exacto donde dejamos. 🚀
