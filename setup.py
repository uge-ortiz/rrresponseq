from setuptools import setup

APP = ['app_launcher.py']
OPTIONS = {
    'argv_emulation': False,
    'packages': ['flask', 'rtmidi', 'jinja2', 'werkzeug', 'webview'],
    'includes': [
        'flask', 'rtmidi', 'threading', 'json', 'subprocess',
        'webview', 'time', 'socket',
        'sequencer', 'config',   # módulos locales — py2app no los detecta automáticamente
    ],
    'plist': {
        'NSPrincipalClass': 'NSApplication',
        'CFBundleName': 'rrresponseq',
        'CFBundleDisplayName': 'rrresponseq',
        'CFBundleIdentifier': 'com.rrresponseq.midi',
        'CFBundleVersion': '1.0',
        'CFBundleShortVersionString': '1.0',
        'CFBundleIconFile': 'rrresponseq',
        'NSHumanReadableCopyright': '© 2026',
        'NSMicrophoneUsageDescription': 'Acceso MIDI requerido',
    },
    # 'docs' como directorio — py2app lo copia entero a Resources/docs/
    'resources': ['rrresponseq.icns', 'config.py', 'settings.json', 'scripts.json', 'docs'],
    # deployment target controlado con MACOSX_DEPLOYMENT_TARGET=10.13 al compilar
}

setup(
    name='rrresponseq',
    app=APP,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
