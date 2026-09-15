¡Muy buena la idea del README como memoria! Pero detecté algo importante antes de que lo commites: **ese README describe el sistema de ANTES** — no incluye toda la capa de SEC EDGAR que acabamos de agregar y probar hoy (`sec_edgar.py`, fondos 13F, promoción de vigilancias, insiders). Si lo guardás así, en un chat nuevo el Claude no va a saber que esa capa existe.

Te lo dejo **actualizado con todo lo de esta sesión**. Reemplazá TODO el contenido de tu `README.md` por esto:

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
4. SEC EDGAR     Último 13F de 7 fondos (Berkshire, Pershing, Scion, Third
                 Point, Viking, Lone Pine, Coatue). Vigilancia con ≥2 fondos
                 se PROMUEVE a setup. Form 4 de insiders solo para setups.
5. NOTIFICACIÓN  ntfy solo con setup completo o sector confirmado,
                 deduplicado 7 días. Resumen semanal los domingos.
6. WATCHDOG      Si la corrida falla, aviso ⚠️ por ntfy con el error.
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `config.json` | **Panel de control**: todos los parámetros. Editar acá, no en el código. |
| `datos.py` | Arma el universo y calcula indicadores. Solo números, no decide. |
| `agentes.py` | Aplica filtros y señales → setups / vigilancia / sectores. |
| `sec_edgar.py` | Capa SEC: 13F de fondos (consenso institucional) + Form 4 (insiders). |
| `main.py` | Orquestador: redacta mensajes, Groq, ntfy, deduplicación, resumen. **Debe terminar en `raise`** (ver diagnóstico). |
| `requirements.txt` | Librerías (yfinance, pandas, lxml, requests). |
| `.github/workflows/desk.yml` | El que corre todo: cron + secrets + commit del historial. |
| `enviados.csv` | Historial de avisos (se commitea solo para deduplicar). |
| `.cache_edgar/` | Cache local de EDGAR. Se recrea en cada corrida, no se usa entre corridas. |

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

**Capa institucional (SEC EDGAR, agregada después):**
- Se descarga el **último 13F disponible** de cada fondo (aunque tenga ~3 meses:
  los 13F son trimestrales, con ventana de 21 días quedaría vacío 9 meses al año).
- Consenso: acción en ≥2 fondos → sección "Consenso institucional" del resumen semanal.
- **Promoción**: vigilancia (2 señales) + ≥2 fondos posicionados = se convierte en
  setup (2 señales + respaldo institucional = 3 niveles). Umbral: `min_fondos=2`.
- **Insiders (Form 4)**: últimos 15 días, máx 8 formularios, SOLO para setups
  finales (evita cientos de peticiones). Se muestra solo si compras ≥ ventas.
- Si EDGAR falla (SEC caída, rate limit), la corrida sigue sin datos
  institucionales: en el log aparece `AVISO: EDGAR fallo...`.

