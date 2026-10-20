"""Monitor de citas de visa USA (ais.usvisa-info.com) - solo avisa.

Inicia sesion, consulta las fechas disponibles del consulado y manda un
mensaje a Slack si hay alguna anterior a tu cita actual. No reprograma nada.

Uso:
    python bot.py          # corre en bucle (hasta MAX_RUNTIME_MINUTES si esta definido)
    python bot.py --once   # una sola revision
    python bot.py --test   # solo envia un mensaje de prueba a Slack
    python bot.py --report # ademas manda a Slack el estado de la primera revision
    python bot.py --show   # muestra el navegador (debug local)
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from playwright.sync_api import Page, sync_playwright

load_dotenv(Path(__file__).with_name(".env"))

EMAIL = os.environ["VISA_EMAIL"]
PASSWORD = os.environ["VISA_PASSWORD"]
COUNTRY = os.getenv("COUNTRY_CODE") or "es-pe"
SCHEDULE_ID = os.environ["SCHEDULE_ID"]
# Respaldo: solo se usa si no se puede leer la cita actual del portal.
FALLBACK_DATE = os.getenv("CURRENT_APPOINTMENT_DATE") or ""
INTERVAL_MIN = float(os.getenv("CHECK_INTERVAL_MINUTES") or "10")
MAX_RUNTIME_MIN = float(os.getenv("MAX_RUNTIME_MINUTES") or "0")  # 0 = sin limite
SLACK_WEBHOOK = (os.getenv("SLACK_WEBHOOK_URL") or "").strip()

BASE = f"https://ais.usvisa-info.com/{COUNTRY}/niv"
SIGN_IN_URL = f"{BASE}/users/sign_in"
APPOINTMENT_URL = f"{BASE}/schedule/{SCHEDULE_ID}/appointment?confirmed_limit_message=1&commit=Continue"

# El sitio rechaza (403) el user-agent "HeadlessChrome", usamos uno normal.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

# Si la API devuelve lista vacia varias veces seguidas, suele ser un bloqueo
# temporal por exceso de consultas: en ese caso esperamos mucho mas.
EMPTY_LIMIT = 3
MONTHS_ES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], start=1)}
MONTHS_ES["setiembre"] = 9
BACKOFF_HOURS = 3
# Si vuelve a parecer bloqueado tras la pausa, o hay demasiados errores seguidos,
# se detiene la ejecucion para no empeorar el bloqueo.
MAX_BLOCKS = 2
MAX_ERRORS = 5

# Enlace a la ejecucion actual de GitHub Actions (vacio si se corre localmente)
RUN_URL = (f"{os.getenv('GITHUB_SERVER_URL')}/{os.getenv('GITHUB_REPOSITORY')}"
           f"/actions/runs/{os.getenv('GITHUB_RUN_ID')}" if os.getenv("GITHUB_RUN_ID") else "")

log = logging.getLogger("visa-bot")
START = time.monotonic()


class SessionExpired(Exception):
    pass


class LoginFailed(Exception):
    pass


def notify_stop(reason: str) -> None:
    link = f"\nRun: {RUN_URL}" if RUN_URL else ""
    notify(f":octagonal_sign: *Visa bot stopped*\n{reason}{link}")


def notify(text: str) -> None:
    log.info("AVISO: %s", text)
    if not SLACK_WEBHOOK:
        log.warning("SLACK_WEBHOOK_URL no configurado; no se envio el aviso.")
        return
    try:
        requests.post(SLACK_WEBHOOK, json={"text": text}, timeout=15).raise_for_status()
    except requests.RequestException as e:
        log.error("No se pudo enviar a Slack: %s", e)


def read_current_appointment(page: Page) -> date:
    """Lee la cita actual de la pagina de grupos (a la que lleva el login)."""
    for el in page.locator("p.consular-appt").all():
        if SCHEDULE_ID not in (el.inner_html() or ""):
            continue
        m = re.search(r"(\d{1,2}) (\w+), (\d{4})", el.inner_text())
        if m and m.group(2).lower() in MONTHS_ES:
            return date(int(m.group(3)), MONTHS_ES[m.group(2).lower()], int(m.group(1)))
    if FALLBACK_DATE:
        log.warning("No se pudo leer la cita del portal; uso CURRENT_APPOINTMENT_DATE=%s", FALLBACK_DATE)
        return date.fromisoformat(FALLBACK_DATE)
    raise RuntimeError("No se pudo leer la cita actual del portal")


def login(page: Page) -> tuple[str, date]:
    """Inicia sesion y devuelve (facility_id del consulado, fecha de la cita actual)."""
    log.info("Iniciando sesion...")
    # Empezar siempre con una sesion limpia, aunque se este reintentando
    page.context.clear_cookies()
    page.goto(SIGN_IN_URL, wait_until="domcontentloaded")
    page.fill("#user_email", EMAIL)
    page.fill("#user_password", PASSWORD)
    page.check("#policy_confirmed", force=True)
    page.click("input[name=commit]")
    page.wait_for_load_state("networkidle")
    if "sign_in" in page.url:
        raise LoginFailed("Login fallido: revisa usuario/clave")
    current = read_current_appointment(page)

    page.goto(APPOINTMENT_URL, wait_until="domcontentloaded")
    facility = page.locator("#appointments_consulate_appointment_facility_id")
    facility.wait_for(state="attached", timeout=30_000)
    facility_id = facility.input_value()
    # El servidor corta consultas hechas justo despues de cargar la pagina
    page.wait_for_timeout(random.uniform(4, 7) * 1000)
    log.info("Sesion iniciada. Consulado: %s (id %s). Cita actual: %s",
             facility.locator("option:checked").inner_text().strip(), facility_id, current)
    return facility_id, current


def fetch_dates(page: Page, facility_id: str) -> list[date]:
    url = f"{BASE}/schedule/{SCHEDULE_ID}/appointment/days/{facility_id}.json?appointments[expedite]=false"
    # Peticion directa con las cookies de la sesion (no depende del estado de la pagina)
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": APPOINTMENT_URL,
    }
    for attempt in range(3):
        try:
            res = page.context.request.get(url, headers=headers)
            break
        except Exception as e:  # noqa: BLE001  (p. ej. "socket hang up")
            if attempt == 2:
                raise
            log.info("Corte de red (%s); reintentando...", str(e).splitlines()[0])
            time.sleep(5 * (attempt + 1))
    text = res.text()
    if res.status in (401, 403) or text.lstrip().startswith("<"):
        raise SessionExpired(f"HTTP {res.status}")
    data = json.loads(text)
    if isinstance(data, dict) and data.get("error"):
        raise SessionExpired(data["error"])
    return sorted(date.fromisoformat(d["date"]) for d in data)


def wait(minutes: float) -> bool:
    """Duerme; devuelve False si ya no queda tiempo de ejecucion para esperar."""
    if MAX_RUNTIME_MIN and (time.monotonic() - START) / 60 + minutes >= MAX_RUNTIME_MIN:
        log.info("Se alcanzo el tiempo maximo de ejecucion. Fin.")
        return False
    log.info("Proxima revision en %.1f min (%s)", minutes,
             datetime.fromtimestamp(time.time() + minutes * 60).strftime("%H:%M"))
    time.sleep(minutes * 60)
    return True


def run(once: bool, headless: bool, report: bool) -> int:
    best_notified: date | None = None
    empty_streak = 0
    errors = 0
    relogins = 0
    blocks = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(locale="es-PE", user_agent=USER_AGENT)
        facility_id = None

        while True:
            try:
                if facility_id is None:
                    facility_id, current_date = login(page)
                dates = fetch_dates(page, facility_id)
            except LoginFailed as e:
                # No reintentar: varios intentos fallidos pueden bloquear la cuenta
                log.error("%s. Deteniendo.", e)
                notify_stop("Login failed: wrong email or password. Not retrying, to avoid locking "
                            "the account.\nUpdate the `VISA_PASSWORD` secret, or disable the workflow "
                            "until you do (scheduled runs will try again).")
                return 1
            except Exception as e:  # noqa: BLE001
                facility_id = None
                if relogins == 0:
                    relogins += 1
                    log.info("Fallo la consulta (%s); se volvera a iniciar sesion.", e)
                    continue
                errors += 1
                log.error("Error en la revision: %s", e)
                first_line = str(e).splitlines()[0]
                if errors == 3:
                    notify(f":warning: Visa bot: 3 checks failed in a row, will keep retrying. "
                           f"Last error: {first_line}")
                if errors >= MAX_ERRORS:
                    notify_stop(f"{MAX_ERRORS} checks failed in a row. Last error: {first_line}\n"
                                "The next scheduled run will try again.")
                    return 1
                if once:
                    notify(f":warning: *Visa bot: check failed*\n{str(e).splitlines()[0]}")
                    return 1
                if not wait(INTERVAL_MIN * 2):
                    return 0
                continue

            errors = relogins = 0
            if not dates:
                empty_streak += 1
                log.warning("Sin fechas disponibles (%d seguidas).", empty_streak)
                if report:
                    notify(":large_yellow_circle: *Visa bot running* - the consulate shows no open dates "
                           f"right now.\nYour current appointment: {current_date:%b %d, %Y}")
            else:
                empty_streak = blocks = 0
                earliest = dates[0]
                earlier = [d for d in dates if d < current_date]
                log.info("Fecha mas proxima: %s | %d fechas antes de tu cita (%s)",
                         earliest, len(earlier), current_date)
                if earlier and (best_notified is None or earliest < best_notified):
                    best_notified = earliest
                    preview = ", ".join(d.strftime("%b %d, %Y") for d in earlier[:5])
                    notify(f":white_check_mark: :rotating_light: *Appointment found! Earlier date available in Lima*\n"
                           f"Dates: {preview}\nYour current appointment: {current_date:%b %d, %Y}\n"
                           f"Reschedule here: {APPOINTMENT_URL}")
                elif report and not earlier:
                    notify(f":red_circle: *No dates before your appointment*\n"
                           f"Earliest available: {earliest:%b %d, %Y}\n"
                           f"Your current appointment: {current_date:%b %d, %Y}")
            report = False

            if once:
                return 0

            if empty_streak >= EMPTY_LIMIT:
                blocks += 1
                if blocks >= MAX_BLOCKS:
                    log.warning("Sigue pareciendo bloqueado. Deteniendo.")
                    notify_stop("The site still looks rate-limited after a pause. Stopping this run "
                                "to let the block clear.\nThe next scheduled run will try again; "
                                "consider raising `CHECK_INTERVAL_MINUTES`.")
                    return 1
                log.warning("Posible bloqueo temporal. Pausando %d horas.", BACKOFF_HOURS)
                notify(f":large_yellow_circle: Visa bot: the site looks rate-limited (no dates "
                       f"{EMPTY_LIMIT} times in a row). Pausing {BACKOFF_HOURS} hours.")
                empty_streak = 0
                facility_id = None
                ok = wait(BACKOFF_HOURS * 60)
            else:
                ok = wait(INTERVAL_MIN * random.uniform(0.8, 1.4))
            if not ok:
                return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="una sola revision")
    ap.add_argument("--test", action="store_true", help="enviar mensaje de prueba a Slack")
    ap.add_argument("--report", action="store_true", help="enviar a Slack el estado de la primera revision")
    ap.add_argument("--show", action="store_true", help="mostrar el navegador")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    if args.test:
        notify(":white_check_mark: Visa bot connected. I'll post here when an appointment "
               "earlier than your current one shows up.")
        return
    try:
        sys.exit(run(args.once, headless=not args.show, report=args.report or args.once))
    except KeyboardInterrupt:
        log.info("Detenido.")


if __name__ == "__main__":
    main()
