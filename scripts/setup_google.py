"""
Einmaliges Setup für Google Calendar OAuth.

Voraussetzungen:
1. Google Cloud Project erstellen: https://console.cloud.google.com
2. Google Calendar API aktivieren
3. OAuth2 Credentials erstellen (Typ: Desktop App)
4. credentials.json herunterladen → nach ~/.jarvis/google_credentials.json kopieren

Dann dieses Script ausführen:
    python3 setup_google.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

credentials_path = Path.home() / ".jarvis" / "google_credentials.json"

if not credentials_path.exists():
    print(f"[!] Credentials nicht gefunden: {credentials_path}")
    print()
    print("Schritte:")
    print("1. https://console.cloud.google.com → Neues Projekt")
    print("2. APIs & Dienste → Bibliothek → 'Google Calendar API' aktivieren")
    print("3. APIs & Dienste → Anmeldedaten → OAuth 2.0-Client-ID erstellen (Desktop App)")
    print("4. JSON herunterladen → speichern als:", credentials_path)
    sys.exit(1)

print("Starte OAuth-Flow (Browser öffnet sich)...")
from services import google_auth
creds = google_auth.get_credentials()
print(f"✓ Token gespeichert: {google_auth.TOKEN_PATH}")
print("Google Calendar ist jetzt für JARVIS eingerichtet.")
