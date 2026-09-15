Primero, tu pregunta directa: **¿el historial quita minutos? Casi nada, y te explico por qué**: en Actions pagás minutos por *tiempo de corrida*, y lo que consume tiempo es la **red** (descargar balances, filings, precios) — no escribir archivos JSON, que es instantáneo y local al runner. El diseño inteligente es: **ficha fundamental pesada solo cuando la empresa ENTRA al seguimiento** (una vez), y después actualización liviana diaria (precio + filings nuevos + señales). Así seguir 20 empresas durante un año cuesta lo mismo que seguir 5. El único costo real es espacio en el repo (~5-10 MB/año, git lo maneja sobrado). Tu idea no solo es viable: para un inversor de largo plazo es **mejor** que lo que había diseñado (watchlist de 30 días) — la permanencia mínima pasa a ser 12 meses con archivo por año que se va cerrando como un cuaderno: `UNH_2025.json`, `UNH_2026.json`...

Acá va el leeme4.md completo, con todo lo de la sesión + tu modificación:

````markdown
# Desk de Inversión — Léeme 4: el HIJO (agente analista) + historial anual

Estado al cierre de esta sesión: la MADRE está verificada funcionando end-to-end.
Este documento registra el diseño aprobado del HIJO (repo nuevo, agente analista
de fundamentals con historial anual por ticker) para retomar mañana sin perder nada.

---

## 1. Estado actual de la MADRE (todo verde)

- Corridas verificadas con log real: universo 177, `EMBUDO: entrada 25-26/155 |
  rsi 7 | rsi+macd 1 | +volumen 5-6 | setups 1 | vigilancia 3`, escaneo insiders
  155/155 limpio (0 hallazgos = filtro funcionando, no falla).
- **Dedup probado en producción**: UNH fue setup, al día siguiente el log dijo
  `UNH: ya avisado esta semana, se omite` — el sistema recuerda. ✅
- Watchdog, ntfy, resumen semanal: OK.

### Pendientes de la madre (para mañana, en orden)
1. **Test Groq** (el usuario lo corre desde su máquina):
   `python -c "import requests;r=requests.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+input('GROQ key: ')},json={'model':'openai/gpt-oss-120b','messages':[{'role':'user','content':'Responde en una sola linea: listo'}],'max_tokens':500,'reasoning_effort':'low'},timeout=30);print('HTTP',r.status_code);print(r.json()['choices'][0]['message']['content'] if r.ok else r.text[:300])"`
   - HTTP 200 = cerrado. 404/400 = probar `openai/gpt-oss-20b`.
   - Contexto: llama-3.3-70b pasó a Enterprise (por eso el 404 original). Los
     accesibles self-serve hoy son los `openai/gpt-oss-*`. Elegir SIEMPRE
     Production, nunca Preview (se discontinúan sin aviso).
   - El modelo se lee de `ia.modelo` en config.json (main.py ya no lo tiene
     hardcodeado). Los gpt-oss razonan: el código pide `reasoning_effort: "low"`
     y recorta local a 2 líneas.
2. Toques de la madre para el hijo (ver sección 5).
3. Leeme3: agregar a "log sano" la línea `TICKER: ya avisado esta semana, se omite`.
   Nota estética (no tocar): el cierre dice "Sin setups" cuando hubo setup
   deduplicado; la info ya está en la línea de dedup.

## 2. El HIJO: qué es

Repo nuevo en la misma cuenta (ej: `desk-analista`). Agente independiente que
TOMA los resultados de la madre y hace el seguimiento fino y de largo plazo que
el desk no hace: fundamentals, filings, tesis por empresa. El usuario es
**inversor de largo plazo**: el diseño entero se piensa en años, no días.

Reglas de oro de la familia (NO negociables):
- Flujo UNIDIRECCIONAL: madre → hijo. El hijo NUNCA escribe en el repo madre.
- El hijo se alimenta de UN SOLO archivo público de la madre (estado.json).
- Cada repo con su propio README de memoria (el ritual de leeme1→4 se replica).
- Un agente a la vez: primero el hijo completo y estable; agentes nuevos
  (auditor de señales, eventos, macro) recién después.

