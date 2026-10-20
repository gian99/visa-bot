# visa-bot

Revisa cada ~10 min las fechas libres del consulado de Lima en ais.usvisa-info.com
y manda un mensaje a **Slack** si aparece una fecha **anterior** a tu cita actual
(la lee sola del portal en cada inicio de sesión; `CURRENT_APPOINTMENT_DATE` es solo respaldo).
Solo avisa, **no reprograma**. Corre gratis en **GitHub Actions**.

## Cómo corre en GitHub
- Cada 6 h arranca una ejecución que revisa cada ~10 min durante ~5h35m (mantiene la sesión, no re-loguea en cada revisión).
- 3 respuestas vacías seguidas = posible bloqueo → pausa 3 h.
- 3 errores seguidos → aviso de alerta en Slack.

## Configuración (Settings → Secrets and variables → Actions)
**Secrets:** `VISA_EMAIL`, `VISA_PASSWORD`, `SLACK_WEBHOOK_URL`
**Variables:** `SCHEDULE_ID`, `CURRENT_APPOINTMENT_DATE` (opcional, YYYY-MM-DD), `COUNTRY_CODE` (`es-pe`), `CHECK_INTERVAL_MINUTES` (`10`)

Probar: pestaña **Actions → visa-bot → Run workflow** con `test` (mensaje a Slack) o `once` (una revisión).

## Webhook de Slack
https://api.slack.com/apps → *Create New App* → *From scratch* → *Incoming Webhooks* → activar →
*Add New Webhook to Workspace* → elegir canal → copiar la URL.

## Local
```
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env   # y completar
python bot.py --once
```
