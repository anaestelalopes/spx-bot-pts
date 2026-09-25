import datetime
import json
import os
import sys
import requests
import zoneinfo
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- CONFIGURAÇÕES ---
# APP_ID e APP_SECRET vêm de variáveis de ambiente (configuradas como Secret
# no GitHub) em vez de ficarem escritos direto no código - importante pra
# poder deixar o repositório público sem expor essas credenciais.
APP_ID = os.environ["SEATALK_APP_ID"]
APP_SECRET = os.environ["SEATALK_APP_SECRET"]
GROUP_ID = "NzM0OTYxODk2NDQ5"
CALENDAR_ID = "c_ba4842f0ff394c31890a1505258ed5d0f0279c7961766ba64cba3f360725325f@group.calendar.google.com"

CREDENTIALS_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

TIMEZONE_LOCAL = "America/Sao_Paulo"

# Com o controle de eventos já notificados (arquivo notified_events.json),
# não tem mais risco de avisar duas vezes o mesmo evento - então a janela
# pode ser mais generosa, cobrindo qualquer atraso do disparo do cron.
JANELA_MIN_MINUTOS = 5
JANELA_MAX_MINUTOS = 15

NOTIFIED_FILE = "notified_events.json"
# Quanto tempo guardar o registro de um evento já notificado antes de
# poder "esquecer" ele (evita o arquivo crescer pra sempre).
RETENCAO_HORAS = 24


def load_notified():
    if not os.path.exists(NOTIFIED_FILE):
        return {}
    try:
        with open(NOTIFIED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_notified(notified):
    with open(NOTIFIED_FILE, "w", encoding="utf-8") as f:
        json.dump(notified, f, ensure_ascii=False, indent=2)


def limpar_antigos(notified, now_utc):
    limite = now_utc - datetime.timedelta(hours=RETENCAO_HORAS)
    return {
        event_id: ts
        for event_id, ts in notified.items()
        if datetime.datetime.fromisoformat(ts) > limite
    }


def get_seatalk_token():
    url = "https://openapi.seatalk.io/auth/app_access_token"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    response = requests.post(url, json=payload, timeout=10)
    data = response.json()
    if data.get("code") == 0:
        return data.get("app_access_token") or data.get("access_token")
    print("⚠️ Erro ao obter token do SeaTalk:", data)
    return None


def send_seatalk_card(token, summary, meeting_link, event_description=""):
    url = "https://openapi.seatalk.io/messaging/v2/group_chat"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    link = meeting_link if meeting_link else "https://calendar.google.com"

    elements = [
        {
            "element_type": "title",
            "title": {
                "text": "⏰ Lembrete de Treinamento"
            }
        },
        {
            "element_type": "description",
            "description": {
                "format": 1,
                "text": f"O treinamento **{summary}** vai começar em 10 minutos!"
            }
        }
    ]

    # Só adiciona o bloco de descrição do evento se ele tiver conteúdo.
    if event_description.strip():
        elements.append({
            "element_type": "description",
            "description": {
                "format": 1,
                "text": event_description.strip()
            }
        })

    elements.append(
        {
            "element_type": "button",
            "button": {
                "button_type": "redirect",
                "text": "Entrar no treinamento",
                "mobile_link": {
                    "type": "web",
                    "path": link
                },
                "desktop_link": {
                    "type": "web",
                    "path": link
                }
            }
        }
    )

    payload = {
        "group_id": GROUP_ID,
        "message": {
            "tag": "interactive_message",
            "interactive_message": {
                "elements": elements
            }
        }
    }

    res = requests.post(url, json=payload, headers=headers, timeout=10)
    print("Resposta do SeaTalk:", res.text)


def check_calendar_and_notify():
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    service = build("calendar", "v3", credentials=creds)

    tz = zoneinfo.ZoneInfo(TIMEZONE_LOCAL)
    now_local = datetime.datetime.now(tz)
    now_utc = now_local.astimezone(datetime.timezone.utc)
    time_min = now_local.isoformat()
    time_max = (now_local + datetime.timedelta(minutes=JANELA_MAX_MINUTOS + 2)).isoformat()

    print(f"Buscando eventos no fuso {TIMEZONE_LOCAL} entre {time_min} e {time_max}...")

    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])
    print(f"Total de eventos retornados pela API: {len(events)}")

    notified = limpar_antigos(load_notified(), now_utc)
    houve_mudanca = False

    token = None
    for event in events:
        event_id = event.get("id")
        start_str = event["start"].get("dateTime", event["start"].get("date"))
        summary_debug = event.get("summary", "Sem título")

        if "T" not in start_str:
            print(f"  - '{summary_debug}': evento de dia inteiro (sem horário), ignorado.")
            continue

        if event_id in notified:
            print(f"  - '{summary_debug}': já tinha sido notificado antes, ignorando.")
            continue

        start_time = datetime.datetime.fromisoformat(start_str)
        diff_minutes = (start_time - now_local).total_seconds() / 60.0

        dentro_da_janela = JANELA_MIN_MINUTOS <= diff_minutes <= JANELA_MAX_MINUTOS
        print(f"  - '{summary_debug}': começa em {start_str} | faltam {diff_minutes:.1f} min | dentro da janela? {dentro_da_janela}")

        if dentro_da_janela:
            summary = event.get("summary", "Treinamento sem título")
            meeting_link = event.get("hangoutLink", event.get("location", ""))
            event_description = event.get("description", "")

            if token is None:
                token = get_seatalk_token()
                if not token:
                    return

            print(f"    -> Notificando evento: {summary}")
            send_seatalk_card(token, summary, meeting_link, event_description)

            notified[event_id] = now_utc.isoformat()
            houve_mudanca = True

    if houve_mudanca:
        save_notified(notified)

    if not events:
        print("Nenhum evento encontrado na janela de aviso.")


if __name__ == "__main__":
    check_calendar_and_notify()
    print("Concluído. Encerrando processo.")
    sys.stdout.flush()
    os._exit(0)
