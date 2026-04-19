from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import config

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_PATH = config.JARVIS_DIR / "google_token.json"
CREDENTIALS_PATH = config.JARVIS_DIR / "google_credentials.json"


def is_configured() -> bool:
    return TOKEN_PATH.exists() or CREDENTIALS_PATH.exists()


def get_credentials() -> Credentials:
    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_PATH.exists():
                raise FileNotFoundError(
                    f"Google Credentials fehlen. Bitte OAuth-JSON als {CREDENTIALS_PATH} ablegen."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_PATH.write_text(creds.to_json())
    return creds
