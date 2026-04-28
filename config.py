# ── Puertos MIDI ────────────────────────────────────────────────────────────
# Cambia estos nombres para que coincidan con los de tu sistema.
# En macOS/Linux ejecuta: python3 -c "import rtmidi; print(rtmidi.MidiOut().get_ports())"
# para ver los puertos disponibles.

MIDI_OUT_PORT  = "MIDI4x4 Midi Out 1"         # puerto de salida MIDI principal
MIDI_OUT_PORT2 = "MIDI4x4 Midi Out 2"         # segundo puerto MIDI (gear sin thru, etc.)
MIDI_CLK_PORT  = "Driver IAC Bus 1"            # puerto virtual para clock al DAW ("" para desactivar)
MIDI_SYNC_PORT = "Driver IAC Bus 1"            # puerto para recibir clock del DAW (esclavo); "" para desactivar
MIDI_KB_PORT   = "Launchkey MK4 49 MIDI Out"  # teclado para thru/grabación (opcional, "" para desactivar)
LAUNCHPAD_PORT = "Launchpad"                   # Launchpad MK1 (opcional, "" para desactivar)
NK_IN_PORT     = "nanoKONTROL SLIDER/KNOB"    # controlador de knobs/faders — entrada
NK_OUT_PORT    = "nanoKONTROL"                 # controlador de knobs/faders — salida (LEDs)

# ── CC del controlador de knobs ──────────────────────────────────────────────
# Ajusta si usas otro controlador (Arturia, BCR2000, Launch Control, etc.)
# Cada bloque es una fila de 8 controles:
NK_KNOB_BASE  = 11   # CC 11-18: knobs 0-7   (parámetros de la página activa)
NK_FADER_BASE = 1    # CC  1-8:  faders 0-7  (cantidad estocástica por parámetro)
NK_BTN_S_BASE = 21   # CC 21-28: botones S   (randomizar parámetro)
NK_BTN_M_BASE = 31   # CC 31-38: botones M   (activar/desactivar modo estocástico)
NK_BPM_CC     = 19   # CC 19:    knob BPM    (control global de tempo)
NK_UNDO_CC    = 47   # CC 47:    botón <<    (deshacer)
NK_REDO_CC    = 48   # CC 48:    botón >>    (rehacer)

# ── Parámetros por defecto ───────────────────────────────────────────────────
BPM           = 120
STEPS         = 16
PULSES        = 4
MIDI_CHANNEL  = 0
ROOT_NOTE     = 48
