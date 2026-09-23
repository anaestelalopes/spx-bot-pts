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

# Fuso horário configurado para Brasília / São Paulo
TIMEZONE_LOCAL = "America/Sao_Paulo"


def get_seatalk_token():
    url = "https://openapi.seatalk.io/auth/app_access_token"
    payload = {
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }
    response = requests.post(url, json=payload, timeout=10)
    data = response.json()
    if data.get("code") == 0:
        return data.get("app_access_token") or data.get("access_token")
    print("⚠️ Erro ao obter token do SeaTalk:", data)
    return None


def send_seatalk_card(token, title, start_time_str, description):
    url = "https://openapi.seatalk.io/messaging/v2/group_chat"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Estrutura confirmada pela documentação oficial "Build a Card"
    # (tag / element_type - não "msg_type" / "components")
    desc_text = f"**Evento:** {title}\n**Horário:** {start_time_str}\n**Descrição:** {description or 'Sem descrição'}"

    payload = {
        "group_id": GROUP_ID,
        "message": {
            "tag": "interactive_message",
            "interactive_message": {
                "elements": [
                    {
                        "element_type": "title",
                        "title": {
                            "text": "⏰ Lembrete de Reunião!"
                        }
                    },
                    {
                        "element_type": "description",
                        "description": {
                            "format": 1,
                            "text": desc_text
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
    next_10_min = now_local + datetime.timedelta(minutes=10)

    time_min = now_local.isoformat()
    time_max = next_10_min.isoformat()

    print(f"Buscando eventos no fuso {TIMEZONE_LOCAL} entre {time_min} e {time_max}...")

    events_result = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    events = events_result.get("items", [])

    if not events:
        print("Nenhum evento encontrado nos próximos 10 minutos.")
        return

    token = get_seatalk_token()
    if not token:
        return

    for event in events:
        summary = event.get("summary", "Sem título")
        description = event.get("description", "")
        start = event["start"].get("dateTime", event["start"].get("date"))

        print(f"Notificando evento: {summary} em {start}")
        send_seatalk_card(token, summary, start, description)


if __name__ == "__main__":
    check_calendar_and_notify()
