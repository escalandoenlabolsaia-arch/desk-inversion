Recibido — devuelto **completo y listo para reemplazar**. Primero, la lista exacta de lo que cambié (para que verifiques que no hay nada sorpresa), después el archivo.

## Las 2 adiciones acordadas (toque 3/3)

1. **Sub-sección nueva** `salidas/estado.json (contrato v1)` — después de la tabla de Archivos: qué es, quién lo consume, cuándo se escribe, qué significa si falta, y la regla de versionado.
2. **Log sano**: agregué la línea `TICKER: ya avisado esta semana, se omite` (la dedup verificada con UNH) y, de paso, las dos líneas nuevas reales del cierre (`estado.json publicado: ...` y su aviso no crítico en el diagnóstico).

## Puesta al día de consistencia (3 cosas del manual que ya mentían)

3. El ejemplo de alerta decía **"Groq llama-3.3-70b"** — el modelo jubilado. Ahora dice "modelo de `ia.modelo` (hoy openai/gpt-oss-120b)". (Justo el tipo de trampa que haría "recordar mal" a un chat futuro.)
4. Decía "cada commit con `on: push` dispara una corrida" — **falso** en el yml actual (cron + botón manual). Corregido.
5. La tabla de Archivos (fila `main.py` y `desk.yml`) y el Flujo (paso 7 ESTADO) ahora mencionan la habilidad nueva.

Todo lo demás está **carácter por carácter igual**. Reemplazá TODO el `README.md` con esto:

````markdown
# Desk de Inversión 📊

Sistema automático de screener bursátil con agentes técnicos que analiza
177 activos diarios y avisa por ntfy solo cuando aparece un setup que cumple
las reglas. Incluye capa institucional (13F de fondos) y escaneo de insiders
(Form 4) de TODO el universo, independiente de los técnicos. Corre gratis en
GitHub Actions. Silencio = todo bien.

---

## Flujo completo

```
1. UNIVERSO      S&P 500 (Wikipedia, respaldo: dataset GitHub, último recurso:
                 screener Yahoo) → top 15 por sector por LIQUIDEZ real (20 días)
                 + 22 ETFs sectoriales fijos = 177 activos
2. INDICADORES   EMA200, EMA50, RSI(14), MACD(12,26,9), volumen vs 20 días
                 (de HOY y de VENTANA), tendencia HH/HL 6 meses,
                 VWAP anclado al 13/10/2022 (faro de ciclo)
3. AGENTES       Filtro de entrada → 3 señales con lógica SECUENCIAL
                 (suelo RSI → cruce MACD después → volumen en ventana)
                 → línea EMBUDO en el log → veredicto
4. SEC EDGAR     13F de 7 fondos (Berkshire, Pershing, Scion, Third Point,
                 Viking, Lone Pine, Coatue): consenso + promoción de vigilancias.
                 Insiders Form 4: de los setups Y de TODO el universo (bloque 🐋).
5. NOTIFICACIÓN  ntfy: setups, sectores, bloque diario de insiders,
                 resumen semanal. Deduplicación (7 días general, 15 días insiders).
6. WATCHDOG      Si la corrida falla, aviso ⚠️ por ntfy con el error.
7. ESTADO        Publica salidas/estado.json (contrato v1) al final de cada
                 corrida: el archivo público que consume el repo hijo.
                 try/except propio: si falla, la corrida sigue igual.
```

## Archivos

