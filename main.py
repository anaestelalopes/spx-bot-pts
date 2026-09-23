import datetime
import requests
import zoneinfo
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# --- CONFIGURAÇÕES ---
APP_ID = "MTg5MzcyOTE2NDQ1"
APP_SECRET = "GPRU40_jkKzQO7hIwdTlv3qL2sYGIaMR"
GROUP_ID = "NzM0OTYxODk2NDQ5"
CALENDAR_ID = "c_ba4842f0ff394c31890a1505258ed5d0f0279c7961766ba64cba3f360725325f@group.calendar.google.com"

CREDENTIALS_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

TIMEZONE_LOCAL = "America/Sao_Paulo"

# O workflow do GitHub Actions roda a cada 5 minutos (mínimo permitido pelo
# GitHub). Pra não perder nem duplicar avisos, olhamos uma janela de alguns
# minutos ao redor dos "10 minutos antes do evento", em vez de um valor fixo.
JANELA_MIN_MINUTOS = 8
JANELA_MAX_MINUTOS = 13


def get_seatalk_token():
    url = "https://openapi.seatalk.io/auth/app_access_token"
    payload = {"app_id": APP_ID, "app_secret": APP_SECRET}
    response = requests.post(url, json=payload, timeout=10)
    data = response.json()
    if data.get("code") == 0:
        return data.get("app_access_token") or data.get("access_token")
    print("⚠️ Erro ao obter token do SeaTalk:", data)
    return None


def send_seatalk_card(token, summary, meeting_link):
    url = "https://openapi.seatalk.io/messaging/v2/group_chat"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    link = meeting_link if meeting_link else "https://calendar.google.com"

    payload = {
        "group_id": GROUP_ID,
        "message": {
            "tag": "interactive_message",
            "interactive_message": {
                "elements": [
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
                    },
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
                ]
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

    token = None
    for event in events:
        start_str = event["start"].get("dateTime", event["start"].get("date"))
        if "T" not in start_str:
            continue  # evento de dia inteiro, sem horário - ignora

        start_time = datetime.datetime.fromisoformat(start_str)
        diff_minutes = (start_time - now_local).total_seconds() / 60.0

        if JANELA_MIN_MINUTOS <= diff_minutes <= JANELA_MAX_MINUTOS:
            summary = event.get("summary", "Treinamento sem título")
            meeting_link = event.get("hangoutLink", event.get("location", ""))

            if token is None:
                token = get_seatalk_token()
                if not token:
                    return

            print(f"Notificando evento: {summary}")
            send_seatalk_card(token, summary, meeting_link)

    if not events:
        print("Nenhum evento encontrado na janela de aviso.")


if __name__ == "__main__":
    check_calendar_and_notify()
