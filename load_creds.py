import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import streamlit as st
import json
import webbrowser
from pathlib import Path

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/cloud-platform',
    'https://www.googleapis.com/auth/generative-language.retriever'
]

def load_creds():
    """
    Converts client secrets to a credential object.
    """
    creds = None
    
    # First check if we have a token.json file
    token_path = Path('token.json')
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            st.error(f"Error loading token file: {str(e)}")
            
    # If we don't have valid credentials, let's get them
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                st.error(f"Error refreshing credentials: {str(e)}")
                creds = None
        else:
            try:
                # Create client config from streamlit secrets
                client_config = {
                    "installed": {
                        "client_id": st.secrets["gmail_credentials"]["client_id"],
                        "client_secret": st.secrets["gmail_credentials"]["client_secret"],
                        "project_id": st.secrets["gmail_credentials"]["project_id"],
                        "auth_uri": st.secrets["gmail_credentials"]["auth_uri"],
                        "token_uri": st.secrets["gmail_credentials"]["token_uri"],
                        "auth_provider_x509_cert_url": st.secrets["gmail_credentials"]["auth_provider_x509_cert_url"],
                        "redirect_uris": ["http://localhost:8501/"]
                    }
                }

                # Initialize flow
                flow = InstalledAppFlow.from_client_config(
                    client_config,
                    SCOPES,
                    redirect_uri="http://localhost:8501/"
                )
                
                # Run local server
                creds = flow.run_local_server(
                    host='localhost',
                    port=8501,
                    authorization_prompt_message='Please authorize access to continue:',
                    success_message='Authorization complete! You may close this window.',
                    open_browser=True
                )
                
                # Save the credentials for the next run
                with open(token_path, 'w') as token:
                    token.write(creds.to_json())
                
                # Also save to session state
                st.session_state['gmail_token'] = creds.to_json()
                
            except Exception as e:
                st.error(f"Error during authentication flow: {str(e)}")
                return None

    return creds

def reset_credentials():
    """Reset the stored credentials"""
    # Clear session state
    if 'gmail_token' in st.session_state:
        del st.session_state['gmail_token']
    
    # Delete token file if it exists
    token_path = Path('token.json')
    if token_path.exists():
        token_path.unlink()
    
    st.success("Credentials have been reset. Please re-authenticate.")

def is_authenticated():
    """Check if valid credentials exist"""
    try:
        # First check token file
        token_path = Path('token.json')
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            return creds is not None and creds.valid
        return False
    except Exception:
        return False

def get_project_id():
    """Get the project ID from credentials"""
    try:
        return st.secrets["gmail_credentials"]["project_id"]
    except Exception:
        return None

if __name__ == "__main__":
    st.set_page_config(page_title="Authentication Test")
    st.title("Authentication Test")
    
    if st.button("Test Authentication"):
        with st.spinner("Authenticating..."):
            creds = load_creds()
            if creds and creds.valid:
                st.success("Authentication successful!")
                st.write("Credentials JSON:")
                st.json(json.loads(creds.to_json()))
            else:
                st.error("Authentication failed!")
    
    if st.button("Reset Credentials"):
        reset_credentials()