**Alerta de ejemplo:**
```
🟢 SETUP COMPRA — NUE (Energy)
Precio: USD 82.30 · −3.0% vs EMA200 · EMA50: −1.2% ✓
RSI 28 (suelo hace 4 días) · MACD cruzó al alza hace 2 días
Volumen: 3.1× promedio (2.1M vs 680K)
Tendencia HH/HL 6m: ✓ · (VWAP-2022 +18%)
Fondos institucionales: Berkshire Hathaway, Third Point ($140M)
Insiders: 2 compras vs 0 ventas (neto +$4.1M)
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
| `sec_edgar.email` | escalandoenlabolsaia@gmail.com | User-Agent exigido por la SEC |
| `sec_edgar.dias_filings` | 21 | Ventana 13F recientes (usa el último disponible igual) |
| `sec_edgar.fondos` | 7 fondos con CIK | Berkshire, Pershing, Scion, Third Point, Viking, Lone Pine, Coatue |
| `insiders.dias` / `max_formularios` | 15 / 8 | Ventana y tope del Form 4 |

**Calibración**: si tras ~1 mes es demasiado silencioso, aflojar por este orden:
1. `rsi.ventana_dias` 10→15 · 2. `macd.ventana_dias` 5→8 · 3. `vigilancia` 2.0→1.8

## Horarios y costos

- Cron: `0 23 * * 0-5` = domingo a viernes, 23:00 UTC (**20:00 hs Argentina**),
  después del cierre de Wall Street. El resumen semanal sale el día configurado.
- Duración sana de una corrida: **2 a 6 minutos** (descarga 525 tickers + EDGAR).
- Secrets: `NTFY_TOPIC` y `GROQ_API_KEY` (Settings → Secrets and variables → Actions).

## Si algo falla (diagnóstico en orden)

1. **Me llegó ⚠️ "la corrida falló"** → Actions → run rojo → log del paso
   "Ejecutar desk". La última línea suele nombrar el problema.
2. **Líneas clave de un log sano:**
   - `S&P 500 desde Wikipedia: 503 empresas`
   - `Universo final: 177 activos (155 acciones + 22 ETFs)`
   - `Silencio...` o `Enviado: X bloque(s)`
3. **REGLA DE ORO — corrida en verde pero sospechosa:** si el paso
   "python3 main.py" dura ~1 segundo y **no imprime nada**, el script no corrió:
   casi seguro el archivo quedó **cortado al copiarlo**. Verificación rápida:
   `main.py` debe TERMINAR con el bloque `if __name__ == "__main__":` y su última
   línea debe ser `raise`. Solución: reemplazar el archivo completo, nunca a medias.
4. Si dice `AVISO: EDGAR fallo...` → la capa institucional falló pero el resto
   funcionó. No es crítico; revisar conexión con SEC o el email del config.
5. Si dice `fuente S&P falló... uso screener de Yahoo` → funcionó el plan B;
   si el universo queda chico (<100 acciones), revisar fuentes.
6. **Cómo leer logs**: en Actions, expandir el paso con clic. Si no aparece nada,
   engranaje ⚙️ → "Download log archive" → abrir el .txt del paso.

## Problemas ya resueltos (no volver a pisarlos)

- **main.py copiado a medias** (sin el bloque final `if __name__`): la corrida
  dura 1 segundo, da ✅ verde y NO HACE NADA. Detectado y resuelto reemplazando
  el archivo completo. Verificar siempre que la última línea sea `raise`.
- **Screener Yahoo "Invalid field marketcap"**: los campos internos cambian por
  versión. Filtros siempre en Python, no en la query.
- **`EquityQuery` no soporta `&`**: una sola condición por query, máximo.
- **stockanalysis.com devolvía 404**: su API cambió. Descartado como fuente.
- **Tickers extranjeros (.BA CEDARs, .KS coreanas)**: regex solo-EE.UU.
  `^[A-Z0-9]+(-[A-Z0-9]+)?$` en respaldos; el S&P 500 ya viene limpio.
- **Wikipedia 403**: exige User-Agent de navegador. Descargar con `requests`
  + headers y parsear del texto.
- **YAML del workflow roto**: `run:` debe estar alineado con `env:` (8 espacios).
  Al editar, reemplazar el archivo completo, nunca líneas sueltas.
- **13F valores en USD vs miles**: desde 2023 van en USD; antes en miles.
  `sec_edgar.py` normaliza según la fecha del reporte.
- **CUSIP → ticker**: los 13F no traen ticker. Se resuelve con OpenFIGI
  (gratis, sin API key) con fallback por nombre contra el mapa oficial de la SEC.
- **Regla general aprendida**: ante API externa frágil → fuente principal +
  respaldo en cascada + try/except que degrada sin morir.

## Cómo usar este README con Claude (chat nuevo)

1. Abrir chat nuevo y pegar: *"Te pego el README de mi repo desk-inversion"* + este archivo.
2. Pegar el log de Actions del run fallido (o decir "corrida verde pero de X segundos").
3. Claude tiene el contexto completo: arquitectura, reglas, capa EDGAR, errores históricos.

## Recordatorio importante

Las señales 🟢 son setups que cumplen criterios técnicos definidos, **no
predicciones ni garantías**. El sistema dice dónde mirar y por qué; la decisión,
el tamaño de posición y el riesgo son del operador. Sin dinero real conectado.
````

**Pasos para guardarlo:**

1. Repo → abrí `README.md` → lápiz ✏️ → borrá todo → pegá esto → **Commit changes**
2. Listo. El sistema quedó cerrado y documentado:

✅ Código corriendo solo (cron lun-vie + domingo, 20:00 hs Argentina)
✅ Capa de fondos 13F e insiders funcionando
✅ Watchdog que te avisa si algo se rompe
✅ Manual vivo dentro del repo con todos los errores aprendidos

La próxima vez que algo falle: chat nuevo → pegás el README → pegás el log → y arrancamos desde donde dejamos, sin volver a empezar de cero. 🚀
