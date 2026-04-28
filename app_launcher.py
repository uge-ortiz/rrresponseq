#!/usr/bin/env python3.12
"""
rrresponseq — ventana nativa macOS con WebKit
"""
import sys
import os
import time
import socket
import threading
import subprocess

script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

PORT = 5001

# ── Instancia única: abortar si ya hay una copia corriendo ────────────────────
def _already_running():
    """True si algo ya escucha en PORT."""
    try:
        s = socket.create_connection(('127.0.0.1', PORT), timeout=0.5)
        s.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False

if _already_running():
    subprocess.run([
        'osascript', '-e',
        'display alert "rrresponseq ya está abierto" '
        'message "Solo puede haber una instancia en ejecución. '
        'Cierra la ventana actual antes de abrir otra." '
        'buttons {"OK"} default button "OK" '
        'as warning'
    ])
    sys.exit(0)

# ── Log de diagnóstico ────────────────────────────────────────────────────────
import traceback
log_path = os.path.expanduser('~/Desktop/rrresponseq.log')
log_fd = open(log_path, 'w', buffering=1, encoding='utf-8')
log_fd.write(f'[launcher] start\n')
log_fd.flush()

devnull_fd = open(os.devnull, 'w', encoding='utf-8')

def _suppress():
    import logging
    logging.getLogger('werkzeug').setLevel(logging.CRITICAL)
    logging.getLogger('flask').setLevel(logging.CRITICAL)

# ── Inicia sequencer en thread background ─────────────────────────────────────
def run_sequencer():
    _suppress()
    sys.stdout = log_fd
    sys.stderr = log_fd
    try:
        log_fd.write('[sequencer] importing...\n'); log_fd.flush()
        import sequencer
        sequencer._APP_MODE = True          # suprime terminal display en el log
        log_fd.write('[sequencer] imported OK\n'); log_fd.flush()
        seq = sequencer.Sequencer()
        log_fd.write('[sequencer] Sequencer() OK\n'); log_fd.flush()
        seq.start()
        log_fd.write('[sequencer] start() OK\n'); log_fd.flush()
        while True:
            time.sleep(1)
    except Exception:
        log_fd.write('[sequencer] EXCEPTION:\n')
        log_fd.write(traceback.format_exc())
        log_fd.flush()

seq_thread = threading.Thread(target=run_sequencer, daemon=True)
seq_thread.start()

# ── Espera a que Flask esté listo ─────────────────────────────────────────────
def wait_for_server(host='127.0.0.1', port=PORT, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1)
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.3)
    return False

wait_for_server()

# ── Abre ventana nativa con pywebview ─────────────────────────────────────────
import webview

window = webview.create_window(
    title='rrresponseq',
    url=f'http://127.0.0.1:{PORT}',
    width=1440,
    height=960,
    resizable=True,
    min_size=(800, 600),
)

webview.start()
