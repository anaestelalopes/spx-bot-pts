import datetime
import html
import json
import os
import re
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
# Lista de grupos do SeaTalk que vão receber os avisos. Pra incluir outro
# grupo, é só adicionar o group_id dele aqui (e adicionar o bot como membro
# do grupo no SeaTalk).
GROUP_IDS = [
    "NzM0OTYxODk2NDQ5",  # grupo original (teste agenda sea)
    "ODk3NDYyOTY5NDA3",  # MG
    "NzI0MzY5Njk0MTgw",  # SPM/SPC
    "NzExMzg5MTI0MDAx",  # RJ/ES
    "MjY0NDQ3ODg1MTky",  # SPI/SPO
    "MjYwNjAwNzM5Mzk1",  # NE
    "OTA5NTcwODQ2MzE2",  # SUL
]
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


def limpar_descricao_html(texto):
    """Converte a descrição do evento (que o Google Calendar salva como HTML
    quando criada com formatação rica) num texto simples, com Markdown básico
    que o SeaTalk entende."""
    if not texto:
        return ""

    t = texto
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"</p>", "\n\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<p[^>]*>", "", t, flags=re.IGNORECASE)

    # Negrito: <b>, <strong>, ou <span style="font-weight: bold/700...">,
    # aceitando qualquer atributo dentro da tag.
    t = re.sub(r"<(?:b|strong)\b[^>]*>", "**", t, flags=re.IGNORECASE)
    t = re.sub(r"</(?:b|strong)>", "**", t, flags=re.IGNORECASE)
    t = re.sub(
        r'<span\b[^>]*style="[^"]*font-weight\s*:\s*(?:bold|[6-9]00)[^"]*"[^>]*>',
        "**", t, flags=re.IGNORECASE
    )

    # Itálico: <i>, <em>, ou <span style="font-style: italic...">.
    t = re.sub(r"<(?:i|em)\b[^>]*>", "_", t, flags=re.IGNORECASE)
    t = re.sub(r"</(?:i|em)>", "_", t, flags=re.IGNORECASE)
    t = re.sub(
        r'<span\b[^>]*style="[^"]*font-style\s*:\s*italic[^"]*"[^>]*>',
        "_", t, flags=re.IGNORECASE
    )

    # Fecha qualquer </span> que tenha sobrado como marcador de negrito/itálico
    # aberto (não dá pra saber qual símbolo fechar sem rastrear a pilha, então
    # a técnica prática é: se sobrou </span>, ele já cumpriu o papel de
    # fechamento em conjunto com o próprio "**"/"_" acima; qualquer </span>
    # remanescente sem abertura correspondente é apenas removido mais abaixo.

    t = re.sub(r"<li[^>]*>", "• ", t, flags=re.IGNORECASE)
    t = re.sub(r"</li>", "\n", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", "", t)  # remove qualquer outra tag que sobrou
    t = html.unescape(t)  # &nbsp;, &amp;, etc.
    t = re.sub(r"\n{3,}", "\n\n", t)  # não deixa acumular linhas em branco demais
    return t.strip()


def escapar_markdown(texto):
    """Neutraliza só os colchetes ([ ]) do nome do evento, porque eles fazem
    o motor de Markdown do SeaTalk tentar interpretar como início de link e
    quebram a formatação do card inteiro. Deixa `**negrito**` e `_itálico_`
    passarem livres, caso o nome do evento já venha com essa formatação."""
    if not texto:
        return texto
    return texto.replace("[", "(").replace("]", ")")


def send_seatalk_card(token, group_id, summary, meeting_link, event_description="", start_time=None):
    url = "https://openapi.seatalk.io/messaging/v2/group_chat"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    link = meeting_link if meeting_link else "https://calendar.google.com"
    summary_seguro = escapar_markdown(summary)
    event_description = limpar_descricao_html(event_description)

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
                "text": f"O treinamento **{summary_seguro}** vai começar em alguns minutos!"
            }
        }
    ]

    # Data e horário exatos do evento, no fuso de Brasília.
    if start_time is not None:
        start_local = start_time.astimezone(zoneinfo.ZoneInfo(TIMEZONE_LOCAL))
        data_txt = start_local.strftime("%d/%m/%Y")
        hora_txt = start_local.strftime("%H:%M")
        elements.append({
            "element_type": "description",
            "description": {
                "format": 1,
                "text": f"📅 **Data:** {data_txt}\n🕐 **Horário:** {hora_txt}"
            }
        })

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
        "group_id": group_id,
        "message": {
            "tag": "interactive_message",
            "interactive_message": {
                "elements": elements
            }
        }
    }

    try:
        res = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"Resposta do SeaTalk (grupo {group_id}):", res.text)
        return res.json().get("code") == 0
    except Exception as err:
        print(f"⚠️ Erro ao enviar pro grupo {group_id}: {err}")
        return False


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

        # O controle de "já avisado" é feito por evento + grupo. Também respeita
        # registros antigos (só com o id do evento), de antes da lista de grupos.
        grupos_pendentes = [
            g for g in GROUP_IDS
            if event_id not in notified and f"{event_id}|{g}" not in notified
        ]
        if not grupos_pendentes:
            print(f"  - '{summary_debug}': já tinha sido notificado em todos os grupos, ignorando.")
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
            for group_id in grupos_pendentes:
                ok = send_seatalk_card(token, group_id, summary, meeting_link, event_description, start_time)
                if ok:
                    notified[f"{event_id}|{group_id}"] = now_utc.isoformat()
                    houve_mudanca = True
                else:
                    print(f"    -> Falha no grupo {group_id}; tenta de novo na próxima execução (se ainda estiver na janela).")

    if houve_mudanca:
        save_notified(notified)

    if not events:
        print("Nenhum evento encontrado na janela de aviso.")


if __name__ == "__main__":
    check_calendar_and_notify()
    print("Concluído. Encerrando processo.")
    sys.stdout.flush()
    os._exit(0)