## 3. LA MODIFICACIÓN CLAVE: historial anual por ticker

Requisito del usuario: las empresas que salen del filtro (ej: UNH) se siguen
con un detalle que guarde **historial mínimo de 1 año**, en **un archivo por
ticker por año**, que al cambiar el año se cierra y se abre el siguiente.

### Estructura en el repo del hijo
```
historial/
  UNH_2025.json      <- se cierra al terminar el año, queda inmutable
  UNH_2026.json      <- se abre solo el 1er día hábil de 2026
  NUE_2025.json
watchlist.json       <- qué empresas están en seguimiento y desde cuándo
```

### Contenido de cada entrada diaria por ticker
```json
{
  "fecha": "2025-06-12",
  "precio": 302.4,
  "dist_ema200": -0.031, "dist_ema50": -0.012,
  "rsi": 41.2, "vol_ratio": 0.9,
  "senales": ["rsi", "macd"],
  "n_fondos": 2, "fondos": ["Berkshire Hathaway", "Third Point"],
  "insiders_neto_usd": 4100000,
  "fundamentos": {"pe": 14.2, "pb": 2.1, "roe": 18.5, "deuda_ebitda": 2.8,
                   "margen_op": 6.1, "score": "solido"},
  "proximo_earnings": "2025-07-15",
  "nuevos_filings": ["8-K 2025-06-10"],
  "tesis_1_linea": "Aseguradora con ROE alto comprada en pánico del sector."
}
```
- **Entrada PESADA el primer día** de seguimiento (ficha completa: financials,
  balance, cashflow, 10-K/10-Q, scoring). **Actualización LIVIANA los demás días**
  (precio, señales, filings nuevos del día, earnings). Así el año entero de una
  empresa cuesta casi lo mismo que una semana.
- La fila del día de HOY se reescribe si la corrida es del mismo día (idempotente);
  las de días anteriores nunca se tocan.

### Cierre de año (automático)
El código elige el archivo por la fecha de la corrida. El 1 de enero cada ticker
en seguimiento empieza a escribir en `TICKER_2026.json`; el `TICKER_2025.json`
queda cerrado para siempre = cuaderno histórico consultable. Nada se borra nunca.

### Permanencia y salida del watchlist
- Entra: setup de la madre, insider del bloque 🐋, o vigilancia con ≥2 fondos.
- Permanencia mínima: **12 meses** (decisión de diseño por ser inversor LT).
- Sale: año cumplido + sin señales en 60 días + sin cambios fundamentales.
  (El hijo AVISA antes de dar de baja, el usuario decide. Nada sale en silencio.)

## 4. ¿Cuánto consume? (respondido)

- **Escribir los JSON del historial: ~0 minutos** (escritura local al runner).
- Lo que consume tiempo es la RED. Estrategia ficha-pesada-una-vez +
  actualización liviana = seguir 20-40 empresas durante un año cuesta lo mismo
  que seguir 5 una semana.
- Presupuesto estimado del hijo: ~6-9 min/corrida (~260 min/mes). Madre ~5-8 min.
  Pozo gratis GitHub: 2.000 min/mes. Sobra incluso con otro agente futuro.
- Espacio en repo: ~5-10 MB/año de historial. Git lo maneja sin problema.
- El workflow del hijo debe commitear `historial/` y `watchlist.json` cada corrida
  (mismo patrón que `enviados.csv` en la madre).

## 5. Únicos toques permitidos a la MADRE (30 líneas, aditivos)

