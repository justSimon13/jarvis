from setuptools import setup

APP = ['app.py']
DATA_FILES = []
OPTIONS = {
    'argv_emulation': False,
    'packages': [
        'customtkinter', 'pystray', 'PIL',
        'anthropic', 'elevenlabs', 'whisper',
        'sounddevice', 'scipy', 'numpy',
        'silero_vad', 'openwakeword',
        'notion_client', 'google', 'ddgs',
        'dotenv',
    ],
    'includes': [
        'tkinter', '_tkinter', 'plistlib', 'queue',
        'threading', 'json', 're', 'os', 'time',
    ],
    'excludes': ['matplotlib', 'test', 'unittest'],
    'iconfile': 'assets/icon.icns',
    'resources': ['assets/'],
    'plist': {
        'CFBundleName': 'JARVIS',
        'CFBundleDisplayName': 'J.A.R.V.I.S.',
        'CFBundleIdentifier': 'com.simonfischer.jarvis',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0',
        'NSMicrophoneUsageDescription': 'J.A.R.V.I.S. benötigt Mikrofonzugriff für Spracherkennung.',
        'LSMinimumSystemVersion': '13.0',
        'LSUIElement': False,
        'NSHighResolutionCapable': True,
    },
}

setup(
    name='JARVIS',
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
