import sys
if sys.version_info < (3, 10):
    try:
        import importlib.metadata
        import importlib_metadata
        if not hasattr(importlib.metadata, 'packages_distributions'):
            importlib.metadata.packages_distributions = importlib_metadata.packages_distributions
    except ImportError:
        pass

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os.path
import datetime
import base64

# If modifying these scopes, delete the file token.json.
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def get_gmail_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)
    else:
        # We expect token.json to exist in the container/environment
        raise FileNotFoundError("token.json not found. Please run setup_gmail_auth.py locally first.")
    
    return build('gmail', 'v1', credentials=creds)

def fetch_recent_emails(hours=24):
    """
    Fetches emails from the last n hours.
    Returns a string summary of the emails.
    """
    try:
        service = get_gmail_service()
        
        # Calculate time query
        # 'newer_than:1d' is easier, but let's be precise if we want hours
        # Actually Gmail query supports 'newer_than:Xd' or 'newer_than:Xh'
        query = f'newer_than:{hours}h category:primary' 
        
        results = service.users().messages().list(userId='me', q=query).execute()
        messages = results.get('messages', [])

        if not messages:
            return "No new emails in the last 24 hours."

        email_data = []
        for msg in messages:
            try:
                msg_detail = service.users().messages().get(userId='me', id=msg['id'], format='full').execute()
                payload = msg_detail['payload']
                headers = payload.get('headers', [])
                
                subject = "No Subject"
                sender = "Unknown Sender"
                
                for h in headers:
                    if h['name'] == 'Subject':
                        subject = h['value']
                    if h['name'] == 'From':
                        sender = h['value']
                
                snippet = msg_detail.get('snippet', '')
                
                email_data.append(f"From: {sender}\nSubject: {subject}\nSnippet: {snippet}\n---")
            except Exception as e:
                print(f"Error processing message {msg['id']}: {e}")
                continue

        return "\n".join(email_data)

    except Exception as e:
        return f"Error fetching emails: {str(e)}"

if __name__ == "__main__":
    print(fetch_recent_emails())
