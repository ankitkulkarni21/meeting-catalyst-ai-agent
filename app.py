import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import datetime


load_dotenv()

app = Flask(__name__)


DESCOPE_PROJECT_ID = os.getenv('DESCOPE_PROJECT_ID')
DESCOPE_MANAGEMENT_KEY = os.getenv('DESCOPE_MANAGEMENT_KEY')

# Hugging Face API
HF_MODEL = "sshleifer/distilbart-cnn-12-6"  

@app.route('/')
def home():
    return "Meeting Catalyst Agent is running!"

@app.route('/trigger-agent', methods=['POST'])
def trigger_agent():
    user_login_id = "test.user@example.com"
    
    tokens = get_tokens_for_user(user_login_id)
    if not tokens:
        return jsonify({"error": "Could not retrieve tokens"}), 500
        
    google_token = tokens.get('google')
    if not google_token:
        return jsonify({"error": "User has not connected Google account"}), 400

    meetings = get_upcoming_meetings(google_token)
    
    meeting_briefings = []
    
    for event in meetings:
        meeting_title = event.get('summary', 'No Title')
        attendees = [att['email'] for att in event.get('attendees', [])] if 'attendees' in event else ['Unknown']
        
        document_texts = [
            "Document 1 content about project updates and strategies.",
            "Document 2 content about action items and deadlines."
        ]
        
        briefing = generate_briefing(meeting_title, attendees, document_texts)
        
        meeting_briefings.append({
            "title": meeting_title,
            "briefing": briefing
        })
    
    return jsonify({
        "status": "Agent triggered successfully",
        "briefings": meeting_briefings
    })

def get_tokens_for_user(login_id):
    """
    Asks Descope for the external API tokens and user details for a given user.
    """
    url = f"https://api.descope.com/v1/mgmt/user/provider/token?loginId={login_id}"
    headers = {"Authorization": f"Bearer {DESCOPE_MANAGEMENT_KEY}"}
    
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        provider_data = {}
        for item in data['providers']:
            provider_name = item['provider']
            provider_data[provider_name] = {
                'accessToken': item['accessToken'],
                'userId': item.get('providerUserId') 
            }
        return provider_data
    else:
        print(f"Error fetching tokens: {response.text}")
        return None

def get_upcoming_meetings(google_access_token):
    """
    Fetch upcoming Google Calendar events using the user's access token.
    """
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
    """
    Generate a meeting briefing using Hugging Face Inference API.
    """
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
            print("Hugging Face response:", response.json())
            summary = response.json()[0]['summary_text']
            return summary
        else:
            print(f"Hugging Face API error: {response.status_code}, {response.text}")
            return "Error: Could not generate briefing."
    except Exception as e:
        print(f"Error calling Hugging Face API: {e}")
        return "Error: Could not generate briefing."

if __name__ == '__main__':
    app.run(debug=True, port=5001)

def validate_descope_session(session_token: str) -> bool:
    """
    Validates a Descope session token. Returns the user's details if valid.
    """
    api_host = f"https://api.descope.com"
    project_id = os.getenv('DESCOPE_PROJECT_ID')
    headers = {
        'Authorization': f'Bearer {project_id}',
    }
    response = requests.post(f'{api_host}/v1/auth/validate', 
                             headers=headers, 
                             json={'sessionToken': session_token})
    
    if response.status_code == 200:
        print("Session is valid.")
        return response.json() 
    else:
        print(f"Session invalid: {response.text}")
        return None

@app.route('/validate-session', methods=['POST'])
def validate_session():
    data = request.get_json()
    session_token = data.get('token')

    if not session_token:
        return jsonify({"error": "Session token is missing"}), 400

    user_details = validate_descope_session(session_token)

    if user_details:
        login_id = user_details['token']['loginIds'][0]
        print(f"Validated user with Login ID: {login_id}")
        return jsonify({"status": "success", "user": login_id})
    else:
        return jsonify({"error": "Invalid session"}), 401

def search_drive_and_get_content(access_token, query):
    """
    Searches Google Drive for files and returns their content.
    """
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
            print(f"Found file: {item['name']}")
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
    """
    Searches Notion and returns the content of found pages.
    """
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
    print(f"Found Notion pages: {page_titles}")

    return page_titles

def run_catalyst_for_user(login_id):
    """
    The main orchestrator for the Meeting Catalyst agent.
    """
    print(f"--- Running Catalyst Agent for user: {login_id} ---")

    tokens = get_tokens_for_user(login_id)
    if not tokens:
        print("Could not get tokens. Aborting.")
        return
    
    google_token = tokens.get('google')
    notion_token = tokens.get('notion')
    slack_token = tokens.get('slack')


    if not google_token:
        print("User has not connected Google. Cannot get meetings.")
        return
        
    meetings = get_upcoming_meetings(google_token)
    if not meetings:
        print("No upcoming meetings to process.")
        return


    next_meeting = meetings[0]
    title = next_meeting.get('summary', 'No Title')
    attendees = [attendee.get('email') for attendee in next_meeting.get('attendees', [])]
    print(f"\nProcessing meeting: '{title}'")


    all_context_docs = []
    search_query = title.split(' ')[0] 

    if google_token:
        drive_content = search_drive_and_get_content(google_token, search_query)
        all_context_docs.extend(drive_content)

    if notion_token:
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
    

    print("Generating AI summary...")
    briefing = generate_briefing(title, attendees, all_context_docs)
    print("--- BRIEFING ---")
    print(briefing)
    print("----------------")
    

    if tokens.get('slack'):
        slack_data = tokens['slack']
        slack_token = slack_data.get('accessToken')
        slack_user_id = slack_data.get('userId') 
        
        if slack_token and slack_user_id:
            send_slack_message(slack_token, slack_user_id, briefing)
        else:
            print("Slack token or User ID is missing.")
    else:
        print("Slack is not connected for this user.")

    print(f"--- Agent run for {login_id} complete. ---")

    
    print(f"--- Agent run for {login_id} complete. ---")


@app.route('/trigger-agent', methods=['POST'])
def trigger_agent():
    data = request.get_json()
    login_id = data.get('loginId')
    if not login_id:
        return jsonify({"error": "loginId is required"}), 400
        
    run_catalyst_for_user(login_id)
    return jsonify({"status": "Agent run started for " + login_id})


def send_slack_message(access_token, user_id, message_text):
    """
    Sends a message to a user in Slack as a direct message.
    """
    if not user_id:
        print("❌ Slack User ID is missing. Cannot send DM.")
        return

    print(f"✉️ Sending Slack message to user {user_id}...")
    
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
        print("✅ Slack message sent successfully!")
    else:
        print(f" Error sending Slack message: {response.json().get('error')}")