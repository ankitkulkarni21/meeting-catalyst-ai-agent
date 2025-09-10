import os
from descope import DescopeClient
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from dotenv import load_dotenv
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import datetime

load_dotenv()

app = Flask(__name__)
CORS(app)

descope_client = DescopeClient(project_id=os.getenv('DESCOPE_PROJECT_ID'))
DESCOPE_PROJECT_ID = os.getenv('DESCOPE_PROJECT_ID')
DESCOPE_MANAGEMENT_KEY = os.getenv('DESCOPE_MANAGEMENT_KEY')

HF_MODEL = "sshleifer/distilbart-cnn-12-6"

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/login')
def login():
    return render_template('index.html')


def get_tokens_for_user(login_id, provider):
    url = f"https://api.descope.com/v1/mgmt/user/provider/token?loginId={login_id}&provider={provider}"
    headers = {"Authorization": f"Bearer {DESCOPE_PROJECT_ID}:{DESCOPE_MANAGEMENT_KEY}"}
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        return {
            'provider': data['provider'],
            'providerUserId': data['providerUserId'],
            'accessToken': data['accessToken'],
            'expiration': data['expiration'],
            'scopes': data['scopes'],
            'refreshToken': data.get('refreshToken')
        }
    else:
        print(f"Error fetching tokens for {provider}: {response.text}")
        return None

def get_upcoming_meetings(google_access_token):
    try:
        creds = Credentials(token=google_access_token)
        service = build('calendar', 'v3', credentials=creds)

        now = datetime.datetime.utcnow().isoformat() + 'Z'
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=5,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        return events
    except Exception as e:
        print(f"Google Calendar API error: {e}")
        return []


def generate_briefing(meeting_title, attendees, document_texts):
    full_context = "\n---\n".join(document_texts)
    
    prompt = f"""Meeting Title: {meeting_title}
Attendees: {', '.join(attendees)}

Context:
{full_context}

Provide a concise briefing summarizing the purpose, key points, action items, and potential questions.
"""
    
    headers = {
        "Authorization": f"Bearer {os.getenv('HUGGINGFACE_API_KEY')}"
    }
    
    payload = {
        "inputs": prompt,
        "parameters": {"max_new_tokens": 300}
    }
    
    try:
        response = requests.post(
            f"https://api-inference.huggingface.co/models/{HF_MODEL}",
            headers=headers,
            json=payload
        )
        
        if response.status_code == 200:
            summary = response.json()[0]['summary_text']
            return summary
        else:
            print(f"Hugging Face API error: {response.status_code}, {response.text}")
            return "Error: Could not generate briefing."
    except Exception as e:
        print(f"Error calling Hugging Face API: {e}")
        return "Error: Could not generate briefing."


def validate_descope_session(session_token: str):
    try:
        auth_info = descope_client.validate_session(session_token)
        print("Session is valid. Auth info:", auth_info)
        print(f"Available keys in auth_info: {list(auth_info.keys()) if isinstance(auth_info, dict) else 'Not a dict'}")
        return auth_info
    except Exception as e:
        print(f"Session validation failed: {e}")
        return None


@app.route('/validate-session', methods=['POST'])
def validate_session():
    print('Validate session endpoint hit')
    data = request.get_json()
    print('Received data:', data)
    session_token = data.get('token')

    if not session_token:
        return jsonify({"error": "Session token is missing"}), 401

    user_details = validate_descope_session(session_token)

    if user_details:
        print(f"Full session validation response: {user_details}")
        print(f"Available keys: {user_details.keys()}")
        
        # Try to access the correct user identifier
        # The JWT structure shows 'sub' contains the User ID
        user_id = user_details.get('sub')  # This is the User ID
        
        if user_id:
            print(f"Validated user with User ID: {user_id}")
            return jsonify({"status": "success", "user": user_id})
        else:
            return jsonify({"error": "Could not extract user ID"}), 401
    else:
        return jsonify({"error": "Invalid session"}), 401