| Archivo | Qué hace |
|---|---|
| `config.json` | **Panel de control**: todos los parámetros. Editar acá, no en el código. |
| `datos.py` | Arma el universo y calcula indicadores. **Acá se aplican los umbrales** (RSI<35, cruce MACD, ratios de volumen): convierte series en `rsi_dias`, `macd_dias`, `vol_dias`, `vol_ratio`. Solo números, no decide. |
| `agentes.py` | Decide con esos datos: filtro de entrada, señales (solo pregunta "¿hubo o no?"), embudo, veredictos. |
| `sec_edgar.py` | Capa SEC: 13F de fondos + Form 4 de insiders (setups y universo). Con CLI de prueba propia. |
| `main.py` | Orquestador: redacta mensajes, Groq, ntfy, deduplicación, resumen, **publica estado.json**. **Debe terminar en `raise`** (ver diagnóstico). |
| `requirements.txt` | Librerías (yfinance, pandas, lxml, requests). |
| `.github/workflows/desk.yml` | El que corre todo: cron + secrets + commit del historial y del estado. |
| `.gitignore` | Excluye `.cache_edgar/` y `__pycache__/` del historial. |
| `enviados.csv` | Historial de avisos (se commitea solo para deduplicar). |
| `salidas/estado.json` | **Contrato v1 madre → hijo** (ver sub-sección abajo). Se commitea en cada corrida. |
| `.cache_edgar/` | Cache diario de EDGAR (submissions, Form 4, CUSIPs). Se recrea en cada corrida; **ignorada por git**. |

### salidas/estado.json (contrato v1 madre → hijo)

- **Qué es:** la foto JSON del resultado de cada corrida: `version` (1), `fecha`,
  `corrida_ok`, `embudo`, `setups`, `vigilancia`, `insiders_universo`. Solo datos
  públicos de mercado — **jamás contiene datos de cartera**.
- **Quién lo consume:** el repo hijo (`desk-analista`), que lo descarga por URL
  pública (`raw.githubusercontent.com/<usuario>/desk-inversion/main/salidas/estado.json`).
- **Cuándo se escribe:** al final de CADA corrida (última acción de `main.py`),
  con try/except propio: si falla, la corrida sigue igual.
- **Si la carpeta `salidas/` NO aparece en el repo:** esa corrida no publicó (o no
  se pudo commitear). La corrida puede haber estado verde igual: revisar el log.
- **Regla del contrato:** si el formato cambia algún día, se sube `version` a 2
  manteniendo compatibilidad (un hijo viejo sigue leyendo v1 sin romperse).

## La estrategia (reglas exactas, recalibradas)

**Filtro de entrada** (quién es evaluable):
- Precio ≤ EMA200 +10%
- Precio ≥ EMA50 −8% *(aflojado de −5% para no expulsar al que viene de la caída)*
- Tendencia HH/HL 6 meses: máximos y mínimos de 3 tramos de 2 meses, crecientes

**Las 3 señales, con LÓGICA SECUENCIAL** (sobre los que pasan el filtro):
1. **RSI**: hubo RSI < **35** en los últimos **20 días** (suelo reciente)
2. **MACD**: cruce alcista en los últimos **10 días**. Si hay suelo de RSI,
   el cruce debe ser posterior o el mismo día (`macd_dias <= rsi_dias`):
   pánico → giro → confirmación. Si NO hay suelo, el MACD cuenta solo
   (setup de momentum alternativo).
3. **Volumen**: hubo al menos un día con volumen ≥ **1.5×** el promedio de 20
   días dentro de los últimos **5 días** (`vol_dias`) — no exige que sea HOY.

**Veredictos:**
- 🟢 **Setup** = 3 señales → alerta ntfy inmediata con análisis IA
- 🟡 **Vigilancia** = 2 señales → solo en el resumen semanal
- 💰 **Sector confirmado** = algún ETF ≥2× volumen HOY + ≥3 acciones del sector
  ≥2× HOY (evento del día, usa `vol_ratio`, no la ventana)
- Menos de 2 señales = silencio

**Capa institucional (SEC EDGAR):**
- 13F: último filing disponible de cada fondo (son trimestrales, ~3 meses de edad
  es normal). ≥2 fondos sobre una acción = consenso (resumen semanal) y
  **promoción**: vigilancia + ≥2 fondos = setup. `min_fondos=2`.
