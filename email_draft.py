import streamlit as st
import base64
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

# Update scopes to include gmail.modify which is required for drafts
SCOPES = [
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify'  # Added this scope
]

def get_gmail_service():
    """Initialize Gmail service with token-based authentication"""
    try:
        # Create credentials directly from tokens - simplified approach
        creds = Credentials(
            token=st.secrets["gmail_token"]["token"],
            refresh_token=st.secrets["gmail_token"]["refresh_token"],
            token_uri=st.secrets["gmail_token"]["token_uri"],
            client_id=st.secrets["gmail_token"]["client_id"],
            client_secret=st.secrets["gmail_token"]["client_secret"],
            scopes=SCOPES  # Use the defined SCOPES directly
        )
        
        # Build the service
        service = build('gmail', 'v1', credentials=creds)
        return service
            
    except Exception as e:
        st.error(f"Gmail service initialization error: {str(e)}")
        # Debug information
        st.error("Checking credentials configuration:")
        try:
            for key in ["token_uri", "client_id"]:
                st.write(f"{key}: {st.secrets['gmail_token'][key]}")
        except Exception as debug_e:
            st.error(f"Debug error: {str(debug_e)}")
        return None

def create_settlement_template(currency, value):
    """Create settlement email template with proper formatting"""
    subject = f"Settlement of Subscription"
    
    # Format the value with commas for thousands
    formatted_value = "{:,.2f}".format(float(value))
    
    # Plain text version
    body = f"""Dear Sirs,

We have settled {currency} {formatted_value} for the subscription.

Enclosed is the payment reference and order information for your kind reference.

Should you have any questions, please feel free to contact us."""

    # HTML version with the exact signature
    html_body = f"""
    <div style="font-family: Tahoma, sans-serif;">
        <p>Dear Sirs,</p>
        <p>We have settled {currency} {formatted_value} for the subscription.</p>
        <p>Enclosed is the payment reference and order information for your kind reference.</p>
        <p>Should you have any questions, please feel free to contact us.</p>
        <br>
        <div dir="ltr">
            <span style="font-size:12.8px;font-family:tahoma,sans-serif"><font color="#000000"><span style="font-size:10pt">Finance Department<br></span></font></span>
            <b style="font-size:12.8px"><span lang="EN-US" style="font-size:8pt;font-family:tahoma,sans-serif"><font color="#006600">Wealth Management Cube Limited<br></font></span></b>
            <font color="#000000" face="tahoma, sans-serif"><span style="font-size:10.6667px">Room 804A2, 8/F, World Wide House,19 Des Voeux Road Central, Central, Hong Kong</span></font><br>
            <span lang="EN-US" style="font-size:8pt;font-family:tahoma,sans-serif;color:black">General Line:&nbsp;<a href="tel:+85225169555" value="+85225169555" style="color:rgb(17,85,204)">+852 2516 9555</a>&nbsp;|&nbsp;Fax:&nbsp;<a href="tel:+85225086027" value="+85225086027" style="color:rgb(17,85,204)">+852 2508 6027&nbsp;</a>&nbsp;|&nbsp;Email:&nbsp;</span>
            <span lang="EN-US" style="font-size:8pt;font-family:tahoma,sans-serif"><a href="mailto:finance@wmcubeHK.com" style="color:rgb(17,85,204)">finance@wmcubeHK.com</a><br></span>
            <span style="font-family:tahoma,sans-serif;font-size:8pt">Visit us at:&nbsp;</span><a href="http://www.wmcubehk.com/" style="color:rgb(17,85,204);font-family:tahoma,sans-serif;font-size:8pt">www.wmcubeHK.com</a><br>
            <div style="text-align:start">
                <span style="color:black;font-family:tahoma,sans-serif;font-size:8pt;text-align:justify"><br>
                The message and any attachment are confidential and may be privileged or otherwise protected from disclosure. If you are not the intended recipient you must not copy, distribute, publish, rely on or otherwise use it without our consent. Some of our communications may contain confidential information which it could be a criminal offence for you to disclose or use without authority. If you have received this message in error, please notify us immediately by reply and delete it from your system.</span>
            </div>
        </div>
    </div>"""

    return subject, body, html_body

def create_draft_email(service, files, email_data):
    """Create email draft with attachments"""
    try:
        message = MIMEMultipart('alternative')
        message['cc'] = 'ops@wmcubehk.com, finance@wmcubehk.com'
        message['subject'] = email_data['subject']
        
        # Add plain text and HTML versions
        part1 = MIMEText(email_data['body'], 'plain', 'utf-8')
        part2 = MIMEText(email_data['html_body'], 'html', 'utf-8')
        
        message.attach(part1)
        message.attach(part2)
        
        # Add attachments
        for file_info in files:
            try:
                # If file_info is a dict with 'content' key
                if isinstance(file_info, dict) and 'content' in file_info:
                    content = file_info['content']
                    filename = file_info['filename']
                else:
                    # If file_info is a FileUploader object
                    content = file_info.read()
                    filename = file_info.name
                    
                attachment = MIMEApplication(content, _subtype='pdf')
                attachment.add_header(
                    'Content-Disposition', 
                    'attachment', 
                    filename=filename
                )
                message.attach(attachment)
            except Exception as e:
                st.error(f"Error attaching file {filename}: {str(e)}")
                continue
        
        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode('utf-8')
        
        draft = service.users().drafts().create(
            userId='me',
            body={'message': {'raw': raw_message}}
        ).execute()
        
        return draft['id']
    
    except Exception as e:
        st.error(f"Failed to create draft: {str(e)}")
        return None

def main():
    st.title('Email Draft Generator')

    service = get_gmail_service()
    if not service:
        st.error("Failed to initialize Gmail service. Please check your authentication.")
        return

    uploaded_files = st.file_uploader("Upload PDF files", type=['pdf'], accept_multiple_files=True)
    currency = st.selectbox("Currency", ["HKD", "USD", "EUR", "GBP"])
    value = st.number_input("Value", min_value=0.0, format="%.2f")

    if st.button("Create Draft") and uploaded_files:
        with st.status("Creating email draft...", expanded=True) as status:
            try:
                subject, body, html_body = create_settlement_template(currency, value)
                
                email_data = {
                    'subject': subject,
                    'body': body,
                    'html_body': html_body
                }

                draft_id = create_draft_email(service, uploaded_files, email_data)
                
                if draft_id:
                    status.update(label="Draft created successfully!", state="complete")
                    st.success("Email draft has been created in your Gmail account.")
                else:
                    status.update(label="Failed to create draft", state="error")
                    st.error("Failed to create email draft. Please try again.")
                    
            except Exception as e:
                status.update(label="Error occurred", state="error")
                st.error(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()