1. `main.py`: función `publicar_estado()` que al final de cada corrida escribe
   `salidas/estado.json` con try/except PROPIO (si falla, la corrida sigue):
   ```json
   { "version": 1, "fecha": "...", "corrida_ok": true,
     "embudo": "entrada 25/155 | ...",
     "setups": [ {"ticker","sector","precio","rsi","rsi_dias","macd_dias",
                  "vol_ratio","vol_dias","n_fondos","insiders_neto_usd"} ],
     "vigilancia": [ ...mismo formato menos vol... ],
     "insiders_universo": [ {"ticker","neto_usd","direccion","compras","ventas"} ] }
   ```
   - `"version": 1` es el contrato: si mañana cambia el formato, cambia la
     versión y el hijo viejo sigue leyendo v1 sin romperse.
2. `desk.yml`: agregar `salidas/estado.json` al paso que ya commitea `enviados.csv`.
3. `leeme3`: una línea documentando que `salidas/estado.json` existe.

## 6. Diseño del HIJO (para construir mañana)

- Cron: `30 0 * * 0-6`? NO — `30 0` es medianoche UTC. Correcto: **`0 0 * * 1-6`**
  00:00 UTC (21:00 Argentina) o mejor **`30 0`** = 00:30 UTC (21:30 AR) para
  dar 30 min de colchón sobre la madre (Actions demora los crons a veces).
- Flujo de cada corrida:
  1. Descarga `estado.json` de la madre vía
     `raw.githubusercontent.com/<usuario>/<repo-madre>/main/salidas/estado.json`
     (con reintentos; si la madre no publicó hoy, usa el último válido y AVISA).
  2. Actualiza `watchlist.json` (entradas nuevas, salidas con aviso).
  3. Para cada empresa del watchlist: actualización liviana del día
     (precio/señales ya vienen de la madre; el hijo agrega filings del día y
     próxima earnings). Entrada pesada solo para las nuevas.
  4. Escribe `historial/TICKER_<año>.json` (crea el del año si no existe).
  5. Redacta informes: ficha nueva de empresa, eventos (8-K nuevo, earnings
     en 5 días, cambio de scoring) → ntfy con **topic propio** (secret
     `NTFY_TOPIC_HIJO`, NO reutilizar el de la madre).
  6. Análisis Groq igual que la madre (mismo fix: modelo desde config del hijo).
  7. Watchdog propio (avisa si su propia corrida falla).
- Archivos del hijo: `config.json` (panel propio), `analista.py` (fundamentals
  + scoring), `filings.py` (10-K/10-Q/8-K, reutiliza el patrón de sec_edgar.py
  de la madre COPIADO, no referenciado), `main.py`, `watchlist.json`,
  `historial/`, workflow, README propio.
- El scoring usa LA SECCIÓN `fundamentos` del config de la madre como modelo
  (pe_max 40, pb_max 10, roe_min 8...) — esa sección huérfana encuentra hogar
  en el hijo. Veredictos: solido / mixto / fragil + 2-3 puntos débiles señalados.

## 7. Próximos pasos (mañana)

1. Usuario: test Groq (sección 1) → confirmamos modelo o cambiamos a gpt-oss-20b.
2. Toques a la madre (sección 5): main.py + desk.yml + leeme3, commit por commit.
3. Crear repo hijo `desk-analista` en la misma cuenta.
4. Ritual de siempre: archivo por archivo, commit por commit, README de
   memoria del hijo al final (leeme1 del hijo).
5. Primer run manual del hijo → verificar: lee estado.json de la madre, arma
   watchlist con las empresas de los últimos días (UNH debería entrar), escribe
   el primer historial, ntfy con topic del hijo.
6. Dejar correr 1 semana → revisar juntos tamaño de corrida y ruido de avisos.

## Recordatorio

Igual que la madre: las fichas, los scores y los historiales son datos que
informan, no recomendaciones. Decisión, tamaño y riesgo = del operador.
Sin dinero real conectado.
````

Commitealo como `leeme4.md` (y el marcador de obsoleto en los tres anteriores si no lo hiciste). Mañana retomamos por la sección 7: primero tu test de Groq, después los toques de la madre, y de ahí al repo hijo. Buen día de trabajo hoy — el sistema quedó probado de punta a punta. 🚀