- **Insiders de setups**: línea en la alerta si compras ≥ ventas.
- **Insiders de TODO el universo (bloque 🐋 diario)**: `insiders.solo_setups=false`.
  Cualquier acción del universo (pase o no los técnicos) con **compras netas
  ≥ USD 100.000** en 15 días entra al bloque, ordenado por monto, tope 6 nombres,
  deduplicado **15 días** (`insider-TICKER`). Solo código P (compra a mercado);
  ejercicios de opciones (M) y ventas 10b5-1 (S) no disparan compras.
  Por cada nombre: compras/ventas, neto y la mayor compra con nombre y cargo.
- Si EDGAR falla, la corrida sigue sin datos institucionales
  (`AVISO: EDGAR fallo...` / `AVISO: escaneo de insiders fallo...`).

**Alerta de ejemplo:**
```
🟢 SETUP COMPRA — NUE (Energy)
Precio: USD 82.30 · −3.0% vs EMA200 · EMA50: −1.2% ✓
RSI 33 (suelo hace 8d) · MACD cruzó al alza hace 2d
Volumen: 1.2x promedio (2.1M vs 1.8M) — disparo hace 3d
Tendencia HH/HL 6m: ✓ · (VWAP-2022 +18%)
Fondos institucionales: Berkshire Hathaway, Third Point ($140M)
Insiders: 2 compras vs 0 ventas (neto +$4.1M)
[Ver en TradingView](link)
🤖 Análisis: (Groq, modelo de ia.modelo — hoy openai/gpt-oss-120b —
tono escéptico, o plantilla local si falla)
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
| `volumen_inusual.vigilancia` | **1.5** *(antes 2.0)* | Umbral del disparo de volumen |
| `volumen_inusual.alerta` | **2.5** *(antes 3.0)* | Volumen fuerte |
| `volumen_inusual.ventana_dias` | no está en config (default 5 en datos.py) | Ventana del disparo de volumen |
| `tecnicos.ema200_max_sobre` | 0.10 | Máx +10% sobre EMA200 |
| `tecnicos.ema50_max_debajo` | **0.08** *(antes 0.05)* | Máx −8% bajo EMA50 |
| `tecnicos.rsi.sobrevendido` | **35** *(antes 30)* | Umbral del suelo de RSI |
| `tecnicos.rsi.ventana_dias` | **20** *(antes 10)* | Ventana del suelo |
| `tecnicos.macd.ventana_dias` | **10** *(antes 5)* | Ventana del cruce |
| `vwap_ancla` | 2022-10-13 | Faro del mínimo del bear market |
| `niveles` | setup 3 / vigilancia 2 | Señales necesarias |
| `resumen_semanal_dia` | sunday | Día del resumen (en inglés) |
| `insiders.dias` / `max_formularios` | 15 / 8 | Ventana y tope del Form 4 |
| `insiders.solo_setups` | **false** | false = escanea todo el universo (bloque 🐋) |
| `insiders.solo_compras` | true | true = solo informan compras netas |
| `insiders.monto_min_usd` | 100000 | Piso del neto para avisar |
| `insiders.max_nombres` | 6 | Tope de tickers en el bloque 🐋 |
| `insiders.dedup_dias` | 15 | Días sin repetir el mismo ticker |
| `sec_edgar.email` | (el declarado en config.json) | User-Agent exigido por la SEC |
| `ia.modelo` | openai/gpt-oss-120b | Modelo Groq del análisis IA. Si da 404, el modelo dejó de estar disponible para el plan: elegir otro en console.groq.com/docs/models y cambiar SOLO esta línea |
| `sec_edgar.dias_filings` | 21 | Ventana de búsqueda (el 13F usado es siempre el último disponible) |
| `sec_edgar.fondos` | 7 fondos con CIK | Berkshire, Pershing, Scion, Third Point, Viking, Lone Pine, Coatue |
| `fundamentos` | pe_max 40, pb_max 10, etc. | **NO conectado al flujo actual** (sección definida pero sin uso en datos/agentes/main). No editar: no tiene efecto. Será el modelo del scoring del hijo. |

**Calibración con el EMBUDO** (línea que imprime cada corrida):
`EMBUDO: entrada X/155 | rsi Y | rsi+macd Z | +volumen V | setups S | vigilancia W`
- `entrada` chico (<30) → el cuello son EMA/HH-HL, aflojar `ema50_max_debajo`
- `rsi` = 0 → algo rompió el umbral 35, revisar config/código
- `rsi+macd` ≈ `rsi` pero `setups` siempre 0 → el cuello es el volumen:
  bajar `volumen_inusual.vigilancia` (1.5 → 1.3) o ampliar `ventana_dias` (5 → 8)
- Mes tranquilo esperado: setups 0-3/mes, bloque 🐋 0-5 nombres/día.
  Si el bloque 🐋 se inunda (raro con ≥$100K): subir `monto_min_usd` a 250000.

## Horarios y costos

- Cron: `0 23 * * 0-5` = domingo a viernes, 23:00 UTC (**20:00 hs Argentina**),
  después del cierre de Wall Street. Resumen semanal el día configurado.
- Duración sana: **5-10 minutos** (descarga ~525 tickers + 13F + escaneo Form 4
  del universo). El workflow corre por cron + botón manual (`workflow_dispatch`);
  los commits del bot NO disparan corridas nuevas.
- Costo: ~USD 1-3/mes del pozo gratis de USD 16 (Settings → Billing → Usage;
  "Amount due" debe ser $0.00).
- Secrets: `NTFY_TOPIC` y `GROQ_API_KEY`.

## Si algo falla (diagnóstico en orden)

1. **Me llegó ⚠️ "la corrida falló"** → Actions → run rojo → log de "Ejecutar desk".
2. **Líneas de un log sano:**
   - `S&P 500 desde Wikipedia: 503 empresas` (o `dataset GitHub`)
   - `Universo final: 177 activos (155 acciones + 22 ETFs)`
   - `EMBUDO: entrada X/155 | ...` (siempre)
   - `[insiders-universo] progreso 40/155...` y `N tickers con |neto| >= $100,000`
   - `TICKER: ya avisado esta semana, se omite` (dedup semanal funcionando — NO es error)
   - `Silencio...` o `Enviado: X bloque(s)`
   - `estado.json publicado: X setups, Y vigilancias, Z insiders universo` (cierre nuevo)
3. **REGLA DE ORO — corrida verde pero de ~1 segundo sin imprimir nada:**
   el archivo quedó **cortado al copiarlo**. `main.py` debe terminar con
   `if __name__ == "__main__":` y última línea `raise`. Siempre reemplazar
   archivos COMPLETOS, nunca a medias.
4. `AVISO: EDGAR fallo...` → siguió sin capa institucional. No crítico.
5. `AVISO: escaneo de insiders fallo...` → siguió sin bloque 🐋. No crítico.
6. `AVISO: no se pudo publicar estado.json...` → la corrida sigue; el hijo usa
   el último estado válido. No crítico.
7. Corrida verde de 5-10 min pero sin línea EMBUDO → versión vieja de agentes.py.

## Dónde vive cada parámetro (no buscar en el archivo equivocado)

- Umbrales numéricos (35, 20, 10, 1.5...) → se **leen del config** y se
  **aplican en datos.py** (`indicadores()`), que entrega `rsi_dias`, `macd_dias`,
  `vol_dias` ya calculados.
- agentes.py **no conoce los umbrales**: solo pregunta "¿el dato existe?"
  (`rsi_dias is not None`) y aplica la secuencia (`macd_dias <= rsi_dias`).
  Excepción: `ema200_max_sobre`/`ema50_max_debajo` se usan directo en agentes.py.
- Cambiar un valor = editar SOLO config.json. Cambiar la LÓGICA (secuencia,
  condiciones) = editar agentes.py/datos.py.

## Problemas ya resueltos (no volver a pisarlos)

- **Groq 404 en chat/completions**: el modelo dejó de estar disponible para el
  plan (llama-3.3-70b pasó a Enterprise). Los llama-3.x hoy son Enterprise;
  los accesibles self-serve son los openai/gpt-oss-*. Elegir Production, nunca
  Preview. Fix = una línea en ia.modelo (el código ya lo lee del config).
  Los gpt-oss razonan: el código pide reasoning_effort low y recorta a 2 líneas.
- **main.py copiado a medias**: corrida de 1 segundo, verde, no hace nada.
  Verificar última línea = `raise`. Reemplazar archivos completos.
- **Texto IA con "suelo bajo 30" hardcodeado**: al recalibrar a 35, el texto
  de Groq mentía. Resuelto: ahora dice "mínimo reciente" sin número fijo.
- **Dónde están los umbrales**: NO están en agentes.py; datos.py los aplica
  al calcular `rsi_dias`/`macd_dias`/`vol_dias`. No agregar duplicados.
- **Volumen "de hoy" mataba la señal**: con MACD cruzando días después del
  pánico, exigir 2× HOY era lotería. Resuelto con `vol_dias` (ventana de 5 días).
- **Coincidencia simultánea RSI+MACD casi imposible**: el RSI se hunde en el
  pánico y el MACD cruza días después, ya recuperado. Resuelto con secuencia
  lógica (cruce posterior al suelo) + ventanas ampliadas (20/10) + umbral 35.
- **Screener Yahoo "Invalid field marketcap"**: filtros siempre en Python.
- **`EquityQuery` no soporta `&`**: una condición por query.
- **stockanalysis.com 404**: descartado como fuente.
- **Tickers extranjeros**: regex `^[A-Z0-9]+(-[A-Z0-9]+)?$` en respaldos.
- **Wikipedia 403**: User-Agent de navegador + requests + parseo del texto.
- **YAML del workflow roto**: `run:` alineado con `env:` (8 espacios).
- **13F valores USD vs miles**: desde 2023 en USD; antes en miles. Normalizado
  por fecha de reporte.
- **CUSIP → ticker**: OpenFIGI (gratis) + fallback por nombre contra el mapa SEC.
- **`.cache_edgar/` en git**: ignorada vía .gitignore (cache diario, se recrea).
- **Regla general**: API externa frágil → fuente principal + respaldo en
  cascada + try/except que degrada sin morir. Todo escaneo/capa con try/except
  PROPIO para no tumbar la corrida entera.

## Cómo usar este README con Claude (chat nuevo)

1. Chat nuevo: *"Te pego el README de mi repo desk-inversion"* + este archivo.
2. Pegar el log de Actions del run fallido (o decir "corrida verde pero de X segundos").
3. Claude tiene el contexto completo: arquitectura, reglas RECALIBRADAS, capa
   EDGAR + insiders del universo, embudo, errores históricos.

## Recordatorio importante

Las señales 🟢 y el bloque 🐋 son datos que cumplen criterios definidos, **no
predicciones ni garantías**. Las compras de insiders informan, no recomiendan
(los insiders se equivocan y compran por motivos no públicos). El sistema dice
dónde mirar y por qué; la decisión, el tamaño y el riesgo son del operador.
Sin dinero real conectado.
````

**Commit:** mensaje `leeme3: documenta estado.json + dedup semanal en log sano`.

Fijate un detalle extra que aproveché: en la fila `fundamentos` de la tabla actualicé la nota — decía solo "no tiene efecto" y ahora también dice *"Será el modelo del scoring del hijo"*. Esa sección huérfana del config de la madre es la que va a encontrar su hogar en el `analista.py`, como quedó acordado en el leeme4.

Con este commit, **la madre queda oficialmente terminada: 3/3 toques hechos y manual al día**. Siguiente estación, el corazón del hijo: su `main.py` en dos etapas (primera etapa: leer madre + hoja + archivar en `historial`, con botón de prueba; segunda: avisos ntfy y fichas). Cuando hayas commiteado, decime y arrancamos. 🚀
