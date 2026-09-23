import time
import requests
from datetime import datetime, timezone, timedelta
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# ==========================================
# CONFIGURAÇÕES
# ==========================================
APP_ID = "MTg5MzcyOTE2NDQ1"
APP_SECRET = "GPRU40_jkKzQO7hIwdTlv3qL2sYGIaMR"
GROUP_ID = "NzM0OTYxODk2NDQ5"
CALENDAR_ID = "c_ba4842f0ff394c31890a1505258ed5d0f0279c7961766ba64cba3f360725325f@group.calendar.google.com"

CREDENTIALS_FILE = "credentials.json"
SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# ==========================================
# FUNÇÕES SEATALK (OPENAPI COM BOTÃO)
# ==========================================
def get_seatalk_token():
    url = "https://openapi.seatalk.io/auth/app_access_token"
    payload = {
        "app_id": APP_ID,
        "app_secret": APP_SECRET
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        data = response.json()
        if data.get("code") == 0:
            return data.get("app_access_token") or data.get("access_token")
        print(f"⚠️ Erro de credenciais no SeaTalk: {data}")
        return None
    except Exception as err:
        print(f"⚠️ Falha ao obter token do SeaTalk: {err}")
        return None

def send_seatalk_interactive_button(token, summary, meeting_link):
    url = "https://openapi.seatalk.io/messaging/v2/group_chat"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    link = meeting_link if meeting_link else "https://calendar.google.com"
    
    payload = {
        "group_id": GROUP_ID,
        "msg_type": 1,
        "message": {
            "interactive": {
                "elements": [
                    {
                        "element_type": "description",
                        "description": {
                            "text": f"⏰ **Lembrete de Treinamento**\n\nO treinamento **{summary}** vai começar em breve!"
                        }
                    },
                    {
                        "element_type": "button_group",
                        "button_group": {
                            "buttons": [
                                {
                                    "text": "Entrar no treinamento",
                                    "type": "default",
                                    "url": link
                                }
                            ]
                        }
                    }
                ]
            }
        }
    }
    
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Resposta do SeaTalk: {res.text}")
    except Exception as err:
        print(f"⚠️ Erro ao enviar mensagem para o SeaTalk: {err}")

# ==========================================
# FUNÇÃO GOOGLE CALENDAR (CHECAGEM ÚNICA)
# ==========================================
def check_calendar_and_notify():
    try:
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
        service = build("calendar", "v3", credentials=creds)

        now = datetime.now(timezone.utc)
        # Busca eventos que iniciam entre agora e os próximos 12 minutos
        time_min = now.isoformat()
        time_max = (now + timedelta(minutes=12)).isoformat()

        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        events = events_result.get("items", [])

        if not events:
            print("Nenhum evento próximo encontrado no momento.")
            return

        for event in events:
            start_str = event["start"].get("dateTime", event["start"].get("date"))
            start_time = datetime.fromisoformat(start_str)

            diff_minutes = (start_time - now).total_seconds() / 60.0

            # Dispara se o evento estiver entre 0 e 12 minutos de começar
            if 0 <= diff_minutes <= 12:
                summary = event.get("summary", "Treinamento sem título")
                meeting_link = event.get("hangoutLink", event.get("location", ""))
                
                print(f"Encontrado evento próximo ({summary}). Enviando notificação...")
                
                token = get_seatalk_token()
                if token:
                    send_seatalk_interactive_button(token, summary, meeting_link)

    except Exception as e:
        print(f"❌ Erro na execução: {e}")

if __name__ == "__main__":
    print("🔎 Verificando Google Calendar via GitHub Actions...")
    check_calendar_and_notify()