def search_drive_and_get_content(access_token, query):
    all_content = []
    try:
        creds = Credentials(token=access_token)
        service = build('drive', 'v3', credentials=creds)
        
        response = service.files().list(
            q=f"name contains '{query}' and mimeType != 'application/vnd.google-apps.folder'",
            pageSize=3,
            fields="files(id, name, mimeType)"
        ).execute()
        
        files = response.get('files', [])
        if not files:
            print(f"No files found in Drive for query: {query}")
            return []

        for item in files:
            file_id = item['id']
            mime_type = item['mimeType']
            
            if 'google-apps' in mime_type:
                request = service.files().export(fileId=file_id, mimeType='text/plain')
            else:
                request = service.files().get_media(fileId=file_id)
            
            content = request.execute()
            all_content.append(content.decode('utf-8', errors='ignore'))
            
        return all_content
    except Exception as e:
        print(f"An error occurred with Google Drive API: {e}")
        return []


def search_notion_and_get_content(access_token, query):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    search_payload = {"query": query}
    
    response = requests.post("https://api.notion.com/v1/search", json=search_payload, headers=headers)
    
    if response.status_code != 200:
        print(f"Error searching Notion: {response.text}")
        return []
        
    results = response.json().get('results', [])
    page_titles = [page.get('properties', {}).get('title', {}).get('title', [{}])[0].get('plain_text', 'Untitled') for page in results]

    return page_titles


def run_catalyst_for_user(login_id):
    print(f"--- Running Catalyst Agent for user: {login_id} ---")

    # Get tokens for each provider separately
    google_token_data = get_tokens_for_user(login_id, 'google')
    notion_token_data = get_tokens_for_user(login_id, 'notion')
    slack_token_data = get_tokens_for_user(login_id, 'slack')

    if not google_token_data:
        print("User has not connected Google. Cannot get meetings.")
        return
        
    google_token = google_token_data['accessToken']
    meetings = get_upcoming_meetings(google_token)
    
    if not meetings:
        print("No upcoming meetings to process.")
        return

    next_meeting = meetings[0]
    title = next_meeting.get('summary', 'No Title')
    attendees = [attendee.get('email') for attendee in next_meeting.get('attendees', [])]

    all_context_docs = []
    search_query = title.split(' ')[0]

    # Use Google Drive
    drive_content = search_drive_and_get_content(google_token, search_query)
    all_context_docs.extend(drive_content)

    # Use Notion if available
    if notion_token_data:
        notion_token = notion_token_data['accessToken']
        notion_content = search_notion_and_get_content(notion_token, search_query)
        all_context_docs.extend(notion_content)
    
    if not all_context_docs:
        print("Found no relevant documents. Nothing to summarize.")
        return

    print("Generating AI summary...")
    briefing = generate_briefing(title, attendees, all_context_docs)
    
    print("--- BRIEFING ---")
    print(briefing)
    print("----------------")
    
    # Send to Slack if available
    if slack_token_data:
        slack_token = slack_token_data['accessToken']
        slack_user_id = slack_token_data['providerUserId']
        send_slack_message(slack_token, slack_user_id, briefing)
    else:
        print("Slack is not connected for this user.")

    print(f"--- Agent run for {login_id} complete. ---")


@app.route('/trigger-agent', methods=['POST'])
def trigger_agent():
    data = request.get_json()
    print('Trigger agent called with:', data)

    login_id = data.get('loginId')
    if not login_id:
        return jsonify({"error": "loginId is required"}), 400
        
    run_catalyst_for_user(login_id)
    return jsonify({"status": f"Agent run started for {login_id}"})


def send_slack_message(access_token, user_id, message_text):
    if not user_id:
        print("Slack User ID is missing. Cannot send DM.")
        return

    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "channel": user_id,
        "text": message_text,
        "unfurl_links": False,
        "unfurl_media": False
    }
    
    response = requests.post(url, headers=headers, json=payload)
    
    if response.json().get('ok'):
        print("Slack message sent successfully!")
    else:
        print(f"Error sending Slack message: {response.json().get('error')}")
