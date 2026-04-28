import sys
if sys.version_info >= (3, 14):
    sys.exit("ERROR: Usa Python 3.12 (python-rtmidi crashea con 3.14+)\n"
             "Lanza con:  source venv/bin/activate && python sequencer.py")

import rtmidi, time, threading, random, os, re, json, copy, config, subprocess
from flask import Flask, Response, request as _flask_request

# ── Directorio de datos de usuario ───────────────────────────────────────────
# Siempre ~/Library/Application Support/rrresponseq/ para que los datos
# sobrevivan actualizaciones de la app compilada (py2app).
_APP_SUPPORT = os.path.expanduser('~/Library/Application Support/rrresponseq')
os.makedirs(_APP_SUPPORT, exist_ok=True)

# Migración automática: si hay archivos de datos junto al .py (versiones viejas),
# los mueve al directorio de usuario en el primer arranque.
def _migrate_legacy(filename):
    legacy = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    target = os.path.join(_APP_SUPPORT, filename)
    if os.path.exists(legacy) and not os.path.exists(target):
        try:
            import shutil
            shutil.copy2(legacy, target)
        except Exception:
            pass

for _fn in ('banks.json', 'settings.json'):
    _migrate_legacy(_fn)

BANKS_FILE    = os.path.join(_APP_SUPPORT, 'banks.json')
SETTINGS_FILE = os.path.join(_APP_SUPPORT, 'settings.json')

# Referencia global al sequencer para que los endpoints Flask puedan llamarle
_seq_ref = None

# True cuando se ejecuta como app compilada (py2app / pywebview) — suprime prints de terminal
_APP_MODE = False

def _launchkey_cc_map():
    """Preset para Launchkey MK4 49 (modo Custom/DAW — 8 knobs CC 21-28)."""
    def _rep(row): return [row[:] for _ in range(4)]
    return {
        'knob':  _rep(list(range(21, 29))),   # CC 21-28: 8 knobs
        'fade':  _rep([None]*8),               # sin faders físicos
        'btn_s': _rep([None]*8),               # sin botones S
        'btn_m': _rep([None]*8),               # sin botones M
        'misc': {
            'bpm':   None,
            'undo':  None,
            'redo':  None,
            'copy':  None,
            'paste': None,
            'sbank': None,
            'lbank': None,
            'shift': None,
        },
    }

def _default_cc_map():
    """Mapa CC por defecto — 5 grupos: knob, fade, btn_s, btn_m, misc."""
    def _rows(base): return [list(range(base, base + 8)) for _ in range(4)]
    return {
        'knob':  _rows(NK_KNOB_BASE),
        'fade':  _rows(NK_FADER_BASE),
        'btn_s': _rows(NK_BTN_S_BASE),
        'btn_m': _rows(NK_BTN_M_BASE),
        'misc': {
            'bpm':   NK_BPM_CC,
            'undo':  NK_UNDO_CC,
            'redo':  NK_REDO_CC,
            'copy':  None,
            'paste': None,
            'sbank': NK_SAVE_CC,
            'lbank': NK_LOAD_CC,
            'shift': None,
        },
    }

def _cc_map_to_human(cc_map):
    """Convierte el cc_map interno (arrays) a formato legible: param→CC por página.
    PAGE_PARAMS y PAGES se referencian en tiempo de llamada (están definidos más abajo)."""
    _PAGE_PARAMS = {
        0: ["PULS","STEP","PROB","MODE","VEL","SWNG","RESL","ROTA"],
        1: ["NOTE","SCAL","OCT","DENS","SPRD","HARM","INTV","NLEN"],
        2: ["DLY","DTIM","FDBK","RDEC","RSPD","RTCH","GATE","CC"],
        3: ["PROG","BANK","PTBK","PTRN","CHAN","CLK","PORT","SCRI"],
    }
    _PAGE_NAMES  = {0:"A·SEQ", 1:"B·NOTE", 2:"C·FX", 3:"D·CONF"}
    _MISC_LABELS = {
        'bpm':'BPM','undo':'UNDO','redo':'REDO','copy':'COPY','paste':'PASTE',
        'sbank':'SAVE_BANK','lbank':'LOAD_BANK','shift':'SHIFT',
    }
    _GRP_LABEL   = {'knob':'knob','fade':'fader','btn_s':'btn_s','btn_m':'btn_m'}

    out = {'version': 2}
    for grp, label in _GRP_LABEL.items():
        rows = cc_map.get(grp, [])
        out[label] = {}
        for pi, params in _PAGE_PARAMS.items():
            row = rows[pi] if pi < len(rows) else [None]*8
            out[label][_PAGE_NAMES[pi]] = {
                params[i]: (row[i] if i < len(row) else None)
                for i in range(len(params))
            }
    misc = cc_map.get('misc', {})
    out['misc'] = {_MISC_LABELS.get(k, k.upper()): v for k, v in misc.items()}
    return out


def _human_to_cc_map(data):
    """Convierte el formato legible de vuelta al cc_map interno (arrays)."""
    _PAGE_PARAMS = {
        0: ["PULS","STEP","PROB","MODE","VEL","SWNG","RESL","ROTA"],
        1: ["NOTE","SCAL","OCT","DENS","SPRD","HARM","INTV","NLEN"],
        2: ["DLY","DTIM","FDBK","RDEC","RSPD","RTCH","GATE","CC"],
        3: ["PROG","BANK","PTBK","PTRN","CHAN","CLK","PORT","SCRI"],
    }
    _PAGE_NAMES_INV = {"A·SEQ":0, "B·NOTE":1, "C·FX":2, "D·CONF":3}
    _MISC_INV = {
        'BPM':'bpm','UNDO':'undo','REDO':'redo','COPY':'copy','PASTE':'paste',
        'SAVE_BANK':'sbank','LOAD_BANK':'lbank','SHIFT':'shift',
    }
    _GRP_KEY = {'knob':'knob','fader':'fade','btn_s':'btn_s','btn_m':'btn_m'}

    def _parse_grp(label):
        grp_data = data.get(label, {})
        rows = [[None]*8 for _ in range(4)]
        for pg_name, pd in grp_data.items():
            pi = _PAGE_NAMES_INV.get(pg_name)
            if pi is None: continue
            params = _PAGE_PARAMS[pi]
            rows[pi] = [pd.get(params[i]) for i in range(len(params))]
        return rows

    cc_map = {internal: _parse_grp(label) for label, internal in _GRP_KEY.items()}
    misc_raw = data.get('misc', {})
    cc_map['misc'] = {_MISC_INV.get(k, k.lower()): v for k, v in misc_raw.items()}
    return cc_map


def _load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_settings(data):
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[SETTINGS] Error guardando: {e}")

# ── Visual display (Flask SSE → navegador/proyector) ──────────────────────────
TRACK_COLORS = ['#00ff88','#ff4455','#4499ff','#ffaa00','#cc44ff','#44ffee','#ffff44','#ff8844']

import queue as _queue_mod
_display_state = {'label':'READY','value':'—','bar':0.0,'track':0,'color':TRACK_COLORS[0],'bpm':120,'playing':False,'ts':0.0}
_display_queue = _queue_mod.Queue(maxsize=20)
# Evento que se dispara cada vez que _display_state se actualiza — desbloquea SSE sin polling
_display_event = threading.Event()

def _push_display(label, value='', bar=0.0, track=0, bpm=120, playing=False, extra=None):
    """Non-blocking: encola el update; si la cola está llena lo descarta."""
    try:
        d = {'label':label,'value':str(value),
             'bar':max(0.0,min(1.0,bar)),
             'track':track,'color':TRACK_COLORS[track%8],
             'bpm':bpm,'playing':playing,'ts':time.time()}
        if extra:
            d.update(extra)
        _display_queue.put_nowait(d)
    except _queue_mod.Full:
        pass

def _display_worker():
    """Hilo dedicado: consume la cola y actualiza _display_state."""
    while True:
        try:
            state = _display_queue.get(timeout=0.5)
            _display_state.update(state)
            _display_event.set()   # despierta al hilo SSE inmediatamente
        except _queue_mod.Empty:
            pass

_visual_app = Flask(__name__)

_VISUAL_HTML = r'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>SEQ</title>
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<style>
@font-face{font-family:'Disket Mono';src:url('/font/disket-mono.ttf') format('truetype');font-weight:400;font-style:normal}
@font-face{font-family:'Disket Mono';src:url('/font/disket-mono.ttf') format('truetype');font-weight:700;font-style:normal}
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:100%;height:100%;overflow:hidden;background:#191919;
  font-family:'Disket Mono',monospace;user-select:none;color:#fff}
#root{position:absolute;width:1440px;height:920px;transform-origin:top left}

#hdr-page{position:absolute;left:48px;top:40px;font-size:96px;line-height:108px;color:#A1A3A5}
#hdr-mode{position:absolute;left:672px;top:31px;font-size:96px;line-height:108px;color:#A1A3A5}
#hdr-bpm{position:absolute;left:1191px;top:40px;font-size:96px;line-height:108px;color:#FFFFFF}
/* Icono MIDI */
/* ── Page overview: 8 params in 4×2 grid ── */
#page-view{position:absolute;top:0;left:0;width:1440px;height:752px}
.pv-cell{position:absolute;width:330px;height:190px}
.pv-v{font-size:96px;line-height:84px;color:#FFFFFF;white-space:nowrap;overflow:hidden}
.pv-n{position:absolute;top:134px;font-size:64px;line-height:51px;color:#8C9AA3;white-space:nowrap;overflow:hidden;width:330px}
@keyframes stoch-pulse{0%,100%{opacity:1}50%{opacity:0.3}}
.pv-n.stoch{color:#FF8C00;animation:stoch-pulse 1.8s ease-in-out infinite}
.pv-n.toggled{color:#3AC0E0}
.pv-v.disabled{color:#3A4A53!important}
.pv-n.disabled{color:#3A4A53!important}
.pv-cell.kb-sel .pv-n{color:#FFD700!important}
.pv-cell.kb-sel .pv-v{text-decoration:underline;text-underline-offset:10px;text-decoration-color:#FFD700}
@keyframes plock-pulse{0%,100%{opacity:1}50%{opacity:0.4}}
/* Header icons */
#hdr-map-icon{position:absolute;left:1060px;top:40px;width:72px;height:108px;display:none}

/* ── Mapping mode / CONF view ── */
@keyframes learn-pulse{0%,100%{opacity:1}50%{opacity:0.4}}
/* === CONF VIEW === */
#conf-view{position:absolute;top:0;left:0;width:1440px;height:920px;
  display:none;background:#191919;z-index:20}
#conf-hdr{position:absolute;left:48px;top:40px;font-size:96px;line-height:108px;color:#A1A3A5}
#conf-nav{position:absolute;right:48px;top:31px;display:none;
  align-items:center;gap:32px;font-size:96px;line-height:108px;color:#A1A3A5}
#conf-nav .arrow{cursor:pointer;color:#3A4A53;transition:color .15s;padding:0 16px}
#conf-nav .arrow:hover{color:#FFFFFF}
#conf-settings,#conf-mapping,#conf-impexp,#conf-script{
  position:absolute;top:0;left:0;width:1440px;height:840px;display:none}
#conf-script{padding:140px 48px 0}
#cfsc-track-info{font-size:64px;color:#A1A3A5;margin-bottom:20px}
#cfsc-track-info span{color:#FFB73A}
#cfsc-list{display:grid;grid-template-columns:1fr 1fr;gap:8px 32px;
  font-family:"Disket Mono",monospace;font-size:32px}
.cfsc-item{padding:8px 12px;color:#5A6A73;cursor:pointer;border-radius:4px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cfsc-item:hover{background:#1E2C35;color:#FFFFFF}
.cfsc-item.active{color:#FFB73A;background:#1E2C35}
.cfsc-item.off{color:#FF4466}
.cf-row{position:absolute;width:628px;height:138px;cursor:pointer}
.cf-row::after{content:'';position:absolute;bottom:0;left:0;width:100%;height:3px;background:#3A4A53}
.cf-row:hover .cf-val{opacity:0.75}
.cf-name{position:absolute;left:0;top:0;font-size:64px;line-height:138px;
  color:#8C9AA3;white-space:nowrap}
.cf-val{position:absolute;right:0;top:0;font-size:96px;line-height:138px;
  color:#FFFFFF;white-space:nowrap}
.cf-row.cf-active .cf-val{color:#FF8C00}
.cf-row.cf-learning .cf-val{color:#FF8C00!important;animation:learn-pulse 0.7s ease-in-out infinite}
.cf-row.cf-unmapped .cf-val{color:#3A4A53!important}
.cf-row.cf-sel .cf-name{color:#FFD700!important}
.cf-row.cf-sel .cf-val{text-decoration:underline;text-underline-offset:8px;text-decoration-color:#FFD700}
/* SETTINGS rows: full-width, 5 rows → height 120px */
#conf-settings .cf-row{width:1392px;height:120px}
#conf-settings .cf-name{font-size:48px}
#conf-settings .cf-val{font-size:52px;line-height:120px}
/* IMPEXP rows: full-width, altura estándar */
#conf-impexp .cf-row{width:1392px}
#conf-impexp .cf-name{font-size:52px}
#conf-impexp .cf-val{font-size:56px;line-height:138px}
/* IMPEXP status feedback */
#cfie-status{position:absolute;left:48px;top:785px;font-size:40px;transition:opacity 0.3s}
.cf-tab{position:absolute;bottom:28px;font-size:96px;line-height:108px;cursor:pointer}
#cf-tab-settings{left:48px}
#cf-tab-mapping{left:528px}
#cf-tab-impexp{left:1008px}
#cf-tab-script{left:1398px}
.cf-tab.cf-tab-active{color:#FFFFFF}
.cf-tab:not(.cf-tab-active){color:#3A4A53}
/* Pattern Script Editor */
/* ── Detail view: single param ── */
#detail-view{position:absolute;top:0;left:0;width:1440px;height:752px;display:none}
#param-name{position:absolute;width:1440px;height:140px;left:0;top:220px;
  font-size:200px;line-height:140px;text-align:center;color:#FFFFFF;overflow:hidden;white-space:nowrap}
#param-value{position:absolute;width:1440px;height:140px;left:0;top:488px;
  font-size:200px;line-height:140px;text-align:center;color:#FFFFFF;overflow:hidden;white-space:nowrap}

#trk-text{position:absolute;left:52px;top:756px;width:280px;height:120px;
  display:flex;align-items:center;justify-content:center;
  font-size:96px;line-height:108px;color:#FFFFFF}

/* ─── Bank View — vista dentro de #root, coords SVG 1440×920 ─── */
#bvTitleBank   {position:absolute;left:56px; top:55px;font-size:96px;line-height:66px;color:#A1A3A5}
#bvTitlePattern{position:absolute;right:57px;top:55px;font-size:96px;line-height:66px;color:#A1A3A5;text-align:right}
#bvTitleBank span,#bvTitlePattern span{color:#FFB73A}
#bvStatus      {position:absolute;left:0;right:0;bottom:15px;text-align:center;font-size:28px;line-height:34px;color:#5BD075;white-space:pre;pointer-events:none}
#bvStatus.warn {color:#FFB73A}
.bv-cell{position:absolute;width:64px;height:64px;background:#2A3540;transition:background 0.12s}
.bv-cell.has    {background:#FFFFFF}
.bv-cell.active {background:#FFB73A}
.bv-cell.pending{background:#FFB73A;animation:bvPending 0.6s ease-in-out infinite}
.bv-cell.morph-b{background:#CC44FF;animation:bvMorphB 1.0s ease-in-out infinite}
.bv-cell.cursor {outline:4px solid #00FF88;outline-offset:-2px;z-index:2}
.bv-chain-badge{position:absolute;top:4px;right:6px;font-family:"Disket Mono",monospace;
  font-size:11px;color:#000;background:#00FF88;padding:1px 5px;border-radius:3px;z-index:3}
@keyframes bvPending{0%,100%{opacity:1}50%{opacity:0.25}}
@keyframes bvMorphB {0%,100%{opacity:1}50%{opacity:0.35}}

/* ── Compact View ── */
#compact-view{position:absolute;top:130px;left:0;width:1440px;height:790px;
  background:#191919;display:none;pointer-events:none;z-index:5}
#cv-tracks{position:absolute;top:20px;left:24px;width:1392px;height:280px;display:flex;gap:16px}
.cv-half{position:relative;width:688px;flex-shrink:0}
.cv-track{position:absolute;left:0;width:688px;height:58px}
.cv-trk-box{position:absolute;left:0;top:0;width:42px;height:58px;
  box-sizing:border-box;border:3px solid #FFFFFF;border-radius:3px;
  display:flex;align-items:center;justify-content:center;
  font-family:"Disket Mono",monospace;font-size:36px;color:#FFFFFF}
.cv-trk-box.active{background:#FFD13A;border-color:#FFD13A;color:#000}
.cv-trk-box.dim{border-color:#6A6A6A;color:#676767}
.cv-track canvas{position:absolute;left:50px;top:2px}
#cv-params{position:absolute;top:370px;left:24px;right:24px;bottom:24px;display:flex;gap:31px}
.cv-col{width:324.75px;flex-shrink:0;display:flex;flex-direction:column;gap:21px}
.cv-col-hdr{font-family:"Disket Mono",monospace;font-size:40px;line-height:45px;color:#FFFFFF;height:45px}
.cv-col-rows{display:flex;flex-direction:column}
.cv-row{box-sizing:border-box;display:flex;justify-content:space-between;align-items:center;
  height:27px;border-bottom:1px solid #828282;font-family:"Disket Mono",monospace;font-size:24px}
.cv-rl{color:#FFFFFF}
.cv-rv{color:#FFFFFF}
.cv-row.stoch   .cv-rl{color:#FF8C00}
.cv-row.toggled .cv-rv{color:#FFB73A}
.cv-row.disabled .cv-rl,.cv-row.disabled .cv-rv{color:#505050}
.cv-row.focused{background:#1E2C35}
</style></head>
<body><div id="root">

<svg id="svg-bg" width="1440" height="920" viewBox="0 0 1440 920"
     style="position:absolute;top:0;left:0;pointer-events:none"
     fill="none" xmlns="http://www.w3.org/2000/svg">

  <!-- Hourglass -->
  <g id="icon-bpm" transform="translate(0,-6)">
  <path d="M1140 98L1160.42 111.898C1162.24 113.139 1163.33 115.203 1163.33 117.409V126.333C1163.33 127.254 1162.59 128 1161.67 128H1118.33C1117.41 128 1116.67 127.254 1116.67 126.333V117.409C1116.67 115.203 1117.76 113.139 1119.58 111.898L1140 98ZM1140 98L1160.42 84.1021C1162.24 82.8606 1163.33 80.7972 1163.33 78.591V69.6667C1163.33 68.7462 1162.59 68 1161.67 68H1118.33C1117.41 68 1116.67 68.7462 1116.67 69.6667V78.591C1116.67 80.7972 1117.76 82.8606 1119.58 84.1021L1140 98Z"
    stroke="white" stroke-width="6.66667" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M1150 125.357V127.5C1150 127.776 1149.78 128 1149.5 128H1130.5C1130.22 128 1130 127.776 1130 127.5V125.357C1130 124.915 1130.18 124.491 1130.49 124.179L1138.11 116.553C1139.16 115.511 1140.84 115.511 1141.89 116.553L1149.51 124.179C1149.82 124.491 1150 124.915 1150 125.357Z" fill="white"/>
  <path d="M1140 94.667L1156.67 84.667H1123.33L1140 94.667Z" fill="white"/>
  <path d="M1140 118V98" stroke="white" stroke-width="6.66667" stroke-linecap="round"/>
  </g>

  <!-- Recording indicator -->
  <circle id="rec-dot" cx="1054.5" cy="94.5" r="30.5" fill="#FF3333" opacity="0"/>

  <!-- TRK box -->
  <rect id="trk-box" x="52" y="756" width="280" height="120" rx="4" stroke="white" stroke-width="4"/>

  <!-- Step rects -->
  <g id="step-rects"></g>
  <!-- Page frame -->
  <rect id="page-frame" x="0" y="0" width="0" height="0" rx="10"
        fill="none" stroke="#FFFFFF" stroke-width="3" style="display:none"/>
</svg>

<!-- Page overview panel -->
<div id="page-view">
  <div class="pv-cell" style="left:52px;top:208px">  <div class="pv-v" id="pvv0">0</div><div class="pv-n" id="pvn0">PULS</div></div>
  <div class="pv-cell" style="left:362px;top:208px"> <div class="pv-v" id="pvv1">0</div><div class="pv-n" id="pvn1">STEP</div></div>
  <div class="pv-cell" style="left:720px;top:208px"> <div class="pv-v" id="pvv2">0</div><div class="pv-n" id="pvn2">PROB</div></div>
  <div class="pv-cell" style="left:1080px;top:208px"><div class="pv-v" id="pvv3">0</div><div class="pv-n" id="pvn3">MODE</div></div>
  <div class="pv-cell" style="left:52px;top:496px">  <div class="pv-v" id="pvv4">0</div><div class="pv-n" id="pvn4">VEL</div></div>
  <div class="pv-cell" style="left:362px;top:496px"> <div class="pv-v" id="pvv5">0</div><div class="pv-n" id="pvn5">SWNG</div></div>
  <div class="pv-cell" style="left:720px;top:496px"> <div class="pv-v" id="pvv6">0</div><div class="pv-n" id="pvn6">RESL</div></div>
  <div class="pv-cell" style="left:1080px;top:496px"><div class="pv-v" id="pvv7">0</div><div class="pv-n" id="pvn7">ROTA</div></div>
</div>

<!-- Detail view panel -->
<div id="detail-view" style="display:none">
  <div id="param-name">READY</div>
  <div id="param-value">—</div>
</div>

<div id="hdr-page">A·SEQ</div>
<div id="hdr-mode">FWD</div>
<div id="hdr-bpm">120</div>
<div id="trk-text">TRK1</div>
<!-- Mapping icon: sliders — visible when mapping mode active -->
<div id="hdr-map-icon"><svg viewBox="0 0 92 92" width="72" height="72" fill="none" xmlns="http://www.w3.org/2000/svg">
  <path d="M42.1667 30.6667L76.6667 30.6667" stroke="#A1A3A5" stroke-width="7.66667" stroke-linecap="round"/>
  <path d="M15.3333 61.3333L53.6666 61.3333" stroke="#A1A3A5" stroke-width="7.66667" stroke-linecap="round"/>
  <ellipse cx="26.8333" cy="30.6667" rx="11.5" ry="11.5" transform="rotate(90 26.8333 30.6667)" stroke="#A1A3A5" stroke-width="7.66667" stroke-linecap="round"/>
  <ellipse cx="65.1667" cy="61.3333" rx="11.5" ry="11.5" transform="rotate(90 65.1667 61.3333)" stroke="#A1A3A5" stroke-width="7.66667" stroke-linecap="round"/>
</svg></div>

<!-- Bank View — vista dentro de #root, hereda el scale automáticamente -->
<div id="bank-view" style="display:none;position:absolute;top:0;left:0;width:1440px;height:920px;background:#191919">
  <div id="bvTitleBank">BANK<span id="bvBankNum"> 01</span></div>
  <div id="bvTitlePattern">PAT<span id="bvPatNum"> —</span></div>
  <div id="bvStatus"></div>
  <div id="bvGrid"></div>
</div>

<!-- Compact View — solo lectura, todas las pistas + todos los params -->
<div id="compact-view">
  <div id="cv-tracks">
    <div class="cv-half">
      <div class="cv-track" style="top:0">   <div class="cv-trk-box" id="cvb0">1</div><canvas id="cvc0" width="638" height="54"></canvas></div>
      <div class="cv-track" style="top:74px"><div class="cv-trk-box" id="cvb1">2</div><canvas id="cvc1" width="638" height="54"></canvas></div>
      <div class="cv-track" style="top:148px"><div class="cv-trk-box" id="cvb2">3</div><canvas id="cvc2" width="638" height="54"></canvas></div>
      <div class="cv-track" style="top:222px"><div class="cv-trk-box" id="cvb3">4</div><canvas id="cvc3" width="638" height="54"></canvas></div>
    </div>
    <div class="cv-half">
      <div class="cv-track" style="top:0">   <div class="cv-trk-box" id="cvb4">5</div><canvas id="cvc4" width="638" height="54"></canvas></div>
      <div class="cv-track" style="top:74px"><div class="cv-trk-box" id="cvb5">6</div><canvas id="cvc5" width="638" height="54"></canvas></div>
      <div class="cv-track" style="top:148px"><div class="cv-trk-box" id="cvb6">7</div><canvas id="cvc6" width="638" height="54"></canvas></div>
      <div class="cv-track" style="top:222px"><div class="cv-trk-box" id="cvb7">8</div><canvas id="cvc7" width="638" height="54"></canvas></div>
    </div>
  </div>
  <div id="cv-params">
    <div class="cv-col">
      <div class="cv-col-hdr">A·SEQ</div>
      <div class="cv-col-rows">
        <div class="cv-row" id="cvp0_0"><span class="cv-rl" id="cvpl0_0">PULS</span><span class="cv-rv" id="cvpv0_0">—</span></div>
        <div class="cv-row" id="cvp0_1"><span class="cv-rl" id="cvpl0_1">STEP</span><span class="cv-rv" id="cvpv0_1">—</span></div>
        <div class="cv-row" id="cvp0_2"><span class="cv-rl" id="cvpl0_2">PROB</span><span class="cv-rv" id="cvpv0_2">—</span></div>
        <div class="cv-row" id="cvp0_3"><span class="cv-rl" id="cvpl0_3">MODE</span><span class="cv-rv" id="cvpv0_3">—</span></div>
        <div class="cv-row" id="cvp0_4"><span class="cv-rl" id="cvpl0_4">VEL</span> <span class="cv-rv" id="cvpv0_4">—</span></div>
        <div class="cv-row" id="cvp0_5"><span class="cv-rl" id="cvpl0_5">SWNG</span><span class="cv-rv" id="cvpv0_5">—</span></div>
        <div class="cv-row" id="cvp0_6"><span class="cv-rl" id="cvpl0_6">RESL</span><span class="cv-rv" id="cvpv0_6">—</span></div>
        <div class="cv-row" id="cvp0_7"><span class="cv-rl" id="cvpl0_7">ROTA</span><span class="cv-rv" id="cvpv0_7">—</span></div>
      </div>
    </div>
    <div class="cv-col">
      <div class="cv-col-hdr">B·NOTE</div>
      <div class="cv-col-rows">
        <div class="cv-row" id="cvp1_0"><span class="cv-rl" id="cvpl1_0">NOTE</span><span class="cv-rv" id="cvpv1_0">—</span></div>
        <div class="cv-row" id="cvp1_1"><span class="cv-rl" id="cvpl1_1">SCAL</span><span class="cv-rv" id="cvpv1_1">—</span></div>
        <div class="cv-row" id="cvp1_2"><span class="cv-rl" id="cvpl1_2">OCT</span> <span class="cv-rv" id="cvpv1_2">—</span></div>
        <div class="cv-row" id="cvp1_3"><span class="cv-rl" id="cvpl1_3">DENS</span><span class="cv-rv" id="cvpv1_3">—</span></div>
        <div class="cv-row" id="cvp1_4"><span class="cv-rl" id="cvpl1_4">SPRD</span><span class="cv-rv" id="cvpv1_4">—</span></div>
        <div class="cv-row" id="cvp1_5"><span class="cv-rl" id="cvpl1_5">HARM</span><span class="cv-rv" id="cvpv1_5">—</span></div>
        <div class="cv-row" id="cvp1_6"><span class="cv-rl" id="cvpl1_6">INTV</span><span class="cv-rv" id="cvpv1_6">—</span></div>
        <div class="cv-row" id="cvp1_7"><span class="cv-rl" id="cvpl1_7">NLEN</span><span class="cv-rv" id="cvpv1_7">—</span></div>
      </div>
    </div>
    <div class="cv-col">
      <div class="cv-col-hdr">C·FX</div>
      <div class="cv-col-rows">
        <div class="cv-row" id="cvp2_0"><span class="cv-rl" id="cvpl2_0">DLY</span> <span class="cv-rv" id="cvpv2_0">—</span></div>
        <div class="cv-row" id="cvp2_1"><span class="cv-rl" id="cvpl2_1">DTIM</span><span class="cv-rv" id="cvpv2_1">—</span></div>
        <div class="cv-row" id="cvp2_2"><span class="cv-rl" id="cvpl2_2">FDBK</span><span class="cv-rv" id="cvpv2_2">—</span></div>
        <div class="cv-row" id="cvp2_3"><span class="cv-rl" id="cvpl2_3">RDEC</span><span class="cv-rv" id="cvpv2_3">—</span></div>
        <div class="cv-row" id="cvp2_4"><span class="cv-rl" id="cvpl2_4">RSPD</span><span class="cv-rv" id="cvpv2_4">—</span></div>
        <div class="cv-row" id="cvp2_5"><span class="cv-rl" id="cvpl2_5">RTCH</span><span class="cv-rv" id="cvpv2_5">—</span></div>
        <div class="cv-row" id="cvp2_6"><span class="cv-rl" id="cvpl2_6">GATE</span><span class="cv-rv" id="cvpv2_6">—</span></div>
        <div class="cv-row" id="cvp2_7"><span class="cv-rl" id="cvpl2_7">CC</span>  <span class="cv-rv" id="cvpv2_7">—</span></div>
      </div>
    </div>
    <div class="cv-col">
      <div class="cv-col-hdr">D·CONF</div>
      <div class="cv-col-rows">
        <div class="cv-row" id="cvp3_0"><span class="cv-rl" id="cvpl3_0">PROG</span><span class="cv-rv" id="cvpv3_0">—</span></div>
        <div class="cv-row" id="cvp3_1"><span class="cv-rl" id="cvpl3_1">BANK</span><span class="cv-rv" id="cvpv3_1">—</span></div>
        <div class="cv-row" id="cvp3_2"><span class="cv-rl" id="cvpl3_2">PTBK</span><span class="cv-rv" id="cvpv3_2">—</span></div>
        <div class="cv-row" id="cvp3_3"><span class="cv-rl" id="cvpl3_3">PTRN</span><span class="cv-rv" id="cvpv3_3">—</span></div>
        <div class="cv-row" id="cvp3_4"><span class="cv-rl" id="cvpl3_4">CHAN</span><span class="cv-rv" id="cvpv3_4">—</span></div>
        <div class="cv-row" id="cvp3_5"><span class="cv-rl" id="cvpl3_5">CLK</span> <span class="cv-rv" id="cvpv3_5">—</span></div>
        <div class="cv-row" id="cvp3_6"><span class="cv-rl" id="cvpl3_6">PORT</span><span class="cv-rv" id="cvpv3_6">—</span></div>
        <div class="cv-row" id="cvp3_7"><span class="cv-rl" id="cvpl3_7">SCRI</span> <span class="cv-rv" id="cvpv3_7">—</span></div>
      </div>
    </div>
  </div>
</div>

<!-- CONF view: Settings / Mapping / Imp·Exp -->
<div id="conf-view">
  <div id="conf-hdr">CONF</div>
  <div id="conf-nav">
    <span id="conf-grp-lbl" style="color:#A1A3A5;margin-right:16px">KNOB</span>
    <span class="arrow" id="conf-prev">◄</span>
    <span id="conf-pg-num">1/4</span>
    <span class="arrow" id="conf-next">►</span>
  </div>

  <!-- SETTINGS panel -->
  <div id="conf-settings">
    <div class="cf-row" id="cfs0" style="left:48px;top:116px">
      <span class="cf-name">MIDI OUT 1</span><span class="cf-val" id="cfv-int1">—</span>
    </div>
    <div class="cf-row" id="cfs1" style="left:48px;top:240px">
      <span class="cf-name">MIDI OUT 2</span><span class="cf-val" id="cfv-int2">—</span>
    </div>
    <div class="cf-row" id="cfs2" style="left:48px;top:364px">
      <span class="cf-name">PAD</span><span class="cf-val" id="cfv-lp">—</span>
    </div>
    <div class="cf-row" id="cfs3" style="left:48px;top:488px">
      <span class="cf-name">MIDI IN 1</span><span class="cf-val" id="cfv-midi-in1">—</span>
    </div>
    <div class="cf-row" id="cfs4" style="left:48px;top:612px">
      <span class="cf-name">MIDI IN 2</span><span class="cf-val" id="cfv-midi-in2">—</span>
    </div>
  </div>

  <!-- MAPPING panel -->
  <div id="conf-mapping">
    <div class="cf-row" id="cfr0" style="left:48px;top:208px">
      <span class="cf-name" id="cfn0">PULS</span><span class="cf-val" id="cfc0">N/A</span>
    </div>
    <div class="cf-row" id="cfr1" style="left:48px;top:346px">
      <span class="cf-name" id="cfn1">STEP</span><span class="cf-val" id="cfc1">N/A</span>
    </div>
    <div class="cf-row" id="cfr2" style="left:48px;top:484px">
      <span class="cf-name" id="cfn2">PROB</span><span class="cf-val" id="cfc2">N/A</span>
    </div>
    <div class="cf-row" id="cfr3" style="left:48px;top:622px">
      <span class="cf-name" id="cfn3">MODE</span><span class="cf-val" id="cfc3">N/A</span>
    </div>
    <div class="cf-row" id="cfr4" style="left:764px;top:208px">
      <span class="cf-name" id="cfn4">VEL</span><span class="cf-val" id="cfc4">N/A</span>
    </div>
    <div class="cf-row" id="cfr5" style="left:764px;top:346px">
      <span class="cf-name" id="cfn5">SWNG</span><span class="cf-val" id="cfc5">N/A</span>
    </div>
    <div class="cf-row" id="cfr6" style="left:764px;top:484px">
      <span class="cf-name" id="cfn6">RESL</span><span class="cf-val" id="cfc6">N/A</span>
    </div>
    <div class="cf-row" id="cfr7" style="left:764px;top:622px">
      <span class="cf-name" id="cfn7">ROTA</span><span class="cf-val" id="cfc7">N/A</span>
    </div>
  </div>

  <!-- IMP/EXP panel -->
  <div id="conf-impexp">
    <div class="cf-row" id="cfie0" style="left:48px;top:208px">
      <span class="cf-name">EXPORTAR MAPEO</span>
      <span class="cf-val" id="cfiev0">↓ .json</span>
    </div>
    <div class="cf-row" id="cfie1" style="left:48px;top:346px">
      <span class="cf-name">IMPORTAR FICHERO</span>
      <span class="cf-val" id="cfiev1">↑ abrir…</span>
    </div>
    <div class="cf-row" id="cfie2" style="left:48px;top:484px">
      <span class="cf-name">PRESET</span>
      <span class="cf-val" id="cfiev2">NANO KONTROL</span>
    </div>
    <div class="cf-row" id="cfie3" style="left:48px;top:622px">
      <span class="cf-name">PRESET</span>
      <span class="cf-val" id="cfiev3">LAUNCHKEY MK4</span>
    </div>
    <div id="cfie-status"></div>
    <input type="file" id="cfie-file-input" accept=".json" style="display:none">
  </div>

  <!-- SCRIPT panel -->
  <div id="conf-script">
    <div id="cfsc-track-info">T1 → <span id="cfsc-current">OFF</span></div>
    <div id="cfsc-list"></div>
  </div>

  <!-- Pestañas -->
  <div class="cf-tab cf-tab-active" id="cf-tab-settings">STG</div>
  <div class="cf-tab" id="cf-tab-mapping">MAP</div>
  <div class="cf-tab" id="cf-tab-impexp">MOV</div>
  <div class="cf-tab" id="cf-tab-script">SCR</div>
</div>

</div>

<script>
function scaleRoot(){
  const r=document.getElementById('root');
  const s=Math.min(window.innerWidth/1440,window.innerHeight/920);
  r.style.transform=`scale(${s})`;
  r.style.left=Math.round((window.innerWidth-1440*s)/2)+'px';
  r.style.top=Math.round((window.innerHeight-920*s)/2)+'px';
}
window.addEventListener('resize',scaleRoot); scaleRoot();

const NS='http://www.w3.org/2000/svg';
const STEP_X0=362,STEP_Y=752,STEP_H=128,STEP_GAP=16;
const STEP_ROW_H=56,STEP_ROW_GAP=16,STEP_GAP2=8;
let lastN=0;

function updateSteps(pattern,cursor,playing,stepPg,locks,focusStep,recStep){
  const g=document.getElementById('step-rects');
  const n=pattern.length||16;
  const twoRow=n>32;
  const cols=twoRow?32:n;
  const gap=twoRow?STEP_GAP2:STEP_GAP;
  const sw=(1030-gap*(cols-1))/cols;
  const sh=twoRow?STEP_ROW_H:STEP_H;
  if(lastN!==n){
    g.innerHTML='';
    for(let i=0;i<n;i++){
      const r=document.createElementNS(NS,'rect');
      r.setAttribute('rx','6');
      const col=twoRow?(i%32):i;
      const row=twoRow?Math.floor(i/32):0;
      r.setAttribute('x',STEP_X0+col*(sw+gap));
      r.setAttribute('y',STEP_Y+row*(STEP_ROW_H+STEP_ROW_GAP));
      r.setAttribute('width',sw);
      r.setAttribute('height',sh);
      r.setAttribute('fill','#2E383D');
      g.appendChild(r);
    }
    lastN=n;
  }
  for(let i=0;i<n;i++){
    const r=g.children[i];
    const on=pattern[i],cur=(i===cursor);
    const hasLock=locks&&locks[i];
    const isFocus=(focusStep!=null&&i===focusStep);
    const isRecStep=(recStep!=null&&recStep>=0&&i===recStep);
    const isRest=(i===_restFlashStep);
    let fill;
    if(isFocus)          fill='#FFD700';           // gold — step enfocado/held (p-lock)
    else if(isRecStep)   fill=on?'#FF8C00':'#FF5555'; // rojo — cursor de grabación
    else if(isRest)      fill='#FF4466';           // rosa — rest (step saltado)
    else if(cur)         fill=on?'#FF8C00':'#8C9AA3';
    else if(on)          fill=hasLock?'#00CCCC':'#FFFFFF'; // teal si tiene lock
    else                 fill=hasLock?'#1A4A54':'#2E383D'; // teal oscuro si lock + off
    if(r.getAttribute('fill')!==fill) r.setAttribute('fill',fill);
  }
  // Marco de la página activa: envuelve los 8 steps de la página
  const frame=document.getElementById('page-frame');
  const pg0=stepPg*8;
  if(frame && pg0<n){
    const col0=twoRow?(pg0%32):pg0;
    const row0=twoRow?Math.floor(pg0/32):0;
    const x=STEP_X0+col0*(sw+gap)-6;
    const y=STEP_Y+row0*(STEP_ROW_H+STEP_ROW_GAP)-6;
    const w=8*sw+7*gap+12;
    const h=sh+12;
    frame.setAttribute('x',x);
    frame.setAttribute('y',y);
    frame.setAttribute('width',w);
    frame.setAttribute('height',h);
    frame.style.display='block';
  } else if(frame){
    frame.style.display='none';
  }
}

function setText(id,v){const e=document.getElementById(id);if(e&&e.textContent!==String(v))e.textContent=String(v);}
function setAttr(id,attr,v){const e=document.getElementById(id);if(e&&e.getAttribute(attr)!==String(v))e.setAttribute(attr,String(v));}
function show(id){const e=document.getElementById(id);if(e&&e.style.display!=='block')e.style.display='block';}
function hide(id){const e=document.getElementById(id);if(e&&e.style.display!=='none')e.style.display='none';}

const MODE_ABBR={forward:'FWD',reverse:'RVR',bounce:'BNC',random:'RND',snake:'SNK',drunk:'DRK'};

// ── Cursor: reflejo directo del servidor ──────────────────────────────────────
// Sin timer local. El SSE ahora es event-driven (no polling) → <1ms de latencia.
let _localCursor=0, _localPattern=[], _localStepMs=125, _localStepPg=0;
let _lastPlaying=false, _localLocks=[], _localFocusStep=null;
let _restFlashStep=-1, _restFlashTimer=null;
let _recStep=-1;
var _recording=false;

function _startCursorAnim(cursor,pattern,stepMs,stepPg,playing,serverTs,locks,focusStep,recStep){
  _localPattern=pattern;
  _localStepMs=Math.max(30,stepMs||125);
  _localStepPg=stepPg||0;
  _lastPlaying=playing;
  _localCursor=cursor;
  _localLocks=locks||[];
  _localFocusStep=(focusStep!=null)?focusStep:null;
  updateSteps(_localPattern,_localCursor,playing,_localStepPg,_localLocks,_localFocusStep,recStep!=null?recStep:_recStep);
}
// ─────────────────────────────────────────────────────────────────────────────

// Grid 8×8 — coords exactas del SVG "Bank View.svg" (1440×920 nativo)
var _BV_COLS=[380,468,556,644,732,820,908,996];
var _BV_ROWS=[176,264,352,440,528,616,704,792];
(function(){
  var h='';
  for(var r=0;r<8;r++){
    for(var c=0;c<8;c++){
      var slot=r*8+c;
      h+='<div class="bv-cell" id="bvc'+slot+'" style="left:'+_BV_COLS[c]+'px;top:'+_BV_ROWS[r]+'px"></div>';
    }
  }
  var g=document.getElementById('bvGrid');
  if(g) g.innerHTML=h;
})();

function renderBankView(s){
  var view=document.getElementById('bank-view');
  if(!view) return;

  var isOpen=!!s.kb_bank_view;
  view.style.display=isOpen?'block':'none';
  _bankViewActive=isOpen;
  if(!isOpen) return;

  var bankIdx=(s.pat_bank!=null?s.pat_bank:0);
  var bank=(s.banks_grid&&s.banks_grid[bankIdx])||{};
  var cur=s.kb_bank_cursor||[0,0];
  var activeSlot =(typeof s.slot==='number'  && s.slot>=0)?s.slot:-1;
  var pendingSlot=(typeof s.pending_slot==='number')?s.pending_slot:-1;
  var morphBSlot =(s.morph_b_bank===bankIdx && s.morph_b_slot>=0)?s.morph_b_slot:-1;

  var bnEl=document.getElementById('bvBankNum');
  if(bnEl) bnEl.textContent=' '+String(bankIdx+1).padStart(2,'0');
  var pnEl=document.getElementById('bvPatNum');
  if(pnEl) pnEl.textContent=' '+(activeSlot>=0?String(activeSlot+1).padStart(2,'0'):'—');
  // Status (save/load/warn)
  var stEl=document.getElementById('bvStatus');
  if(stEl){
    stEl.textContent=s.bv_status||'';
    stEl.className=s.bv_status_warn?'warn':'';
  }

  // Mapa: slot → posición(es) en el chain [1-based]
  var chain=(s.export_chain||[]);
  var chainMap={};  // slot → [pos, pos, ...] para el banco actual
  chain.forEach(function(entry,i){
    if(entry[0]===bankIdx){
      var sl=entry[1];
      if(!chainMap[sl]) chainMap[sl]=[];
      chainMap[sl].push(i+1);
    }
  });

  var curSlot=cur[0]*8+cur[1];
  for(var slot=0;slot<64;slot++){
    var el=document.getElementById('bvc'+slot);
    if(!el) continue;
    var cls='bv-cell';
    if(slot===pendingSlot)      cls+=' pending';
    else if(slot===morphBSlot)  cls+=' morph-b';
    else if(slot===activeSlot)  cls+=' active';
    else if(bank[String(slot)]!==undefined) cls+=' has';
    if(slot===curSlot)          cls+=' cursor';
    el.className=cls;
    // Badge de posición en chain
    var badge=el.querySelector('.bv-chain-badge');
    var positions=chainMap[slot];
    if(positions&&positions.length){
      if(!badge){badge=document.createElement('span');badge.className='bv-chain-badge';el.appendChild(badge);}
      badge.textContent=positions.join(',');
    } else {
      if(badge) badge.remove();
    }
  }

}

const CVC_W=638, CVC_H=54;
function renderCompactView(s){
  const el=document.getElementById('compact-view');
  if(!el) return;
  if(!s.compact_view){
    el.style.display='none';
    // Restaurar posiciones originales del header
    const _hp=document.getElementById('hdr-page');
    const _hb=document.getElementById('hdr-bpm');
    if(_hp){_hp.style.left='48px';  _hp.style.right='';}
    if(_hb){_hb.style.left='1191px';_hb.style.right='';}
    return;
  }
  el.style.display='block';

  // Dibujar step grids en canvas (dimensiones fijas, sin getBoundingClientRect)
  const activeTi=s.track||0;
  for(let ti=0;ti<8;ti++){
    const td=s.tracks&&s.tracks[ti];
    if(!td) continue;
    const cvs=document.getElementById('cvc'+ti);
    if(!cvs) continue;
    // Canvas con dimensiones fijas; ajustar solo si difieren (evita reset de contexto)
    if(cvs.width!==CVC_W)  cvs.width=CVC_W;
    if(cvs.height!==CVC_H) cvs.height=CVC_H;
    const ctx=cvs.getContext('2d');
    ctx.clearRect(0,0,CVC_W,CVC_H);
    const pat=td.pattern||[];
    const n=Math.max(pat.length,1);
    const twoRow=n>32;
    const cols=twoRow?32:n;
    const rowGap=4;
    const gap=twoRow?2:3;
    const sh=twoRow?Math.floor((CVC_H-rowGap)/2):CVC_H;
    const sw=(CVC_W-(cols-1)*gap)/cols;
    const isActive=(ti===activeTi);
    const cur=td.cursor||0;
    const locks=td.locks||[];
    const isMuted=!!td.muted;
    for(let i=0;i<n;i++){
      const col=twoRow?(i%32):i;
      const row=twoRow?Math.floor(i/32):0;
      const x=Math.round(col*(sw+gap));
      const y=row*(sh+rowGap);
      const on=pat[i],isCur=(i===cur),hasLock=locks[i];
      let fill;
      if(isCur&&isActive)  fill=on?'#FF8C00':'#8C9AA3';   // cursor naranja solo en pista activa
      else if(on&&hasLock) fill=isMuted?'#1A3A40':'#00CCCC';
      else if(on)          fill=isMuted?'#283640':'#FFFFFF'; // no-muted = blanco igual que activo
      else if(hasLock)     fill=isMuted?'#111820':'#1A3540';
      else                 fill=isMuted?'#141C22':'#2A3540';
      ctx.fillStyle=fill;
      ctx.beginPath();
      if(ctx.roundRect) ctx.roundRect(x,y,Math.max(sw,1),sh,2);
      else ctx.rect(x,y,Math.max(sw,1),sh);
      ctx.fill();
    }
    // Borde blanco de página activa (solo pista activa)
    if(isActive && s.step_pg!=null){
      const pgStart=(s.step_pg||0)*8;
      const pgEnd=Math.min(pgStart+7, n-1);
      if(pgEnd>=pgStart){
        const rowF=twoRow?Math.floor(pgStart/32):0;
        const c0=twoRow?(pgStart%32):pgStart;
        const c1=twoRow?(pgEnd%32):pgEnd;
        const fx=Math.round(c0*(sw+gap));
        const fy=rowF*(sh+rowGap);
        const fw=Math.round(c1*(sw+gap))+sw-fx;
        const fh=sh;
        // Clampear al interior del canvas para evitar clipping en los bordes
        const pad=3;
        const rx=Math.max(1, fx-pad);
        const ry=Math.max(1, fy-pad);
        const rx2=Math.min(CVC_W-1, fx+fw+pad);
        const ry2=Math.min(CVC_H-1, fy+fh+pad);
        ctx.strokeStyle='#FFFFFF';
        ctx.lineWidth=2;
        ctx.beginPath();
        if(ctx.roundRect) ctx.roundRect(rx,ry,rx2-rx,ry2-ry,4);
        else ctx.rect(rx,ry,rx2-rx,ry2-ry);
        ctx.stroke();
      }
    }
    // Track box: active = amarillo, muted = dim, normal = blanco
    const box=document.getElementById('cvb'+ti);
    if(box){
      box.classList.toggle('active',isActive&&!td.muted);
      box.classList.toggle('dim',!!td.muted);
    }
  }

  // Alinear header a los bordes del canvas cuando compact view está activo
  const hdrPage=document.getElementById('hdr-page');
  const hdrBpm =document.getElementById('hdr-bpm');
  if(hdrPage){hdrPage.style.left='74px';  hdrPage.style.right='';}
  if(hdrBpm) {hdrBpm.style.right='24px';  hdrBpm.style.left='';}

  // Actualizar columnas de params + focus en param activo
  const activePg=s.page_idx!=null?s.page_idx:(s.page_params?s.page_params._pg:undefined);
  // page_idx no existe; inferimos la página activa por nombre de página
  const PAGE_NAMES_CV=['A·SEQ','B·NOTE','C·FX','D·CONF'];
  const curPg=PAGE_NAMES_CV.indexOf(s.page||'');  // 0-3
  const curParam=s.kb_param!=null?s.kb_param:-1;
  if(s.all_page_params){
    for(let pg=0;pg<4;pg++){
      const pgp=s.all_page_params[pg]||[];
      for(let i=0;i<8;i++){
        const p=pgp[i]||{};
        const rowEl=document.getElementById(`cvp${pg}_${i}`);
        const lblEl=document.getElementById(`cvpl${pg}_${i}`);
        const valEl=document.getElementById(`cvpv${pg}_${i}`);
        if(lblEl) lblEl.textContent=(p.label||'').toUpperCase();
        if(valEl) valEl.textContent=(p.value!=null&&p.value!==''?String(p.value):'—').toUpperCase();
        if(rowEl){
          rowEl.classList.toggle('stoch',  !!p.stoch);
          rowEl.classList.toggle('toggled',!!p.toggled);
          rowEl.classList.toggle('disabled',!!p.disabled);
          rowEl.classList.toggle('focused', pg===curPg&&i===curParam);
        }
      }
    }
  }
}

function applyState(s){
  renderBankView(s);
  renderCompactView(s);
  renderScriptView(s);
  // Sincronizar estado de teclado con servidor
  if(s.kb_step_focus!==undefined) _kbStepFocus=!!s.kb_step_focus;
  if(s.kb_enabled!==undefined)    _kbEnabled=!!s.kb_enabled;
  if(s.rec_step!==undefined)      _recStep=s.rec_step;
  _recording=!!s.recording;
  // Header icons
  var mapIcon=document.getElementById('hdr-map-icon');
  if(mapIcon) mapIcon.style.display=s.mapping_mode?'block':'none';
  // Header (always visible)
  if(s.page)  setText('hdr-page',s.page);
  setText('hdr-bpm',s.bpm||120);
  if(s.header_counter){
    setText('hdr-mode',s.header_counter);
  } else if(s.step_pg!==undefined){
    const tot=s.step_pg_total||1;
    setText('hdr-mode',(s.step_pg+1)+'/'+tot);
  }
  var _allMode=!!s.kb_all_mode;
  setText('trk-text',_allMode?'ALL':'TRK'+((s.track||0)+1));
  setAttr('rec-dot','opacity',s.recording?'1':'0');
  // Mute state of active track → dim TRK label + steps
  var _activeMuted=!_allMode&&s.tracks&&s.tracks[s.track||0]&&s.tracks[s.track||0].muted;
  var _trkTxtEl=document.getElementById('trk-text');
  if(_trkTxtEl) _trkTxtEl.style.color=_activeMuted?'#3A4A53':(_allMode?'#FFB73A':'#FFFFFF');
  setAttr('trk-box','stroke',_activeMuted?'#3A4A53':(_allMode?'#FFB73A':'#FFFFFF'));
  var _sg=document.getElementById('step-rects');
  if(_sg) _sg.setAttribute('opacity',_activeMuted?'0.35':'1');
  // Steps: sincronizar cursor desde servidor y arrancar animación local
  if(s.tracks&&s.tracks[s.track]){
    const td=s.tracks[s.track];
    const locks=td.locks||[];
    // step_focus=[ti,step_idx]; solo aplica si es la pista activa
    const focusStep=(s.step_focus&&s.step_focus[0]===s.track)?s.step_focus[1]:null;
    _startCursorAnim(td.cursor||0,td.pattern||[],s.step_ms,s.step_pg,!!s.playing,s.server_ts,locks,focusStep,_recStep);
  }
  if(s.rest_flash!=null){
    _restFlashStep=s.rest_flash;
    clearTimeout(_restFlashTimer);
    _restFlashTimer=setTimeout(()=>{_restFlashStep=-1;},350);
  }

  // View toggle
  if(s.mapping_mode){
    hide('page-view'); hide('detail-view');
  } else if(s.view_mode==='detail'){
    hide('page-view'); show('detail-view');
    if(s.label) setText('param-name',s.label.toUpperCase());
    if(s.value) setText('param-value',String(s.value).toUpperCase());
  } else {
    show('page-view'); hide('detail-view');
    if(s.page_params){
      const kbp=s.kb_param!=null?s.kb_param:-1;
      s.page_params.forEach((p,i)=>{
        setText('pvn'+i,(p.label||'').toUpperCase());
        const n=document.getElementById('pvn'+i);
        const v=document.getElementById('pvv'+i);
        const cell=n&&n.parentElement;
        if(n) n.classList.toggle('stoch',!!p.stoch);
        if(n) n.classList.toggle('toggled',!!p.toggled);
        if(n) n.classList.toggle('disabled',!!p.disabled);
        if(v) v.classList.toggle('disabled',!!p.disabled);
        if(cell) cell.classList.toggle('kb-sel',i===kbp);
        setText('pvv'+i,(p.value!=null&&p.value!==''?String(p.value):'—').toUpperCase());
      });
    }
  }
  applyMappingMode(s);
}

// rAF batching — coalesce rapid updates into one paint frame
var _pendingState=null;
var _rafPending=false;
function _scheduleApply(s){
  _pendingState=s;
  if(!_rafPending){
    _rafPending=true;
    requestAnimationFrame(function(){
      _rafPending=false;
      if(_pendingState){applyState(_pendingState);_pendingState=null;}
    });
  }
}

// Polling mode — más fiable que SSE en móvil
let _pollTimer=null;
function startPolling(){
  if(_pollTimer) return;
  _pollTimer=setInterval(()=>{
    fetch('/api/state').then(r=>r.json()).then(_scheduleApply).catch(()=>{});
  },150);
}

// SSE mode — más eficiente en desktop
let _es=null;
function connectSSE(){
  if(_es) try{_es.close();}catch(e){}
  _es=new EventSource('/events');
  _es.onmessage=ev=>_scheduleApply(JSON.parse(ev.data));
  _es.onerror=()=>{_es.close();_es=null;startPolling();};
}

updateSteps(Array(16).fill(false),-1,false,0,[],null,-1);

// ── CONF view ────────────────────────────────────────────────────────────────
var _confTab='settings';
var _confCursor=0;
var _confActive=false;
var _confGroup='knob';
const MISC_LABELS=['BPM','UNDO','REDO','COPY','PASTE','S.BANK','L.BANK','SHIFT'];
const GRP_DISPLAY={'knob':'KNOB','fade':'FADE','btn_s':'S-BTN','btn_m':'M-BTN','misc':'MISC'};
const GRP_MAX={'knob':4,'fade':4,'btn_s':4,'btn_m':4,'misc':1};

function _confCursorMax(){
  if(_confTab==='mapping') return 7;
  if(_confTab==='settings') return 4;  // 5 filas: INT1 INT2 LP CTRL1 CTRL2
  if(_confTab==='impexp') return 3;
  return 0;
}

function _updateConfCursor(){
  document.querySelectorAll('.cf-row').forEach(r=>r.classList.remove('cf-sel'));
  const prefix=_confTab==='mapping'?'cfr':_confTab==='settings'?'cfs':_confTab==='impexp'?'cfie':null;
  if(prefix){
    const row=document.getElementById(prefix+_confCursor);
    if(row) row.classList.add('cf-sel');
  }
}

function _cfieStatus(msg){
  const el=document.getElementById('cfie-status');
  if(!el) return;
  el.textContent=msg;
  el.style.color=msg.startsWith('✓')?'#00ff88':'#ff4455';
  clearTimeout(_cfieStatusTimer);
  _cfieStatusTimer=setTimeout(()=>{ el.textContent=''; },2800);
}
var _cfieStatusTimer=null;

function _cfieActivate(idx){
  if(idx===0){
    // Exportar mapeo actual
    fetch('/api/mapping/export').then(r=>{
      if(!r.ok){_cfieStatus('✗ ERROR');return;}
      return r.blob();
    }).then(b=>{
      if(!b) return;
      const url=URL.createObjectURL(b);
      const a=document.createElement('a');
      a.href=url; a.download='mapping.json'; document.body.appendChild(a); a.click();
      document.body.removeChild(a); URL.revokeObjectURL(url);
      _cfieStatus('✓ EXPORTADO');
    }).catch(()=>_cfieStatus('✗ ERROR'));
  } else if(idx===1){
    // Importar desde fichero
    document.getElementById('cfie-file-input').click();
  } else if(idx===2){
    fetch('/api/mapping/preset',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:'nano'})})
    .then(r=>_cfieStatus(r.ok?'✓ NANO KONTROL CARGADO':'✗ ERROR'))
    .catch(()=>_cfieStatus('✗ ERROR'));
  } else if(idx===3){
    fetch('/api/mapping/preset',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:'launchkey'})})
    .then(r=>_cfieStatus(r.ok?'✓ LAUNCHKEY MK4 CARGADO':'✗ ERROR'))
    .catch(()=>_cfieStatus('✗ ERROR'));
  }
}

document.getElementById('cfie-file-input').addEventListener('change',function(e){
  const file=e.target.files[0];
  if(!file) return;
  const reader=new FileReader();
  reader.onload=ev=>{
    try{
      const data=JSON.parse(ev.target.result);
      fetch('/api/mapping/import',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(data)})
      .then(r=>{
        if(r.ok) _cfieStatus('✓ MAPEO IMPORTADO');
        else r.text().then(t=>_cfieStatus('✗ '+(t||'ERROR')));
      }).catch(()=>_cfieStatus('✗ ERROR'));
    } catch(err){ _cfieStatus('✗ JSON INVÁLIDO'); }
  };
  reader.readAsText(file);
  e.target.value='';
});

[0,1,2,3].forEach(i=>{
  const el=document.getElementById('cfie'+i);
  if(el) el.onclick=()=>_cfieActivate(i);
});

function _confCursorActivate(){
  if(_confTab==='mapping'){
    const pi=_pendingState?(_pendingState.page_idx||0):0;
    const grp=_confGroup||'knob';
    fetch('/api/learn',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({knob:_confCursor,page:pi,group:grp})});
  } else if(_confTab==='settings'){
    const keys=['midi_out','midi_out2','lp_port','kb_port','nk_in'];
    fetch('/api/settings/cycle_port',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({slot:keys[_confCursor]})});
  } else if(_confTab==='impexp'){
    _cfieActivate(_confCursor);
  }
}

document.getElementById('conf-prev').onclick=()=>confPageDelta(-1);
document.getElementById('conf-next').onclick=()=>confPageDelta(1);

function confPageDelta(d){
  fetch('/api/mapping/page',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({delta:d})});
}

function switchConfTab(tab){
  _confTab=tab;
  _confCursor=0;
  ['settings','mapping','impexp','script'].forEach(t=>{
    if(t==='script'){
      const el=document.getElementById('conf-script');
      if(el) el.style.display=(t===tab)?'block':'none';
    }else{
      const el=document.getElementById('conf-'+t);
      if(el) el.style.display=(t===tab)?'block':'none';
    }
    const btn=document.getElementById('cf-tab-'+t);
    if(btn) btn.classList.toggle('cf-tab-active',t===tab);
  });
  const nav=document.getElementById('conf-nav');
  if(nav) nav.style.display=(tab==='mapping')?'flex':'none';
  _updateConfCursor();
}

document.getElementById('cf-tab-settings').onclick=()=>switchConfTab('settings');
document.getElementById('cf-tab-mapping').onclick=()=>switchConfTab('mapping');
document.getElementById('cf-tab-impexp').onclick=()=>switchConfTab('impexp');
document.getElementById('cf-tab-script').onclick=()=>switchConfTab('script');

var _scriptsList=null;
function _loadScriptsList(){
  fetch('/api/scripts').then(function(r){return r.json();}).then(function(data){
    _scriptsList=data;
  }).catch(function(){});
}
_loadScriptsList();

function renderScriptView(s){
  if(!s||!_scriptsList) return;
  var trk=(s.track||0)+1;
  var curId=(s.tracks&&s.tracks[s.track||0])?s.tracks[s.track||0].script_id:null;
  var curName='OFF';
  if(curId){
    var found=_scriptsList.find(function(sc){return sc.id===curId;});
    if(found) curName=found.name;
  }
  var info=document.getElementById('cfsc-track-info');
  if(info) info.innerHTML='T'+trk+' → <span>'+curName.toUpperCase()+'</span>';
  var list=document.getElementById('cfsc-list');
  if(!list) return;
  // Solo reconstruir si cambió el contenido
  var sig='t'+trk+'|'+(curId||'');
  if(list._sig===sig) return;
  list._sig=sig;
  var html='<div class="cfsc-item off" data-sid="">— OFF —</div>';
  _scriptsList.forEach(function(sc){
    var act=(sc.id===curId)?' active':'';
    html+='<div class="cfsc-item'+act+'" data-sid="'+sc.id+'">'+sc.name+'</div>';
  });
  list.innerHTML=html;
  list.querySelectorAll('.cfsc-item').forEach(function(el){
    el.onclick=function(){
      var sid=el.getAttribute('data-sid')||null;
      _cmd({script_id: sid});
    };
  });
}

function applyMappingMode(s){
  const inMap=!!s.mapping_mode;
  const confView=document.getElementById('conf-view');
  if(!inMap){confView.style.display='none';_confActive=false;return;}
  if(confView.style.display==='none') switchConfTab(_confTab);
  confView.style.display='block';
  _confActive=true;

  // ── MAPPING tab ──
  const pi=s.page_idx||0;
  const grp=s.mapping_group||'knob';
  _confGroup=grp;
  const isMisc=(grp==='misc');
  const pgMax=GRP_MAX[grp]||4;
  const nav=document.getElementById('conf-nav');
  if(nav) nav.style.display=(_confTab==='mapping')?'flex':'none';
  setText('conf-grp-lbl',GRP_DISPLAY[grp]||grp);
  setText('conf-pg-num',(pi+1)+'/'+pgMax);
  const ccs=s.cc_map_current||[];
  const midiCh=pi+1;
  ccs.forEach((cc,i)=>{
    const row=document.getElementById('cfr'+i);
    const nameEl=document.getElementById('cfn'+i);
    const ccEl=document.getElementById('cfc'+i);
    if(!row) return;
    if(nameEl){
      nameEl.textContent=isMisc
        ?MISC_LABELS[i]||'—'
        :(s.page_params&&s.page_params[i]?(s.page_params[i].label||'').toUpperCase():'');
    }
    const isLearning=(s.learn_target===i);
    if(ccEl) ccEl.textContent=(cc!=null?'CC'+cc+':'+midiCh:'N/A');
    row.classList.remove('cf-learning','cf-unmapped');
    row.classList.toggle('cf-learning',isLearning);
    row.classList.toggle('cf-unmapped',cc==null);
    row.onclick=()=>{
      fetch('/api/learn',{method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({knob:i,page:pi,group:grp})});
    };
  });

  // ── SETTINGS tab ──
  if(s.midi_ports){
    const mp=s.midi_ports;
    const slots=[
      ['cfv-int1',     'midi_out',  mp.midi_out],
      ['cfv-int2',     'midi_out2', mp.midi_out2],
      ['cfv-lp',       'lp_port',   mp.lp_port],
      ['cfv-midi-in1', 'kb_port',   mp.kb_port],
      ['cfv-midi-in2', 'nk_in',     mp.nk_in],
    ];
    slots.forEach(([elId,key,val],idx)=>{
      const el=document.getElementById(elId);
      const row=document.getElementById('cfs'+idx);
      if(!el) return;
      const shortVal=val?(val.length>24?val.slice(0,23)+'…':val):'Select';
      el.textContent=shortVal+' ▼';
      if(row){
        row.classList.toggle('cf-active',!!val&&val!=='');
        row.onclick=()=>{
          fetch('/api/settings/cycle_port',{method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify({slot:key})});
        };
      }
    });
  }
  _updateConfCursor();
}

// Intentar SSE primero; si falla, polling automático
connectSSE();

// ── Atajos de teclado ─────────────────────────────────────────────────────
// Usa e.code (posición física) en lugar de e.key (carácter) →
// funciona igual con cualquier layout de teclado (ES, DE, FR, etc.)
function _cmd(obj){fetch('/api/cmd',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(obj)});}

var _kbStepFocus=false;
var _kbEnabled=true;  // sincronizado con servidor vía SSE
var _bankViewActive=false;  // true si modal Bank View está abierto

// Dígitos físicos Digit1-Digit8 → índice 0-7
var _DIGIT_CODES={Digit1:0,Digit2:1,Digit3:2,Digit4:3,Digit5:4,Digit6:5,Digit7:6,Digit8:7};
// Home row física KeyA-KeyK → índice 0-7
var _STEP_CODES={KeyA:0,KeyS:1,KeyD:2,KeyF:3,KeyG:4,KeyH:5,KeyJ:6,KeyK:7};

document.addEventListener('keydown',function(e){
  var tag=document.activeElement&&document.activeElement.tagName;
  if(tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT') return;

  var ctrl =e.metaKey||e.ctrlKey;
  var shift=e.shiftKey;
  var code =e.code;

  // ── Siempre activos (ignoran kb_enabled) ──
  // Ctrl+K eliminado — teclado siempre activo
  if(ctrl&&code==='KeyS'){e.preventDefault();_cmd({bank_save_slot:true});return;}
  if(ctrl&&code==='KeyM'){e.preventDefault();fetch('/api/mapping/toggle',{method:'POST'});return;}
  if(ctrl&&code==='Comma'){e.preventDefault();fetch('/api/mapping/toggle',{method:'POST'});return;}

  // ── CONF view: navegación por teclado ──
  if(_confActive){
    const tabs=['settings','mapping','impexp','script'];
    // Tab o flechas ◄/► para navegar entre tabs
    if(code==='Tab'){
      e.preventDefault();
      switchConfTab(tabs[(tabs.indexOf(_confTab)+1)%tabs.length]);
      return;
    }
    if(code==='ArrowLeft'&&_confTab!=='mapping'){
      e.preventDefault();
      const idx=tabs.indexOf(_confTab);
      switchConfTab(tabs[idx>0?idx-1:tabs.length-1]);
      return;
    }
    if(code==='ArrowRight'&&_confTab!=='mapping'){
      e.preventDefault();
      switchConfTab(tabs[(tabs.indexOf(_confTab)+1)%tabs.length]);
      return;
    }
    if(code==='ArrowUp'){
      e.preventDefault();
      _confCursor=Math.max(0,_confCursor-1);
      _updateConfCursor();
      return;
    }
    if(code==='ArrowDown'){
      e.preventDefault();
      _confCursor=Math.min(_confCursorMax(),_confCursor+1);
      _updateConfCursor();
      return;
    }
    if(_confTab==='mapping'){
      if(code==='ArrowLeft'){e.preventDefault();confPageDelta(-1);return;}
      if(code==='ArrowRight'){e.preventDefault();confPageDelta(1);return;}
    }
    if(code==='Enter'||code==='Space'){e.preventDefault();_confCursorActivate();return;}
    return;
  }

  // ── Bank View: tecla 0 toggle (ignora kb_enabled) ──
  if(!ctrl&&!shift&&!e.altKey&&code==='Digit0'){
    e.preventDefault();_cmd({bank_view_toggle:true});return;
  }

  // ── Compact View: tecla 9 toggle (ignora kb_enabled) ──
  if(!ctrl&&!shift&&!e.altKey&&code==='Digit9'){
    e.preventDefault();_cmd({compact_view_toggle:true});return;
  }

  // ── Bank View activo: capturar navegación y acciones ──
  if(_bankViewActive){
    if(code==='Escape'){e.preventDefault();_cmd({bank_view_toggle:true});return;}
    if(code==='ArrowUp'){e.preventDefault();_cmd({bank_cursor_move:[-1,0]});return;}
    if(code==='ArrowDown'){e.preventDefault();_cmd({bank_cursor_move:[1,0]});return;}
    if(code==='ArrowLeft'){
      e.preventDefault();
      _cmd(shift?{bank_delta:-1}:{bank_cursor_move:[0,-1]});return;
    }
    if(code==='ArrowRight'){
      e.preventDefault();
      _cmd(shift?{bank_delta:1}:{bank_cursor_move:[0,1]});return;
    }
    if(code==='Tab'){e.preventDefault();_cmd({bank_delta:shift?-1:1});return;}
    if(code==='Enter'){e.preventDefault();_cmd({bank_load_slot:true});return;}
    if(code==='Delete'||code==='Backspace'){e.preventDefault();_cmd({bank_delete_slot:true});return;}
    if(ctrl&&code==='KeyS'){e.preventDefault();_cmd({bank_save_slot:true});return;}
    if(code==='Space'){e.preventDefault();_cmd({chain_toggle_slot:true});return;}
    if(code==='KeyX'){e.preventDefault();_cmd({chain_clear:true});return;}
    if(code==='KeyE'){e.preventDefault();window.open('/export/midi','_blank');return;}
    if(code==='KeyF'){e.preventDefault();_cmd({chain_flatten:true});return;}
    if(code==='KeyC'){e.preventDefault();_cmd({morph_set_b:true});return;}
    // Bloquea el resto de shortcuts mientras Bank View esté abierto
    return;
  }


  // ── Si KB desactivado, no procesar nada más ──
  if(!_kbEnabled) return;

  // ── Escape: salir de step-focus o mapping mode ──
  if(code==='Escape'){
    if(_kbStepFocus){e.preventDefault();_kbStepFocus=false;_cmd({step_focus_toggle:true});return;}
    if(_pendingState&&_pendingState.mapping_mode)fetch('/api/mapping/toggle',{method:'POST'});
    return;
  }

  // ── Undo / Redo: Cmd+Z / Cmd+Shift+Z ──
  if(ctrl&&shift&&code==='KeyZ'){e.preventDefault();_cmd({redo:true});return;}
  if(ctrl&&code==='KeyZ'){e.preventDefault();_cmd({undo:true});return;}

  // ── All-tracks mode: Cmd+A ──
  if(ctrl&&code==='KeyA'){e.preventDefault();_cmd({all_mode_toggle:true});return;}

  // ── File dialogs: guardar/cargar bank y pattern ──
  if(ctrl&&shift&&code==='KeyS'){e.preventDefault();_cmd({dialog_save_bank:true});return;}
  if(ctrl&&shift&&code==='KeyO'){e.preventDefault();_cmd({dialog_load_bank:true});return;}
  if(ctrl&&shift&&code==='KeyP'){e.preventDefault();_cmd({dialog_save_pattern:true});return;}
  if(ctrl&&shift&&code==='KeyL'){e.preventDefault();_cmd({dialog_load_pattern:true});return;}

  // ── Duplicar pattern: Cmd+D ──
  if(ctrl&&code==='KeyD'){e.preventDefault();_cmd({duplicate:true});return;}

  // ── Backspace: borrar p-locks (p-lock) / step anterior (resto) ──
  if(code==='Backspace'&&!ctrl){
    e.preventDefault();
    if(_kbStepFocus) _cmd({clear_step_locks:true});
    else             _cmd({rec_backspace:true});
    return;
  }

  // ── Borrar track completo (patrón + locks): Cmd+Backspace ──
  if(ctrl&&code==='Backspace'){e.preventDefault();_cmd({clear_track:true});return;}

  // ── Copiar step: Cmd+C (con step focus) ──
  if(ctrl&&code==='KeyC'&&_kbStepFocus){e.preventDefault();_cmd({copy_step:true});return;}

  // ── Pegar step: Cmd+V (con step focus) ──
  if(ctrl&&code==='KeyV'&&_kbStepFocus){e.preventDefault();_cmd({paste_step:true});return;}

  // ── Cambio de página: Cmd+← / Cmd+→ ──
  if(ctrl&&code==='ArrowLeft'){e.preventDefault();_cmd({page_delta:-1});return;}
  if(ctrl&&code==='ArrowRight'){e.preventDefault();_cmd({page_delta:1});return;}

  // ── BPM: Cmd+↑/↓ (Shift = ±10) ──
  if(ctrl&&code==='ArrowUp'){e.preventDefault();_cmd({bpm_delta:shift?10:1});return;}
  if(ctrl&&code==='ArrowDown'){e.preventDefault();_cmd({bpm_delta:shift?-10:-1});return;}

  // ── Selección de pista: Cmd+1-8 ──
  if(ctrl&&_DIGIT_CODES.hasOwnProperty(code)){e.preventDefault();_cmd({track:_DIGIT_CODES[code]});return;}

  // ── Selección directa de param: Alt+1-8 ──
  if(e.altKey&&_DIGIT_CODES.hasOwnProperty(code)){e.preventDefault();_cmd({param:_DIGIT_CODES[code]});return;}

  // ── Cambio de pista: 1-8 sin modificador ──
  if(!ctrl&&!shift&&!e.altKey&&_DIGIT_CODES.hasOwnProperty(code)){
    e.preventDefault();_cmd({track:_DIGIT_CODES[code]});return;
  }

  // ── Tab / Shift+Tab: navegar entre páginas (A·SEQ → B·NOTE → C·FX → D·CONF) ──
  if(code==='Tab'){e.preventDefault();_cmd({page_delta:shift?-1:1});return;}

  // ── Enter: toggle P-lock en step actual ──
  if(code==='Enter'){
    e.preventDefault();_kbStepFocus=!_kbStepFocus;_cmd({step_focus_toggle:true});return;
  }

  // ── ← / →: param o step según modo ──
  // ── ← / →: param (p-lock/normal) o steps (rec); Shift en p-lock = mueve step ──
  if(!ctrl&&code==='ArrowLeft'){
    e.preventDefault();
    if(_kbStepFocus){
      if(shift) _cmd({step_delta:-1});                 // p-lock + Shift: mueve step
      else      _cmd({param_delta:-1});                // p-lock: cambia parámetro
      return;
    }
    if(_recording&&!_lastPlaying){_cmd({step_delta:-1});return;}
    _cmd({param_delta:-1});return;
  }
  if(!ctrl&&code==='ArrowRight'){
    e.preventDefault();
    if(_kbStepFocus){
      if(shift) _cmd({step_delta:1});                  // p-lock + Shift: mueve step
      else      _cmd({param_delta:1});                 // p-lock: cambia parámetro
      return;
    }
    if(_recording&&!_lastPlaying){_cmd({step_delta:1});return;}
    _cmd({param_delta:1});return;
  }

  // ── ↑ / ↓: valor del p-lock / stoch / param ──
  if(!ctrl&&code==='ArrowUp'){
    e.preventDefault();
    if(_kbStepFocus)   _cmd({nudge:shift?5:1});
    else if(shift)     _cmd({stoch_amount_nudge:1});
    else               _cmd({nudge:1});
    return;
  }
  if(!ctrl&&code==='ArrowDown'){
    e.preventDefault();
    if(_kbStepFocus)   _cmd({nudge:shift?-5:-1});
    else if(shift)     _cmd({stoch_amount_nudge:-1});
    else               _cmd({nudge:-1});
    return;
  }

  // ── Space: play/pause ──
  if(code==='Space'){e.preventDefault();_cmd({play:true});return;}

  // ── Record: R ──
  if(!ctrl&&code==='KeyR'){e.preventDefault();_cmd({rec:true});return;}

  // ── Página de steps: Q (anterior) / W (siguiente) ──
  if(!ctrl&&code==='KeyQ'){e.preventDefault();_cmd({step_pg_delta:-1});return;}
  if(!ctrl&&code==='KeyW'){e.preventDefault();_cmd({step_pg_delta:1});return;}

  // ── Stochastic randomize: E ──
  if(!ctrl&&code==='KeyE'){e.preventDefault();_cmd({stoch_rand:true});return;}

  // ── Stochastic toggle: M (posición física KeyM) ──
  if(!ctrl&&code==='KeyM'){e.preventDefault();_cmd({stoch_toggle:true});return;}

  // ── Shift+1-8: mute toggle del track ──
  if(!ctrl&&shift&&!e.altKey&&_DIGIT_CODES.hasOwnProperty(code)){
    e.preventDefault();_cmd({mute_track:_DIGIT_CODES[code]});return;
  }

  // ── Home row ASDFGHJK: toggle steps 0-7 de la página actual ──
  if(!ctrl&&!shift&&!e.altKey&&_STEP_CODES.hasOwnProperty(code)){
    e.preventDefault();_cmd({step_toggle:_STEP_CODES[code]});return;
  }
});

// No-sleep: oscilador silencioso mantiene sesión audio activa → iOS no duerme
(function(){
  var ctx=null;
  function wake(){
    if(ctx) return;
    ctx=new (window.AudioContext||window.webkitAudioContext)();
    var osc=ctx.createOscillator();
    var gain=ctx.createGain();
    gain.gain.value=0.001; // prácticamente inaudible
    osc.connect(gain);gain.connect(ctx.destination);
    osc.start();
  }
  document.addEventListener('touchstart',wake,{once:true});
  document.addEventListener('click',wake,{once:true});
  document.addEventListener('visibilitychange',function(){
    if(document.visibilityState==='visible'&&ctx&&ctx.state==='suspended') ctx.resume();
  });
})();


</script></body></html>'''

@_visual_app.route('/')
def _visual_index():
    return Response(_VISUAL_HTML, mimetype='text/html',
                    headers={'Cache-Control': 'no-store, no-cache, must-revalidate',
                             'Pragma': 'no-cache'})

_DISKET_PATH = '/Users/user/Downloads/Disket Rostype/Disket-Mono-Regular.ttf'

_DOCS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs')

@_visual_app.route('/docs')
@_visual_app.route('/docs/')
def _docs_index():
    path = os.path.join(_DOCS_DIR, 'index.html')
    try:
        with open(path, encoding='utf-8') as f:
            return Response(f.read(), mimetype='text/html',
                            headers={'Cache-Control': 'no-store'})
    except Exception:
        return Response('docs not found', status=404)

@_visual_app.route('/docs/<path:filename>')
def _docs_static(filename):
    from flask import send_from_directory
    return send_from_directory(_DOCS_DIR, filename)

@_visual_app.route('/font/disket-mono.ttf')
def _visual_font():
    try:
        with open(_DISKET_PATH, 'rb') as f:
            data = f.read()
        return Response(data, mimetype='font/ttf',
                        headers={'Cache-Control': 'public, max-age=86400'})
    except Exception:
        return Response(status=404)

@_visual_app.route('/events')
def _visual_events():
    def stream():
        last_ts = -1.0
        last_json = ''
        # Primer snapshot inmediato
        s = dict(_display_state)
        j = json.dumps(s)
        last_ts = s['ts']
        last_json = j
        yield f"data: {j}\n\n"
        while True:
            # Bloqueo: despertar en cuanto _display_worker actualice el estado
            fired = _display_event.wait(timeout=4.0)
            _display_event.clear()
            if fired:
                s = dict(_display_state)
                if s['ts'] > last_ts or last_ts < 0:
                    j = json.dumps(s)
                    if j != last_json:
                        last_ts   = s['ts']
                        last_json = j
                        yield f"data: {j}\n\n"
            else:
                # ~4s sin datos → ping para mantener conexión
                yield f": ping\n\n"
    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@_visual_app.route('/api/state')
def _visual_state():
    return Response(json.dumps(_display_state), mimetype='application/json',
                    headers={'Cache-Control':'no-cache'})

@_visual_app.route('/api/scripts')
def _api_scripts():
    global _seq_ref
    if not _seq_ref:
        return Response('[]', mimetype='application/json')
    data = [{'id': sid, 'name': sd.get('name', sid), 'desc': sd.get('desc', '')}
            for sid, sd in _seq_ref.scripts_lib.items()]
    return Response(json.dumps(data), mimetype='application/json',
                    headers={'Cache-Control':'no-cache'})

@_visual_app.route('/api/mapping/page', methods=['POST'])
def _api_mapping_page():
    global _seq_ref
    if _seq_ref:
        data  = _flask_request.get_json(silent=True) or {}
        delta = int(data.get('delta', 1))
        with _seq_ref.lock:
            _GROUPS   = ['knob','fade','btn_s','btn_m','misc']
            _PG_MAX   = {'knob':4,'fade':4,'btn_s':4,'btn_m':4,'misc':1}
            new_page  = _seq_ref.page + delta
            cur_max   = _PG_MAX[_seq_ref.mapping_group]
            if new_page >= cur_max:
                gi = (_GROUPS.index(_seq_ref.mapping_group) + 1) % len(_GROUPS)
                _seq_ref.mapping_group = _GROUPS[gi]
                _seq_ref.page = 0
            elif new_page < 0:
                gi = (_GROUPS.index(_seq_ref.mapping_group) - 1) % len(_GROUPS)
                _seq_ref.mapping_group = _GROUPS[gi]
                _seq_ref.page = _PG_MAX[_GROUPS[gi]] - 1
            else:
                _seq_ref.page = new_page
            _seq_ref._render()
    return ('', 204)

@_visual_app.route('/api/mapping/toggle', methods=['POST'])
def _api_mapping_toggle():
    global _seq_ref
    if _seq_ref:
        with _seq_ref.lock:
            _seq_ref.mapping_mode = not _seq_ref.mapping_mode
            _seq_ref.learn_target = None
            _seq_ref._render()
    return ('', 204)

@_visual_app.route('/api/learn', methods=['POST'])
def _api_learn():
    global _seq_ref
    if _seq_ref:
        data = _flask_request.get_json(silent=True) or {}
        knob = data.get('knob')
        page = data.get('page', 0)
        group = data.get('group', 'knob')
        if knob is not None:
            with _seq_ref.lock:
                _seq_ref.learn_target = (group, int(page), int(knob))
                _seq_ref._render()
    return ('', 204)

@_visual_app.route('/api/mapping/group', methods=['POST'])
def _api_mapping_group():
    global _seq_ref
    if _seq_ref:
        data  = _flask_request.get_json(silent=True) or {}
        group = data.get('group', 'knob')
        if group in ('knob', 'fade', 'btn_s', 'btn_m', 'misc'):
            with _seq_ref.lock:
                _seq_ref.mapping_group = group
                _seq_ref.page = 0
                _seq_ref._render()
    return ('', 204)

@_visual_app.route('/api/settings/cycle_port', methods=['POST'])
def _api_cycle_port():
    global _seq_ref
    if not _seq_ref:
        return ('', 204)
    data = _flask_request.get_json(silent=True) or {}
    slot = data.get('slot', '')
    SLOT_TO_CONFIG = {
        'midi_out':  'MIDI_OUT_PORT',
        'midi_out2': 'MIDI_OUT_PORT2',
        'lp_port':   'LAUNCHPAD_PORT',
        'nk_in':     'NK_IN_PORT',
        'kb_port':   'MIDI_KB_PORT',
    }
    if slot not in SLOT_TO_CONFIG:
        return ('', 400)
    import rtmidi as _rtmidi
    out_ports = _rtmidi.MidiOut().get_ports()
    in_ports  = _rtmidi.MidiIn().get_ports()
    # lp_port y controllers usan entradas; midi_out usa salidas
    is_input  = slot in ('nk_in', 'kb_port', 'lp_port')
    ports     = [''] + (in_ports if is_input else out_ports)
    cfg_key   = SLOT_TO_CONFIG[slot]
    current   = getattr(config, cfg_key, '')
    try:    idx = ports.index(current)
    except: idx = 0
    new_port  = ports[(idx + 1) % len(ports)]
    setattr(config, cfg_key, new_port)
    s = _load_settings()
    s[cfg_key] = new_port
    _save_settings(s)
    with _seq_ref.lock:
        _seq_ref._reconnect_port(slot)
        _seq_ref._render()
    return ('', 204)

@_visual_app.route('/api/mapping/export', methods=['GET'])
def _api_mapping_export():
    global _seq_ref
    if not _seq_ref:
        return ('', 503)
    with _seq_ref.lock:
        cc_map = json.loads(json.dumps(_seq_ref._cc_map))  # deep copy
    human  = _cc_map_to_human(cc_map)
    payload = json.dumps(human, indent=2, ensure_ascii=False)
    from flask import Response as _FResponse
    return _FResponse(
        payload,
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename="mapping.json"'}
    )

@_visual_app.route('/api/mapping/import', methods=['POST'])
def _api_mapping_import():
    global _seq_ref
    if not _seq_ref:
        return ('', 503)
    data = _flask_request.get_json(silent=True)
    if not data:
        return ('JSON inválido', 400)
    # ── Detectar formato ──────────────────────────────────────────────────────
    version = data.get('version', 1)
    if version == 2 or 'knob' in data or 'fader' in data:
        # Formato legible v2: {knob:{página:{param:cc,...},...}, fader:..., misc:...}
        try:
            cc_map = _human_to_cc_map(data)
        except Exception as e:
            return (f'Error al parsear formato v2: {e}', 400)
    else:
        # Formato legacy v1: {cc_map: {knob:[[...],..], fade:..., misc:...}}
        raw = data.get('cc_map', data)
        valid = {'knob', 'fade', 'btn_s', 'btn_m', 'misc'}
        if not isinstance(raw, dict) or not any(k in raw for k in valid):
            return ('Estructura inválida', 400)
        def_map = _default_cc_map()
        def _lg(key):
            s = raw.get(key, def_map[key])
            if isinstance(s, list) and s and not isinstance(s[0], list):
                s = [s[:] for _ in range(4)]
            return s
        cc_map = {
            'knob':  _lg('knob'),
            'fade':  _lg('fade'),
            'btn_s': _lg('btn_s'),
            'btn_m': _lg('btn_m'),
            'misc':  {**def_map['misc'], **raw.get('misc', {})},
        }
    with _seq_ref.lock:
        _seq_ref._cc_map = cc_map
        s = _load_settings()
        s['cc_map'] = cc_map
        _save_settings(s)
        _seq_ref._render()
    return ('', 204)

@_visual_app.route('/api/mapping/preset', methods=['POST'])
def _api_mapping_preset():
    global _seq_ref
    if not _seq_ref:
        return ('', 503)
    data = _flask_request.get_json(silent=True) or {}
    name = data.get('name', '')
    if name == 'nano':
        cc_map = _default_cc_map()      # nanoKONTROL — valores de config.py
    elif name == 'launchkey':
        cc_map = _launchkey_cc_map()    # Launchkey MK4 — knobs CC 21-28
    else:
        return ('Preset desconocido', 400)
    with _seq_ref.lock:
        _seq_ref._cc_map = cc_map
        s = _load_settings()
        s['cc_map'] = cc_map
        _save_settings(s)
        _seq_ref._render()
    return ('', 204)

@_visual_app.route('/api/files/list')
def _api_files_list():
    global _seq_ref
    if _seq_ref:
        return Response(json.dumps(_seq_ref._file_list()),
                        mimetype='application/json')
    return Response('[]', mimetype='application/json')

@_visual_app.route('/api/files/save', methods=['POST'])
def _api_files_save():
    global _seq_ref
    if _seq_ref:
        data = _flask_request.get_json(silent=True) or {}
        name = data.get('name')
        with _seq_ref.lock:
            _seq_ref._file_save(name)
    return ('', 204)

@_visual_app.route('/api/files/load', methods=['POST'])
def _api_files_load():
    global _seq_ref
    if _seq_ref:
        data = _flask_request.get_json(silent=True) or {}
        filename = data.get('filename')
        if filename:
            with _seq_ref.lock:
                _seq_ref._file_load(filename)
    return ('', 204)

@_visual_app.route('/api/files/delete', methods=['POST'])
def _api_files_delete():
    global _seq_ref
    if _seq_ref:
        data = _flask_request.get_json(silent=True) or {}
        filename = data.get('filename')
        if filename:
            filepath = os.path.join(SAVES_DIR, filename)
            try:
                os.remove(filepath)
            except OSError:
                pass
    return ('', 204)

# ── MIDI file export (sin dependencias externas) ────────────────────────────
def _vlen(n):
    """Variable-length quantity (MIDI spec)."""
    buf = [n & 0x7F]; n >>= 7
    while n:
        buf.append((n & 0x7F) | 0x80); n >>= 7
    return bytes(reversed(buf))

def _midi_file(tpb, track_event_lists):
    """Type-1 MIDI file. Each list = [(abs_tick, b1, b2, …)]."""
    def _trk(evs):
        evs = sorted(evs, key=lambda e: e[0])
        data = bytearray(); prev = 0
        for ev in evs:
            data += _vlen(ev[0] - prev); prev = ev[0]
            data += bytes(ev[1:])
        data += b'\x00\xff\x2f\x00'          # end-of-track
        return b'MTrk' + len(data).to_bytes(4,'big') + bytes(data)
    n = len(track_event_lists)
    hdr = (b'MThd' + (6).to_bytes(4,'big') + (1).to_bytes(2,'big')
           + n.to_bytes(2,'big') + tpb.to_bytes(2,'big'))
    return hdr + b''.join(_trk(e) for e in track_event_lists)

@_visual_app.route('/export/midi')
def _export_midi_route():
    global _seq_ref
    s = _seq_ref
    if s is None:
        return ('no seq', 503)
    data = s._export_midi_chain()
    if data is None:
        return ('chain vacío', 400)
    from flask import Response as FR
    return FR(data,
              mimetype='audio/midi',
              headers={'Content-Disposition': 'attachment; filename="rrresponseq.mid"'})

@_visual_app.route('/api/cmd', methods=['POST'])
def _api_cmd():
    """Comandos de teclado: track, page_delta, param, param_delta,
       nudge, bpm_delta, undo, redo, play, rec, stoch_rand, stoch_toggle."""
    global _seq_ref
    if not _seq_ref:
        return ('', 204)
    data = _flask_request.get_json(silent=True) or {}
    s = _seq_ref
    with s.lock:
        # ── Toggle keyboard enable (siempre permitido) ──
        # kb_enabled siempre activo — toggle eliminado
        # ── All-tracks mode toggle (Cmd+A) ──
        if data.get('all_mode_toggle'):
            s.kb_all_mode = not s.kb_all_mode
            s.last_msg = f"ALL MODE {'ON' if s.kb_all_mode else 'OFF'}"
            s._render()
        # ── Mute toggle de track (Shift+1-8) ──
        if 'mute_track' in data:
            ti = int(data['mute_track']) % 8
            s.tracks[ti].muted = not s.tracks[ti].muted
            s.last_msg = f"T{ti+1} {'MUTE' if s.tracks[ti].muted else 'ACTIVE'}"
            s._render()
        # ── Selección de pista ──
        if 'track' in data:
            s.active = int(data['track']) % 8
            s.last_msg = f"Track → {s.active+1}"
            s._render()
        # ── Cambio de página ──
        if 'page_delta' in data:
            s.page = (s.page + int(data['page_delta'])) % 4
            s.kb_param = 0
            s.last_msg = f"Page → {list({0:'A·SEQ',1:'B·NOTE',2:'C·FX',3:'D·CONF'}.values())[s.page]}"
            s._kb_render()
        # ── Selección directa de parámetro ──
        if 'param' in data:
            s.kb_param = max(0, min(7, int(data['param'])))
            params = PAGE_PARAMS.get(s.page, [])
            pname = params[s.kb_param] if s.kb_param < len(params) else '?'
            s.last_msg = f"Param → {pname}"
            s._kb_render()
        # ── Desplazamiento de parámetro (←/→ sin modificador) ──
        if 'param_delta' in data:
            s.kb_param = (s.kb_param + int(data['param_delta'])) % 8
            params = PAGE_PARAMS.get(s.page, [])
            pname = params[s.kb_param] if s.kb_param < len(params) else '?'
            s.last_msg = f"Param → {pname}"
            s._kb_render()
        # ── Nudge de valor (↑/↓) ──
        if 'nudge' in data:
            if s.kb_step_focus:
                s._nudge_step_lock(int(data['nudge']))
            elif s.kb_all_mode:
                _saved = s.active
                s._suppress_kb_render = True
                for _i in range(8):
                    s.active = _i
                    s._nudge_param(int(data['nudge']))
                s._suppress_kb_render = False
                s.active = _saved
                _parts = s.last_msg.split('→')
                if len(_parts) == 2:
                    _p = _parts[0].split(None, 1)[-1].strip()
                    _v = _parts[1].strip()
                    s.last_msg = f"ALL {_p} → {_v}"
                    _push_display(f"ALL {_p}", _v, track=s.active, bpm=int(s.bpm),
                                  playing=s.running,
                                  extra={'view_mode': 'detail', 'label': f"ALL {_p}", 'value': _v})
                s.view_mode = 'detail'
                if s._detail_timer: s._detail_timer.cancel()
                s._detail_timer = threading.Timer(2.5, s._to_page_view)
                s._detail_timer.start()
                s._render()  # único render
            else:
                s._nudge_param(int(data['nudge']))
        # ── BPM (Shift+↑/↓) ──
        if 'bpm_delta' in data:
            s.bpm = max(20, min(300, s.bpm + int(data['bpm_delta'])))
            s.last_msg = f"BPM → {int(s.bpm)}"
            s._render()
        # ── Undo / Redo ──
        if data.get('undo'):
            s._undo()
        if data.get('redo'):
            s._redo()
        # ── Play/Pause ──
        if data.get('play'):
            if s.running:
                s.pause()
            else:
                s.resume()
        # ── Record toggle ──
        if data.get('rec'):
            s.recording = not s.recording
            if not s.recording:
                s._rec_pending.clear()
            elif not s.running:
                # Sync kb_step al cursor actual para que rec_step se muestre desde el paso 1
                t = s.tracks[s.active]
                s.kb_step = t.cursor % max(1, t.steps)
            s.last_msg = "● REC" if s.recording else "REC off"
            s._render()
        # ── Backspace: borrar el step actual (sin mover cursor) ──
        if data.get('rec_backspace'):
            t = s.tracks[s.active]
            pat_len = max(len(t.pattern), 1)
            step    = t.cursor % pat_len
            # Si no hay nada que borrar (ni nota ni locks), no hacer nada
            if t.pattern[step] or step in t.step_locks:
                t.pattern[step] = False
                t.pulses = sum(t.pattern)
                t.step_locks.pop(step, None)
                s.last_msg = f"DEL S{step+1}"
                s._render()
        # ── Stochastic: porcentaje de amount (Shift+↑/↓) — o morph blend si B activo ──
        if 'stoch_amount_nudge' in data:
            if s.xfade_snap and s.xfade_snap_a:
                # Morph B activo: redirigir al blend (5% por paso)
                s.xfade_amt = max(0.0, min(1.0, round(s.xfade_amt + int(data['stoch_amount_nudge']) * 0.05, 2)))
                s._apply_xfade()
                _lbl = f"MORPH {s.xfade_label}"
                _val = f"{int(s.xfade_amt*100)}%"
                s.last_msg = f"{_lbl} → {_val}"
                s.view_mode = 'detail'
                if s._detail_timer: s._detail_timer.cancel()
                s._detail_timer = threading.Timer(2.5, s._to_page_view)
                s._detail_timer.start()
                _push_display(_lbl, _val, bar=s.xfade_amt, track=s.active, bpm=int(s.bpm),
                              playing=s.running,
                              extra={'view_mode': 'detail', 'label': _lbl, 'value': _val})
                s._render()
            else:
                pp = PAGE_PARAMS.get(s.page, [])
                if s.kb_param < len(pp):
                    param = pp[s.kb_param]
                    _tracks_to_nudge = s.tracks if s.kb_all_mode else [s.tracks[s.active]]
                    for _tr in _tracks_to_nudge:
                        cur = _tr.stoch_amounts.get(param, 0.0)
                        _tr.stoch_amounts[param] = max(0.0, min(1.0, round(cur + int(data['stoch_amount_nudge']) * 0.05, 3)))
                    _new = s.tracks[s.active].stoch_amounts.get(param, 0.0)
                    _pfx = "ALL" if s.kb_all_mode else f"T{s.active+1}"
                    s.last_msg = f"{_pfx} ±{param} amt → {int(_new*100)}%"
                    s._render()
        # ── Stochastic: randomize param seleccionado ──
        if data.get('stoch_rand'):
            t = s.tracks[s.active]
            s._randomize_param(s.page, s.kb_param, t)
            _parts = s.last_msg.split('→')
            _lbl = _parts[0].strip() if len(_parts) == 2 else s.last_msg
            _val = _parts[1].strip() if len(_parts) == 2 else ''
            if not s.kb_bank_view:
                s.view_mode = 'detail'
                if s._detail_timer: s._detail_timer.cancel()
                s._detail_timer = threading.Timer(2.5, s._to_page_view)
                s._detail_timer.start()
            _push_display(_lbl, _val, track=s.active, bpm=int(s.bpm), playing=s.running,
                          extra={'view_mode': 'detail', 'label': _lbl, 'value': _val})
            s._render()
        # ── Stochastic: toggle on/off param seleccionado ──
        if data.get('stoch_toggle'):
            t = s.tracks[s.active]
            pp = PAGE_PARAMS.get(s.page, [])
            if s.kb_param < len(pp):
                param_name = pp[s.kb_param]
                s._handle_btn_m(s.page, s.kb_param, t,
                                not t.stoch_enabled.get(param_name, False))
                # SCRI maneja su propio _push_display internamente
                if param_name != 'SCRI':
                    _parts = s.last_msg.split('→')
                    _lbl = _parts[0].strip() if len(_parts) == 2 else s.last_msg
                    _val = _parts[1].strip() if len(_parts) == 2 else ''
                    if not s.kb_bank_view:
                        s.view_mode = 'detail'
                        if s._detail_timer: s._detail_timer.cancel()
                        s._detail_timer = threading.Timer(2.5, s._to_page_view)
                        s._detail_timer.start()
                    _push_display(_lbl, _val, track=s.active, bpm=int(s.bpm), playing=s.running,
                                  extra={'view_mode': 'detail', 'label': _lbl, 'value': _val})
                s._render()
        # ── Morph: fijar patrón B (C en bank view) ──
        if s.kb_bank_view and data.get('morph_set_b'):
            row, col = s.kb_bank_cursor
            slot = row * 8 + col
            candidate = f"B{s.current_bank+1}.{slot+1}"
            if s.xfade_snap and s.xfade_label == candidate:
                s.xfade_snap = None; s.xfade_snap_a = None
                s.xfade_amt = 0.0;   s.xfade_label = ''
                s.last_msg = "MORPH OFF"
                _lbl, _val = "MORPH", "OFF"
            elif slot in s.banks[s.current_bank]:
                s.xfade_snap_a = [tr.snapshot() for tr in s.tracks]
                s.xfade_snap   = list(s._slot_tracks(s.banks[s.current_bank][slot]))
                s.xfade_amt    = 0.0
                s.xfade_label  = candidate
                s.last_msg = f"MORPH B → {candidate}"
                _lbl, _val = "MORPH B", candidate
            else:
                s.last_msg = f"EMPTY: {candidate}"
                _lbl, _val = "MORPH", f"EMPTY {candidate}"
            # Cerrar bank view y mostrar detail screen
            s.kb_bank_view = False
            s.view_mode = 'detail'
            if s._detail_timer: s._detail_timer.cancel()
            s._detail_timer = threading.Timer(2.5, s._to_page_view)
            s._detail_timer.start()
            _push_display(_lbl, _val, track=s.active, bpm=int(s.bpm), playing=s.running,
                          extra={'view_mode': 'detail', 'label': _lbl, 'value': _val,
                                 'kb_bank_view': False})
            s._render()
        # ── Duplicar pattern (Cmd+D) ──
        if data.get('duplicate'):
            t = s.tracks[s.active]
            _duplicate_pattern(t)
            n = s.active + 1
            s.last_msg = f"T{n} DUP → {t.steps} steps"
            s._render()
        # ── Borrar p-locks del step actual (Backspace con step focus) ──
        if data.get('clear_step_locks'):
            t = s.tracks[s.active]
            step = s.kb_step
            if step in t.step_locks:
                del t.step_locks[step]
                s.last_msg = f"T{s.active+1} S{step+1} locks cleared"
            else:
                s.last_msg = f"T{s.active+1} S{step+1} — no locks"
            s._render()
        # ── Borrar track completo (Cmd+Backspace) ──
        if data.get('clear_track'):
            t = s.tracks[s.active]
            n = t.steps
            t.pattern    = [False] * n
            t.step_locks = {}
            t.pulses     = 0
            s.last_msg   = f"T{s.active+1} CLEAR"
            s._render()
        # ── Copiar step (Cmd+C con step focus) ──
        if data.get('copy_step'):
            t    = s.tracks[s.active]
            step = s.kb_step
            s._step_clipboard = {
                'pattern': t.pattern[step] if step < len(t.pattern) else False,
                'locks':   dict(t.step_locks.get(step, {})),
            }
            s.last_msg = f"T{s.active+1} S{step+1} copied"
            s._render()
        # ── Pegar step (Cmd+V con step focus) ──
        if data.get('paste_step'):
            cb = getattr(s, '_step_clipboard', None)
            if cb:
                t    = s.tracks[s.active]
                step = s.kb_step
                if step < len(t.pattern):
                    t.pattern[step] = cb['pattern']
                    t.pulses = sum(t.pattern)
                if cb['locks']:
                    t.step_locks[step] = dict(cb['locks'])
                elif step in t.step_locks:
                    del t.step_locks[step]
                s.last_msg = f"T{s.active+1} S{step+1} pasted"
            else:
                s.last_msg = "clipboard empty"
            s._render()
        # ── Navegación de step para P-lock (Tab / Shift+Tab) ──
        if 'step_delta' in data:
            t   = s.tracks[s.active]
            if s.kb_step_focus:
                # En p-lock: Tab mueve el step lockeado
                new = (s.kb_step + int(data['step_delta'])) % max(1, t.steps)
                s.kb_step = new
                s.step_focus = (s.active, new)
                s.last_msg = f"P-LOCK S{new+1}"
            elif s.recording and not s.running:
                # Recording: usar t.cursor como base
                new = (t.cursor + int(data['step_delta'])) % max(1, t.steps)
                t.cursor  = new
                s.kb_step = new
                s.last_msg = f"REC S{new+1}"
            else:
                new = (s.kb_step + int(data['step_delta'])) % max(1, t.steps)
                s.kb_step = new
                s.last_msg = f"Step → {new+1}"
            s._render()
        # ── Toggle foco de step para P-lock (Enter) ──
        if data.get('step_focus_toggle'):
            s.kb_step_focus = not s.kb_step_focus
            if s.kb_step_focus:
                # Sincronizar con el cursor de grabación si está activo
                if s.recording and not s.running:
                    t = s.tracks[s.active]
                    s.kb_step = t.cursor % max(1, t.steps)
                s.step_focus = (s.active, s.kb_step)
                s.last_msg = f"P-LOCK S{s.kb_step+1}"
                # Abrir detail view con valor actual del p-lock (sin timer — se cierra con Enter/Escape)
                params = PAGE_PARAMS.get(s.page, [])
                param  = params[s.kb_param] if s.kb_param < len(params) else '?'
                t      = s.tracks[s.active]
                lk     = t.step_locks.get(s.kb_step, {})
                _LOCK_KEYS = {
                    'VEL':'vel','PROB':'prob','GATE':'gate','NLEN':'note_len',
                    'RTCH':'ratchet','RSPD':'ratchet_div','RDEC':'ratchet_curve',
                    'DTIM':'delay_steps','FDBK':'delay_fb','PROG':'program',
                    'BANK':'bank_msb','RESL':'resolution','MODE':'play_mode',
                    'NOTE':'notes','OCT':'notes',
                }
                lk_key = _LOCK_KEYS.get(param)
                lk_val = lk.get(lk_key) if lk_key else None
                if lk_val is not None:
                    if isinstance(lk_val, list):
                        disp = note_name(lk_val[0])
                    elif isinstance(lk_val, float):
                        disp = f"{int(lk_val*100)}%" if lk_val <= 1.0 else f"{lk_val:+.2f}"
                    else:
                        disp = str(lk_val)
                else:
                    disp = '—'
                lbl = f"S{s.kb_step+1} {param}"
                s.view_mode = 'detail'
                if s._detail_timer:
                    s._detail_timer.cancel()
                    s._detail_timer = None
                _push_display(lbl, disp, track=s.active, bpm=int(s.bpm),
                              playing=s.running,
                              extra={'view_mode': 'detail', 'label': lbl, 'value': disp,
                                     'step_focus': (s.active, s.kb_step)})
            else:
                s.step_focus = None
                s.last_msg   = "P-lock off"
                s.view_mode  = 'page'
                if s._detail_timer:
                    s._detail_timer.cancel()
                    s._detail_timer = None
            s._render()
        # ── Toggle step on/off: QWERTYUI (índice local 0-7 en la página actual) ──
        if 'step_toggle' in data:
            t    = s.tracks[s.active]
            lidx = int(data['step_toggle'])          # 0-7
            step = s.step_pg * 8 + lidx
            if 0 <= step < t.steps:
                t.pattern[step] = not t.pattern[step]
                t.pulses = sum(t.pattern)
                action = "ON" if t.pattern[step] else "OFF"
                s.last_msg = f"T{s.active+1} S{step+1} {action}"
                # También mover el kb_step al step recién editado (útil para P-lock)
                s.kb_step = step
                if s.kb_step_focus:
                    s.step_focus = (s.active, step)
            else:
                s.last_msg = f"S{step+1} — fuera de rango"
            s._render()
        # ── Página de steps: [ / ] ──
        if 'step_pg_delta' in data:
            t     = s.tracks[s.active]
            total = max(1, (t.steps + 7) // 8)
            s.step_pg = (s.step_pg + int(data['step_pg_delta'])) % total
            s.last_msg = f"Step pg {s.step_pg+1}/{total}"
            s._render()
        # ─────────────────────────────────────────────────────────────────
        # ── BANK VIEW MODE (Tecla 0) ──────────────────────────────────────
        # ─────────────────────────────────────────────────────────────────
        # ── Toggle Bank View (0) ──
        if data.get('compact_view_toggle'):
            s.compact_view = not s.compact_view
            if s.compact_view:
                s.kb_bank_view = False   # mutuamente exclusivas
            s.last_msg = f"Compact View {'ON' if s.compact_view else 'OFF'}"
            s._render()

        if data.get('bank_view_toggle'):
            s.kb_bank_view = not s.kb_bank_view
            if s.kb_bank_view:
                s.compact_view = False   # mutuamente exclusivas
                # Al abrir: cursor sobre el slot activo (si hay) para seguir el show
                sl = s.active_slot if s.active_slot is not None and s.active_slot >= 0 else 0
                s.kb_bank_cursor = (sl // 8, sl % 8)
            else:
                s.kb_bank_cursor = (0, 0)
            s.last_msg = f"Bank View {'ON' if s.kb_bank_view else 'OFF'}"
            print(f"[DEBUG] Bank View toggled: {s.kb_bank_view}", file=sys.stderr)
            s._render()
        # ── Navegar cursor en Bank View (↑↓←→) ──
        if s.kb_bank_view and 'bank_cursor_move' in data:
            dr, dc = data['bank_cursor_move']  # (drow, dcol)
            row, col = s.kb_bank_cursor
            row = (row + dr) % 8
            col = (col + dc) % 8
            s.kb_bank_cursor = (row, col)
            slot = row * 8 + col
            bank = s.banks[s.current_bank]
            has_pattern = slot in bank
            s.last_msg = f"B{s.current_bank+1} [{row+1},{col+1}] #{slot+1} {'●' if has_pattern else '○'}"
            s.bv_status = ""  # limpiar status al moverse
            s._render()
        # ── Guardar patrón al slot (Cmd+S, global) ──
        if data.get('bank_save_slot'):
            row, col = s.kb_bank_cursor
            slot = row * 8 + col
            s._save_pattern(slot)
            # Detectar tracks sin asignación MIDI explícita (program=0 y bank_msb=0)
            missing = [str(i+1) for i, tr in enumerate(s.tracks)
                       if tr.program == 0 and tr.bank_msb == 0]
            if missing:
                s.bv_status = f"SAVED — MIDI INFO MISSING AT: {','.join(missing)}"
                s.bv_status_warn = True
                s.last_msg = f"Saved B{s.current_bank+1}.{slot+1} (MIDI miss: {','.join(missing)})"
            else:
                s.bv_status = f"SAVED → B{s.current_bank+1} SLOT {slot+1}"
                s.bv_status_warn = False
                s.last_msg = f"Saved → B{s.current_bank+1} slot {slot+1}"
            s._render()
        # ── Cargar patrón desde slot (Enter, global) ──
        if data.get('bank_load_slot'):
            row, col = s.kb_bank_cursor
            slot = row * 8 + col
            if slot in s.banks[s.current_bank]:
                s._load_pattern(slot)
                s.last_msg = f"Loaded ← B{s.current_bank+1} slot {slot+1}"
            else:
                s.last_msg = f"Slot empty: B{s.current_bank+1} #{slot+1}"
            s._render()
        # ── Borrar patrón del slot (Delete en Bank View) ──
        if s.kb_bank_view and data.get('bank_delete_slot'):
            row, col = s.kb_bank_cursor
            slot = row * 8 + col
            if slot in s.banks[s.current_bank]:
                del s.banks[s.current_bank][slot]
                s._save_banks()
                s.last_msg = f"Deleted: B{s.current_bank+1} slot {slot+1}"
            else:
                s.last_msg = f"Already empty: B{s.current_bank+1} #{slot+1}"
            s._render()
        # ── Navegar bancos (←/→ en Bank View) ──
        if s.kb_bank_view and 'bank_delta' in data:
            s.current_bank = (s.current_bank + int(data['bank_delta'])) % 8
            s.kb_bank_cursor = (0, 0)
            s.last_msg = f"Bank → {s.current_bank+1}"
            s._render()
        # ── Chain: añadir/quitar slot bajo cursor (Space en Bank View) ──
        if s.kb_bank_view and data.get('chain_toggle_slot'):
            row, col = s.kb_bank_cursor
            slot  = row * 8 + col
            entry = (s.current_bank, slot)
            if slot in s.banks[s.current_bank]:
                if entry in s.chain:
                    s.chain.remove(entry)
                    if s.chain_pos >= len(s.chain):
                        s.chain_pos = max(0, len(s.chain) - 1)
                    s.last_msg = f"Chain: − B{s.current_bank+1}.{slot+1}  [{len(s.chain)}]"
                else:
                    s.chain.append(entry)
                    s.last_msg = f"Chain: + B{s.current_bank+1}.{slot+1}  [{len(s.chain)}]"
            else:
                s.last_msg = f"Slot vacío: B{s.current_bank+1}.{slot+1}"
            s._render()
        # ── Chain: limpiar todo (X en Bank View) ──
        if s.kb_bank_view and data.get('chain_clear'):
            s.chain     = []
            s.chain_pos = 0
            s.last_msg  = "Chain cleared"
            s._render()
        # ── Chain: flatten al slot bajo cursor (F en Bank View) ──
        FLATTEN_MAX = 8
        if s.kb_bank_view and data.get('chain_flatten'):
            row, col = s.kb_bank_cursor
            slot = row * 8 + col
            if not s.chain:
                s.last_msg = "Chain vacía — añade slots con Space primero"
            elif len(s.chain) < 2:
                s.last_msg = "Chain necesita ≥ 2 slots para flatten"
            elif len(s.chain) > FLATTEN_MAX:
                s.last_msg = f"Flatten: máx {FLATTEN_MAX} slots ({len(s.chain)} en chain)"
            else:
                s._flatten_chain(slot)
            s._render()
        # ─────────────────────────────────────────────────────────────────
        # ── QUICK SAVE/LOAD SESSIONS ──────────────────────────────────────
        # ─────────────────────────────────────────────────────────────────
        # ── Save session (Cmd+Shift+S) ──
        if data.get('save_session'):
            name = data.get('save_session')  # puede ser un nombre o 'auto'
            s._file_save(name if name != True else None)
        # ── Load session (Cmd+L) ──
        if data.get('load_session'):
            name = data.get('load_session')
            if name:
                s._file_load(name)
        # ─────────────────────────────────────────────────────────────────
        # ── FILE DIALOGS (JSON import/export) ────────────────────────────
        # ─────────────────────────────────────────────────────────────────
        if data.get('dialog_save_bank'):
            s.last_msg = f"SAVE BANK {s.current_bank+1}..."
            s._render()
            threading.Thread(target=s._file_dialog_save, daemon=True).start()
        if data.get('dialog_load_bank'):
            s.last_msg = f"LOAD BANK {s.current_bank+1}..."
            s._render()
            threading.Thread(target=s._file_dialog_load, daemon=True).start()
        if data.get('dialog_save_pattern'):
            s.last_msg = f"SAVE PATTERN T{s.active+1}..."
            s._render()
            threading.Thread(target=s._file_dialog_save_pattern, daemon=True).start()
        if data.get('dialog_load_pattern'):
            s.last_msg = f"LOAD PATTERN → T{s.active+1}..."
            s._render()
            threading.Thread(target=s._file_dialog_load_pattern, daemon=True).start()
        # ── Asignar script a track ──
        if 'script_id' in data:
            script_id = data.get('script_id', None)
            if script_id and script_id in s.scripts_lib:
                s.tracks[s.active].script_id = script_id
                script_name = s.scripts_lib[script_id].get('name', script_id)
                s.last_msg = f"T{s.active+1} SCRIPT → {script_name}"
            else:
                s.tracks[s.active].script_id = None
                s.last_msg = f"T{s.active+1} SCRIPT OFF"
            s._render()
    return ('', 204)

def _start_visual(port=5001):
    import logging
    from werkzeug.serving import make_server
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    srv = make_server('0.0.0.0', port, _visual_app, threaded=True)
    srv.serve_forever()

# Launchpad MK1 — columna derecha (selección/mute de pista)
LP_RIGHT = [t * 16 + 8 for t in range(8)]   # T1=note8, T2=note24, ..., T8=note120
# Fila superior: CCs 104-111
LP_TOP        = list(range(104, 112))
LP_TRACK_DN   = 104   # pista ▼
LP_TRACK_UP   = 105   # pista ▲
LP_STEP_DN    = 106   # step page ◄
LP_STEP_UP    = 107   # step page ►
LP_SHIFT_CC   = 108   # SHIFT (hold)
LP_COPY_CC    = 109   # COPY (hold): 1er pad = copia, siguientes = pega
LP_DELETE_CC  = 110   # DELETE (hold): col.derecha = borra pista, pad = borra step
LP_BANK_VIEW  = 111   # toggle bank browser (mixer)
# Colores Launchpad MK1: velocity = (green_bits<<4) | red_bits
LP_OFF   = 0    # apagado
LP_GREEN = 48   # verde brillante  (step activo)
LP_RED   = 3    # rojo brillante   (track muted)
LP_AMBER = 51   # ámbar            (cursor, seleccionado)
LP_DIM   = 16   # verde tenue      (ocupado, no activo)
LP_DRED  = 1    # rojo tenue       (step activo en track muted)
# nanoKONTROL — CC leídos de config.py (con defaults para compatibilidad)
NK_KNOB_BASE  = getattr(config, 'NK_KNOB_BASE',  11)
NK_FADER_BASE = getattr(config, 'NK_FADER_BASE', 1)
NK_BTN_S_BASE = getattr(config, 'NK_BTN_S_BASE', 21)
NK_BTN_M_BASE = getattr(config, 'NK_BTN_M_BASE', 31)
NK_BPM_CC     = getattr(config, 'NK_BPM_CC',     19)

# (page, knob_idx) → (param_name, converter) para trig locks
STEP_LOCK_PARAMS = {
    (0, 2): ('prob',        lambda v: round(v/127, 2)),
    (0, 4): ('vel',         lambda v: max(1, v)),
    (1, 0): ('root',        lambda v: int(24 + (v/127)*84)),  # rango 24-108, igual que _handle_knob
    (1, 7): ('note_len',    lambda v: max(1, int(1+(v/127)*15))),
    (2, 1): ('delay_steps', lambda v: max(1, int((v/127)*8))),
    (2, 2): ('delay_fb',    lambda v: round(v/127, 2)),
    (2, 5): ('ratchet',     lambda v: max(1, int(1+(v/127)*15))),
    (2, 6): ('gate',        lambda v: round(v/127, 2)),
    # CC se gestiona en _handle_fader con cc_vals por lane
    # program y bank_msb excluidos: son parámetros de track, no automation por step
    (0, 7): ('micro_time',  lambda v: round((v / 127) - 0.5, 3)),  # -0.5..+0.5 × step_dur
}

SCALES = {
    0: ("Minor",       [0, 2, 3, 5, 7, 8, 10]),
    1: ("Major",       [0, 2, 4, 5, 7, 9, 11]),
    2: ("Dorian",      [0, 2, 3, 5, 7, 9, 10]),
    3: ("Pentatonica", [0, 2, 4, 7, 9]),
    4: ("Frigia",      [0, 1, 3, 5, 7, 8, 10]),
}
SCALE_ABBR = {"Minor": "MNR", "Major": "MJR", "Dorian": "DRN",
              "Pentatonica": "PEN", "Frigia": "FRG"}

def _scale_degree_shift(note, degrees, scale_idx, root):
    """Desplaza una nota MIDI N grados de escala (positivo=sube, negativo=baja)."""
    _, intervals = SCALES[scale_idx]
    n = len(intervals)
    rel = note - root
    octave = rel // 12
    semitone = rel % 12
    # Grado actual más cercano
    current_deg = min(range(n), key=lambda i: abs(intervals[i] - semitone))
    new_pos = current_deg + degrees
    new_oct = octave + new_pos // n
    new_deg = new_pos % n
    return max(0, min(127, root + intervals[new_deg] + new_oct * 12))

def _quantize_to_scale(note, scale_idx, root):
    """Snap una nota MIDI al grado de escala más cercano (manteniendo octava)."""
    _, intervals = SCALES[scale_idx]
    rel        = note - root
    oct_offset = (rel // 12) * 12
    rel_in_oct = rel % 12
    closest    = min(intervals, key=lambda x: abs(x - rel_in_oct))
    return max(0, min(127, root + oct_offset + closest))

def _duplicate_pattern(t):
    """Duplica el contenido del track (pattern + locks + tonal_notes) hasta el doble de steps (máx 64)."""
    old_len = len(t.pattern)
    new_len = min(old_len * 2, 64)
    if new_len <= old_len:
        return  # ya en máximo
    copies = new_len // old_len  # cuántas veces cabe el original
    # Duplicar pattern
    t.pattern      = (t.pattern * copies)[:new_len]
    t.base_pattern = (t.base_pattern * copies)[:new_len]
    t.steps        = new_len
    t.pulses       = sum(t.pattern)
    # Duplicar step_locks
    new_locks = {}
    for c in range(copies):
        offset = c * old_len
        for step, lk in t.step_locks.items():
            new_locks[step + offset] = copy.deepcopy(lk)
    t.step_locks = new_locks
    # Duplicar tonal_notes
    if t.tonal_notes:
        t.tonal_notes = (t.tonal_notes * copies)[:new_len]
    # Limpiar buffers
    t._pattern_buf = []
    t._locks_buf   = {}

def _transpose_track(t, delta):
    """Transpone delta semitonos: root, tonal_notes y step_locks notes."""
    t.root = max(0, min(127, t.root + delta))
    if t.tonal_notes:
        t.tonal_notes = [max(0, min(127, n + delta)) for n in t.tonal_notes]
    for lk in t.step_locks.values():
        if 'notes' in lk:
            lk['notes'] = [max(0, min(127, n + delta)) for n in lk['notes']]
        elif 'note' in lk:
            lk['note'] = max(0, min(127, lk['note'] + delta))

def _requantize(notes, new_scale_idx, root):
    """Re-cuantiza una lista de notas MIDI a la nueva escala, conservando contorno melódico."""
    _, intervals = SCALES[new_scale_idx]
    result = []
    for n in notes:
        rel         = n - root
        oct_offset  = (rel // 12) * 12
        rel_in_oct  = rel % 12
        closest     = min(intervals, key=lambda x: abs(x - rel_in_oct))
        result.append(max(0, min(127, root + oct_offset + closest)))
    return result
PLAY_MODES = ["forward", "reverse", "bounce", "random", "snake", "drunk"]

RESOLUTIONS = {
    0: ("1/128", 0.125),   # modo Aphex Twin
    1: ("1/64",  0.25),    # redobles rápidos
    2: ("1/32t", 0.333),   # tresillos extremos
    3: ("1/32",  0.5),
    4: ("1/16t", 0.666),   # tresillos rápidos
    5: ("1/16",  1.0),     # por defecto
    6: ("1/8t",  1.333),
    7: ("1/8",   2.0),
    8: ("1/4t",  2.666),
    9: ("1/4",   4.0),
    10: ("1/2",  8.0),     # blanca
}
MULTIPLIERS = {
    0: ("x1/2", 0.5),
    1: ("x1",   1.0),
    2: ("x2",   2.0),
    3: ("x4",   4.0),
}

# Subdivisiones musicales para RSPD (ratchet spacing)
# (multiplicador respecto a 1/16, nombre)
# Regla: ratchet * mult <= 1.0 para que los hits queden dentro del step
#   1/32 (×0.5)  → máx 2 hits sin salirse
#   1/48 (×0.33) → máx 3 hits
#   1/64 (×0.25) → máx 4 hits
#   1/96 (×0.16) → máx 6 hits
#   1/128(×0.125)→ máx 8 hits
RSPD_DIVS = [
    (0.5,    '1/32'),
    (1/3,    '1/48'),
    (0.25,   '1/64'),
    (1/6,    '1/96'),
    (0.125,  '/128'),
]

PAGES = {0: "A·SEQ", 1: "B·NOTE", 2: "C·FX", 3: "D·CONF"}
PAGE_PARAMS = {
    0: ["PULS", "STEP", "PROB", "MODE", "VEL", "SWNG", "RESL", "ROTA"],
    1: ["NOTE", "SCAL", "OCT", "DENS", "SPRD", "HARM", "INTV", "NLEN"],
    2: ["DLY", "DTIM", "FDBK", "RDEC", "RSPD", "RTCH", "GATE", "CC"],
    3: ["PROG", "BANK", "PTBK", "PTRN", "CHAN", "CLK", "PORT", "SCRI"],
}
NK_UNDO_CC   = getattr(config, 'NK_UNDO_CC', 47)
NK_REDO_CC   = getattr(config, 'NK_REDO_CC', 48)
NK_SAVE_CC   = getattr(config, 'NK_SAVE_CC', 33)   # nanoKONTROL ch4 cc33  → save banks
NK_LOAD_CC   = getattr(config, 'NK_LOAD_CC', 23)   # nanoKONTROL ch4 cc23  → load banks
NK_SAVE_PAT_CC = getattr(config, 'NK_SAVE_PAT_CC', 34)  # ch4 cc34 → save pattern
NK_LOAD_PAT_CC = getattr(config, 'NK_LOAD_PAT_CC', 24)  # ch4 cc24 → load pattern
MAX_UNDO     = 30
SAVES_DIR    = os.path.join(_APP_SUPPORT, 'saves')

NOTE_NAMES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"]

def lp_grid(track, col):
    """Nota del pad: track 0-7 (T1=fila 0=top físico MK1), col 0-7"""
    return track * 16 + col

def lp_right(track):
    """Nota del botón derecho de la pista"""
    return track * 16 + 8

def note_name(n):
    """MIDI → nombre de nota. Convención Akai/Roland: MIDI 60 = C3 (middle C)."""
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 2}"

def _lock_summary(locks):
    """Resumen compacto de trig locks para el display"""
    if not locks:
        return ""
    parts = []
    if 'notes'       in locks: parts.append('[' + ' '.join(note_name(int(n)) for n in locks['notes']) + ']')
    elif 'note'      in locks: parts.append(note_name(locks['note']))
    elif 'root'      in locks: parts.append(f"R{note_name(locks['root'])}")
    if 'vel'         in locks: parts.append(f"v{locks['vel']}")
    if 'prob'        in locks: parts.append(f"p{int(locks['prob']*100)}%")
    if 'gate'        in locks: parts.append(f"g{int(locks['gate']*100)}%")
    if 'ratchet'      in locks: parts.append(f"r{locks['ratchet']}x")
    if 'ratchet_div'  in locks: parts.append(f"{int(float(locks['ratchet_div'])*100)}%")
    if 'note_len'    in locks: parts.append(f"len{locks['note_len']}")
    if 'delay_steps' in locks: parts.append(f"dt{locks['delay_steps']}")
    if 'delay_fb'    in locks: parts.append(f"df{int(locks['delay_fb']*100)}%")
    if 'cc_vals'     in locks:
        for li, cv in locks['cc_vals'].items():
            parts.append(f"cc{li}:{cv}")
    return f" [{' '.join(parts)}]"

_PLOCK_LABEL = {
    'note':        'NOTE',
    'vel':         'VEL',
    'prob':        'PROB',
    'gate':        'GATE',
    'ratchet':     'RTCH',
    'ratchet_div': 'RSPD',
    'ratchet_curve':'RDEC',
    'note_len':    'LEN',
    'delay_steps': 'DTIME',
    'delay_fb':    'DFBK',
    'cc_vals':     'CC',
}

# Nombres de display para params de p-lock (los que difieren de _param_label(param))
_PARAM_LABEL = {
    'micro_time': 'TIMING',
    'program':    'PC',
    'bank_msb':   'BANK',
    'note_len':   'LEN',
    'delay_steps':'D.TIME',
    'delay_fb':   'D.FB',
    'cc_vals':    'CC',
}
def _param_label(p): return _PARAM_LABEL.get(p, p.upper())

def _fmt_plock_val(param, val):
    if param == 'note':         return note_name(int(val))
    if param in ('prob', 'delay_fb', 'gate'): return f"{int(val*100)}%"
    if param == 'ratchet':      return f"{int(val)}x"
    if param == 'ratchet_div':  return f"{int(float(val)*100)}%"
    if param == 'ratchet_curve':
        c = float(val)
        return ("F/O" if c < -0.05 else "F/I" if c > 0.05 else "---") + f" {c:+.1f}"
    if param == 'note_len': return f"{int(val)}st"
    if param == 'program':    return f"PC{int(val)}"
    if param == 'bank_msb':   return f"BNK{int(val)}"
    if param == 'micro_time': return f"{int(val*100):+d}%"
    return str(val)

def euclidean(pulses, steps, rotation=0):
    if pulses == 0: return [False] * steps
    if pulses >= steps: return [True] * steps
    result, bucket = [], 0
    for _ in range(steps):
        bucket += pulses
        if bucket >= steps:
            bucket -= steps
            result.append(True)
        else:
            result.append(False)
    # Auto-rotar para que el primer hit siempre caiga en el paso 0
    first = next((i for i, v in enumerate(result) if v), 0)
    result = result[first:] + result[:first]
    # Aplicar rotación manual
    if rotation:
        result = result[rotation % steps:] + result[:rotation % steps]
    return result

def gen_euclidean_random(steps_range=(4, 16)):
    steps  = random.randint(*steps_range)
    pulses = random.randint(1, steps)
    return euclidean(pulses, steps), pulses, steps

def gen_density(steps, density):
    return [random.random() < density for _ in range(steps)]

def gen_mutation(pattern, amount=0.2):
    result = pattern[:]
    for i in range(len(result)):
        if random.random() < amount:
            result[i] = not result[i]
    return result

def gen_tonal(steps, scale_idx, root, spread=1.0):
    _, intervals = SCALES[scale_idx]
    max_idx = max(1, int(spread * (len(intervals) * 2 - 1)))
    pattern, notes = [], []
    for _ in range(steps):
        active = random.random() > 0.4
        pattern.append(active)
        if active:
            idx        = random.randint(0, max_idx)
            oct_offset = (idx // len(intervals)) * 12
            note       = root + intervals[idx % len(intervals)] + oct_offset
            notes.append(note)
    return pattern, notes

# ─── Script Pattern Helpers ───
def rotate(pattern, n):
    """Rotar patrón n steps a la derecha."""
    if not pattern: return pattern
    n = n % len(pattern)
    return pattern[-n:] + pattern[:-n] if n else pattern[:]

def mirror(pattern):
    """Invertir patrón (leer de atrás hacia adelante)."""
    return list(reversed(pattern))

def invert(pattern):
    """Invertir lógica: True→False, False→True."""
    return [not p for p in pattern]

def randomize(pattern, probability=0.5):
    """Setear cada step a True con probabilidad dada."""
    return [random.random() < probability for _ in pattern]

def skip_every(pattern, n):
    """Deshabilitar cada n-ésimo step."""
    return [p if (i % n) != 0 else False for i, p in enumerate(pattern)]

def only_every(pattern, n):
    """Solo mantener cada n-ésimo step activo."""
    return [p if (i % n) == 0 else False for i, p in enumerate(pattern)]

def fill_gap(pattern, max_gap=2):
    """Llenar huecos entre steps activos (máx max_gap pasos)."""
    result = pattern[:]
    for i in range(len(result)-1):
        if result[i] and not result[i+1]:
            # Buscar siguiente True
            for j in range(i+1, min(i+max_gap+1, len(result))):
                if result[j]:
                    for k in range(i+1, j):
                        result[k] = True
                    break
    return result

def drunk_walk(steps, prob_change=0.3, start=True):
    """Patrón aleatorio "borracho" que cambia lentamente."""
    pattern = [start]
    for _ in range(steps - 1):
        if random.random() < prob_change:
            pattern.append(not pattern[-1])
        else:
            pattern.append(pattern[-1])
    return pattern

def pulse_train(steps, pulse_width=0.5):
    """Tren de pulsos con ancho configurable (0-1)."""
    return [i % 1.0 < pulse_width for i in range(steps)]

def wobble(pattern, amount=0.2):
    """Variar aleatoriamente el patrón (prob de flip cada step)."""
    return [not p if random.random() < amount else p for p in pattern]

def compress(pattern, factor=2):
    """Comprimir patrón repitiendo cada step factor veces."""
    return [p for p in pattern for _ in range(factor)]

def thin(pattern, ratio=2):
    """Hacer patrón más disperso (mantener cada ratio-ésimo)."""
    return [p if (i % ratio) == 0 else False for i, p in enumerate(pattern)]

def alternating(steps, ratio=2):
    """Patrón alternante: ratio steps ON, ratio steps OFF."""
    return [i // ratio % 2 == 0 for i in range(steps)]

def stutter(pattern, repeat=2):
    """Repetir cada step activo repeat veces."""
    result = []
    for p in pattern:
        if p:
            result.extend([True] * repeat)
        else:
            result.append(False)
    return result[:len(pattern)]

def polyrhythm(steps, div1=3, div2=4):
    """Combinar dos ritmos: div1 pulsos EN div1 steps, div2 pulsos EN div2 steps."""
    p1 = euclidean(1, div1)
    p2 = euclidean(1, div2)
    result = []
    for i in range(steps):
        on1 = p1[i % len(p1)]
        on2 = p2[i % len(p2)]
        result.append(on1 or on2)
    return result


class Track:
    def __init__(self, channel):
        self.channel      = channel
        self.steps        = 16
        self.pulses       = 0
        self.root         = 48
        self.prob         = 1.0
        self.swing        = 0.0
        self.velocity     = 100
        self.scale_idx    = 0
        self.play_mode    = 0
        self.octave       = 0
        self.spread       = 0.0
        self.density      = 0.0
        self.harmony_src  = -1
        self.harmony_on   = False  # toggle independiente del efecto harmony
        self.interval     = 0
        self.delay_on     = False
        self.delay_steps  = 2
        self.delay_fb     = 0.5
        self.humanize     = 0.0
        self.ratchet      = 1
        self.ratchet_div   = 1.0   # spread 0.0–1.0: fracción del step entre hits (1.0=equidistante)
        self.ratchet_curve = 0.0   # envolvente de vel: -1=fade out, 0=plano, +1=fade in
        self.gate         = 0.5
        self.note_len     = 1     # longitud en steps (1 = un step, 4 = cuatro steps...)
        self.strum        = 0.0   # delay entre nota principal y armonía (0-1)
        self.cc_lanes     = [{'num': -1, 'val': 0} for _ in range(8)]  # 8 lanes de CC
        self.active_cc_lane = 0   # lane activa para edición
        self.program      = 0     # MIDI program change (0-127)
        self.bank_msb     = 0     # MIDI bank select (CC 0)
        self.send_pc      = True  # Si False, no envía program/bank change
        self.port         = 0     # 0 = Out 1, 1 = Out 2
        self.resolution   = 5     # 1/16 por defecto
        self.multiplier   = 1
        self.rotation     = 0
        self.tick_acc     = 0.0
        self.muted        = False
        self.step_locks   = {}   # {step_idx: {param: val}} — trig locks por paso
        self.stoch_amounts  = {}   # {param_name: float 0-1}
        self.stoch_enabled  = {}   # {param_name: bool}
        self.stoch_base     = {}   # {param_name: valor base en el momento de activar}
        self.cursor       = 0
        self.play_cursor  = 0
        self.note_cursor  = 0
        self.note_dir     = 1
        self.even_step    = True
        self.display_step = 0
        self.firing       = False
        self.last_fire_time = 0.0
        self.fire_history   = []    # [(perf_counter, step_idx), ...] últimos 6 steps
        self.tonal_notes  = []
        self.tonal_idx    = 0
        self._scale_pos   = 0   # posición en grados de escala (para modo INTV)
        self.last_kbd_note = None  # última nota tocada en teclado (semilla para próximos pads)
        self.note_offset  = 0      # transposición global en semitonos (modificada por NOTE stoch)
        self.base_pattern = euclidean(0, 16)
        self.pattern      = self.base_pattern[:]
        self._pattern_buf = []   # buffer de steps ocultos al acortar (se restauran al ampliar)
        self._locks_buf   = {}   # buffer de step_locks ocultos
        self.auto_lanes   = {}    # {param: [(phase, value), ...]} automation continua
        self.auto_play    = True  # reproducir automation grabada
        self._cycle_start = 0.0   # perf_counter del inicio del ciclo actual
        self.blink_state  = False
        self.script_id    = None  # ID del script de librería (ej: "duplicate_steps")
        self.loop_count   = 0     # Contador de vueltas del patrón (para mutación automática)
        self.scripts_lib  = {}    # Referencia a librería de scripts (set desde Sequencer)

    def reset(self):
        """Reset completo del track — conserva solo canal y asignación MIDI."""
        ch, prog, bank, spc, pt = self.channel, self.program, self.bank_msb, self.send_pc, self.port
        self.__init__(ch)
        self.program   = prog
        self.bank_msb  = bank
        self.send_pc   = spc
        self.port      = pt

    def rebuild(self):
        self.base_pattern = euclidean(self.pulses, self.steps, self.rotation)
        self.pattern      = self.base_pattern[:]
        self._pattern_buf = []   # regeneración: descartar buffer
        self._locks_buf   = {}
        # Ejecutar script si existe
        self.execute_script()

    # ── Propiedades de conveniencia: acceso a la lane CC activa ──────
    @property
    def cc_num(self):
        return self.cc_lanes[self.active_cc_lane]['num']

    @cc_num.setter
    def cc_num(self, v):
        self.cc_lanes[self.active_cc_lane]['num'] = v

    @property
    def cc_val(self):
        return self.cc_lanes[self.active_cc_lane]['val']

    @cc_val.setter
    def cc_val(self, v):
        self.cc_lanes[self.active_cc_lane]['val'] = v

    def tick_interval(self, base_interval):
        _, res_mult = RESOLUTIONS[self.resolution]
        _, mul_mult = MULTIPLIERS[self.multiplier]
        return base_interval * res_mult * mul_mult

    def current_note(self):
        _, intervals = SCALES[self.scale_idx]
        n = len(intervals)
        if self.tonal_notes:
            # Modo tonal: melodía generada, recorrer por índice
            idx  = self.tonal_idx % len(self.tonal_notes)
            base = self.tonal_notes[idx]
        elif self.interval > 0:
            # Modo INTV: ciclar por grados de escala según _scale_pos
            deg       = self._scale_pos % n
            oct_extra = (self._scale_pos // n) * 12
            base      = self.root + intervals[deg] + oct_extra
        else:
            base = self.root

        # SPREAD: variación aleatoria en escala + saltos de octava en valores altos
        if self.spread > 0:
            spread_range = max(1, int(self.spread * 6))  # hasta ±6 grados
            offset = random.randint(-spread_range, spread_range)
            base = _scale_degree_shift(base, offset, self.scale_idx, self.root)
            # >0.5: saltos de octava ocasionales; >0.8: saltos de 2 octavas
            if self.spread > 0.5:
                oct_prob = (self.spread - 0.5) * 2.0   # 0→1 entre 0.5 y 1.0
                if random.random() < oct_prob:
                    max_oct = 2 if self.spread > 0.8 else 1
                    base += random.choice([-1, 1]) * random.randint(1, max_oct) * 12

        # octave se aplica en _tick_track (afecta también p-locks)
        return max(0, min(127, base))

    def advance_note(self):
        _, intervals = SCALES[self.scale_idx]
        n = len(intervals)
        if self.tonal_notes:
            self.tonal_idx = (self.tonal_idx + 1) % len(self.tonal_notes)
            return
        if self.interval > 0:
            # Avanzar por grados de escala según play_mode
            if self.play_mode == 'forward':
                self._scale_pos += self.interval
            elif self.play_mode == 'reverse':
                self._scale_pos -= self.interval
            elif self.play_mode == 'bounce':
                self._scale_pos += self.interval * self.note_dir
                if self._scale_pos >= n * 2 or self._scale_pos < 0:
                    self.note_dir *= -1
                    self._scale_pos = max(0, min(n * 2 - 1, self._scale_pos))
            elif self.play_mode in ('random', 'drunk'):
                self._scale_pos += random.choice([-self.interval, self.interval])
            else:
                self._scale_pos += self.interval
            self._scale_pos = self._scale_pos % (n * 2)  # 2 octavas de rango

    def snapshot(self):
        snap = {k: getattr(self, k) for k in [
            "channel","steps","pulses","root","prob","swing","velocity",
            "scale_idx","play_mode","octave","spread","density","harmony_src","harmony_on",
            "interval","delay_on","delay_steps","delay_fb","humanize","ratchet","ratchet_div","ratchet_curve",
            "gate","note_len","strum",
            "resolution","multiplier","muted","pattern","tonal_notes",
            "program","bank_msb","active_cc_lane","port",
            "send_pc","rotation","note_offset","auto_play","script_id"
        ]}
        snap['pattern']       = self.pattern[:]
        snap['tonal_notes']   = self.tonal_notes[:]
        snap['cc_lanes']      = [dict(l) for l in self.cc_lanes]
        snap['step_locks']    = {str(k): dict(v) for k, v in self.step_locks.items()}
        snap['stoch_enabled'] = dict(self.stoch_enabled)
        snap['stoch_amounts'] = dict(self.stoch_amounts)
        snap['stoch_base']    = dict(self.stoch_base)
        snap['auto_lanes']    = {k: list(v) for k, v in self.auto_lanes.items()}
        snap['_res_version']  = 2   # marca orden nuevo de RESOLUTIONS
        snap['_melodic_version'] = 2  # marca lógica nueva de SPREAD/DENS/INTV
        return snap

    def load(self, snap):
        for k, v in snap.items():
            if k in ('step_locks', 'step_notes', 'stoch_enabled', 'stoch_amounts', 'stoch_base',
                      'cc_lanes', 'cc_num', 'cc_val'):
                continue
            setattr(self, k, v)
        # Defaults para campos nuevos ausentes en snapshots antiguos
        if 'port' not in snap:
            self.port = 0
        # CC lanes: formato nuevo o migrar desde cc_num/cc_val antiguo
        if 'cc_lanes' in snap:
            self.cc_lanes = [dict(l) for l in snap['cc_lanes']]
        elif 'cc_num' in snap:
            self.cc_lanes = [{'num': -1, 'val': 0} for _ in range(8)]
            self.cc_lanes[0] = {'num': snap.get('cc_num', -1), 'val': snap.get('cc_val', 0)}
        # Garantizar que pattern sea lista de booleans (JSON puede devolver 0/1)
        self.pattern = [bool(x) for x in self.pattern]
        # JSON convierte claves int → str; soportar formato antiguo (step_notes)
        raw = snap.get('step_locks', {})
        if not raw:
            raw = {k: {'note': v} for k, v in snap.get('step_notes', {}).items()}
        self.step_locks    = {int(k): dict(v) for k, v in raw.items()}
        # Migrar step_locks antiguos: 'cc_val' → 'cc_vals' {0: val}
        for step, lk in self.step_locks.items():
            if 'cc_val' in lk and 'cc_vals' not in lk:
                lk['cc_vals'] = {0: lk.pop('cc_val')}
        self.stoch_enabled = dict(snap.get('stoch_enabled', {}))
        self.stoch_amounts = dict(snap.get('stoch_amounts', {}))
        self.stoch_base    = dict(snap.get('stoch_base', {}))
        self.auto_lanes    = {k: [tuple(p) for p in v]
                              for k, v in snap.get('auto_lanes', {}).items()}
        # Migrar resolution de orden antiguo → nuevo (si viene de snapshot viejo)
        # Antiguo: {0:1/32, 1:1/16, 2:1/8, 3:1/4, 4:1/8t, 5:1/4t, 6:1/64, 7:1/128, 8:1/16t, 9:1/32t, 10:1/2}
        # Nuevo:   {0:1/128, 1:1/64, 2:1/32t, 3:1/32, 4:1/16t, 5:1/16, 6:1/8t, 7:1/8, 8:1/4t, 9:1/4, 10:1/2}
        _RES_MIGRATE = {0:3, 1:5, 2:7, 3:9, 4:6, 5:8, 6:1, 7:0, 8:4, 9:2, 10:10}
        if snap.get('_res_version', 0) < 2:
            self.resolution = _RES_MIGRATE.get(self.resolution, self.resolution)
        # Migrar SPREAD/DENS/INTV: comportamiento cambió en versión 2
        if snap.get('_melodic_version', 0) < 2:
            self.spread   = 0.0
            self.density  = 0.0
            self.interval = 0
        # Recalcular pulses desde el pattern real (por si hubo edición manual)
        self.pulses        = sum(self.pattern)
        self.base_pattern  = self.pattern[:]
        # Ejecutar script si existe
        self.execute_script()
        # Resetear todo el estado de reproducción
        self.cursor        = 0
        self.play_cursor   = 0
        self.note_cursor   = 0
        self.note_dir      = 1
        self.tonal_idx     = 0
        self.tick_acc      = 0.0
        self.even_step     = True
        self.firing        = False

    def execute_script(self):
        """Ejecutar script - modifica patrón y/o parámetros."""
        if not self.script_id or not self.scripts_lib or self.script_id not in self.scripts_lib:
            return

        script_data = self.scripts_lib[self.script_id]
        code = script_data.get('code', '')
        if not code or not code.strip():
            return

        import math
        # Contexto extendido: patrón + parámetros tonales/rítmicos + funciones
        ctx = {
            # Patrón rítmico
            'pattern': self.pattern[:],
            'steps': self.steps,
            'pulses': self.pulses,
            # Parámetros tonales
            'prob': self.prob,
            'velocity': self.velocity,
            'octave': self.octave,
            'note_offset': self.note_offset,
            'play_mode': self.play_mode,
            'gate': self.gate,
            'ratchet': self.ratchet,
            'ratchet_div': self.ratchet_div,
            'interval': self.interval,
            'note_len': self.note_len,
            'spread': self.spread,
            'density': self.density,
            'humanize': self.humanize,
            'swing': self.swing,
            # Estado
            'loop_count': self.loop_count,
            # Funciones de patrón
            'euclidean': euclidean,
            'rotate': rotate,
            'mirror': mirror,
            'invert': invert,
            'randomize': randomize,
            'skip_every': skip_every,
            'only_every': only_every,
            'fill_gap': fill_gap,
            'drunk_walk': drunk_walk,
            'pulse_train': pulse_train,
            'wobble': wobble,
            'compress': compress,
            'thin': thin,
            'alternating': alternating,
            'stutter': stutter,
            'polyrhythm': polyrhythm,
            'gen_euclidean_random': gen_euclidean_random,
            'gen_density': gen_density,
            'gen_mutation': gen_mutation,
            # Funciones matemáticas
            'random': random.random,
            'randint': random.randint,
            'choice': random.choice,
            'sin': math.sin,
            'cos': math.cos,
            'pi': math.pi,
            'min': min,
            'max': max,
            'abs': abs,
            'int': int,
            'float': float,
        }

        try:
            exec(code, ctx)
            # Patrón
            new_pattern = ctx.get('pattern')
            if new_pattern and isinstance(new_pattern, (list, tuple)):
                self.pattern = list(new_pattern)[:self.steps]
                self.pattern = [bool(p) for p in self.pattern]
                self.pulses = sum(self.pattern)
            # Parámetros tonales (con clamping para evitar valores absurdos)
            self.prob        = max(0.0, min(1.0, float(ctx.get('prob', self.prob))))
            self.velocity    = max(1, min(127, int(ctx.get('velocity', self.velocity))))
            self.octave      = max(-3, min(3, int(ctx.get('octave', self.octave))))
            self.note_offset = max(-24, min(24, int(ctx.get('note_offset', self.note_offset))))
            self.play_mode   = max(0, min(len(PLAY_MODES)-1, int(ctx.get('play_mode', self.play_mode))))
            self.gate        = max(0.05, min(1.0, float(ctx.get('gate', self.gate))))
            self.ratchet     = max(1, min(8, int(ctx.get('ratchet', self.ratchet))))
            self.ratchet_div = max(0.0, min(1.0, float(ctx.get('ratchet_div', self.ratchet_div))))
            self.interval    = max(-7, min(7, int(ctx.get('interval', self.interval))))
            self.note_len    = max(1, min(16, int(ctx.get('note_len', self.note_len))))
            self.spread      = max(0.0, min(1.0, float(ctx.get('spread', self.spread))))
            self.density     = max(0.0, min(1.0, float(ctx.get('density', self.density))))
            self.swing       = max(0.0, min(0.5, float(ctx.get('swing', self.swing))))
        except Exception:
            pass  # Silencio total - no spamear logs


class Sequencer:
    def __init__(self):
        # ── Script library (cargar primero) ──
        self.scripts_lib  = self._load_scripts_library()

        self.tracks       = [Track(i) for i in range(8)]
        # Pasar librería a cada track
        for t in self.tracks:
            t.scripts_lib = self.scripts_lib

        self.active       = 0
        self.bpm          = config.BPM
        self.page         = 0
        self.kb_param      = 0            # índice 0-7 del param seleccionado en página actual (teclado)
        self.kb_step       = 0            # step seleccionado con Tab para P-lock
        self.kb_step_focus = False        # True = ↑/↓ edita P-lock del step, no el param global
        self.kb_enabled    = True         # False = atajos de teclado desactivados (excepto Cmd+K)
        self._page_change_ts = 0.0  # timestamp del último cambio de escena NK
        self.shift        = False
        self.bank_view      = False   # True = grid muestra bancos×slots
        self.undo_stack     = []
        self.redo_stack     = []
        self._last_undo_push = 0.0
        self.running        = False
        self._loop_gen      = 0          # incrementa cada vez que se lanza un nuevo loop
        self.lock           = threading.Lock()
        self.last_msg     = "Ready"
        self.bv_status    = ""        # mensaje de status para bank view (save/load/etc)
        self.bv_status_warn = False   # True = naranja (warning), False = verde (ok)
        self.banks        = [{} for _ in range(8)]
        self.current_bank = 0
        self.active_slot  = -1   # 0-63
        self.chain        = []   # lista de (bank, slot) en orden
        self.chain_pos    = 0    # posición actual en el chain durante playback
        self.tick_count   = 0
        self.pending_load = None
        self._note_gen    = 0   # incrementa en cada carga de patrón; invalida timers en vuelo
        self.step_pg      = 0     # página de steps visible (0=steps 0-7, 1=8-15...)
        self.held_step        = None   # (track_idx, step_idx) del pad actualmente sostenido
        self.held_step_cycle  = 0     # índice actual al ciclar valores del pad sostenido
        self.held_knob_used   = False  # True si se movió un knob durante el hold
        self.held_kbd_count   = 0      # nº notas de teclado recibidas durante el hold actual
        self.step_focus       = None  # foco persistente en un step (no se borra al soltar)
        self.step_focus_cycle = 0     # índice de capa para el step enfocado
        self._last_pad_press  = None  # (ti, step, ts) para detectar doble-click
        self.recording        = False  # True = captura knobs → step_locks en tiempo real
        self.rest_flash       = None   # step saltado como rest (se emite una vez, el JS lo borra)
        self._rec_pending     = {}     # {param: value} — valores pendientes de grabar en cada step
        self.xfade_snap_a   = None  # lista de 8 snapshots (estado A)
        self.xfade_snap     = None  # lista de 8 snapshots (estado B, del banco)
        self.xfade_amt      = 0.0   # 0.0 = solo A, 1.0 = solo B
        self.xfade_label    = ''    # label del slot B
        self._kb_held         = {}     # {note: (step, t_on)} para calcular note_len
        self.copy_mode        = False  # True = botón COPY sostenido
        self.copy_first       = True   # True = próximo pad hace COPY, False = PASTE
        self.clipboard_step   = None   # {'active': bool, 'locks': dict}
        self.clipboard_track  = None   # snapshot de pista para copy/paste entre tracks
        self.delete_mode      = False  # True = botón DELETE sostenido
        self.cc_draw          = False  # True = modo CC draw en el Launchpad
        self._last_render_ts  = 0.0    # throttle de _render (max ~20 fps)
        self._last_leds_ts    = 0.0    # throttle de _update_leds (max ~30 fps)
        self.clock_out        = True   # enviar MIDI Clock por IAC
        self.daw_mirror       = True   # mirror de notas/CC al IAC para grabar en DAW
        self.ext_sync         = False  # True = sincronizar tempo al clock externo del DAW
        self._clock_acc       = 0.0    # acumulador para 24 PPQN
        self._ext_clock_event = threading.Event()  # señaliza tick externo al loop
        self._ext_clock_count = 0      # contador de pulsos 0xF8 (dispara cada 6 = 1/16)
        self._ext_clock_ts    = 0.0    # timestamp del último 0xF8 para calcular BPM
        self._prog_timers = {}    # {track_idx: Timer} debounce para program/bank change
        self._last_pc_sent = {}   # {(port, channel): (bank_msb, program)} — último PC enviado por canal
        self._pending_bank = {}   # {channel: bank_msb} — Bank Select pendiente antes de PC
        self._kb_thru_map  = {}   # {note_original: note_enviada} — para note-off correcto tras cuantizar
        self._led_cache   = {}    # caché para enviar solo cambios de LED
        self._blink_info  = None  # (bank, slot, until_ts) para feedback visual al cargar
        self.view_mode    = 'page'  # 'page' | 'detail'
        self._detail_timer = None   # Timer que vuelve a page view tras inactividad en knobs
        self.mapping_mode = False   # True = UI en modo MIDI mapping
        self.learn_target = None    # (page, knob_idx) esperando asignación de CC
        self.compact_view = False   # True = compact view (todas las pistas + todos los params)
        # Bank View mode (teclado)
        self.kb_bank_view = False   # True = en modo Bank View (grid de slots)
        self.kb_bank_cursor = (0, 0)  # (row, col) posición en grid 8x8
        self.kb_all_mode  = False   # True = knob nudges afectan a los 8 tracks
        # Cargar cc_map desde settings.json con compatibilidad hacia atrás
        global _seq_ref
        _seq_ref = self
        _settings  = _load_settings()
        _def_map   = _default_cc_map()
        _saved_cc  = _settings.get('cc_map', {})
        def _load_group(key):
            s = _saved_cc.get(key, _def_map[key])
            if s and not isinstance(s[0], list):   # formato antiguo (lista plana)
                s = [s[:] for _ in range(4)]
            return s
        self._cc_map = {
            'knob':  _load_group('knob'),
            'fade':  _load_group('fade'),
            'btn_s': _load_group('btn_s'),
            'btn_m': _load_group('btn_m'),
            'misc':  {**_def_map['misc'], **_saved_cc.get('misc', {})},
        }
        self.mapping_group = 'knob'
        self._load_banks()
        _lp_port = getattr(config, 'LAUNCHPAD_PORT', 'Launchpad')
        _nk_in   = getattr(config, 'NK_IN_PORT',     'nanoKONTROL SLIDER/KNOB')
        _nk_out  = getattr(config, 'NK_OUT_PORT',    'nanoKONTROL')
        kb_port  = getattr(config, 'MIDI_KB_PORT',   '')
        self.midi_out  = self._open_output(config.MIDI_OUT_PORT)
        _out2_port = getattr(config, 'MIDI_OUT_PORT2', '')
        self.midi_out2 = self._open_output(_out2_port)
        self.midi_outs = [self.midi_out, self.midi_out2]  # indexado por t.port
        _clk_port = getattr(config, 'MIDI_CLK_PORT', '')
        self.clk_out  = self._open_output(_clk_port)  # puerto virtual para clock al DAW
        self.lp_out   = self._open_output(_lp_port)
        self.nk_out   = self._open_output(_nk_out)
        self.lp_in    = self._open_input(_lp_port, self._on_launchpad)   if _lp_port else None
        self.nk_in = self._open_input(_nk_in,  self._on_nanokontrol) if _nk_in  else None
        self.kb_in = self._open_input(kb_port, self._on_keyboard)    if kb_port else None
        self._ext_ins = self._open_ext_inputs()
        # Puerto de entrada para MIDI Clock externo (sync desde DAW)
        _sync_port = getattr(config, 'MIDI_SYNC_PORT', '')
        self.sync_in  = self._open_sync_input(_sync_port)
        self._nk_enable_leds()

    def _out(self, t):
        """Devuelve el puerto MIDI de salida asignado al track."""
        return self.midi_outs[t.port] if t.port < len(self.midi_outs) else self.midi_out

    def _open_output(self, name):
        out = rtmidi.MidiOut()
        if not name:
            return out          # puerto desactivado — objeto desconectado
        ports = out.get_ports()
        for i, p in enumerate(ports):
            if name in p:
                out.open_port(i)
                print(f"[MIDI OUT] abierto: {p}")
                return out
        if name == config.MIDI_OUT_PORT:   # solo fallback para el out principal
            out.open_port(0)
            print(f"[MIDI OUT] fallback port 0: {ports[0] if ports else '?'}")
        else:
            print(f"[MIDI OUT] '{name}' no encontrado — disponibles: {ports}")
        return out

    def _reconnect_port(self, slot):
        """Reconecta el puerto MIDI real tras cambiar config en cycle_port."""
        if slot == 'midi_out':
            self.midi_out  = self._open_output(config.MIDI_OUT_PORT)
            self.midi_outs[0] = self.midi_out
        elif slot == 'midi_out2':
            self.midi_out2 = self._open_output(getattr(config, 'MIDI_OUT_PORT2', ''))
            if len(self.midi_outs) > 1:
                self.midi_outs[1] = self.midi_out2
        elif slot == 'lp_port':
            lp = getattr(config, 'LAUNCHPAD_PORT', '')
            self.lp_out = self._open_output(lp)
            if self.lp_in and self.lp_in.is_port_open():
                self.lp_in.cancel_callback()
                self.lp_in.close_port()
            self.lp_in = self._open_input(lp, self._on_launchpad) if lp else None
        elif slot == 'nk_in':
            nk = getattr(config, 'NK_IN_PORT', '')
            if self.nk_in and self.nk_in.is_port_open():
                self.nk_in.cancel_callback()
                self.nk_in.close_port()
            self.nk_in = self._open_input(nk, self._on_nanokontrol) if nk else None
        elif slot == 'kb_port':
            kb = getattr(config, 'MIDI_KB_PORT', '')
            if self.kb_in and self.kb_in.is_port_open():
                self.kb_in.cancel_callback()
                self.kb_in.close_port()
            self.kb_in = self._open_input(kb, self._on_keyboard) if kb else None

    def _open_input(self, name, callback):
        inp = rtmidi.MidiIn()
        inp.ignore_types(sysex=False, timing=True, active_sense=True)
        if not name:
            return inp          # puerto desactivado — objeto desconectado
        for i, p in enumerate(inp.get_ports()):
            if name in p:
                inp.open_port(i)
                inp.set_callback(callback)
                return inp
        print(f"[MIDI IN] '{name}' no encontrado — disponibles: {inp.get_ports()}")
        return inp

    def _open_sync_input(self, name):
        """Abre el puerto de entrada para MIDI Clock externo (0xF8/0xFA/0xFC/0xFB)."""
        inp = rtmidi.MidiIn()
        inp.ignore_types(sysex=True, timing=False, active_sense=True)  # timing=False para recibir 0xF8
        if not name:
            return inp
        for i, p in enumerate(inp.get_ports()):
            if name in p:
                inp.open_port(i)
                inp.set_callback(self._on_sync_clock)
                print(f"[SYNC IN] clock externo en: {p}")
                return inp
        print(f"[SYNC IN] '{name}' no encontrado, sync externo desactivado")
        return inp

    def _on_sync_clock(self, msg_data, timestamp):
        """Callback de MIDI Clock externo. Solo actúa si ext_sync está activado."""
        if not self.ext_sync:
            return
        msg, _ = msg_data
        status = msg[0]
        if status == 0xFA:   # Start
            self._ext_clock_count = 0
            self._ext_clock_ts = time.perf_counter()
            if not self.running:
                threading.Thread(target=self.play, daemon=True).start()
        elif status == 0xFB: # Continue
            self._ext_clock_ts = time.perf_counter()
            if not self.running:
                threading.Thread(target=self.play, daemon=True).start()
        elif status == 0xFC: # Stop
            if self.running:
                threading.Thread(target=self.pause, daemon=True).start()
        elif status == 0xF8: # Clock pulse (24 PPQN)
            now = time.perf_counter()
            if self._ext_clock_ts > 0:
                # Actualizar BPM a partir del intervalo entre pulsos
                interval = now - self._ext_clock_ts
                if 0.001 < interval < 0.5:   # sanity check: 30–1000 BPM
                    new_bpm = 60.0 / (interval * 24)
                    # Suavizado exponencial para evitar jitter
                    self.bpm = round(self.bpm * 0.85 + new_bpm * 0.15, 1)
            self._ext_clock_ts = now
            self._ext_clock_count += 1
            if self._ext_clock_count >= 6:   # 6 pulsos = 1 tick de 1/16
                self._ext_clock_count = 0
                self._ext_clock_event.set()

    def _open_ext_inputs(self):
        """Abre todos los puertos MIDI de entrada excepto los ya usados."""
        print("[EXT IN] iniciando...", flush=True)
        skip_keywords = [
            getattr(config, 'LAUNCHPAD_PORT',  'Launchpad'),
            getattr(config, 'NK_IN_PORT',      'nanoKONTROL SLIDER/KNOB'),
            getattr(config, 'MIDI_KB_PORT',    ''),
        ] + list(getattr(config, 'EXT_MIDI_IGNORE', []))
        skip_keywords = [s for s in skip_keywords if s]  # quitar vacíos

        probe = rtmidi.MidiIn()
        all_ports = probe.get_ports()
        print(f"[EXT IN] puertos disponibles: {all_ports}")
        opened = []
        for i, p in enumerate(all_ports):
            if any(s in p for s in skip_keywords):
                print(f"[EXT IN] skip: {p}")
                continue
            inp = rtmidi.MidiIn()
            inp.ignore_types(sysex=True, timing=True, active_sense=True)
            try:
                inp.open_port(i)
                inp.set_callback(self._on_ext_midi)
                opened.append(inp)
                print(f"[EXT IN] abierto: {p}")
            except Exception as e:
                print(f"[EXT IN] error {p}: {e}")
        return opened

    def _on_ext_midi(self, event, _):
        """Captura MIDI externo de los sintes (MIDI Out → MIDI In del interface).
        - Notas ignoradas → evita bucles MIDI (Thru activo en hardware)
        - CC → graba en step-lock del track cuyo canal coincide (si REC+running)
        - CC 0 (Bank Select) → ignorado (Bank Select se configura manualmente)
        - PC (0xC0) → ignorado (Program Change solo se asigna por trigger/knob)
        """
        try:
            msg = event[0]
            if not msg:
                return
            status   = msg[0]
            msg_type = status & 0xF0
            channel  = status & 0x0F

            # PC/Bank Select → ignorado (solo por trigger manual)
            if msg_type in (0xA0, 0xC0):
                return
            # Notas → ignorado (el thru lo hace el hardware; evita bucles MIDI)
            if msg_type in (0x80, 0x90):
                return

            if msg_type == 0xB0 and len(msg) >= 3:
                cc, val = msg[1], msg[2]
                if cc == 0:
                    return  # Bank Select ignorado desde ext MIDI
                # Grabar en step-lock solo si está grabando y corriendo
                # No actualizar lane['val'] — el display es estático (refleja el plock grabado)
                if self.recording and self.running:
                    with self.lock:
                        target = next(
                            (t for t in self.tracks if t.channel == channel),
                            self.tracks[self.active])
                        self._record_cc_to_lane(target, cc, val)
        except Exception:
            pass

    def _flatten_chain(self, target_slot):
        """Concatena todos los slots del chain en un único snapshot y lo guarda."""
        num_tracks = len(self.tracks)
        # Construir snapshot base desde el primer slot del chain
        first_bank, first_slot = self.chain[0]
        first_tracks = self._slot_tracks(self.banks[first_bank][first_slot])
        merged = [dict(first_tracks[i]) for i in range(num_tracks)]
        for i in range(num_tracks):
            merged[i]['pattern']    = []
            merged[i]['tonal_notes'] = []
            merged[i]['step_locks'] = {}

        for (c_bank, c_slot) in self.chain:
            slot_data = self.banks[c_bank].get(c_slot)
            if not slot_data:
                continue
            snap_list = self._slot_tracks(slot_data)
            for i in range(num_tracks):
                snap = snap_list[i]
                offset = len(merged[i]['pattern'])
                merged[i]['pattern'] += list(snap.get('pattern', []))
                merged[i]['tonal_notes'] += list(snap.get('tonal_notes', []))
                for k, v in snap.get('step_locks', {}).items():
                    merged[i]['step_locks'][str(int(k) + offset)] = dict(v)

        # Actualizar steps y pulses en cada track del merged
        for i in range(num_tracks):
            merged[i]['steps']  = len(merged[i]['pattern'])
            merged[i]['pulses'] = sum(merged[i]['pattern'])

        self.banks[self.current_bank][target_slot] = {'bpm': self.bpm, 'tracks': merged}
        self.active_slot = target_slot
        # Cargar inmediatamente en los tracks
        for i, snap in enumerate(merged):
            self.tracks[i].load(snap)
        self.step_pg = 0
        total_steps = merged[0]['steps'] if merged else 0
        self.last_msg = f"Flatten → {total_steps} steps @ B{self.current_bank+1}.{target_slot+1}"
        self.chain = []
        self.chain_pos = 0
        self._save_banks()
        self._render()

    def _save_pattern(self, slot):
        self.banks[self.current_bank][slot] = {
            'bpm': self.bpm,
            'tracks': [t.snapshot() for t in self.tracks],
        }
        self.active_slot = slot
        row, col = divmod(slot, 8)
        self.last_msg = f"Saved B{self.current_bank+1} r{row+1}c{col+1}"
        self._save_banks()

    def _load_pattern(self, slot):
        bank = self.banks[self.current_bank]
        if slot in bank:
            row, col = divmod(slot, 8)
            if self.running:
                # Esperar al final del ciclo para cargar sin glitch
                self.pending_load = slot
                self.last_msg = f"Loading B{self.current_bank+1} r{row+1}c{col+1}..."
                self._start_blink(self.current_bank, slot)
            else:
                # Parado: cargar inmediatamente
                self._apply_slot(bank[slot])
                self.active_slot = slot
                self.last_msg = f"Loaded B{self.current_bank+1} r{row+1}c{col+1}"
                # Si estamos en bank_view, asegurar page view para no mezclar vistas
                if self.bank_view:
                    if self._detail_timer:
                        self._detail_timer.cancel()
                        self._detail_timer = None
                    self.view_mode = 'page'
                self._render()
        else:
            self.last_msg = f"B{self.current_bank+1} slot empty"

    def _start_blink(self, bank, slot, duration=0.7):
        until = time.time() + duration
        self._blink_info = (bank, slot, until)
        def _run():
            while time.time() < until:
                self._lp_render()
                time.sleep(0.08)
            self._blink_info = None
            self._lp_render()
        threading.Thread(target=_run, daemon=True).start()

    # ── Helpers de slot (compatibilidad viejo formato lista / nuevo formato dict) ──────

    @staticmethod
    def _slot_tracks(slot_data):
        """Devuelve la lista de track-snapshots de un slot."""
        return slot_data['tracks'] if isinstance(slot_data, dict) else slot_data

    @staticmethod
    def _slot_bpm(slot_data):
        """Devuelve el BPM guardado en un slot, o None si no tiene."""
        return slot_data.get('bpm') if isinstance(slot_data, dict) else None

    def _restore_midi_state(self):
        """Envía PC + todos los CC de las lanes al hardware para restaurar el estado sonoro.
        Solo envía PC si el programa fue configurado explícitamente (program > 0 o bank > 0).
        Tracks con program=0/bank=0 (default, nunca asignado) no envían PC para no
        interferir con la selección manual del sinte."""
        sent = []
        for t in self.tracks:
            if t.send_pc and (t.program != 0 or t.bank_msb != 0):
                self._send_program(t)
                sent.append(f"T{self.tracks.index(t)+1}ch{t.channel+1}→pc{t.program}")
            # CCs por track: enviar siempre las lanes configuradas (num>=0),
            # no solo del último track con PC.
            ch = t.channel
            for lane in t.cc_lanes:
                if lane['num'] >= 0:
                    try:
                        self._out(t).send_message([0xB0 | ch, lane['num'], lane['val']])
                    except Exception:
                        pass
        if sent:
            self.last_msg = "PC→ " + "  ".join(sent)

    def _apply_slot(self, slot_data):
        """Carga los tracks, restaura BPM y envía PC + CC al hardware."""
        snaps = self._slot_tracks(slot_data)
        for i, snap in enumerate(snaps):
            self.tracks[i].load(snap)
        bpm = self._slot_bpm(slot_data)
        if bpm is not None:
            self.bpm = float(bpm)
        self._restore_midi_state()

    def _save_banks(self):
        # Auto-save de banks.json — PROTEGIDO por:
        # 1) Tests neutralizan _save_banks (no tocan disco)
        # 2) _file_load/merge (cargar saves no wipea bancos existentes)
        # 3) Safety net (backup si se reduce drásticamente)
        # Si el archivo estaba corrupto al arrancar (_banks_loaded_ok=False) no
        # machacamos: preferimos no auto-guardar a destruir datos.
        if not getattr(self, '_banks_loaded_ok', True):
            self.last_msg = "auto-save OFF (banks.json estaba corrupto al arrancar)"
            return
        try:
            data = [{str(slot): snaps for slot, snaps in bank.items()}
                    for bank in self.banks]
            new_total = sum(len(b) for b in data)
            # ── SAFETY NET anti data-loss ────────────────────────────────────
            # Si vamos a escribir DRÁSTICAMENTE menos slots que lo que hay en
            # disco, hacemos backup numerado antes de sobreescribir. Así una
            # futura regresión no puede destruir patrones sin dejar rastro.
            try:
                if os.path.exists(BANKS_FILE):
                    with open(BANKS_FILE) as _f:
                        _old = json.load(_f)
                    old_total = sum(len(b) for b in _old)
                    if old_total >= 10 and new_total < old_total * 0.5:
                        import shutil, traceback
                        bak = f"{BANKS_FILE}.auto_{int(time.time())}"
                        shutil.copy(BANKS_FILE, bak)
                        # Guardar stack trace para forensics
                        try:
                            with open(BANKS_FILE + '.trace', 'a') as _tf:
                                _tf.write(f"\n\n=== {time.ctime()} — {old_total}→{new_total} slots — backup: {bak} ===\n")
                                traceback.print_stack(file=_tf)
                        except Exception:
                            pass
                        self.last_msg = (
                            f"⚠ banks: {old_total}→{new_total} slots. "
                            f"backup: {os.path.basename(bak)}")
            except Exception:
                pass
            # Escritura atómica: escribir a tmp y renombrar
            tmp = BANKS_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(data, f)
            os.replace(tmp, BANKS_FILE)
        except Exception as e:
            self.last_msg = f"Save error: {e}"

    def _load_banks(self):
        if not os.path.exists(BANKS_FILE):
            return
        try:
            with open(BANKS_FILE) as f:
                data = json.load(f)
            for b_idx, bank_data in enumerate(data[:8]):
                for slot_str, snaps in bank_data.items():
                    self.banks[b_idx][int(slot_str)] = snaps
            self._banks_loaded_ok = True
        except Exception as e:
            # banks.json corrupto: hacer backup y dejar los bancos vacíos.
            # _banks_loaded_ok=False evita auto-save posterior que machacaría el archivo.
            try:
                import shutil
                shutil.copy(BANKS_FILE, BANKS_FILE + '.corrupt')
            except Exception:
                pass
            self._banks_loaded_ok = False
            self.last_msg = f"banks.json corrupto → .corrupt ({e})"

    # ── Guardar / cargar sesiones a archivos ─────────────────────────────────
    def _file_list(self):
        if not os.path.isdir(SAVES_DIR):
            return []
        files = []
        for f in sorted(os.listdir(SAVES_DIR), reverse=True):
            if f.endswith('.json'):
                path = os.path.join(SAVES_DIR, f)
                try:
                    st = os.stat(path)
                    files.append({'name': f, 'size': st.st_size,
                                  'mtime': st.st_mtime})
                except OSError:
                    pass
        return files

    def _load_scripts_library(self):
        """Cargar librería de scripts desde scripts.json."""
        scripts_file = os.path.join(os.path.dirname(__file__), 'scripts.json')
        try:
            with open(scripts_file, 'r') as f:
                data = json.load(f)
                # Combinar presets y custom en un dict por ID
                lib = {}
                for script in data.get('presets', []):
                    lib[script['id']] = script
                for script in data.get('custom', []):
                    lib[script['id']] = script
                return lib
        except Exception as e:
            print(f"[Script Library] Error loading: {e}", file=sys.stderr)
            return {}

    def _save_scripts_library(self):
        """Guardar librería de scripts en scripts.json."""
        scripts_file = os.path.join(os.path.dirname(__file__), 'scripts.json')
        try:
            with open(scripts_file, 'r') as f:
                data = json.load(f)
        except:
            data = {'version': 1, 'presets': [], 'custom': []}

        # Actualizar custom scripts
        data['custom'] = [s for s in self.scripts_lib.values()
                         if s['id'] not in [p['id'] for p in data.get('presets', [])]]

        with open(scripts_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _file_save(self, name=None):
        os.makedirs(SAVES_DIR, exist_ok=True)
        if not name:
            name = time.strftime('%Y%m%d_%H%M%S')
        filename = name if name.endswith('.json') else f"{name}.json"
        filepath = os.path.join(SAVES_DIR, filename)
        data = {
            'bpm':          self.bpm,
            'current_bank': self.current_bank,
            'active_slot':  self.active_slot,
            'banks': [{str(slot): snaps for slot, snaps in bank.items()}
                      for bank in self.banks],
            'tracks': [t.snapshot() for t in self.tracks],
            'chain':        [[b, sl] for b, sl in self.chain],
            'ts':           time.time(),
        }
        with open(filepath, 'w') as f:
            json.dump(data, f)
        self.last_msg = f"SAVED → {name}"
        self._render()

    def _file_load(self, filename):
        filepath = os.path.join(SAVES_DIR, filename)
        if not os.path.exists(filepath):
            self.last_msg = f"NOT FOUND: {filename}"
            return
        with open(filepath) as f:
            data = json.load(f)
        # Silenciar todo antes de cargar
        self._note_gen += 1
        self._silence_all_tracks()
        # Restaurar bancos — MERGE en vez de replace. Cargar un save pequeño NO
        # debe borrar los bancos actuales; solo añade/sobreescribe los slots que
        # trae el archivo. Esto evita el data-loss recurrente al cargar saves
        # parciales o antiguos con menos datos.
        loaded_banks = data.get('banks', [])
        has_bank_data = any(bool(b) for b in loaded_banks)
        if has_bank_data:
            for b_idx, bank_data in enumerate(loaded_banks[:8]):
                for slot_str, snaps in bank_data.items():
                    self.banks[b_idx][int(slot_str)] = snaps
        # Restaurar estado global
        if 'bpm' in data:
            self.bpm = float(data['bpm'])
        if 'current_bank' in data:
            self.current_bank = data['current_bank']
        if 'active_slot' in data:
            self.active_slot = data['active_slot']
        # Restaurar tracks si están guardados
        if 'tracks' in data:
            for i, snap in enumerate(data['tracks'][:8]):
                self.tracks[i].load(snap)
        if has_bank_data:
            self._save_banks()
        # Restaurar chain si estaba guardada
        if 'chain' in data:
            self.chain = [tuple(e) for e in data['chain']]
            self.chain_pos = min(self.chain_pos, max(0, len(self.chain) - 1))
        self._restore_midi_state()
        self.last_msg = f"LOADED ← {filename.replace('.json','')}"
        self._render()

    def _file_dialog_save(self):
        """Abre diálogo nativo de macOS para guardar el banco activo."""
        os.makedirs(SAVES_DIR, exist_ok=True)
        try:
            with self.lock:
                bk = self.current_bank
                default_name = f"bank_{bk+1}.json"
            result = subprocess.run([
                'osascript', '-e',
                'tell application "System Events" to activate\n'
                'set fp to POSIX path of (choose file name with prompt '
                f'"Save Bank {bk+1}" default name "{default_name}" '
                f'default location (POSIX file "{SAVES_DIR}"))\n'
                'return fp'
            ], capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                with self.lock:
                    self.last_msg = "Save cancelled"
                    self._render()
                return
            filepath = result.stdout.strip()
            if not filepath:
                return
            if not filepath.endswith('.json'):
                filepath += '.json'
            with self.lock:
                data = {
                    'type':         'bank',
                    'bpm':          self.bpm,
                    'bank_index':   bk,
                    'active_slot':  self.active_slot,
                    'bank': {str(slot): snaps for slot, snaps in self.banks[bk].items()},
                    'tracks': [t.snapshot() for t in self.tracks],
                    'ts':           time.time(),
                }
            with open(filepath, 'w') as f:
                json.dump(data, f)
            with self.lock:
                self.last_msg = f"SAVED [BK{bk+1}] → {os.path.basename(filepath).replace('.json','')}"
                self._render()
        except Exception as e:
            with self.lock:
                self.last_msg = f"Save error: {e}"
                self._render()

    def _file_dialog_save_pattern(self):
        """Abre diálogo nativo de macOS para guardar patrón de la pista activa."""
        os.makedirs(SAVES_DIR, exist_ok=True)
        try:
            with self.lock:
                n = self.active + 1
                default_name = f"pattern_T{n}.json"
            result = subprocess.run([
                'osascript', '-e',
                'tell application "System Events" to activate\n'
                'set fp to POSIX path of (choose file name with prompt '
                f'"Save Pattern T{n}" default name "{default_name}" '
                f'default location (POSIX file "{SAVES_DIR}"))\n'
                'return fp'
            ], capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                with self.lock:
                    self.last_msg = "Save cancelled"
                    self._render()
                return
            filepath = result.stdout.strip()
            if not filepath:
                return
            if not filepath.endswith('.json'):
                filepath += '.json'
            with self.lock:
                t = self.tracks[self.active]
                data = {
                    'type':  'pattern',
                    'track': t.snapshot(),
                    'ts':    time.time(),
                }
            with open(filepath, 'w') as f:
                json.dump(data, f)
            with self.lock:
                self.last_msg = f"SAVED [T{self.active+1}] → {os.path.basename(filepath).replace('.json','')}"
                self._render()
        except Exception as e:
            with self.lock:
                self.last_msg = f"Save error: {e}"
                self._render()

    def _file_dialog_load(self):
        """Abre diálogo nativo de macOS para cargar un banco en el banco activo."""
        os.makedirs(SAVES_DIR, exist_ok=True)
        try:
            with self.lock:
                bk = self.current_bank
            result = subprocess.run([
                'osascript', '-e',
                'tell application "System Events" to activate\n'
                'set fp to POSIX path of (choose file with prompt '
                f'"Load Bank → BK{bk+1}" '
                f'default location (POSIX file "{SAVES_DIR}"))\n'
                'return fp'
            ], capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                with self.lock:
                    self.last_msg = "Load cancelled"
                    self._render()
                return
            filepath = result.stdout.strip()
            if not filepath or not os.path.exists(filepath):
                return
            with open(filepath) as f:
                data = json.load(f)
            label = os.path.basename(filepath).replace('.json', '')
            with self.lock:
                self._note_gen += 1
                self._silence_all_tracks()
                # Cargar banco en el slot activo — MERGE (no wipe) para no
                # perder slots existentes si el archivo trae menos datos.
                bank_data = data.get('bank', {})
                for slot_str, snaps in bank_data.items():
                    self.banks[bk][int(slot_str)] = snaps
                if 'bpm' in data:
                    self.bpm = float(data['bpm'])
                if 'active_slot' in data:
                    self.active_slot = data['active_slot']
                if 'tracks' in data:
                    for i, snap in enumerate(data['tracks'][:8]):
                        self.tracks[i].load(snap)
                self._save_banks()
                self.last_msg = f"LOADED [BK{bk+1}] ← {label}"
                self._render()
        except Exception as e:
            with self.lock:
                self.last_msg = f"Load error: {e}"
                self._render()

    def _file_dialog_load_pattern(self):
        """Abre diálogo nativo de macOS para cargar patrón en la pista activa."""
        os.makedirs(SAVES_DIR, exist_ok=True)
        try:
            with self.lock:
                n = self.active + 1
            result = subprocess.run([
                'osascript', '-e',
                'tell application "System Events" to activate\n'
                'set fp to POSIX path of (choose file with prompt '
                f'"Load Pattern → T{n}" '
                f'default location (POSIX file "{SAVES_DIR}"))\n'
                'return fp'
            ], capture_output=True, text=True, timeout=120)
            if result.returncode != 0:
                with self.lock:
                    self.last_msg = "Load cancelled"
                    self._render()
                return
            filepath = result.stdout.strip()
            if not filepath or not os.path.exists(filepath):
                return
            with open(filepath) as f:
                data = json.load(f)
            label = os.path.basename(filepath).replace('.json', '')
            snap = data.get('track', {})
            if not snap:
                with self.lock:
                    self.last_msg = "Load error: no track data"
                    self._render()
                return
            with self.lock:
                self._note_gen += 1
                self._silence_track(self.active)
                self.tracks[self.active].load(snap)
                self.last_msg = f"LOADED [T{self.active+1}] ← {label}"
                self._render()
        except Exception as e:
            with self.lock:
                self.last_msg = f"Load error: {e}"
                self._render()

    def _on_launchpad(self, event, _):
        try:
            msg = event[0]
            if len(msg) < 3:
                return
            status, data1, data2 = msg[0], msg[1], msg[2]
            with self.lock:
                t = self.tracks[self.active]

                # ── Fila superior (CC) ─────────────────────────────────────
                if status == 0xB0:
                    if data1 == LP_SHIFT_CC:
                        self.shift = data2 > 0
                        return
                    if data1 == LP_COPY_CC:
                        self.copy_mode = data2 > 0
                        if data2 > 0:
                            self.copy_first = True   # al pulsar de nuevo: siguiente pad copia
                        return
                    if data1 == LP_DELETE_CC:
                        self.delete_mode = data2 > 0
                        return
                    if data2 == 0:
                        return
                    if data1 == LP_TRACK_DN:
                        if self.shift:
                            self._push_undo(force=True)
                            step = 1  # semitono fijo, independiente de escala
                            t.root = max(0, t.root - step)
                            if t.tonal_notes:
                                t.tonal_notes = [max(0, min(127, n - step)) for n in t.tonal_notes]
                            self.last_msg = f"T{self.active+1} ROOT ↓ → {note_name(t.root)}"
                            _push_display(f"T{self.active+1} ROOT ↓", note_name(t.root),
                                          track=self.active, bpm=int(self.bpm), playing=self.running,
                                          extra={'view_mode':'detail','label':f"T{self.active+1} ROOT",
                                                 'value': note_name(t.root)})
                            self._render()
                        else:
                            self.active = (self.active + 1) % 8
                            self.step_pg = min(self.step_pg, (self.tracks[self.active].steps - 1) // 8)
                            self.last_msg = f"TRK → {self.active + 1}"
                            self._nk_refresh_page_leds()
                    elif data1 == LP_TRACK_UP:
                        if self.shift:
                            self._push_undo(force=True)
                            step = 1  # semitono fijo, independiente de escala
                            t.root = min(108, t.root + step)
                            if t.tonal_notes:
                                t.tonal_notes = [max(0, min(127, n + step)) for n in t.tonal_notes]
                            self.last_msg = f"T{self.active+1} ROOT ↑ → {note_name(t.root)}"
                            _push_display(f"T{self.active+1} ROOT ↑", note_name(t.root),
                                          track=self.active, bpm=int(self.bpm), playing=self.running,
                                          extra={'view_mode':'detail','label':f"T{self.active+1} ROOT",
                                                 'value': note_name(t.root)})
                            self._render()
                        else:
                            self.active = (self.active - 1) % 8
                            self.step_pg = min(self.step_pg, (self.tracks[self.active].steps - 1) // 8)
                            self.last_msg = f"TRK → {self.active + 1}"
                            self._nk_refresh_page_leds()
                    elif data1 == LP_STEP_DN:
                        if self.shift and self.bank_view:
                            self.current_bank = max(0, self.current_bank - 1)
                            self.last_msg = f"Bank → {self.current_bank+1}"
                            self._render()
                        else:
                            self.step_pg = max(0, self.step_pg - 1)
                            total_pgs = (t.steps + 7) // 8
                            counter = f"{self.step_pg + 1}/{total_pgs}"
                            self.last_msg = f"PG → {counter}"
                            _push_display("PAG", counter, bar=0.0,
                                          track=self.active, bpm=int(self.bpm), playing=self.running,
                                          extra={'view_mode': self.view_mode, 'step_counter': counter,
                                                 'step_pg': self.step_pg, 'step_pg_total': total_pgs})
                    elif data1 == LP_STEP_UP:
                        if self.shift and self.bank_view:
                            self.current_bank = min(7, self.current_bank + 1)
                            self.last_msg = f"Bank → {self.current_bank+1}"
                            self._render()
                        else:
                            max_pg = (t.steps - 1) // 8
                            self.step_pg = min(self.step_pg + 1, max_pg)
                            total_pgs = (t.steps + 7) // 8
                            counter = f"{self.step_pg + 1}/{total_pgs}"
                            self.last_msg = f"PG → {counter}"
                            _push_display("PAG", counter, bar=0.0,
                                          track=self.active, bpm=int(self.bpm), playing=self.running,
                                          extra={'view_mode': self.view_mode, 'step_counter': counter,
                                                 'step_pg': self.step_pg, 'step_pg_total': total_pgs})
                    elif data1 == LP_BANK_VIEW:
                        if self.shift:
                            # SHIFT+BANK_VIEW → toggle CC draw mode
                            t = self.tracks[self.active]
                            self.cc_draw = not self.cc_draw
                            if self.cc_draw:
                                self.bank_view = False
                                self.step_focus = None
                                self.step_focus_cycle = 0
                            lane = t.active_cc_lane
                            cc_n = t.cc_lanes[lane]['num']
                            cc_lbl = f"CC{cc_n}" if cc_n >= 0 else "—"
                            self.last_msg = f"CC DRAW {'ON' if self.cc_draw else 'OFF'} L{lane+1} {cc_lbl}"
                            self._render()
                        else:
                            self.bank_view = not self.bank_view
                            if self.bank_view:
                                self.cc_draw = False
                                self.step_focus = None
                                self.step_focus_cycle = 0
                                self.held_step = None        # liberar pad sostenido para no mezclar vistas
                                self.held_knob_used = False
                                # Forzar page view: cancelar timer de detail para evitar mezcla
                                if self._detail_timer:
                                    self._detail_timer.cancel()
                                    self._detail_timer = None
                                self.view_mode = 'page'
                            self.last_msg = "PATTERNS  (pad=load  Shift+pad=save)" if self.bank_view else "STEPS"
                            self._render()
                    return

                # Note-off: liberar pad sostenido (0x80 o 0x90 vel=0)
                if (status == 0x80) or (status == 0x90 and data2 == 0):
                    # En bank_view los pads son slots, no steps: limpiar held_step y salir
                    if self.bank_view:
                        self.held_step = None
                        self.held_knob_used = False
                        return
                    if self.held_step is not None:
                        row = data1 // 16
                        col = data1 % 16
                        if col < 8 and row <= 7:
                            ti   = row
                            step = self.step_pg * 8 + col
                            if self.held_step == (ti, step):
                                tr = self.tracks[ti]
                                has_locks = step in tr.step_locks
                                # Toggle solo si no hay p-locks y no se usó knob
                                if not self.held_knob_used and not has_locks:
                                    if step < len(tr.pattern):
                                        self._push_undo(force=True)
                                        tr.pattern[step] = not tr.pattern[step]
                                        tr.pulses = sum(tr.pattern)
                                        tr.tonal_notes = []
                                        # Si hay una nota de teclado armada, fijarla a este step
                                        # como p-lock para que no cambie con futuros knobs/teclas
                                        if tr.pattern[step] and tr.last_kbd_note is not None:
                                            tr.step_locks.setdefault(step, {})['note'] = tr.last_kbd_note
                                        state = "●" if tr.pattern[step] else "·"
                                        self.last_msg = f"T{ti+1} step {step+1} → {state}"
                                self.held_step = None
                                self.held_knob_used = False
                                self.held_kbd_count = 0
                                # Arrancar timer de retorno a page view al soltar
                                if self._detail_timer: self._detail_timer.cancel()
                                self._detail_timer = threading.Timer(2.5, self._to_page_view)
                                self._detail_timer.start()
                    return

                if status != 0x90:
                    return

                # ── Columna derecha ────────────────────────────────────────
                if data1 in LP_RIGHT:
                    ti = LP_RIGHT.index(data1)
                    # En CC draw, la columna derecha selecciona lane
                    if self.cc_draw:
                        t = self.tracks[self.active]
                        t.active_cc_lane = ti
                        lane = t.cc_lanes[ti]
                        cc_n = lane['num']
                        p_name = lane.get('param')
                        if p_name:
                            cc_lbl = p_name
                        elif cc_n >= 0:
                            cc_lbl = f"CC{cc_n}"
                        else:
                            cc_lbl = "N/A"
                        self.last_msg = f"L{ti+1} → {cc_lbl}"
                        self.view_mode = 'detail'
                        if self._detail_timer: self._detail_timer.cancel()
                        self._detail_timer = threading.Timer(2.5, self._to_page_view)
                        self._detail_timer.start()
                        _push_display(f"L{ti+1}", cc_lbl,
                                      track=self.active, bpm=int(self.bpm), playing=self.running,
                                      extra={'view_mode': 'detail',
                                             'label': f"L{ti+1}",
                                             'value': cc_lbl})
                        self._update_leds()
                        return
                    if self.copy_mode and self.shift:
                        self.clipboard_track = self.tracks[ti].snapshot()
                        self.last_msg = f"T{ti+1} copiada  (COPY+pad → pegar)"
                    elif self.copy_mode and not self.shift and ti == self.active:
                        # COPY + pad derecho de la pista activa = duplicar patrón
                        self._push_undo(force=True)
                        _duplicate_pattern(self.tracks[ti])
                        self.last_msg = f"T{ti+1} DUP → {self.tracks[ti].steps} steps"
                    elif self.copy_mode and self.clipboard_track is not None:
                        self._push_undo(force=True)
                        dst = self.tracks[ti]
                        ch, prog, bank, spc, pt = dst.channel, dst.program, dst.bank_msb, dst.send_pc, dst.port
                        dst.load(self.clipboard_track)
                        dst.channel  = ch
                        dst.program  = prog
                        dst.bank_msb = bank
                        dst.send_pc  = spc
                        dst.port     = pt
                        self.last_msg = f"T{ti+1} ← pegada"
                    elif self.delete_mode and self.shift:
                        self._push_undo(force=True)
                        self.tracks[ti].reset()
                        self.last_msg = f"T{ti+1} RESET"
                        self._nk_refresh_page_leds()
                    elif self.delete_mode:
                        tr = self.tracks[ti]
                        self._push_undo(force=True)
                        tr.pattern    = [False] * tr.steps
                        tr.pulses     = 0
                        tr.step_locks = {}
                        tr.tonal_notes = []
                        self.last_msg = f"T{ti+1} cleared"
                    elif self.shift:
                        self.tracks[ti].muted = not self.tracks[ti].muted
                        self.last_msg = f"T{ti+1} {'MUTE' if self.tracks[ti].muted else 'ACTIVE'}"
                    else:
                        self.active = ti
                        self.last_msg = f"TRK → {ti+1}"
                        self._nk_refresh_page_leds()
                    return

                # ── Grid ───────────────────────────────────────────────────
                row = data1 // 16
                col = data1 % 16
                if col >= 8 or row > 7:
                    return

                # ── CC DRAW MODE ──────────────────────────────────────────
                if self.cc_draw:
                    t = self.tracks[self.active]
                    lane = t.active_cc_lane
                    lane_cfg = t.cc_lanes[lane]
                    cc_num = lane_cfg['num']
                    param  = lane_cfg.get('param')
                    step = self.step_pg * 8 + col
                    if step >= len(t.pattern):
                        return
                    cc_val = int((7 - row) * (127 / 7))  # 0-127

                    if param:
                        # Param lane: write to auto_lanes at this step's phase
                        phase = (step + 0.5) / max(len(t.pattern), 1)
                        real_val = self._auto_denorm(param, cc_val)
                        # Check if same row → clear this phase region
                        points = t.auto_lanes.get(param, [])
                        # Find if there's already a point near this phase (within 1 step)
                        tol = 1.0 / max(len(t.pattern), 1)
                        near = [p for p in points if abs(p[0] - phase) < tol]
                        near_row = 7 - round(self._auto_norm(param, near[0][1]) * 7 / 127) if near else None
                        if near and near_row == row:
                            # Toggle: remove nearby points
                            t.auto_lanes[param] = [p for p in points if abs(p[0] - phase) >= tol]
                            if not t.auto_lanes[param]:
                                del t.auto_lanes[param]
                                # Remove param from cc_lanes
                                lane_cfg.pop('param', None)
                            if hasattr(t, '_auto_phases'): t._auto_phases.pop(param, None)
                            if hasattr(t, '_auto_last'): t._auto_last.pop(param, None)
                            self.last_msg = f"T{self.active+1} s{step+1} {param} cleared"
                        else:
                            self._push_undo(force=False)
                            if param not in t.auto_lanes:
                                t.auto_lanes[param] = []
                            # Remove existing points near this phase
                            t.auto_lanes[param] = [p for p in t.auto_lanes[param] if abs(p[0] - phase) >= tol]
                            from bisect import insort
                            insort(t.auto_lanes[param], (phase, real_val))
                            if hasattr(t, '_auto_phases'): t._auto_phases.pop(param, None)
                            self._stoch_param_set(t, param, real_val)
                            self.last_msg = f"T{self.active+1} s{step+1} {param} → {real_val}"
                        label = f"L{lane+1} {param}"
                        lbl_display = param
                    else:
                        # CC lane: original behavior
                        cc_vals = t.step_locks.get(step, {}).get('cc_vals', {})
                        current = cc_vals.get(lane, None)
                        current_row = 7 - round((current or 0) * 7 / 127)
                        if current is not None and current_row == row:
                            cc_vals.pop(lane, None)
                            if not cc_vals and 'cc_vals' in t.step_locks.get(step, {}):
                                del t.step_locks[step]['cc_vals']
                            if step in t.step_locks and not t.step_locks[step]:
                                del t.step_locks[step]
                            self.last_msg = f"T{self.active+1} s{step+1} CC{cc_num} cleared"
                        else:
                            self._push_undo(force=False)
                            lk = t.step_locks.setdefault(step, {})
                            lk.setdefault('cc_vals', {})[lane] = cc_val
                            self.last_msg = f"T{self.active+1} s{step+1} CC{cc_num} → {cc_val}"
                            if cc_num >= 0:
                                self._out(t).send_message([0xB0 | t.channel, cc_num, cc_val])
                        label = f"L{lane+1} CC{cc_num}"
                        lbl_display = f"CC{cc_num}"
                    if not self.bank_view:
                        self.view_mode = 'detail'
                        if self._detail_timer: self._detail_timer.cancel()
                        self._detail_timer = threading.Timer(2.5, self._to_page_view)
                        self._detail_timer.start()
                    _push_display(lbl_display, str(cc_val), bar=cc_val/127,
                                  track=self.active, bpm=int(self.bpm), playing=self.running,
                                  extra={'view_mode': 'detail', 'label': label, 'value': str(cc_val)})
                    return

                ti   = row
                step = self.step_pg * 8 + col
                tr   = self.tracks[ti]

                # Autoselect: al tocar cualquier pad la pista se selecciona
                if not self.bank_view and ti != self.active:
                    self.active = ti
                    self._nk_refresh_page_leds()
                    self._update_leds()

                # ── SHIFT+COPY+pad en bank view = chain toggle ─────────────
                if self.shift and self.copy_mode and self.bank_view:
                    slot = row * 8 + col
                    if slot in self.banks[self.current_bank]:
                        entry = (self.current_bank, slot)
                        if entry in self.chain:
                            self.chain.remove(entry)
                            self.last_msg = f"Chain: removed B{self.current_bank+1}.{slot+1}"
                        else:
                            self.chain.append(entry)
                            self.last_msg = f"Chain: +B{self.current_bank+1}.{slot+1} [{len(self.chain)}]"
                        if self.chain_pos >= len(self.chain):
                            self.chain_pos = 0
                    else:
                        self.last_msg = f"B{self.current_bank+1} slot vacío"
                    self._render()
                    return

                # ── COPY / PASTE ───────────────────────────────────────────
                if self.copy_mode:
                    # COPY + pad en bank view = fijar patrón B para crossfade
                    if self.bank_view:
                        slot      = row * 8 + col
                        candidate = f"B{self.current_bank+1}.{slot+1}"
                        if self.xfade_snap and self.xfade_label == candidate:
                            # mismo pad → cancelar morph
                            self.xfade_snap   = None
                            self.xfade_snap_a = None
                            self.xfade_amt    = 0.0
                            self.xfade_label  = ''
                            self.last_msg = "MORPH OFF"
                            _push_display("MORPH", "OFF",
                                          track=self.active, bpm=int(self.bpm), playing=self.running,
                                          extra={'view_mode': 'detail', 'label': 'MORPH', 'value': 'OFF'})
                        elif slot in self.banks[self.current_bank]:
                            self.xfade_snap_a = [tr.snapshot() for tr in self.tracks]
                            self.xfade_snap   = list(self._slot_tracks(self.banks[self.current_bank][slot]))
                            self.xfade_amt    = 0.0
                            self.xfade_label  = candidate
                            lbl = self.xfade_label
                            self.last_msg = f"MORPH B → {lbl}"
                            _push_display("MORPH B", lbl,
                                          track=self.active, bpm=int(self.bpm), playing=self.running,
                                          extra={'view_mode': 'detail', 'label': 'MORPH B', 'value': lbl})
                        else:
                            self.last_msg = f"B{self.current_bank+1} slot vacío"
                        self._render()
                        return
                    if self.copy_first:
                        self.clipboard_step = {
                            'active': tr.pattern[step] if step < len(tr.pattern) else False,
                            'locks':  dict(tr.step_locks.get(step, {})),
                        }
                        self.copy_first = False
                        self.last_msg = f"T{ti+1} step {step+1} copied{_lock_summary(self.clipboard_step['locks'])}"
                    elif self.clipboard_step is not None and step < len(tr.pattern):
                        self._push_undo(force=True)
                        tr.pattern[step] = self.clipboard_step['active']
                        if self.clipboard_step['locks']:
                            tr.step_locks[step] = dict(self.clipboard_step['locks'])
                        else:
                            tr.step_locks.pop(step, None)
                        tr.pulses = sum(tr.pattern)
                        tr.tonal_notes = []
                        self.last_msg = f"T{ti+1} step {step+1} pasted"
                    return

                # ── DELETE ─────────────────────────────────────────────────
                if self.delete_mode:
                    if self.bank_view:
                        slot = row * 8 + col
                        if slot in self.banks[self.current_bank]:
                            del self.banks[self.current_bank][slot]
                            # Quitar del chain si estaba
                            entry = (self.current_bank, slot)
                            if entry in self.chain:
                                self.chain.remove(entry)
                                if self.chain_pos >= len(self.chain):
                                    self.chain_pos = 0
                            if self.active_slot == slot:
                                self.active_slot = -1
                            self._save_banks()
                            self.last_msg = f"B{self.current_bank+1}.{slot+1} deleted"
                        else:
                            self.last_msg = f"B{self.current_bank+1} slot empty"
                        self._render()
                        return
                    if step < len(tr.pattern):
                        self._push_undo(force=True)
                        tr.pattern[step] = False
                        tr.pulses = sum(tr.pattern)
                        tr.step_locks.pop(step, None)
                        tr.tonal_notes = []
                        self.last_msg = f"T{ti+1} step {step+1} cleared"
                    return

                # ── Bank view ──────────────────────────────────────────────
                if self.bank_view:
                    slot = row * 8 + col
                    if self.shift and self.chain:
                        # Flatten: concatenar todos los slots del chain en este slot
                        if len(self.chain) > 8:
                            self.last_msg = f"Flatten: máx 8 slots ({len(self.chain)} en chain)"
                            self._render()
                        else:
                            self._flatten_chain(slot)
                    elif self.shift:
                        self._save_pattern(slot)
                    elif slot in self.banks[self.current_bank]:
                        self._push_undo(force=True)
                        self.chain = []
                        self.chain_pos = 0
                        self._load_pattern(slot)
                    else:
                        self.last_msg = f"B{self.current_bank+1} slot empty"
                    return

                # ── Vista normal: edición de steps ─────────────────────────
                if not self.shift:
                    if step < len(tr.pattern):
                        # Doble-click rápido (< 250 ms) sobre un step CON locks → borra locks
                        now_ts = time.time()
                        lp = self._last_pad_press
                        is_double = (lp is not None and lp[0] == ti and lp[1] == step
                                     and (now_ts - lp[2]) < 0.25
                                     and step in tr.step_locks)
                        if is_double:
                            self._push_undo(force=True)
                            tr.step_locks.pop(step, None)
                            self.step_focus       = None
                            self.step_focus_cycle = 0
                            self.held_step        = None
                            self.held_knob_used   = True   # evita toggle off al soltar
                            self._last_pad_press  = None
                            self.last_msg = f"T{ti+1} s{step+1} locks borrados"
                            _push_display("LOCKS", "CLR", track=self.active,
                                          bpm=int(self.bpm), playing=self.running,
                                          extra={'view_mode':'detail',
                                                 'label':f"S{step+1} LOCKS", 'value':'CLR'})
                            return
                        self._last_pad_press = (ti, step, now_ts)
                        if self.step_focus == (ti, step):
                            # mismo pad pulsado de nuevo → ciclar al siguiente valor
                            self.step_focus_cycle += 1
                        else:
                            self.step_focus       = (ti, step)
                            self.step_focus_cycle = 0
                        # held_step siempre apunta al pad actualmente físicamente pulsado
                        self.held_step       = (ti, step)
                        self.held_knob_used  = False
                        self.held_kbd_count  = 0
                        self.view_mode = 'detail'
                        if self._detail_timer: self._detail_timer.cancel()
                        self._detail_timer = None  # timer arranca en note-off
                        _push_display(*self._held_step_display(ti, step, tr))
                else:
                    if ti == self.active:
                        self._gen_pad(col, t)
                    else:
                        if step < len(tr.pattern) and step in tr.step_locks:
                            del tr.step_locks[step]
                            self.last_msg = f"T{ti+1} step {step+1} locks cleared"
                        else:
                            self.last_msg = f"T{ti+1} step {step+1} no locks"

        except Exception as e:
            import traceback
            with open('/tmp/seq_error.log', 'a') as f:
                traceback.print_exc(file=f)
        finally:
            if not self.running:
                self._update_leds()
                self._render()

    def _gen_pad(self, col, t):
        """Funciones generativas: SHIFT + columna 0-7 en la fila de la pista activa"""
        if col == 0:
            t.muted = not t.muted
            self.last_msg = f"T{self.active+1} {'MUTE' if t.muted else 'ACTIVE'}"
        elif col == 1:
            t.pattern, t.pulses, t.steps = gen_euclidean_random()
            t.tonal_notes = []
            self.last_msg = f"T{self.active+1} EUCL → {t.pulses}/{t.steps}"
        elif col == 2:
            t.pattern = gen_mutation(t.pattern, 0.2)
            t.pulses  = sum(t.pattern); t.tonal_notes = []
            self.last_msg = f"T{self.active+1} MUTATE → 20%"
        elif col == 3:
            t.pattern = gen_density(t.steps, t.density)
            t.pulses  = sum(t.pattern); t.tonal_notes = []
            self.last_msg = f"T{self.active+1} DENS → {int(t.density*100)}%"
        elif col == 4:
            t.pattern, t.tonal_notes = gen_tonal(t.steps, t.scale_idx, t.root, t.spread)
            t.pulses    = sum(t.pattern); t.tonal_idx = 0
            sname, _    = SCALES[t.scale_idx]
            self.last_msg = f"T{self.active+1} TONAL → {sname}"
        elif col == 5:
            t.rebuild(); t.note_cursor = 0; t.note_dir = 1; t.tonal_notes = []
            self.last_msg = f"T{self.active+1} RESET → ok"
        elif col == 6:
            if t.steps <= 16:
                t.steps *= 2; t.pulses *= 2; t.rebuild()
                self.last_msg = f"T{self.active+1} DBL → {t.steps}"
        elif col == 7:
            t.pattern = [not p for p in t.pattern]
            t.pulses  = sum(t.pattern)
            self.last_msg = f"T{self.active+1} INV → ok"

    def _on_nanokontrol(self, event, _):
        try:
            msg = event[0]
            if not msg: return
            status = msg[0]

            # SysEx — detectar cambio de escena Y página por canal
            if status == 0xF0:
                if (len(msg) >= 11 and msg[1] == 0x42 and
                        msg[3:9] == [0x00, 0x01, 0x04, 0x00, 0x5F, 0x4F]):
                    scene = msg[9]
                    if 0 <= scene <= 3:
                        with self.lock:
                            self.page = scene
                            self._page_change_ts = time.time()
                            self.view_mode = 'page'
                            if self._detail_timer:
                                self._detail_timer.cancel()
                                self._detail_timer = None
                            if not self.kb_step_focus:
                                self.step_focus = None
                                self.step_focus_cycle = 0
                            summary = self._stoch_summary(self.tracks[self.active])
                            self.last_msg = f"Page → {PAGES[self.page]}{summary}"
                        # El nanoKONTROL necesita ~120 ms para inicializar la nueva
                        # escena antes de aceptar mensajes de LED externos.
                        self._nk_refresh_page_leds(delay=0.12)
                        if not self.running:
                            self._render()
                return
            if len(msg) < 3: return

            cc_type = status & 0xF0
            if cc_type != 0xB0: return

            cc      = msg[1]
            val     = msg[2]
            channel = status & 0x0F
            # La página activa viene del SysEx (escena del controlador).
            # El canal MIDI solo se usa para saber qué fila del cc_map mirar en los knobs,
            # NO para cambiar self.page — eso evita que botones M/S en ch0 salten a pág 1.
            page      = self.page
            knob_page = channel if 0 <= channel <= 3 else self.page

            with self.lock:
                # ── MIDI learn: captura el próximo CC como asignación del knob ──
                if self.mapping_mode and self.learn_target is not None:
                    lg, lp, lk = self.learn_target
                    if lg == 'misc':
                        _misc_keys = ['bpm','undo','redo','copy','paste','sbank','lbank','shift']
                        if lk < len(_misc_keys):
                            self._cc_map.setdefault('misc', {})[_misc_keys[lk]] = cc
                    else:
                        self._cc_map[lg][lp][lk] = cc
                    self.learn_target = None
                    _s = _load_settings()
                    _s['cc_map'] = self._cc_map
                    _save_settings(_s)
                    self.last_msg = f"{lg.upper()} P{lp+1}·{lk+1} → CC{cc}"
                    self._render()
                    return

                t = self.tracks[self.active]
                n = self.active + 1

                # ── Transport (cualquier canal) ──
                if cc == 44:
                    # Fuera del guard val>0 para soportar modo toggle/latch del nanoKONTROL
                    # (press 1 → val=127 → ON, press 2 → val=0 → OFF)
                    new_rec = val > 0
                    if new_rec != self.recording:
                        self.recording = new_rec
                        if not new_rec:
                            self._rec_pending.clear()
                        self._nk_led(44, self.recording)
                        self.last_msg = "● REC" if self.recording else "REC off"
                        self._render()
                    return
                _misc_map = self._cc_map.get('misc', {})
                if val > 0:
                    if cc == _misc_map.get('undo', NK_UNDO_CC):
                        if self.shift:
                            self._clear_auto(self.active)
                            self._render()
                        else:
                            self._undo()
                        return
                    if cc == _misc_map.get('redo', NK_REDO_CC): self._redo(); return
                    if cc == 45: self.resume(); return
                    if cc == 46:
                        if self.shift:
                            self._midi_panic()
                            self.last_msg = "⚡ PANIC — all notes off"
                            self._render()
                        else:
                            self.pause()
                        return
                    if cc == _misc_map.get('lbank', NK_LOAD_CC) and channel == 3:
                        self.last_msg = f"LOAD BANK {self.current_bank+1}..."
                        self._render()
                        threading.Thread(target=self._file_dialog_load, daemon=True).start()
                        return
                    if cc == _misc_map.get('sbank', NK_SAVE_CC) and channel == 3:
                        self.last_msg = f"SAVE BANK {self.current_bank+1}..."
                        self._render()
                        threading.Thread(target=self._file_dialog_save, daemon=True).start()
                        return
                    if cc == NK_LOAD_PAT_CC and channel == 3:
                        self.last_msg = f"LOAD PATTERN → T{self.active+1}..."
                        self._render()
                        threading.Thread(target=self._file_dialog_load_pattern, daemon=True).start()
                        return
                    if cc == NK_SAVE_PAT_CC and channel == 3:
                        self.last_msg = f"SAVE PATTERN T{self.active+1}..."
                        self._render()
                        threading.Thread(target=self._file_dialog_save_pattern, daemon=True).start()
                        return

                # ── BPM global (CC configurable) ──
                if cc == _misc_map.get('bpm', NK_BPM_CC):
                    self.bpm = int(40 + (val/127) * 200)
                    self.last_msg = f"BPM → {self.bpm}"
                    _push_display('BPM', self.bpm, bar=val/127,
                                  track=self.active, bpm=self.bpm, playing=self.running)
                    return

                # ── Guard: ignorar dump de escena del nanoKONTROL (150ms tras cambio) ──
                if time.time() - self._page_change_ts < 0.15:
                    return

                self._dispatch_from_cc_map(cc, val, page, knob_page, t)

        except Exception as e:
            import traceback
            with open('/tmp/seq_error.log', 'a') as f:
                traceback.print_exc(file=f)
        finally:
            if not self.running:
                self._render()

    def _dispatch_from_cc_map(self, cc, val, page, knob_page, t):
        """Despacha un CC entrante via cc_map (knob/fader/btn_s/btn_m).
        Debe llamarse con self.lock adquirido. Devuelve True si fue procesado."""

        # ── Knobs ──
        _all_knob_ccs = self._cc_map.get('knob', [list(range(NK_KNOB_BASE, NK_KNOB_BASE+8))]*4)
        _knob_ccs = _all_knob_ccs[knob_page] if knob_page < len(_all_knob_ccs) else _all_knob_ccs[0]
        if cc in _knob_ccs:
            knob_idx = _knob_ccs.index(cc)

            # Trig lock: pad sostenido + knob
            if self.held_step is not None:
                key = (page, knob_idx)
                if key in STEP_LOCK_PARAMS:
                    ti, step = self.held_step
                    tr = self.tracks[ti]
                    self.held_knob_used = True
                    param, conv = STEP_LOCK_PARAMS[key]
                    locks = tr.step_locks.setdefault(step, {})
                    if val == 0:
                        locks.pop(param, None)
                        if not locks: tr.step_locks.pop(step, None)
                        self.last_msg = f"T{ti+1} p{step+1} {param} → free"
                        disp_val = "FREE"
                    else:
                        locks[param] = conv(val)
                        self.last_msg = f"T{ti+1} p{step+1} {param} → {locks[param]}"
                        disp_val = _fmt_plock_val(param, locks[param])
                    self.view_mode = 'detail'
                    if self._detail_timer: self._detail_timer.cancel()
                    self._detail_timer = threading.Timer(2.5, self._to_page_view)
                    self._detail_timer.start()
                    _push_display(_param_label(param), disp_val, bar=val/127,
                                  track=ti, bpm=int(self.bpm), playing=self.running,
                                  extra={'view_mode': 'detail',
                                         'label': _param_label(param), 'value': disp_val})
                    return True

            # Grabación en tiempo real: guardar valor como pendiente
            if self.recording and self.held_step is None:
                key = (page, knob_idx)
                if key in STEP_LOCK_PARAMS:
                    param, conv = STEP_LOCK_PARAMS[key]
                    if val == 0:
                        self._rec_pending.pop(param, None)
                    else:
                        self._rec_pending[param] = conv(val)

            # Tocar knob → detail view, reiniciar timer de vuelta a page view
            self.view_mode = 'detail'
            if self._detail_timer:
                self._detail_timer.cancel()
            self._detail_timer = threading.Timer(2.5, self._to_page_view)
            self._detail_timer.start()

            if self.shift:
                for tr in self.tracks:
                    self._handle_knob(page, knob_idx, val, tr)
                parts = self.last_msg.split('→')
                if len(parts) == 2:
                    param_label = parts[0].split(None, 1)[-1].strip()
                    param_value = parts[1].strip()
                    self.last_msg = f"ALL {param_label} → {param_value}"
                    _push_display(f"ALL {param_label}", param_value, bar=val/127,
                                  track=self.active, bpm=int(self.bpm), playing=self.running,
                                  extra={'view_mode': 'detail'})
            else:
                self._handle_knob(page, knob_idx, val, t)
                parts = self.last_msg.split('→')
                if len(parts) == 2:
                    param_label = parts[0].split(None, 1)[-1].strip()
                    param_value = parts[1].strip()
                    _push_display(param_label, param_value, bar=val/127,
                                  track=self.active, bpm=int(self.bpm), playing=self.running,
                                  extra={'view_mode': 'detail',
                                         'label': param_label, 'value': param_value})
            return True

        # ── Faders ──
        _fade_ccs = (self._cc_map.get('fade', [list(range(NK_FADER_BASE, NK_FADER_BASE+8))]*4)
                     [knob_page if knob_page < 4 else 0])
        if cc in _fade_ccs:
            self._handle_fader(page, _fade_ccs.index(cc), val, t)
            return True

        # ── Fader 9 CC 9 → crossfade A↔B ──
        if cc == 9:
            self.xfade_amt = val / 127
            self._apply_xfade()
            label = f"{int(self.xfade_amt*100)}%"
            src = self.xfade_label if self.xfade_snap else 'NO B SET'
            self.last_msg = f"MORPH → {label} ({src})"
            if not self.bank_view:
                self.view_mode = 'detail'
                if self._detail_timer: self._detail_timer.cancel()
                self._detail_timer = threading.Timer(2.5, self._to_page_view)
                self._detail_timer.start()
            _push_display("MORPH", label, bar=self.xfade_amt,
                          track=self.active, bpm=int(self.bpm), playing=self.running,
                          extra={'view_mode': 'detail', 'label': 'MORPH', 'value': label})
            self._render()
            return True

        # ── Botones S → random ──
        _btns_ccs = (self._cc_map.get('btn_s', [list(range(NK_BTN_S_BASE, NK_BTN_S_BASE+8))]*4)
                     [knob_page if knob_page < 4 else 0])
        if cc in _btns_ccs and val > 0:
            self._randomize_param(page, _btns_ccs.index(cc), t)
            return True

        # ── Botones M → stochastic on/off ──
        _btnm_ccs = (self._cc_map.get('btn_m', [list(range(NK_BTN_M_BASE, NK_BTN_M_BASE+8))]*4)
                     [knob_page if knob_page < 4 else 0])
        if cc in _btnm_ccs:
            self._handle_btn_m(page, _btnm_ccs.index(cc), t, val > 0)
            return True

        return False

    def _on_keyboard(self, event, _):
        """MIDI thru + recording desde teclado."""
        msg, _ = event
        if not msg:
            return
        status = msg[0] & 0xF0

        # Solo channel voice messages que nos interesan
        # 0x80=NoteOff, 0x90=NoteOn, 0xA0=PolyAT, 0xB0=CC, 0xD0=ChanAT, 0xE0=PitchBend
        if status not in (0x80, 0x90, 0xA0, 0xB0, 0xD0, 0xE0):
            return
        # Filtrar CCs del controlador (knobs/faders del Launchkey) —
        # solo pasar CCs de performance: mod, breath, volume, expression,
        # sustain, pedals, y los que el usuario haya asignado a CC lanes.
        # CCs no reconocidos se intentan despachar via cc_map (controlador en KB_PORT).
        if status == 0xB0:
            if len(msg) < 3:
                return
            cc  = msg[1]
            val = msg[2]
            # MIDI learn: capturar el CC antes de filtrar (para poder aprender knobs del Launchkey)
            if self.mapping_mode and self.learn_target is not None:
                with self.lock:
                    lg, lp, lk = self.learn_target
                    if lg == 'misc':
                        _misc_keys = ['bpm','undo','redo','copy','paste','sbank','lbank','shift']
                        if lk < len(_misc_keys):
                            self._cc_map.setdefault('misc', {})[_misc_keys[lk]] = cc
                    else:
                        self._cc_map[lg][lp][lk] = cc
                    self.learn_target = None
                    _s = _load_settings()
                    _s['cc_map'] = self._cc_map
                    _save_settings(_s)
                    self.last_msg = f"{lg.upper()} P{lp+1}·{lk+1} → CC{cc}"
                    self._render()
                return
            _PERF_CCS = {1,2,4,5,7,10,11,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79}
            t_tmp = self.tracks[self.active]
            lane_ccs = {ln['num'] for ln in t_tmp.cc_lanes if ln['num'] >= 0}
            if cc not in _PERF_CCS and cc not in lane_ccs:
                # CCs no-performance: intentar dispatch via cc_map antes de ignorar
                with self.lock:
                    if self._dispatch_from_cc_map(cc, val, self.page, self.page,
                                                   self.tracks[self.active]):
                        if not self.running:
                            self._render()
                        return
                return

        t  = self.tracks[self.active]
        ch = t.channel

        # MIDI thru: cuantizar notas a la escala efectiva del track
        if status in (0x90, 0x80) and len(msg) >= 3:
            orig_note = msg[1]
            vel       = msg[2]
            note_on   = (status == 0x90 and vel > 0)
            note_off  = (status == 0x80) or (status == 0x90 and vel == 0)
            if note_on:
                # Escala efectiva: si el track sigue a otro, usa su escala y root
                if t.harmony_src >= 0 and t.harmony_src < len(self.tracks):
                    src = self.tracks[t.harmony_src]
                    eff_scale, eff_root = src.scale_idx, src.root
                else:
                    eff_scale, eff_root = t.scale_idx, t.root
                q_note = _quantize_to_scale(orig_note, eff_scale, eff_root)
                self._kb_thru_map[orig_note] = q_note
                self._out(t).send_message([status | ch, q_note, vel])
            elif note_off:
                # Enviar note-off para la nota cuantizada (no la original)
                q_note = self._kb_thru_map.pop(orig_note, orig_note)
                self._out(t).send_message([status | ch, q_note, vel])
        else:
            # CC, pitch bend, aftertouch → thru directo sin modificar
            self._out(t).send_message([status | ch] + list(msg[1:]))

        # Grabar CC / pitch bend / aftertouch en tiempo real
        if status == 0xB0 and self.recording and self.running:
            cc_num, cc_val = msg[1], msg[2]
            self._record_cc_to_lane(t, cc_num, cc_val)
        elif status == 0xE0 and self.recording and self.running:
            # Pitch bend → lane especial CC 128
            pb_val = int(msg[2] * 127 / 127)  # normalizar MSB a 0-127 para visualización
            self._record_cc_to_lane(t, 128, pb_val)
            self._rec_pending['pitch_bend'] = msg[1] | (msg[2] << 7)
        elif status == 0xD0 and self.recording and self.running:
            self._record_cc_to_lane(t, 129, msg[1])  # aftertouch → lane CC 129
            self._rec_pending['pressure'] = msg[1]

        # Solo procesar notas para grabación/harmony
        if status not in (0x80, 0x90, 0xA0):
            return

        orig_note = msg[1]          # nota MIDI original sin cuantizar — clave estable para _kb_held
        vel       = msg[2]
        note_on  = (status == 0x90 and vel > 0)
        note_off = (status == 0x80) or (status == 0x90 and vel == 0)

        # Cuantizar la nota a la escala efectiva igual que el thru live, para
        # que lo grabado suene exactamente como lo que el usuario oyó al tocar.
        if t.harmony_src >= 0 and t.harmony_src < len(self.tracks):
            src = self.tracks[t.harmony_src]
            eff_scale, eff_root = src.scale_idx, src.root
        else:
            eff_scale, eff_root = t.scale_idx, t.root
        note = _quantize_to_scale(orig_note, eff_scale, eff_root)

        # Note-on: siempre guardamos la nota como semilla para pads
        # (step-compose). La raíz de la pista solo se actualiza si HARM
        # (toggle azul) está activo, que es lo que habilita el tracking
        # del teclado para el efecto harmony.
        if note_on:
            # ── Shift + nota = REST: avanza cursor sin grabar ──
            if self.shift and self.recording and not self.running:
                with self.lock:
                    pat_len       = max(len(t.pattern), 1)
                    skipped       = t.cursor % pat_len
                    t.cursor      = (skipped + 1) % pat_len
                    # kb_step sigue al cursor para que rec_step se actualice
                    self.kb_step  = t.cursor
                self.last_msg = f"REST S{skipped+1} → S{t.cursor+1}"
                self._render()
                return

            t.last_kbd_note = note
            if t.harmony_on:
                t.root = max(0, min(127, note))
            # Si hay un pad sostenido: 1ª nota sustituye, siguientes acumulan acorde
            if self.held_step is not None:
                ti, hstep = self.held_step
                tr = self.tracks[ti]
                if 0 <= hstep < len(tr.pattern):
                    with self.lock:
                        locks = tr.step_locks.setdefault(hstep, {})
                        if self.held_kbd_count == 0:
                            # Primera nota durante este hold: reemplaza
                            locks.pop('notes', None)
                            locks.pop('note', None)
                            locks['note'] = note
                        else:
                            # Notas siguientes: convierte en acorde
                            existing = locks.get('notes')
                            if existing is None:
                                # Promueve 'note' → 'notes'
                                seed = locks.pop('note', None)
                                existing = [seed] if seed is not None else []
                            if note not in existing:
                                existing.append(note)
                            locks['notes'] = existing
                        tr.pattern[hstep] = True
                        tr.pulses = max(tr.pulses, sum(tr.pattern))
                        # Guardar harm_root si el track tiene harmony_src activo
                        if tr.harmony_src >= 0 and tr.harmony_src < len(self.tracks):
                            locks['harm_root'] = self.tracks[tr.harmony_src].root
                        self.held_knob_used = True  # evita que al soltar se toggle off
                        self.held_kbd_count += 1
                        self._push_undo(force=False)
                        if self.held_kbd_count > 1:
                            self.last_msg = f"T{ti+1} s{hstep+1} CHRD +{note_name(note)}"
                        else:
                            self.last_msg = f"T{ti+1} s{hstep+1} ← {note_name(note)}"
                    return

        if not self.recording:
            return

        if note_off:
            # ── Note-off: calcular duración y guardarla ────────────────────
            # Usamos orig_note como clave — es determinista aunque cambie la escala/root
            entry = self._kb_held.pop(orig_note, None)
            if entry is not None:
                step, track_idx, t_on = entry
                held = time.time() - t_on
                # Usar el track del momento de grabación, no self.active (puede haber cambiado)
                tr = self.tracks[track_idx]
                base_interval = 60.0 / self.bpm / 4
                step_dur = tr.tick_interval(base_interval)
                note_len = max(1, min(32, round(held / step_dur)))
                with self.lock:
                    lk = tr.step_locks.setdefault(step, {})
                    lk['note_len'] = note_len
                    lk['gate'] = 1.0  # sin recorte: la duración grabada es la duración real
            return

        if not note_on:
            return

        with self.lock:
            pat_len = max(len(t.pattern), 1)
            if self.running:
                now_kb = time.perf_counter()
                # Calcular step_dur para la ventana de snap
                _base = 60.0 / self.bpm / 4
                _sdur = t.tick_interval(_base)
                # Copiar la lista bajo lock para evitar race con el tick loop
                fire_snap = list(t.fire_history)
                # Solo mirar dentro de una ventana de ±2 steps — evita snaps a
                # pasos muy antiguos cuando el usuario lleva un rato sin tocar
                window = _sdur * 2.0
                recent = [(ts, s) for ts, s in fire_snap if now_kb - ts <= window]
                if recent:
                    _, step = max(recent, key=lambda e: e[0])
                    step = step % pat_len
                elif fire_snap:
                    # Fuera de ventana pero hay historial: usar el más reciente
                    _, step = max(fire_snap, key=lambda e: e[0])
                    step = step % pat_len
                else:
                    step = t.display_step % pat_len
            else:
                # Step record: si hay un step en foco (Tab), grabar ahí sin avanzar
                if self.kb_step_focus:
                    step = self.kb_step % pat_len
                else:
                    step      = t.cursor % pat_len
                    t.cursor  = (step + 1) % pat_len
                    self.kb_step = t.cursor  # mantener kb_step sincronizado con el cursor de grabación

            t.pattern[step] = True
            t.pulses = max(t.pulses, sum(t.pattern))
            # Siempre guardar la nota exacta tocada como plock en el step concreto
            locks = t.step_locks.setdefault(step, {})
            existing = locks.get('notes', [locks.pop('note', None)] if 'note' in locks else [])
            if note not in existing:
                existing.append(note)
            locks['notes'] = existing
            # Si hay harmony_src activo, guardar el root actual del source para poder
            # transponerlo en playback cuando el source cambie de root (harmony follow).
            if t.harmony_src >= 0 and t.harmony_src < len(self.tracks):
                locks['harm_root'] = self.tracks[t.harmony_src].root
            # Velocidad solo si difiere de la del track
            if vel != t.velocity:
                locks['vel'] = vel
            self._push_undo(force=False)
            self.last_msg = f"T{self.active+1} REC → {note_name(note)} s{step+1}"
            if not self.running:
                self._render()

        # Clave = nota original sin cuantizar → inmune a cambios de escala/root durante hold
        self._kb_held[orig_note] = (step, self.active, time.time())

    def _handle_knob(self, page, idx, val, t):
        self._push_undo(force=False)
        # Sincronizar kb_param con el knob físico que se acaba de mover
        self.kb_param = idx
        n = self.active + 1

        if page == 0:   # A·seq
            if idx == 0:
                t.pulses = max(0, int((val/127)*t.steps)); t.rebuild(); t.tonal_notes=[]
                self.last_msg = f"T{n} PULSE → {t.pulses}"
            elif idx == 1:
                old_len = len(t.pattern)
                t.steps = max(4, round(4+(val/127)*60))
                t.pulses = min(t.pulses, t.steps)
                t.cursor = t.cursor % t.steps
                if t.steps != old_len:
                    if t.steps < old_len:
                        # Acortar: guardar steps ocultos en buffer
                        t._pattern_buf = t.pattern[t.steps:] + t._pattern_buf
                        t._locks_buf.update({k: v for k, v in t.step_locks.items() if k >= t.steps})
                        t.pattern      = t.pattern[:t.steps]
                        t.base_pattern = t.base_pattern[:t.steps]
                        t.step_locks   = {k: v for k, v in t.step_locks.items() if k < t.steps}
                    else:
                        # Ampliar: restaurar desde buffer primero, luego pad con False
                        need = t.steps - old_len
                        if t._pattern_buf:
                            restore = t._pattern_buf[:need]
                            t._pattern_buf = t._pattern_buf[need:]
                            t.pattern      = t.pattern + restore
                            t.base_pattern = t.base_pattern + restore
                        if len(t.pattern) < t.steps:
                            pad = t.steps - len(t.pattern)
                            t.pattern      += [False] * pad
                            t.base_pattern += [False] * pad
                        # Restaurar step_locks ocultos
                        for k, v in list(t._locks_buf.items()):
                            if k < t.steps:
                                t.step_locks[k] = v
                                del t._locks_buf[k]
                    t.pulses = sum(t.pattern)
                    if t.tonal_notes:
                        t.tonal_notes = t.tonal_notes[:t.steps]
                self.last_msg = f"T{n} Steps → {t.steps}"
            elif idx == 2:
                t.prob = val/127
                self.last_msg = f"T{n} Prob → {int(t.prob*100)}%"
            elif idx == 3:
                t.play_mode = int((val/127)*(len(PLAY_MODES)-1))
                self.last_msg = f"T{n} Mode → {PLAY_MODES[t.play_mode]}"
            elif idx == 4:
                t.velocity = max(1, val)
                self.last_msg = f"T{n} Vel → {t.velocity}"
            elif idx == 5:
                t.swing = val/127
                self.last_msg = f"T{n} Swing → {int(t.swing*100)}%"
            elif idx == 6:
                t.resolution = int((val/127)*(len(RESOLUTIONS)-1))
                # Posicionar tick_acc para que el siguiente tick dispare inmediatamente
                _tn = t.tick_interval(60.0 / self.bpm / 4.0) / (60.0 / self.bpm / 4.0)
                t.tick_acc = max(0.0, _tn - 1.0)
                name,_ = RESOLUTIONS[t.resolution]
                if self.recording:
                    self._rec_pending['resolution'] = t.resolution
                self.last_msg = f"T{n} Resol → {name}"
            elif idx == 7:
                old_rot    = t.rotation
                t.rotation = int((val/127)*(t.steps-1))
                delta      = (t.rotation - old_rot) % len(t.pattern) if t.pattern else 0
                if delta:
                    n_steps        = len(t.pattern)
                    t.pattern      = t.pattern[delta:]      + t.pattern[:delta]
                    t.base_pattern = t.base_pattern[delta:] + t.base_pattern[:delta]
                    t.step_locks   = {(k - delta) % n_steps: v
                                      for k, v in t.step_locks.items()}
                self.last_msg = f"T{n} Rotat → {t.rotation}"

        elif page == 1:  # B·note
            if idx == 0:
                new_root = int(24 + (val/127)*84)  # C0 (24) … C8 (108)
                delta = new_root - t.root
                if delta:
                    _transpose_track(t, delta)
                self.last_msg = f"T{n} Note → {note_name(t.root)}"
            elif idx == 1:
                t.scale_idx = int((val/127)*(len(SCALES)-1))
                name,_ = SCALES[t.scale_idx]
                if t.tonal_notes:
                    t.tonal_notes = _requantize(t.tonal_notes, t.scale_idx, t.root)
                for lk in t.step_locks.values():
                    if 'notes' in lk:
                        lk['notes'] = _requantize(lk['notes'], t.scale_idx, t.root)
                    elif 'note' in lk:
                        lk['note'] = _requantize([lk['note']], t.scale_idx, t.root)[0]
                t.note_cursor = 0
                self.last_msg = f"T{n} Scale → {name}"
            elif idx == 2:
                t.octave = int((val/127)*4)-2
                self.last_msg = f"T{n} OCT → {t.octave:+d}"
            elif idx == 3:
                t.density = val/127
                self.last_msg = f"T{n} DENS → {int(t.density*100)}%"
            elif idx == 4:
                t.spread = val/127
                self.last_msg = f"T{n} SPRD → {int(t.spread*100)}%"
            elif idx == 5:
                t.harmony_src = int((val/127)*8)-1
                src = f"TRK{t.harmony_src+1}" if t.harmony_src>=0 else "off"
                self.last_msg = f"T{n} HARM → {src}"
            elif idx == 6:
                t.interval = int((val/127)*7)
                t._scale_pos = 0  # reset posición al cambiar intervalo
                self.last_msg = f"T{n} INTV → {t.interval}°"
            elif idx == 7:
                t.note_len = max(1, int(1+(val/127)*15))
                self.last_msg = f"T{n} NLEN → {t.note_len} steps"

        elif page == 2:  # C·fx
            if idx == 0:
                t.delay_on = val > 63
                self.last_msg = f"T{n} Delay → {'ON' if t.delay_on else 'OFF'}"
            elif idx == 1:
                t.delay_steps = max(1, int((val/127)*8))
                self.last_msg = f"T{n} DTIME → {t.delay_steps}"
            elif idx == 2:
                t.delay_fb = val/127
                self.last_msg = f"T{n} FDBK → {int(t.delay_fb*100)}%"
            elif idx == 3:
                # -1 (fade out) … 0 (plano) … +1 (fade in)
                curve = round((val / 127) * 2.0 - 1.0, 2)
                if abs(curve) < 0.05:
                    curve = 0.0
                _rdc_name = ("F/O" if curve < -0.05 else "F/I" if curve > 0.05 else "---")
                # Trig lock si hay un step mantenido
                if self.held_step is not None:
                    _hs_ti, _hs_step = self.held_step
                    _hs_tr = self.tracks[_hs_ti]
                    if 0 <= _hs_step < len(_hs_tr.pattern):
                        _hs_tr.step_locks.setdefault(_hs_step, {})['ratchet_curve'] = curve
                        self.held_knob_used = True
                        self.last_msg = f"T{_hs_ti+1} S{_hs_step+1} RDEC → {_rdc_name} ({curve:+.2f})"
                        self.view_mode = 'detail'
                        if self._detail_timer: self._detail_timer.cancel()
                        self._detail_timer = threading.Timer(2.5, self._to_page_view)
                        self._detail_timer.start()
                        _push_display(f"S{_hs_step+1} RDEC", f"{_rdc_name} ({curve:+.2f})",
                                      bar=(val/127), track=_hs_ti, bpm=int(self.bpm),
                                      playing=self.running,
                                      extra={'view_mode': 'detail',
                                             'label': f"S{_hs_step+1} RDEC", 'value': _rdc_name})
                        return
                t.ratchet_curve = curve
                self.last_msg = f"T{n} RDEC → {_rdc_name} ({curve:+.2f})"
            elif idx == 4:
                # RSPD: spread del ratchet como fracción del step (0%=flam, 100%=equidistante, 200%=derrame al step siguiente)
                spread = round(val / 127 * 2.0, 2)
                _spd_name = f"{int(spread*100)}%"
                if self.held_step is not None:
                    _hs_ti, _hs_step = self.held_step
                    _hs_tr = self.tracks[_hs_ti]
                    if 0 <= _hs_step < len(_hs_tr.pattern):
                        _hs_tr.step_locks.setdefault(_hs_step, {})['ratchet_div'] = spread
                        self.held_knob_used = True
                        self.last_msg = f"T{_hs_ti+1} S{_hs_step+1} RSPD → {_spd_name}"
                        self.view_mode = 'detail'
                        if self._detail_timer: self._detail_timer.cancel()
                        self._detail_timer = threading.Timer(2.5, self._to_page_view)
                        self._detail_timer.start()
                        _push_display(f"S{_hs_step+1} RSPD", _spd_name, bar=spread,
                                      track=_hs_ti, bpm=int(self.bpm), playing=self.running,
                                      extra={'view_mode': 'detail',
                                             'label': f"S{_hs_step+1} RSPD", 'value': _spd_name})
                        return
                t.ratchet_div = spread
                self.last_msg = f"T{n} RSPD → {_spd_name}"
            elif idx == 5:
                t.ratchet = max(1, int(1+(val/127)*15))
                self.last_msg = f"T{n} RTCH → {t.ratchet}x"
            elif idx == 6:
                t.gate = val/127
                self.last_msg = f"T{n} Gate → {int(t.gate*100)}%"
            elif idx == 7:
                t.cc_num = int((val/127)*127) if val>0 else -1
                lane = t.active_cc_lane
                self.last_msg = f"T{n} L{lane+1} CC → {t.cc_num if t.cc_num>=0 else 'off'}"

        elif page == 3:  # D·settings
            if idx == 0:
                t.program = int((val/127)*127)
                if t.send_pc:
                    self._send_program(t)
                # program no entra en _rec_pending — solo como held-step plock explícito
                self.last_msg = f"T{n} Prog → {t.program}"
            elif idx == 1:
                t.bank_msb = int((val/127)*15)
                self._send_program_debounced(self.active)
                # bank_msb tampoco como automation global
                self.last_msg = f"T{n} Bank → {t.bank_msb}"
            elif idx == 2:
                self.current_bank = int((val/127)*7)
                self.last_msg = f"PatBank → {self.current_bank + 1}"
            elif idx == 3:
                # PTRN: read-only (muestra el slot cargado actual)
                return
            elif idx == 4:
                # All-notes-off en el canal anterior para evitar notas colgadas
                self._out(t).send_message([0xB0 | t.channel, 123, 0])
                t.channel = int((val/127)*15)
                self.last_msg = f"T{n} Chan → {t.channel + 1}"
            elif idx == 5:
                if self.shift:
                    # SHIFT+CLK: toggle sync externo (esclavo del DAW)
                    self.ext_sync = val > 63
                    if self.ext_sync:
                        self.clock_out = False   # no enviar clock si somos esclavos
                        self._ext_clock_count = 0
                        self._ext_clock_event.clear()
                    state = "EXT" if self.ext_sync else "OFF"
                    self.last_msg = f"CLK SYNC → {state}"
                else:
                    self.ext_sync = False
                    self.clock_out = val > 63
                    state = "OUT" if self.clock_out else "OFF"
                    self.last_msg = f"CLK → {state}"
            elif idx == 6:
                # All-notes-off en el puerto anterior
                self._out(t).send_message([0xB0 | t.channel, 123, 0])
                t.port = 1 if val > 63 else 0
                self.last_msg = f"T{n} Port → OUT{t.port + 1}"
            elif idx == 7:
                # SCRI: select script based on knob position (0-127)
                script_ids = list(self.scripts_lib.keys())
                if script_ids:
                    script_idx = min(len(script_ids) - 1, int(val / 127 * len(script_ids)))
                    t.script_id = script_ids[script_idx]
                    script_name = self.scripts_lib[t.script_id].get('name', t.script_id)
                    self.last_msg = f"T{n} SCRIPT → {script_name}"
                else:
                    t.script_id = None
                    self.last_msg = f"T{n} SCRIPT → OFF"

        # Push al display visual
        parts = self.last_msg.split('→')
        if len(parts) == 2:
            disp_label = parts[0].split(None,1)[-1].strip()
            disp_value = parts[1].strip()
            _push_display(disp_label, disp_value, bar=val/127,
                          track=self.active, bpm=int(self.bpm), playing=self.running,
                          extra={'view_mode': 'detail'})

        # ── Automation recording: graba movimiento continuo del knob ──
        if self.recording and self.running and page < len(PAGE_PARAMS):
            params = PAGE_PARAMS[page]
            if idx < len(params):
                param = params[idx]
                auto_val = self._stoch_param_get(t, param)
                if auto_val is not None:
                    self._record_auto(self.active, param, auto_val)

    # ── helpers para leer/restaurar parámetros por nombre ─────────────────────
    def _stoch_param_get(self, t, param):
        """Lee el valor actual de un parámetro por su nombre de página."""
        return {
            'PULS': t.pulses, 'STEP': t.steps, 'PROB': t.prob, 'VEL': t.velocity,
            'SWNG': t.swing, 'ROTA': t.rotation, 'NOTE': t.note_offset,
            'SPRD': t.spread, 'GATE': t.gate, 'RDEC': t.ratchet_curve,
            'RTCH': t.ratchet, 'DENS': t.density, 'INTV': t.interval,
            'SCAL': t.scale_idx, 'OCT': t.octave, 'NLEN': t.note_len,
            'HARM': t.harmony_src, 'DTIM': t.delay_steps, 'FDBK': t.delay_fb,
            'RSPD': t.ratchet_div, 'RESL': t.resolution, 'MULT': t.multiplier,
            'PROG': t.program, 'BANK': t.bank_msb,
        }.get(param)

    def _stoch_param_set(self, t, param, val):
        if   param == 'PULS':   t.pulses = val;      t.rebuild()
        elif param == 'STEP':   t.steps = val;       t.rebuild()
        elif param == 'PROB':     t.prob = val
        elif param == 'VEL':      t.velocity = val
        elif param == 'SWNG':    t.swing = val
        elif param == 'ROTA':    t.rotation = val;    t.rebuild()
        elif param == 'NOTE':     t.note_offset = int(round(val))  # semitone shift ±24
        elif param == 'SPRD':   t.spread = val
        elif param == 'GATE':     t.gate = val
        elif param == 'RDEC': t.ratchet_curve = max(-1.0, min(1.0, val))
        elif param == 'RTCH':  t.ratchet = val
        elif param == 'DENS':  t.density = val
        elif param == 'INTV':   t.interval = val
        elif param == 'SCAL': t.scale_idx = val
        elif param == 'OCT':       t.octave = val
        elif param == 'NLEN':    t.note_len = val
        elif param == 'HARM': t.harmony_src = val
        elif param == 'DTIM': t.delay_steps = val
        elif param == 'FDBK':    t.delay_fb = val
        elif param == 'RSPD': t.ratchet_div = max(0.0, min(2.0, float(val)))
        elif param == 'RESL':  t.resolution = val
        elif param == 'MULT':  t.multiplier = val
        elif param == 'PROG':
            t.program = val
            if t.send_pc:
                self._send_program_debounced(self.tracks.index(t))
        elif param == 'BANK':
            t.bank_msb = val
            if t.send_pc:
                self._send_program_debounced(self.tracks.index(t))
        elif param.startswith('CC_'):
            cc_num = int(param[3:])
            self._out(t).send_message([0xB0 | t.channel, cc_num, int(val)])
            self._mirror([0xB0 | t.channel, cc_num, int(val)])
        elif param == 'PITCHBEND':
            pb = int(val)
            self._out(t).send_message([0xE0 | t.channel, pb & 0x7F, (pb >> 7) & 0x7F])
        elif param == 'AFTERTOUCH':
            self._out(t).send_message([0xD0 | t.channel, int(val)])

    def _nudge_param(self, direction):
        """Sube/baja el parámetro kb_param de la página actual en la pista activa.
        direction: +1 ó -1
        """
        t = self.tracks[self.active]
        page = self.page
        idx  = self.kb_param
        params = PAGE_PARAMS.get(page, [])
        if idx >= len(params):
            return
        param = params[idx]
        n = self.active + 1
        self._push_undo(force=False)

        if page == 0:   # A·SEQ
            if param == 'PULS':
                t.pulses = max(0, min(t.steps, t.pulses + direction))
                t.rebuild(); t.tonal_notes = []
                self.last_msg = f"T{n} PULSE → {t.pulses}"
            elif param == 'STEP':
                old_len = len(t.pattern)
                t.steps = max(4, min(64, t.steps + direction))
                t.pulses = min(t.pulses, t.steps)
                t.cursor = t.cursor % t.steps
                if t.steps != old_len:
                    if t.steps < old_len:
                        t._pattern_buf = t.pattern[t.steps:] + t._pattern_buf
                        t._locks_buf.update({k: v for k, v in t.step_locks.items() if k >= t.steps})
                        t.pattern      = t.pattern[:t.steps]
                        t.base_pattern = t.base_pattern[:t.steps]
                        t.step_locks   = {k: v for k, v in t.step_locks.items() if k < t.steps}
                    else:
                        need = t.steps - old_len
                        if t._pattern_buf:
                            restore = t._pattern_buf[:need]
                            t._pattern_buf = t._pattern_buf[need:]
                            t.pattern      = t.pattern + restore
                            t.base_pattern = t.base_pattern + restore
                        if len(t.pattern) < t.steps:
                            pad = t.steps - len(t.pattern)
                            t.pattern      += [False] * pad
                            t.base_pattern += [False] * pad
                        for k, v in list(t._locks_buf.items()):
                            if k < t.steps:
                                t.step_locks[k] = v
                                del t._locks_buf[k]
                    t.pulses = sum(t.pattern)
                    if t.tonal_notes:
                        t.tonal_notes = t.tonal_notes[:t.steps]
                self.last_msg = f"T{n} Steps → {t.steps}"
            elif param == 'PROB':
                t.prob = max(0.0, min(1.0, round(t.prob + direction * 0.05, 2)))
                self.last_msg = f"T{n} Prob → {int(t.prob*100)}%"
            elif param == 'MODE':
                t.play_mode = (t.play_mode + direction) % len(PLAY_MODES)
                self.last_msg = f"T{n} Mode → {PLAY_MODES[t.play_mode]}"
            elif param == 'VEL':
                t.velocity = max(1, min(127, t.velocity + direction * 5))
                self.last_msg = f"T{n} Vel → {t.velocity}"
            elif param == 'SWNG':
                t.swing = max(0.0, min(1.0, round(t.swing + direction * 0.05, 2)))
                self.last_msg = f"T{n} Swing → {int(t.swing*100)}%"
            elif param == 'RESL':
                t.resolution = max(0, min(len(RESOLUTIONS)-1, t.resolution + direction))
                _tn = t.tick_interval(60.0 / self.bpm / 4.0) / (60.0 / self.bpm / 4.0)
                t.tick_acc = max(0.0, _tn - 1.0)
                name, _ = RESOLUTIONS[t.resolution]
                self.last_msg = f"T{n} Resol → {name}"
            elif param == 'ROTA':
                old_rot = t.rotation
                t.rotation = max(0, min(max(0, t.steps-1), t.rotation + direction))
                delta = (t.rotation - old_rot) % len(t.pattern) if t.pattern else 0
                if delta:
                    ns = len(t.pattern)
                    t.pattern      = t.pattern[delta:]      + t.pattern[:delta]
                    t.base_pattern = t.base_pattern[delta:] + t.base_pattern[:delta]
                    t.step_locks   = {(k - delta) % ns: v for k, v in t.step_locks.items()}
                self.last_msg = f"T{n} Rotat → {t.rotation}"

        elif page == 1:  # B·NOTE
            if param == 'NOTE':
                new_root = max(24, min(108, t.root + direction))
                delta = new_root - t.root
                if delta:
                    _transpose_track(t, delta)
                self.last_msg = f"T{n} Note → {note_name(t.root)}"
            elif param == 'SCAL':
                t.scale_idx = (t.scale_idx + direction) % len(SCALES)
                name, _ = SCALES[t.scale_idx]
                if t.tonal_notes:
                    t.tonal_notes = _requantize(t.tonal_notes, t.scale_idx, t.root)
                for lk in t.step_locks.values():
                    if 'notes' in lk:
                        lk['notes'] = _requantize(lk['notes'], t.scale_idx, t.root)
                    elif 'note' in lk:
                        lk['note'] = _requantize([lk['note']], t.scale_idx, t.root)[0]
                t.note_cursor = 0
                self.last_msg = f"T{n} Scale → {name}"
            elif param == 'OCT':
                t.octave = max(-2, min(2, t.octave + direction))
                self.last_msg = f"T{n} OCT → {t.octave:+d}"
            elif param == 'DENS':
                t.density = max(0.0, min(1.0, round(t.density + direction * 0.1, 2)))
                self.last_msg = f"T{n} DENS → {int(t.density*100)}%"
            elif param == 'SPRD':
                t.spread = max(0.0, min(1.0, round(t.spread + direction * 0.1, 2)))
                self.last_msg = f"T{n} SPRD → {int(t.spread*100)}%"
            elif param == 'HARM':
                t.harmony_src = max(-1, min(len(self.tracks)-1, t.harmony_src + direction))
                src = f"TRK{t.harmony_src+1}" if t.harmony_src >= 0 else "off"
                self.last_msg = f"T{n} HARM → {src}"
            elif param == 'INTV':
                t.interval = max(0, min(7, t.interval + direction))
                t._scale_pos = 0
                self.last_msg = f"T{n} INTV → {t.interval}°"
            elif param == 'NLEN':
                t.note_len = max(1, min(16, t.note_len + direction))
                self.last_msg = f"T{n} NLEN → {t.note_len} steps"

        elif page == 2:  # C·FX
            if param == 'DLY':
                t.delay_on = not t.delay_on
                self.last_msg = f"T{n} Delay → {'ON' if t.delay_on else 'OFF'}"
            elif param == 'DTIM':
                t.delay_steps = max(1, min(8, t.delay_steps + direction))
                self.last_msg = f"T{n} DTIME → {t.delay_steps}"
            elif param == 'FDBK':
                t.delay_fb = max(0.0, min(1.0, round(t.delay_fb + direction * 0.1, 2)))
                self.last_msg = f"T{n} FDBK → {int(t.delay_fb*100)}%"
            elif param == 'RDEC':
                t.ratchet_curve = max(-1.0, min(1.0, round(t.ratchet_curve + direction * 0.1, 2)))
                _rdc_name = ("F/O" if t.ratchet_curve < -0.05 else
                             "F/I" if t.ratchet_curve > 0.05 else "---")
                self.last_msg = f"T{n} RDEC → {_rdc_name} ({t.ratchet_curve:+.2f})"
            elif param == 'RSPD':
                t.ratchet_div = max(0.0, min(2.0, round(t.ratchet_div + direction * 0.1, 2)))
                self.last_msg = f"T{n} RSPD → {int(t.ratchet_div*100)}%"
            elif param == 'RTCH':
                t.ratchet = max(1, min(16, t.ratchet + direction))
                self.last_msg = f"T{n} RTCH → {t.ratchet}x"
            elif param == 'GATE':
                t.gate = max(0.0, min(1.0, round(t.gate + direction * 0.05, 2)))
                self.last_msg = f"T{n} Gate → {int(t.gate*100)}%"
            elif param == 'CC':
                t.cc_num = max(-1, min(127, t.cc_num + direction))
                lane = t.active_cc_lane
                self.last_msg = f"T{n} L{lane+1} CC → {t.cc_num if t.cc_num>=0 else 'off'}"

        elif page == 3:  # D·CONF
            if param == 'PROG':
                t.program = max(0, min(127, t.program + direction))
                if t.send_pc:
                    self._send_program(t)
                self.last_msg = f"T{n} Prog → {t.program}"
            elif param == 'BANK':
                t.bank_msb = max(0, min(15, t.bank_msb + direction))
                self._send_program_debounced(self.active)
                self.last_msg = f"T{n} Bank → {t.bank_msb}"
            elif param == 'PTBK':
                self.current_bank = max(0, min(15, self.current_bank + direction))
                self.last_msg = f"PatBank → {self.current_bank+1}"
            elif param == 'PTRN':
                new_slot = max(0, min(63, self.active_slot + direction))
                self._load_pattern(new_slot)
                return   # _load_pattern calls _render
            elif param == 'CHAN':
                self._out(t).send_message([0xB0 | t.channel, 123, 0])
                t.channel = max(0, min(15, t.channel + direction))
                self.last_msg = f"T{n} Chan → {t.channel+1}"
            elif param == 'CLK':
                self.clock_out = not self.clock_out
                self.last_msg = f"CLK → {'OUT' if self.clock_out else 'OFF'}"
            elif param == 'PORT':
                self._out(t).send_message([0xB0 | t.channel, 123, 0])
                t.port = 1 - t.port
                self.last_msg = f"T{n} Port → OUT{t.port+1}"
            elif param == 'SCRI':
                # Cycle through available scripts
                script_ids = list(self.scripts_lib.keys())
                if not script_ids:
                    self.last_msg = f"T{n} SCRIPT → no scripts available"
                else:
                    current_idx = script_ids.index(t.script_id) if t.script_id in script_ids else -1
                    next_idx = (current_idx + 1) % len(script_ids)
                    t.script_id = script_ids[next_idx]
                    script_name = self.scripts_lib[t.script_id].get('name', t.script_id)
                    self.last_msg = f"T{n} SCRIPT → {script_name}"

        self._kb_render()

    def _nudge_step_lock(self, direction):
        """Aplica un P-lock en kb_step para el parámetro kb_param de la página actual.
        Si el lock no existe, parte del valor actual del track.
        direction: +1 ó -1 (o múltiplo para saltos grandes)
        """
        t    = self.tracks[self.active]
        step = self.kb_step
        if step < 0 or step >= len(t.pattern):
            return
        page   = self.page
        idx    = self.kb_param
        params = PAGE_PARAMS.get(page, [])
        if idx >= len(params):
            return
        param = params[idx]
        n = self.active + 1
        self._push_undo(force=False)
        lk = t.step_locks.setdefault(step, {})

        # Tabla: param → (lock_key, default_val, lo, hi, step_size, is_int)
        _INFO = {
            'VEL':  ('vel',           t.velocity,     1,    127,  5,    True),
            'PROB': ('prob',          t.prob,         0.0,  1.0,  0.05, False),
            'GATE': ('gate',          t.gate,         0.0,  1.0,  0.05, False),
            'NLEN': ('note_len',      t.note_len,     1,    16,   1,    True),
            'RTCH': ('ratchet',       t.ratchet,      1,    8,    1,    True),
            'RSPD': ('ratchet_div',   t.ratchet_div,  0.0,  2.0,  0.1, False),
            'RDEC': ('ratchet_curve', t.ratchet_curve,-1.0, 1.0,  0.1,  False),
            'DTIM': ('delay_steps',   t.delay_steps,  1,    8,    1,    True),
            'FDBK': ('delay_fb',      t.delay_fb,     0.0,  1.0,  0.1,  False),
            'PROG': ('program',       t.program,      0,    127,  1,    True),
            'BANK': ('bank_msb',      t.bank_msb,     0,    15,   1,    True),
            'RESL': ('resolution',    t.resolution,   0,    len(RESOLUTIONS)-1, 1, True),
            'MODE': ('play_mode',     t.play_mode,    0,    len(PLAY_MODES)-1,  1, True),
        }

        if param in _INFO:
            key, default, lo, hi, sz, is_int = _INFO[param]
            cur = lk.get(key, default)
            if is_int:
                new_val = max(lo, min(hi, int(round(cur)) + direction * sz))
                lk[key] = new_val
                disp = str(new_val)
            else:
                new_val = max(lo, min(hi, round(float(cur) + direction * sz, 3)))
                lk[key] = new_val
                disp = f"{int(new_val*100)}%" if hi == 1.0 else f"{new_val:+.2f}"
            self.last_msg = f"S{step+1} {param} → {disp}"

        elif param == 'NOTE':
            # Transponer nota(s) del step
            existing = lk.get('notes')
            if existing is None:
                if 'note' in lk:
                    existing = [lk.pop('note')]
                elif t.tonal_notes and step < len(t.tonal_notes) and t.tonal_notes[step]:
                    src = t.tonal_notes[step]
                    existing = list(src) if isinstance(src, (list, tuple)) else [src]
                else:
                    existing = [t.root]
            notes = [max(0, min(127, nt + direction)) for nt in existing]
            lk['notes'] = notes
            self.last_msg = f"S{step+1} NOTE → {note_name(notes[0])}"

        elif param == 'OCT':
            # Desplazar octava en el plock de nota
            existing = lk.get('notes')
            if existing is None:
                if 'note' in lk:
                    existing = [lk.pop('note')]
                elif t.tonal_notes and step < len(t.tonal_notes) and t.tonal_notes[step]:
                    src = t.tonal_notes[step]
                    existing = list(src) if isinstance(src, (list, tuple)) else [src]
                else:
                    existing = [t.root]
            notes = [max(0, min(127, nt + direction * 12)) for nt in existing]
            lk['notes'] = notes
            self.last_msg = f"S{step+1} OCT → {note_name(notes[0])}"

        else:
            # Param no soportado como P-lock individual → redirigir a nudge global
            self._nudge_param(direction)
            return

        # Mostrar detail view con el valor del p-lock, igual que los knobs
        lbl  = f"S{step+1} {param}"
        disp = self.last_msg.split('→')[-1].strip() if '→' in self.last_msg else ''
        self.view_mode = 'detail'
        if self._detail_timer:
            self._detail_timer.cancel()
        self._detail_timer = threading.Timer(2.5, self._to_page_view)
        self._detail_timer.start()
        _push_display(lbl, disp, track=self.active, bpm=int(self.bpm),
                      playing=self.running,
                      extra={'view_mode': 'detail', 'label': lbl, 'value': disp,
                             'step_focus': (self.active, step)})
        self._render()

    def _apply_xfade(self):
        """Interpola todos los parámetros de los 8 tracks entre snapshot A y B."""
        if not self.xfade_snap or not self.xfade_snap_a:
            return
        x = self.xfade_amt

        def li(a, b, ka):
            av, bv = a.get(ka, 0), b.get(ka, a.get(ka, 0))
            return int(round(av + (bv - av) * x))
        def lf(a, b, ka):
            av, bv = a.get(ka, 0.0), b.get(ka, a.get(ka, 0.0))
            return av + (bv - av) * x
        def disc(a, b, ka):
            return b.get(ka, a.get(ka)) if x >= 0.5 else a.get(ka)

        for i, t in enumerate(self.tracks):
            a = self.xfade_snap_a[i]
            b = self.xfade_snap[i]
            t.root        = li(a, b, 'root')
            t.prob        = lf(a, b, 'prob')
            t.swing       = lf(a, b, 'swing')
            t.velocity    = li(a, b, 'velocity')
            t.octave      = li(a, b, 'octave')
            t.spread      = lf(a, b, 'spread')
            t.density     = lf(a, b, 'density')
            t.humanize      = lf(a, b, 'humanize')
            t.ratchet_curve = lf(a, b, 'ratchet_curve')
            t.ratchet_div   = max(0.0, min(2.0, lf(a, b, 'ratchet_div')))
            t.gate          = lf(a, b, 'gate')
            t.ratchet       = max(1, li(a, b, 'ratchet'))
            t.delay_fb    = lf(a, b, 'delay_fb')
            t.delay_steps = max(1, li(a, b, 'delay_steps'))
            t.interval    = li(a, b, 'interval')
            t.note_len    = max(1, li(a, b, 'note_len'))
            t.strum       = lf(a, b, 'strum')
            t.play_mode   = disc(a, b, 'play_mode')
            t.scale_idx   = disc(a, b, 'scale_idx')
            t.delay_on    = disc(a, b, 'delay_on')
            new_steps  = max(4, li(a, b, 'steps'))
            new_pulses = max(0, min(new_steps, li(a, b, 'pulses')))
            new_rot    = li(a, b, 'rotation')
            if new_steps != t.steps or new_pulses != t.pulses or new_rot != t.rotation:
                t.steps    = new_steps
                t.pulses   = new_pulses
                t.rotation = new_rot
                t.rebuild()

    def _held_step_display(self, ti, step, tr):
        """Genera label/value para mostrar el valor ciclado del pad sostenido."""
        lk = tr.step_locks.get(step, {})
        # Construir lista de items: notas del acorde + otros params
        items = []
        notes = lk.get('notes', [lk['note']] if 'note' in lk else [])
        nlbl = 'CHRD' if len(notes) > 1 else 'NOTE'
        for n in notes:
            items.append((nlbl, note_name(int(n))))
        for k, v in lk.items():
            if k in ('note', 'notes'): continue
            if k == 'cc_vals':
                # Expandir cada lane de CC como item separado
                for li, cv in v.items():
                    cc_n = tr.cc_lanes[int(li)]['num']
                    items.append((f"CC{cc_n}" if cc_n >= 0 else f"CC L{int(li)+1}", str(cv)))
                continue
            label = _PLOCK_LABEL.get(k, k.upper())
            items.append((label, _fmt_plock_val(k, v)))

        stp_label = f"STP {step+1}"
        if not items:
            self.last_msg = f"T{ti+1} step {step+1} (hold) —"
            return (stp_label, '—', 0.0, ti, int(self.bpm), self.running,
                    {'view_mode':'detail','label':stp_label,'value':'—',
                     'step_counter': ''})

        idx   = self.step_focus_cycle % len(items)
        total = len(items)
        param, val = items[idx]
        counter   = f"{idx+1}/{total}"
        self.last_msg = f"T{ti+1} step {step+1} {param} {counter} → {val}"
        return (param, val, 0.0, ti, int(self.bpm), self.running,
                {'view_mode':'detail','label':param,'value':val,
                 'step_counter': counter})

    def _step_focus_label_value(self):
        """Devuelve {'label':..,'value':..} del item actual si hay step_focus, si no {}."""
        if not self.step_focus:
            return {}
        ti, step = self.step_focus
        tr = self.tracks[ti]
        lk = tr.step_locks.get(step, {})
        items = []
        notes = lk.get('notes', [lk['note']] if 'note' in lk else [])
        nlbl = 'CHRD' if len(notes) > 1 else 'NOTE'
        for n in notes:
            items.append((nlbl, note_name(int(n))))
        for k, v in lk.items():
            if k in ('note', 'notes'): continue
            if k == 'cc_vals':
                for li, cv in v.items():
                    cc_n = tr.cc_lanes[int(li)]['num']
                    items.append((f"CC{cc_n}" if cc_n >= 0 else f"CC L{int(li)+1}", str(cv)))
                continue
            items.append((_PLOCK_LABEL.get(k, k.upper()), _fmt_plock_val(k, v)))
        if not items:
            return {'label': f'STP {step+1}', 'value': '—'}
        idx = self.step_focus_cycle % len(items)
        param, val = items[idx]
        return {'label': param, 'value': val}

    def _step_focus_counter(self):
        """Devuelve string 'N/M' si hay step_focus con p-locks, '' si no."""
        if not self.step_focus:
            return ''
        ti, step = self.step_focus
        tr = self.tracks[ti]
        lk = tr.step_locks.get(step, {})
        notes = lk.get('notes', [lk['note']] if 'note' in lk else [])
        cc_extra = max(0, len(lk.get('cc_vals', {})) - 1)  # cc_vals cuenta como N items
        total = len(notes) + sum(1 for k in lk if k not in ('note', 'notes')) + cc_extra
        if total == 0:
            return ''
        idx = self.step_focus_cycle % total
        return f"{idx+1}/{total}"

    def _to_page_view(self):
        """Timer callback: vuelve a page view tras inactividad en knobs."""
        with self.lock:
            self.view_mode = 'page'
            self._detail_timer = None
            # Solo limpiar step_focus si no está activo el P-lock de teclado
            if not self.kb_step_focus:
                self.step_focus = None
                self.step_focus_cycle = 0
            self._render()

    def _enter_detail(self, arm_timer=True):
        """Activa detail view (con timer de auto-vuelta) salvo si estamos en
        bank_view, que siempre fuerza page view en la Launchpad y el web UI."""
        if self.bank_view:
            return False
        self.view_mode = 'detail'
        if arm_timer:
            if self._detail_timer:
                self._detail_timer.cancel()
            self._detail_timer = threading.Timer(2.5, self._to_page_view)
            self._detail_timer.start()
        return True

    def _kb_render(self):
        """Render tras acción de teclado: vuelve a page view si había detail activo,
        para no interferir con el detail view de los knobs ni causar parpadeo."""
        if getattr(self, '_suppress_kb_render', False):
            return
        if self.view_mode == 'detail':
            if self._detail_timer:
                self._detail_timer.cancel()
                self._detail_timer = None
            self.view_mode = 'page'
            if not self.kb_step_focus:
                self.step_focus = None
                self.step_focus_cycle = 0
        self._render()

    def _get_group_ccs(self):
        """Devuelve lista de 8 CCs para el grupo/página activos."""
        g = self.mapping_group
        if g == 'misc':
            keys = ['bpm', 'undo', 'redo', 'copy', 'paste', 'sbank', 'lbank', 'shift']
            m = self._cc_map.get('misc', {})
            return [m.get(k) for k in keys]
        rows = self._cc_map.get(g, [list(range(NK_KNOB_BASE, NK_KNOB_BASE + 8))] * 4)
        return rows[self.page] if self.page < len(rows) else rows[0]

    def _compute_header_counter(self, t_a):
        """Devuelve el contador a mostrar en hdr-mode según contexto.
        - P-lock: lock_actual / total_locks_en_step
        - Chord (held_step): step_focus_cycle+1 / total_items
        - Resto: None (usa step_pg/step_pg_total)
        """
        # Chord / pad sostenido
        if self.held_step is not None:
            ti, hstep = self.held_step
            tr = self.tracks[ti]
            lk = tr.step_locks.get(hstep, {})
            notes = lk.get('notes', [lk['note']] if 'note' in lk else [])
            n_items = len(notes) + sum(1 for k in lk if k not in ('note', 'notes', 'cc_vals'))
            if 'cc_vals' in lk:
                n_items += len(lk['cc_vals'])
            if n_items > 0:
                return f"{(self.step_focus_cycle % n_items) + 1}/{n_items}"
            return None
        # P-lock focus: contador de locks en el step actual
        if self.kb_step_focus:
            lk = t_a.step_locks.get(self.kb_step, {})
            n_locks = sum(1 for k in lk if k not in ('cc_vals',))
            if 'cc_vals' in lk:
                n_locks += len(lk['cc_vals'])
            if n_locks > 0:
                return f"{n_locks} LK"
            return "—"
        return None

    def _get_page_params(self):
        """Devuelve lista de 8 {label, value} para la página activa."""
        t  = self.tracks[self.active]
        pg = self.page
        if pg == 0:
            vals = [
                str(sum(t.pattern)) if any(t.pattern) else "0",
                str(t.steps),
                f"{int(t.prob*100)}%",
                PLAY_MODES[t.play_mode][:3].upper(),
                str(t.velocity),
                f"{int(t.swing*100)}%",
                RESOLUTIONS[t.resolution][0],
                str(t.rotation),
            ]
        elif pg == 1:
            vals = [
                note_name(t.root),
                ("→" + SCALE_ABBR.get(SCALES[self.tracks[t.harmony_src].scale_idx][0],
                    SCALES[self.tracks[t.harmony_src].scale_idx][0][:3].upper())
                 if t.harmony_src >= 0 and t.harmony_src < len(self.tracks)
                 else SCALE_ABBR.get(SCALES[t.scale_idx][0], SCALES[t.scale_idx][0][:3].upper())),
                f"{t.octave:+d}",
                f"{int(t.density*100)}%",
                f"{int(t.spread*100)}%",
                f"TRK{t.harmony_src+1}" if t.harmony_src >= 0 else "OFF",
                f"{t.interval}°",
                f"{t.note_len}st",
            ]
        elif pg == 2:
            vals = [
                "ON" if t.delay_on else "OFF",
                str(t.delay_steps),
                f"{int(t.delay_fb*100)}%",
                ("F/O" if t.ratchet_curve < -0.05 else "F/I" if t.ratchet_curve > 0.05 else "---") +
                f" {t.ratchet_curve:+.1f}",
                f"{int(t.ratchet_div*100)}%",
                f"{t.ratchet}x",
                f"{int(t.gate*100)}%",
                str(t.cc_num) if t.cc_num >= 0 else "—",
            ]
        else:  # pg == 3
            script_name = "OFF"
            if t.script_id and t.script_id in self.scripts_lib:
                script_name = self.scripts_lib[t.script_id].get('name', t.script_id)[:4].upper()
            vals = [
                str(t.program),
                str(t.bank_msb),
                str(self.current_bank + 1),
                str(self.active_slot + 1) if self.active_slot >= 0 else "—",
                f"CH{t.channel + 1}",
                "EXT" if self.ext_sync else ("OUT" if self.clock_out else "OFF"),
                f"OUT{t.port + 1}",
                script_name,
            ]
        params = list(PAGE_PARAMS[pg])
        # Label dinámico para CC: muestra la lane activa (CC 1, CC 2, ...)
        if pg == 2:
            params[7] = f"CC {t.active_cc_lane + 1}"
        _scale_borrowed = (t.harmony_src >= 0 and t.harmony_src < len(self.tracks))
        def _toggled(p):
            if p == 'HARM': return t.harmony_on
            if p == 'CLK':  return self.clock_out or self.ext_sync
            if p == 'SCRI': return bool(t.script_id)
            return False
        def _disabled(p):
            # SCAL aparece disabled cuando la escala es prestada de otra pista
            if p == 'SCAL': return _scale_borrowed
            return False
        return [{'label': params[i], 'value': vals[i],
                 'stoch': bool(t.stoch_enabled.get(params[i], False)),
                 'toggled': _toggled(params[i]),
                 'disabled': _disabled(params[i]),
                 'stoch_amount': int(t.stoch_amounts.get(params[i], 0) * 100)} for i in range(8)]

    def _get_all_page_params(self):
        """Devuelve lista de 4 × 8 params (todas las páginas, pista activa)."""
        orig = self.page
        result = []
        for pg in range(4):
            self.page = pg
            result.append(self._get_page_params())
        self.page = orig
        return result

    def _handle_fader(self, page, idx, val, t):
        params = PAGE_PARAMS.get(page, [])
        if idx >= len(params): return
        param = params[idx]
        if param == "—": return
        # Fader de CC → control en vivo de cc_val en la lane activa
        if param == "CC":
            lane = t.active_cc_lane
            cc_num = t.cc_lanes[lane]['num']
            lbl = f"L{lane+1} CC{cc_num}" if cc_num >= 0 else f"L{lane+1} CC—"
            # Trig lock per-step si hay un pad mantenido
            if self.held_step is not None:
                _hs_ti, _hs_step = self.held_step
                _hs_tr = self.tracks[_hs_ti]
                if 0 <= _hs_step < len(_hs_tr.pattern):
                    lk = _hs_tr.step_locks.setdefault(_hs_step, {})
                    lk.setdefault('cc_vals', {})[lane] = val
                    if cc_num >= 0:
                        self._out(t).send_message([0xB0 | t.channel, cc_num, val])
                    self.held_knob_used = True
                    self.last_msg = f"T{_hs_ti+1} S{_hs_step+1} {lbl} → {val}"
                    if not self.bank_view:
                        self.view_mode = 'detail'
                        if self._detail_timer: self._detail_timer.cancel()
                        self._detail_timer = threading.Timer(2.5, self._to_page_view)
                        self._detail_timer.start()
                    _push_display(f"S{_hs_step+1} {lbl}", str(val),
                                  bar=val/127, track=self.active, bpm=int(self.bpm),
                                  playing=self.running,
                                  extra={'view_mode': 'detail',
                                         'label': f"S{_hs_step+1} {lbl}",
                                         'value': str(val)})
                    return
            t.cc_val = val
            if cc_num >= 0:
                self._out(t).send_message([0xB0 | t.channel, cc_num, val])
            # Grabar como p-lock si está en modo grabación
            if self.recording and self.running:
                step = t.display_step % max(len(t.pattern), 1)
                if step < len(t.pattern) and t.pattern[step]:
                    lk = t.step_locks.setdefault(step, {})
                    lk.setdefault('cc_vals', {})[lane] = val
            self.last_msg = f"T{self.active+1} {lbl} → {val}"
            self.view_mode = 'detail'
            if self._detail_timer: self._detail_timer.cancel()
            self._detail_timer = threading.Timer(2.5, self._to_page_view)
            self._detail_timer.start()
            _push_display(lbl, str(val), bar=val/127,
                          track=self.active, bpm=int(self.bpm), playing=self.running,
                          extra={'view_mode': 'detail', 'label': lbl, 'value': str(val)})
            return
        # Fader de RTCH → spread del ratchet (igual que knob RSPD)
        if param == "RTCH":
            spread = round(val / 127 * 2.0, 2)
            _spd_name = f"{int(spread*100)}%"
            if self.held_step is not None:
                _hs_ti, _hs_step = self.held_step
                _hs_tr = self.tracks[_hs_ti]
                if 0 <= _hs_step < len(_hs_tr.pattern):
                    _hs_tr.step_locks.setdefault(_hs_step, {})['ratchet_div'] = spread
                    self.held_knob_used = True
                    self.last_msg = f"T{_hs_ti+1} S{_hs_step+1} RSPD → {_spd_name}"
                    if not self.bank_view:
                        self.view_mode = 'detail'
                        if self._detail_timer: self._detail_timer.cancel()
                        self._detail_timer = threading.Timer(2.5, self._to_page_view)
                        self._detail_timer.start()
                    _push_display(f"S{_hs_step+1} RSPD", _spd_name, bar=spread,
                                  track=_hs_ti, bpm=int(self.bpm), playing=self.running,
                                  extra={'view_mode': 'detail',
                                         'label': f"S{_hs_step+1} RSPD", 'value': _spd_name})
                    return
            t.ratchet_div = spread
            self.last_msg = f"T{self.active+1} RSPD → {_spd_name}"
            self.view_mode = 'detail'
            if self._detail_timer: self._detail_timer.cancel()
            self._detail_timer = threading.Timer(2.5, self._to_page_view)
            self._detail_timer.start()
            _push_display("RSPD", _div_name, bar=val/127,
                          track=self.active, bpm=int(self.bpm), playing=self.running,
                          extra={'view_mode': 'detail', 'label': 'RSPD', 'value': _div_name})
            return
        # Fader de RESL → multiplier (cuando stoch off); estocástico cuando stoch on
        if param == "RESL" and not t.stoch_enabled.get(param, False):
            t.multiplier = int((val / 127) * (len(MULTIPLIERS) - 1))
            _tn = t.tick_interval(60.0 / self.bpm / 4.0) / (60.0 / self.bpm / 4.0)
            t.tick_acc = max(0.0, _tn - 1.0)
            name, _ = MULTIPLIERS[t.multiplier]
            if self.recording:
                self._rec_pending['multiplier'] = t.multiplier
            self.last_msg = f"T{self.active+1} Multi → {name}"
            self.view_mode = 'detail'
            if self._detail_timer: self._detail_timer.cancel()
            self._detail_timer = threading.Timer(2.5, self._to_page_view)
            self._detail_timer.start()
            _push_display("Multi", name, bar=val/127,
                          track=self.active, bpm=int(self.bpm), playing=self.running,
                          extra={'view_mode': 'detail', 'label': 'Multi', 'value': name})
            return
        t.stoch_amounts[param] = val / 127
        on = t.stoch_enabled.get(param, False)
        state = "ON" if on else "off"
        self.last_msg = f"T{self.active+1} \xb1{param} → {int(val/127*100)}%"
        self.view_mode = 'detail'
        if self._detail_timer: self._detail_timer.cancel()
        self._detail_timer = threading.Timer(2.5, self._to_page_view)
        self._detail_timer.start()
        _push_display(f"\xb1{param}", f"{int(val/127*100)}%", bar=val/127,
                      track=self.active, bpm=int(self.bpm), playing=self.running,
                      extra={'view_mode': 'detail',
                             'label': f"\xb1{param}", 'value': f"{int(val/127*100)}%"})

    def _handle_btn_m(self, page, idx, t, new_state: bool):
        """new_state viene directamente del val MIDI: True=ON (127), False=OFF (0)."""
        params = PAGE_PARAMS.get(page, [])
        if idx >= len(params): return
        param = params[idx]
        if param == "—": return
        # SCRI: toggle script on/off (no es estocástico — M hace toggle real)
        if param == "SCRI":
            if t.script_id and t.script_id in self.scripts_lib:
                # Estaba ON → apagar
                t.script_id = None
                self.last_msg = "SCRI → OFF"
            else:
                # Estaba OFF → encender con primer script disponible
                script_ids = list(self.scripts_lib.keys())
                if script_ids:
                    t.script_id = script_ids[0]
                    script_name = self.scripts_lib[t.script_id].get('name', t.script_id)
                    self.last_msg = f"SCRI → {script_name[:8].upper()}"
                else:
                    self.last_msg = "SCRI → NO SCRIPTS"
            _lbl, _val = ("SCRI", self.last_msg.split("→")[1].strip() if "→" in self.last_msg else self.last_msg)
            _push_display(_lbl, _val, track=self.active, bpm=int(self.bpm), playing=self.running,
                          extra={'view_mode': 'detail', 'label': _lbl, 'value': _val})
            return
        # CLK: toggle MIDI Clock Out (no es estocástico)
        if param == "CLK":
            self.clock_out = new_state
            state = "ON" if new_state else "OFF"
            self.last_msg = f"CLK OUT → {state}"
            return
        # HARM no es estocástico: el botón actúa como toggle del efecto harmony
        if param == "HARM":
            if t.harmony_on == new_state: return
            t.harmony_on = new_state
            # Si activamos sin source válido, elegimos por defecto otra pista
            # (la primera distinta a la activa) para que el efecto sea audible.
            if new_state and t.harmony_src < 0:
                t.harmony_src = 1 if self.active == 0 else 0
            state = "ON" if new_state else "OFF"
            src   = f"TRK{t.harmony_src+1}" if t.harmony_src >= 0 else "—"
            self.last_msg = f"T{self.active+1} HARM {state} ({src})"
            return
        current = t.stoch_enabled.get(param, False)
        if current == new_state: return           # sin cambio, ignorar
        t.stoch_enabled[param] = new_state
        if new_state:
            # Al activar: guardar valor actual como base de referencia
            base = self._stoch_param_get(t, param)
            if base is not None:
                t.stoch_base[param] = base
        else:
            # Al desactivar: mantener el valor actual (congelar la variación)
            # El valor que haya elegido el stoch se convierte en el nuevo valor real
            t.stoch_base.pop(param, None)
            # NOTE usa offset transitorio: limpiarlo al desactivar
            if param == "NOTE":
                t.note_offset = 0
        state = "ON" if new_state else "OFF"
        summary = self._stoch_summary(t)
        self.last_msg = f"T{self.active+1} ±{param} → {state}{summary}"

    def _stoch_summary(self, t):
        """Devuelve string con todos los params que tienen stoch activo en el track."""
        active = [p for p, en in t.stoch_enabled.items() if en]
        if not active:
            return ""
        return " ±[" + " ".join(active) + "]"

    def _randomize_param(self, page, idx, t):
        """Aleatorio moderado por parámetro — disparado por botones S del nanoKONTROL"""
        self._push_undo(force=True)
        n = self.active + 1
        params = PAGE_PARAMS.get(page, [])
        if idx >= len(params): return
        param = params[idx]

        if param == "PULS":
            t.pulses = random.randint(1, max(1, t.steps)); t.rebuild(); t.tonal_notes=[]
            self.last_msg = f"T{n} PULSE~ → {t.pulses}"
        elif param == "STEP":
            t.steps = random.choice([4,6,8,12,16,24,32]); t.pulses=min(t.pulses,t.steps)
            t.cursor=t.cursor%t.steps; t.rebuild(); t.tonal_notes=[]
            self.last_msg = f"T{n} Steps~ → {t.steps}"
        elif param == "PROB":
            t.prob = round(random.uniform(0.5,1.0),2)
            self.last_msg = f"T{n} Prob~ → {int(t.prob*100)}%"
        elif param == "MODE":
            t.play_mode = random.randint(0, len(PLAY_MODES)-1)
            self.last_msg = f"T{n} Mode~ → {PLAY_MODES[t.play_mode]}"
        elif param == "VEL":
            t.velocity = random.randint(60,127)
            self.last_msg = f"T{n} Vel~ → {t.velocity}"
        elif param == "SWNG":
            t.swing = round(random.uniform(0.0,0.5),2)
            self.last_msg = f"T{n} Swing~ → {int(t.swing*100)}%"
        elif param == "RESL":
            t.resolution = random.randint(0, len(RESOLUTIONS)-1)
            _tn = t.tick_interval(60.0 / self.bpm / 4.0) / (60.0 / self.bpm / 4.0)
            t.tick_acc = max(0.0, _tn - 1.0)
            name,_ = RESOLUTIONS[t.resolution]; self.last_msg = f"T{n} Resol~ → {name}"
        elif param == "ROTA":
            t.rotation = random.randint(0, max(0, t.steps-1)); t.rebuild()
            self.last_msg = f"T{n} ROTAT~ → {t.rotation}"
        elif param == "NOTE":
            t.root = random.randint(36,72)
            if t.tonal_notes:
                t.tonal_notes = _requantize(t.tonal_notes, t.scale_idx, t.root)
            self.last_msg = f"T{n} Note~ → {note_name(t.root)}"
        elif param == "SCAL":
            t.scale_idx = random.randint(0, len(SCALES)-1)
            name,_ = SCALES[t.scale_idx]
            if t.tonal_notes:
                t.tonal_notes = _requantize(t.tonal_notes, t.scale_idx, t.root)
            else:
                t.note_cursor = 0
            self.last_msg = f"T{n} SCALE~ → {name}"
        elif param == "OCT":
            t.octave = random.randint(-2,2); self.last_msg = f"T{n} OCT~ → {t.octave:+d}"
        elif param == "DENS":
            t.density = round(random.uniform(0.2,0.8),2)
            self.last_msg = f"T{n} DENS~ → {int(t.density*100)}%"
        elif param == "SPRD":
            t.spread = round(random.uniform(0.0,1.0),2)
            self.last_msg = f"T{n} SPRD~ → {int(t.spread*100)}%"
        elif param == "HARM":
            t.harmony_src = random.randint(-1,7)
            src = f"TRK{t.harmony_src+1}" if t.harmony_src>=0 else "off"
            self.last_msg = f"T{n} HARM~ → {src}"
        elif param == "INTV":
            t.interval = random.randint(1,7); t._scale_pos = 0; self.last_msg = f"T{n} INTV~ → {t.interval}°"
        elif param == "NLEN":
            t.note_len = random.randint(1,8); self.last_msg = f"T{n} NLEN~ → {t.note_len}"
        elif param == "DTIM":
            t.delay_steps = random.randint(1,8); self.last_msg = f"T{n} D.Time~ → {t.delay_steps}"
        elif param == "FDBK":
            t.delay_fb = round(random.uniform(0.2,0.8),2)
            self.last_msg = f"T{n} FDBK~ → {int(t.delay_fb*100)}%"
        elif param == "RDEC":
            t.ratchet_curve = round(random.uniform(-1.0, 1.0), 2)
            _rn = "F/O" if t.ratchet_curve < -0.1 else "F/I" if t.ratchet_curve > 0.1 else "---"
            self.last_msg = f"T{n} RDEC~ → {_rn} ({t.ratchet_curve:+.2f})"
        elif param == "RSPD":
            t.ratchet_div = round(random.uniform(0.1, 1.0), 2)
            self.last_msg = f"T{n} RSPD~ → {int(t.ratchet_div*100)}%"
        elif param == "RTCH":
            t.ratchet = random.randint(1,8); self.last_msg = f"T{n} Ratchet~ → {t.ratchet}x"
        elif param == "GATE":
            t.gate = round(random.uniform(0.1,0.9),2)
            self.last_msg = f"T{n} Gate~ → {int(t.gate*100)}%"
        elif param == "CC":
            t.cc_num = random.randint(0,127); self.last_msg = f"T{n} CC~ → {t.cc_num}"
        elif param == "MULT":
            t.multiplier = random.randint(0, len(MULTIPLIERS)-1)
            _tn = t.tick_interval(60.0 / self.bpm / 4.0) / (60.0 / self.bpm / 4.0)
            t.tick_acc = max(0.0, _tn - 1.0)
            name,_ = MULTIPLIERS[t.multiplier]; self.last_msg = f"T{n} Multi~ → {name}"

    def _reseed_tonal(self, t):
        """Regenera tonal_notes para los steps activos con la escala/raíz actuales.
        Reparte grados de escala aleatoriamente con un poco de variación octava."""
        _, intervals = SCALES[t.scale_idx]
        n_active = sum(1 for x in t.pattern if x)
        if n_active == 0:
            t.tonal_notes = []
            return
        max_idx = max(1, int(max(0.3, t.spread) * (len(intervals) * 2 - 1)))
        notes = []
        for _ in range(n_active):
            idx        = random.randint(0, max_idx)
            oct_offset = (idx // len(intervals)) * 12
            note       = t.root + intervals[idx % len(intervals)] + oct_offset
            notes.append(max(0, min(127, note)))
        t.tonal_notes = notes
        t.tonal_idx   = 0

    def _apply_stoch(self, structural=False):
        """Aplica variaciones estocásticas a los parámetros habilitados.
        amount = magnitud pura (sin probabilidad interna). El usuario controla la
        densidad de cambios con PROB de la pista (steps que no firen no varían audio).
        Estructurales (PULS/ROTA/RESL/MULT) solo en wrap del master.
        """
        STRUCTURAL = {"PULS", "STEP", "ROTA", "RESL", "MULT"}
        for t in self.tracks:
            for param, enabled in list(t.stoch_enabled.items()):
                if not enabled: continue
                is_struct = param in STRUCTURAL
                if structural != is_struct: continue
                amount = t.stoch_amounts.get(param, 0.0)
                if amount <= 0: continue
                base = t.stoch_base.get(param)
                if base is None: continue

                d = random.uniform(-1.0, 1.0) * amount

                if param == "STEP":
                    new = max(4, min(64, round(base + d * 32)))
                    if new != t.steps:
                        t.steps = new
                        t.pulses = min(t.pulses, t.steps)
                        t.cursor = t.cursor % t.steps
                        t.rebuild()
                elif param == "PULS":
                    new = max(1, min(t.steps, round(base + d * t.steps)))
                    if new != t.pulses:
                        t.pulses = new; t.rebuild()
                elif param == "PROB":
                    t.prob = max(0.0, min(1.0, base + d * 1.0))
                elif param == "VEL":
                    t.velocity = max(1, min(127, round(base + d * 100)))
                elif param == "SWNG":
                    t.swing = max(0.0, min(0.7, base + d * 0.7))
                elif param == "ROTA":
                    new = round(base + d * max(1, t.steps)) % max(1, t.steps)
                    if new != t.rotation:
                        t.rotation = new; t.rebuild()
                elif param == "NOTE":
                    # Offset en GRADOS DE ESCALA (no semitonos cromáticos) →
                    # cuando _tick_track aplica el shift y luego cuantiza, las notas
                    # caen siempre dentro de la escala actual.
                    _, intervals = SCALES[t.scale_idx]
                    n_per_oct = len(intervals)
                    max_deg   = n_per_oct * 2          # ±2 octavas máximo
                    deg       = int(round(d * max_deg))
                    sign      = -1 if deg < 0 else 1
                    abs_deg   = abs(deg)
                    octs      = abs_deg // n_per_oct
                    in_oct    = abs_deg % n_per_oct
                    t.note_offset = sign * (octs * 12 + intervals[in_oct])
                elif param == "SCAL":
                    # Salto a otra escala aleatoria + regenera notas tonales
                    n_scales = len(SCALES)
                    new_scale = (int(base) + random.randint(1, n_scales-1)) % n_scales
                    t.scale_idx = new_scale
                    self._reseed_tonal(t)
                elif param == "OCT":
                    t.octave = max(-3, min(3, round(base + d * 3)))
                elif param == "SPRD":
                    t.spread = max(0.0, min(1.0, base + d * 1.0))
                elif param == "GATE":
                    t.gate = max(0.05, min(0.99, base + d * 0.94))
                elif param == "RDEC":
                    t.ratchet_curve = max(-1.0, min(1.0, base + d * 2.0))
                elif param == "RTCH":
                    t.ratchet = max(1, min(16, round(base + d * 15)))
                elif param == "DENS":
                    t.density = max(0.0, min(1.0, base + d * 1.0))
                elif param == "INTV":
                    t.interval = max(0, min(7, round(base + d * 7))); t._scale_pos = 0
                elif param == "NLEN":
                    t.note_len = max(1, min(16, round(base + d * 8)))
                elif param == "HARM":
                    # Salta a otro source aleatorio (-1 = off, 0..7 = pista)
                    if random.random() < 0.3:  # 30% off, resto pista
                        t.harmony_src = -1
                    else:
                        t.harmony_src = random.randint(0, 7)
                elif param == "DTIM":
                    t.delay_steps = max(1, min(16, round(base + d * 8)))
                elif param == "FDBK":
                    t.delay_fb = max(0.0, min(0.95, base + d * 0.9))
                elif param == "RSPD":
                    t.ratchet_div = max(0.0, min(2.0, round(base + d * 2.0, 2)))

    # ── Automation continua ──────────────────────────────────────────────────
    def _track_phase(self, t, base_interval):
        """Fase actual del ciclo de la pista (0.0–1.0)."""
        cycle_dur = len(t.pattern) * t.tick_interval(base_interval)
        if cycle_dur <= 0:
            return 0.0
        elapsed = time.perf_counter() - t._cycle_start
        return (elapsed / cycle_dur) % 1.0

    def _record_cc_to_lane(self, t, cc_num, cc_val):
        """Graba un CC en la lane correspondiente como step lock. Auto-asigna lane libre si no existe."""
        # Buscar lane existente con este CC number
        lane_idx = None
        for li, lane in enumerate(t.cc_lanes):
            if lane['num'] == cc_num:
                lane_idx = li
                break
        # Si no existe, buscar primera lane libre
        if lane_idx is None:
            for li, lane in enumerate(t.cc_lanes):
                if lane['num'] < 0:
                    lane_idx = li
                    t.cc_lanes[li]['num'] = cc_num
                    break
        if lane_idx is None:
            return  # todas las lanes ocupadas
        # Escribir en step_lock del step actual
        step = t.display_step % max(len(t.pattern), 1)
        lk = t.step_locks.setdefault(step, {})
        lk.setdefault('cc_vals', {})[lane_idx] = cc_val
        # Actualizar valor actual de la lane
        t.cc_lanes[lane_idx]['val'] = cc_val

    # NOTE en auto lanes = desplazamiento en semitonos (-24..+24), 64=centro=0
    _AUTO_RANGES = {
        'NOTE': (-24, 24), 'VEL': (1, 127), 'PROB': (0.0, 1.0),
        'STEP': (4, 64), 'PULS': (0, 64), 'ROTA': (0, 63),
        'SWNG': (0.0, 0.7), 'GATE': (0.05, 0.99), 'RDEC': (-1.0, 1.0),
        'RTCH': (1, 16), 'DENS': (0.0, 1.0), 'INTV': (0, 7),
        'OCT': (-3, 3), 'NLEN': (1, 16), 'DTIM': (1, 16),
        'FDBK': (0.0, 0.95), 'RSPD': (0.0, 2.0),
        'RESL': (0, 10), 'MULT': (0, 5),
    }

    def _auto_norm(self, param, val):
        """Convert param value to 0-127 for display."""
        lo, hi = self._AUTO_RANGES.get(param, (0, 127))
        if hi == lo: return 64
        return int(round((val - lo) / (hi - lo) * 127))

    def _auto_denorm(self, param, display_val):
        """Convert 0-127 display value back to param value."""
        lo, hi = self._AUTO_RANGES.get(param, (0, 127))
        val = lo + (display_val / 127) * (hi - lo)
        if isinstance(lo, int) and isinstance(hi, int):
            val = int(round(val))
        return val

    def _record_auto(self, track_idx, param, value):
        """Graba un punto de automation en la pista activa (fase continua).
        Si ya existe automation para ese param, la sustituye (overdub)."""
        if not self.running or not self.recording:
            return
        t = self.tracks[track_idx]
        base_interval = 60 / self.bpm / 4
        phase = self._track_phase(t, base_interval)
        if param not in t.auto_lanes:
            t.auto_lanes[param] = []
        from bisect import insort
        lane = t.auto_lanes[param]
        insort(lane, (phase, value))
        # Limitar densidad: max 256 puntos por lane (downsample quedando cada 2º)
        if len(lane) > 256:
            t.auto_lanes[param] = lane[::2]
        # Invalidar cache de fases
        if not hasattr(t, '_auto_phases'):
            t._auto_phases = {}
        t._auto_phases.pop(param, None)
        # Auto-register a param lane in cc_lanes for visualization
        has_lane = any(l.get('param') == param for l in t.cc_lanes)
        if not has_lane:
            # Find free lane (num == -1 and no param)
            for li, lane in enumerate(t.cc_lanes):
                if lane['num'] < 0 and 'param' not in lane:
                    t.cc_lanes[li] = {'num': -1, 'val': 64, 'param': param}
                    break

    def _clear_auto(self, track_idx, param=None):
        """Borra automation. Si param=None borra todas las lanes de la pista."""
        t = self.tracks[track_idx]
        if param:
            t.auto_lanes.pop(param, None)
            if hasattr(t, '_auto_phases'):
                t._auto_phases.pop(param, None)
            if hasattr(t, '_auto_last'):
                t._auto_last.pop(param, None)
            self.last_msg = f"T{track_idx+1} AUTO {param} cleared"
        else:
            t.auto_lanes.clear()
            if hasattr(t, '_auto_phases'):
                t._auto_phases.clear()
            if hasattr(t, '_auto_last'):
                t._auto_last.clear()
            self.last_msg = f"T{track_idx+1} AUTO ALL cleared"

    _AUTO_INT_PARAMS = frozenset(("PULS","STEP","VEL","ROTA","RTCH","OCT",
                                  "NLEN","DTIM","HARM","RESL","MULT","NOTE",
                                  "PROG","BANK"))
    # Params que NO interpolan — saltan al valor más cercano (step function)
    _AUTO_STEP_PARAMS = frozenset(("PROG","BANK","RESL","MULT","MODE"))
    _bisect_right = __import__('bisect').bisect_right

    def _apply_auto(self, base_interval):
        """Aplica automation continua: interpola entre puntos grabados.
        Optimizado: fases pre-cacheadas + skip si valor no cambió."""
        now = time.perf_counter()
        _t0 = now
        for t in self.tracks:
            if not t.auto_play or not t.auto_lanes:
                continue
            # Fase: cálculo directo (evitar llamada a método)
            cycle_dur = len(t.pattern) * t.tick_interval(base_interval)
            if cycle_dur <= 0:
                continue
            phase = ((now - t._cycle_start) / cycle_dur) % 1.0
            if not hasattr(t, '_auto_last'):
                t._auto_last = {}
            if not hasattr(t, '_auto_phases'):
                t._auto_phases = {}
            for param, points in t.auto_lanes.items():
                if not points:
                    continue
                n = len(points)
                if n == 1:
                    val = points[0][1]
                else:
                    # Cache de lista de fases (se invalida al grabar)
                    phases = t._auto_phases.get(param)
                    if phases is None or len(phases) != n:
                        phases = [p[0] for p in points]
                        t._auto_phases[param] = phases
                    idx_after = self._bisect_right(phases, phase) % n
                    idx_before = (idx_after - 1) % n
                    if param in self._AUTO_STEP_PARAMS:
                        # Step function: usar el punto anterior (no interpolar)
                        val = points[idx_before][1]
                    else:
                        ph_a, v_a = points[idx_before]
                        ph_b, v_b = points[idx_after]
                        gap = (ph_b - ph_a) % 1.0
                        if gap < 1e-9:
                            val = v_b
                        else:
                            val = v_a + (v_b - v_a) * (((phase - ph_a) % 1.0) / gap)
                # Redondear params enteros
                if param in self._AUTO_INT_PARAMS:
                    val = int(round(val))
                # Solo aplicar si cambió
                if t._auto_last.get(param) == val:
                    continue
                t._auto_last[param] = val
                self._stoch_param_set(t, param, val)
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        if _elapsed_ms > 2.0:
            _lanes = {p: len(pts) for t in self.tracks for p, pts in t.auto_lanes.items() if pts}
            print(f"[AUTO SLOW] {_elapsed_ms:.1f}ms  lanes={_lanes}")

    # ── Undo / Redo ───────────────────────────────────────────────────────────
    def _push_undo(self, force=False):
        """Guarda snapshot del estado actual. force=True ignora debounce (acciones discretas).
           force=False: debounce 800ms para movimientos de knob continuos."""
        now = time.time()
        if not force and now - self._last_undo_push < 0.8:
            return
        self._last_undo_push = now
        snapshot = [copy.deepcopy(t.__dict__) for t in self.tracks]
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    # Campos de playback que NO deben restaurarse con undo/redo
    _UNDO_SKIP = {'cursor', 'play_cursor', 'note_cursor', 'note_dir',
                   'tick_acc', 'display_step', 'even_step', 'last_trigger_time'}

    def _undo(self):
        if not self.undo_stack:
            self.last_msg = "Undo: nada que deshacer"
            self._render()
            return
        # Guarda estado actual en redo antes de deshacer
        self.redo_stack.append([copy.deepcopy(t.__dict__) for t in self.tracks])
        snapshot = self.undo_stack.pop()
        for t, state in zip(self.tracks, snapshot):
            saved = {k: t.__dict__[k] for k in self._UNDO_SKIP if k in t.__dict__}
            t.__dict__.update(state)
            t.__dict__.update(saved)
        self.last_msg = f"↩ Undo  ({len(self.undo_stack)} restantes)"
        self._render()

    def _redo(self):
        if not self.redo_stack:
            self.last_msg = "Redo: nada que rehacer"
            self._render()
            return
        self.undo_stack.append([copy.deepcopy(t.__dict__) for t in self.tracks])
        snapshot = self.redo_stack.pop()
        for t, state in zip(self.tracks, snapshot):
            saved = {k: t.__dict__[k] for k in self._UNDO_SKIP if k in t.__dict__}
            t.__dict__.update(state)
            t.__dict__.update(saved)
        self.last_msg = f"↪ Redo  ({len(self.redo_stack)} restantes)"
        self._render()

    def _send_program(self, t):
        """Envía Bank Select (CC 0) + Program Change al canal de la pista."""
        if not t.send_pc:
            return
        ch = t.channel
        self._out(t).send_message([0xB0 | ch, 0, t.bank_msb])
        self._out(t).send_message([0xC0 | ch,    t.program])
        self._last_pc_sent[(t.port, ch)] = (t.bank_msb, t.program)

    def _silence_track(self, idx):
        """All Notes Off para una pista específica."""
        t = self.tracks[idx]
        self._out(t).send_message([0xB0 | t.channel, 123, 0])

    def _silence_all_tracks(self):
        """All Notes Off a todos los canales activos (para limpiar notas de timers en vuelo)."""
        seen = set()
        for t in self.tracks:
            ch = t.channel
            key = (t.port, ch)
            if key not in seen:
                seen.add(key)
                self._out(t).send_message([0xB0 | ch, 123, 0])  # All Notes Off

    def _send_program_debounced(self, track_idx, delay=0.6):
        """Cancela el timer anterior y programa un nuevo envío tras 'delay' segundos."""
        old = self._prog_timers.get(track_idx)
        if old:
            old.cancel()
        t = self.tracks[track_idx]
        timer = threading.Timer(delay, self._send_program, args=[t])
        timer.daemon = True
        timer.start()
        self._prog_timers[track_idx] = timer

    def _mirror(self, msg):
        """Copia el mensaje al puerto IAC para grabación en DAW."""
        if self.daw_mirror and self.clk_out.is_port_open():
            self.clk_out.send_message(msg)

    def _note_on(self, channel, note, velocity, gen=None, port=0):
        if not self.running:
            return   # ignora timers en vuelo tras parar
        if gen is not None and gen != self._note_gen:
            return   # patrón cambió desde que se programó este timer
        note = max(0, min(127, note))
        msg = [0x90 | channel, note, velocity]
        out = self.midi_outs[port] if port < len(self.midi_outs) else self.midi_out
        out.send_message(msg)
        self._mirror(msg)

    def _note_off(self, channel, note, port=0, gen=None):
        # Si el patrón cambió desde que se programó este timer, no cortes
        # notas nuevas que puedan estar ya sonando en la misma pitch/canal.
        if gen is not None and gen != self._note_gen:
            return
        note = max(0, min(127, note))
        msg = [0x80 | channel, note, 0]
        out = self.midi_outs[port] if port < len(self.midi_outs) else self.midi_out
        out.send_message(msg)
        self._mirror(msg)

    def _nk_enable_leds(self):
        """Activa modo LED externo en nanoKONTROL 1 via SysEx."""
        if not self.nk_out.is_port_open():
            return
        # Korg nanoKONTROL 1: F0 42 40 00 01 04 00 1F 12 00 F7
        self.nk_out.send_message([0xF0, 0x42, 0x40, 0x00, 0x01, 0x04,
                                   0x00, 0x1F, 0x12, 0x00, 0xF7])

    def _nk_led(self, cc, on):
        """Enciende (on=True) o apaga (on=False) un LED del nanoKONTROL via CC."""
        if not self.nk_out.is_port_open():
            return
        self.nk_out.send_message([0xB0, cc, 127 if on else 0])

    def _nk_refresh_page_leds(self, delay=0.0):
        """Reenvía el estado de los LEDs de botones M para la página activa.
        Si delay>0, lo hace en dos pasadas (300 ms y 500 ms) para superar el tiempo
        que tarda el nanoKONTROL en inicializar la nueva escena; también re-activa
        el modo LED externo porque el dispositivo lo pierde al cambiar de escena."""
        def _send():
            if not self.nk_out.is_port_open():
                return
            # Re-activar modo LED externo (el nano lo pierde al cambiar de escena)
            self._nk_enable_leds()
            page   = self.page
            t      = self.tracks[self.active]
            params = PAGE_PARAMS.get(page, [])
            for idx, param in enumerate(params):
                cc  = NK_BTN_M_BASE + idx
                if param == 'HARM':
                    on = t.harmony_on
                else:
                    on = bool(t.stoch_enabled.get(param, False))
                self.nk_out.send_message([0xB0, cc, 127 if on else 0])
            self._nk_led(44, self.recording)
        if delay > 0:
            threading.Timer(0.30, _send).start()   # primer intento a 300 ms
            threading.Timer(0.55, _send).start()   # segundo intento a 550 ms
        else:
            _send()

    def _set_pad(self, note, color):
        if self._led_cache.get(('n', note)) != color:
            self._led_cache[('n', note)] = color
            self.lp_out.send_message([0x90, note, color])

    def _set_top(self, cc, color):
        if self._led_cache.get(('c', cc)) != color:
            self._led_cache[('c', cc)] = color
            self.lp_out.send_message([0xB0, cc, color])

    def _update_leds(self):
        blink = (self.tick_count % 8) < 4

        # ── Fila superior ──────────────────────────────────────────────────
        self._set_top(LP_TRACK_DN,  LP_DIM)
        self._set_top(LP_TRACK_UP,  LP_DIM)
        self._set_top(LP_STEP_DN,   LP_DIM if self.step_pg > 0 else LP_OFF)
        self._set_top(LP_STEP_UP,   LP_DIM)
        self._set_top(LP_SHIFT_CC,   LP_RED   if self.shift       else LP_OFF)
        self._set_top(LP_COPY_CC,   LP_GREEN if self.clipboard_step else (LP_AMBER if self.copy_mode else LP_OFF))
        self._set_top(LP_DELETE_CC, LP_RED   if self.delete_mode   else LP_OFF)
        self._set_top(LP_BANK_VIEW, LP_RED if self.cc_draw else (LP_AMBER if self.bank_view else LP_DIM))

        if self.cc_draw:
            # ── CC DRAW: columnas=steps, filas=niveles CC (bar graph) ────────
            t = self.tracks[self.active]
            lane = t.active_cc_lane
            lane_cfg = t.cc_lanes[lane]
            cc_num = lane_cfg['num']
            param  = lane_cfg.get('param')
            default_val = lane_cfg['val']
            page_start = self.step_pg * 8
            base_interval = 60 / self.bpm / 4
            cycle_dur = len(t.pattern) * t.tick_interval(base_interval)
            for col in range(8):
                step = page_start + col
                if step >= len(t.pattern):
                    for row in range(8):
                        self._set_pad(lp_grid(row, col), LP_OFF)
                    continue
                if param:
                    # Param lane: sample auto_lanes at this step's phase
                    points = t.auto_lanes.get(param, [])
                    if points and cycle_dur > 0:
                        step_phase = (step + 0.5) / len(t.pattern)
                        from bisect import bisect_right
                        phases = [p[0] for p in points]
                        n = len(points)
                        idx_after = bisect_right(phases, step_phase) % n
                        idx_before = (idx_after - 1) % n
                        ph_a, v_a = points[idx_before]
                        ph_b, v_b = points[idx_after]
                        gap = (ph_b - ph_a) % 1.0
                        if gap < 1e-9 or n == 1:
                            raw_val = points[idx_before][1]
                        else:
                            frac = ((step_phase - ph_a) % 1.0) / gap
                            raw_val = v_a + (v_b - v_a) * frac
                        cc_val = max(0, min(127, self._auto_norm(param, raw_val)))
                    else:
                        cc_val = default_val
                else:
                    # CC lane: read from step_locks
                    lk = t.step_locks.get(step, {})
                    cc_vals = lk.get('cc_vals', {})
                    cc_val = cc_vals.get(lane, default_val if cc_num >= 0 else 0)
                level  = round(cc_val * 7 / 127)
                is_cursor = (step == t.display_step) and self.running
                for row in range(8):
                    bar_pos = 7 - row
                    if bar_pos == level:
                        color = LP_GREEN if is_cursor else LP_AMBER
                    elif bar_pos < level:
                        color = LP_DIM
                    else:
                        color = LP_OFF
                    self._set_pad(lp_grid(row, col), color)
            # Col derecha: lanes
            for li in range(8):
                right = lp_right(li)
                lane_i = t.cc_lanes[li]
                has_cc  = lane_i['num'] >= 0
                has_param = 'param' in lane_i
                if li == lane:
                    color = LP_AMBER
                elif has_param:
                    color = LP_RED   # param lanes en rojo
                elif has_cc:
                    color = LP_GREEN
                else:
                    color = LP_OFF
                self._set_pad(right, color)

        elif self.bank_view:
            # ── BANK BROWSER: cada pad = slot en el banco actual (row*8+col) ──
            b = self.banks[self.current_bank]
            chain_slots = {slot for (bank, slot) in self.chain if bank == self.current_bank}
            chain_current = self.chain[self.chain_pos] if self.chain else None
            for row in range(8):
                for col in range(8):
                    slot = row * 8 + col
                    note = lp_grid(row, col)
                    bi = self._blink_info
                    if bi and self.current_bank == bi[0] and slot == bi[1] and time.time() < bi[2]:
                        color = LP_AMBER if int(time.time() * 10) % 2 == 0 else LP_OFF
                    elif slot in b:
                        is_loaded = (slot == self.active_slot)
                        is_pending = (self.pending_load == slot)
                        is_chain_current = (chain_current == (self.current_bank, slot))
                        is_chain = slot in chain_slots
                        is_xfade_b = (self.xfade_snap and
                                      self.xfade_snap_a is not None and
                                      self.xfade_label == f"B{self.current_bank+1}.{slot+1}")
                        if is_xfade_b:
                            color = LP_RED if blink else LP_OFF
                        elif is_pending and not is_loaded:
                            color = LP_RED   # en cola para cargar: rojo fijo
                        elif is_chain_current or is_loaded:
                            color = LP_AMBER if blink else LP_OFF
                        elif is_chain:
                            color = LP_AMBER
                        else:
                            color = LP_GREEN
                    else:
                        color = LP_OFF
                    self._set_pad(note, color)
                # Col derecha: siempre tenue en bank view
                right = lp_right(row)
                self._set_pad(right, LP_DIM)
        else:
            # ── VISTA STEPS: fila=pista, col=step ─────────────────────────
            page_start = self.step_pg * 8
            for ti in range(8):
                t      = self.tracks[ti]
                is_sel = (ti == self.active)
                for col in range(8):
                    step = page_start + col
                    note = lp_grid(ti, col)
                    if step >= len(t.pattern):
                        color = LP_OFF
                    else:
                        is_cursor = is_sel and (step == t.display_step)
                        is_active = t.pattern[step]
                        has_lock   = bool(t.step_locks.get(step))
                        is_held    = (self.held_step == (ti, step))
                        is_empty_track = (t.pulses == 0 and not any(t.pattern))
                        if is_held:
                            color = LP_GREEN if blink else LP_AMBER
                        elif t.muted:
                            color = LP_DRED if is_active else LP_OFF
                        elif is_cursor:
                            color = LP_AMBER
                        elif is_active:
                            color = LP_RED   if has_lock else LP_GREEN
                        elif has_lock:
                            color = LP_DRED              # paso inactivo con lock
                        elif is_sel and is_empty_track:
                            color = LP_DIM               # track activo vacío: pads tenues para indicar interactividad
                        else:
                            color = LP_OFF
                    self._set_pad(note, color)
                # Col derecha: estado de pista
                right = lp_right(ti)
                if is_sel:
                    color = LP_AMBER                     # seleccionado: siempre encendido (no parpadeo confuso)
                elif t.muted:
                    color = LP_RED
                elif t.pulses > 0:
                    color = LP_GREEN
                else:
                    color = LP_DIM
                self._set_pad(right, color)

    def _tick_track(self, t, base_interval, i=0, tick_inc=1.0):
        """Dispara UN solo step si tick_acc ha acumulado suficiente.
        tick_inc es la fracción de tick-base (1/16) que aporta cada llamada del loop."""
        pat_len = len(t.pattern)
        if pat_len == 0 or (t.pulses == 0 and not any(t.pattern)):
            t.tick_acc += tick_inc
            ticks_needed = t.tick_interval(base_interval) / base_interval
            if t.tick_acc < ticks_needed:
                return False
            t.tick_acc -= ticks_needed
            t.display_step = t.cursor % max(pat_len, 1)
            t.cursor = (t.cursor + 1) % max(pat_len, 1)
            t.firing = False
            # Si completó una vuelta (cursor volvió a 0), ejecutar script para mutación automática
            if t.cursor == 0:
                t.loop_count += 1
                t.execute_script()
                return True
            return False
        t.tick_acc += tick_inc
        ticks_needed = t.tick_interval(base_interval) / base_interval
        if t.tick_acc < ticks_needed:
            return False

        # Consumir exactamente un step
        t.tick_acc -= ticks_needed
        step_dur = ticks_needed * base_interval
        wrapped  = False

        mode = PLAY_MODES[t.play_mode]
        pos  = t.cursor % pat_len
        if mode == "forward":
            step = pos
        elif mode == "reverse":
            step = pat_len - 1 - pos
        elif mode == "bounce":
            cycle = (pat_len * 2 - 2) if pat_len > 1 else 1
            t.play_cursor = t.play_cursor % cycle
            p = t.play_cursor
            step = p if p < pat_len else cycle - p
            t.play_cursor = (t.play_cursor + 1) % cycle
        elif mode == "random":
            step = random.randint(0, pat_len - 1)
        elif mode == "snake":
            cycle = pat_len * 2
            t.play_cursor = t.play_cursor % cycle
            p = t.play_cursor
            step = p if p < pat_len else cycle - 1 - p
            t.play_cursor = (t.play_cursor + 1) % cycle
        elif mode == "drunk":
            t.play_cursor = max(0, min(pat_len-1,
                t.play_cursor + random.choice([-1, 0, 0, 1, 1])))
            step = t.play_cursor
        else:
            step = pos
        t.display_step  = step
        now_pc = time.perf_counter()
        t.last_fire_time = now_pc
        t.fire_history.append((now_pc, step))
        if len(t.fire_history) > 6:
            t.fire_history.pop(0)
        active   = t.pattern[step]
        # Crossfade: blend probabilístico usando snapshots A y B del sequencer
        if self.xfade_snap and self.xfade_snap_a and self.xfade_amt > 0:
            pat_a  = self.xfade_snap_a[i].get('pattern', t.pattern)
            pat_b  = self.xfade_snap[i].get('pattern', [])
            step_a = pat_a[step % len(pat_a)] if pat_a else active
            step_b = pat_b[step % len(pat_b)] if pat_b else False
            if self.xfade_amt >= 1:
                active = step_b
            else:
                blend  = (1 - self.xfade_amt) * float(step_a) + self.xfade_amt * float(step_b)
                active = random.random() < blend
        t.firing = active and not t.muted
        # Grabación en tiempo real: escribir valores pendientes en el step actual
        if self.recording and self._rec_pending and i == self.active:
            if step < len(t.pattern):
                locks = t.step_locks.setdefault(step, {})
                for p, v in self._rec_pending.items():
                    if p.startswith('_cc_'):
                        lane_idx = int(p[4:])
                        locks.setdefault('cc_vals', {})[lane_idx] = v
                    else:
                        locks[p] = v
        # Trig locks — sobreescriben los valores de la pista
        lk       = t.step_locks.get(step, {})
        # Resolution / Multiplier p-lock: cambia la resolución de la pista desde este step
        if 'resolution' in lk:
            t.resolution = lk['resolution']
            _tn = t.tick_interval(base_interval) / base_interval
            t.tick_acc = max(0.0, _tn - 1.0)
        if 'multiplier' in lk:
            t.multiplier = lk['multiplier']
            _tn = t.tick_interval(base_interval) / base_interval
            t.tick_acc = max(0.0, _tn - 1.0)
        # Program Change p-lock: se envía aunque el step no dispare nota
        if t.send_pc and ('program' in lk or 'bank_msb' in lk) and not t.muted:
            pc_bank = lk.get('bank_msb', t.bank_msb)
            pc_prog = lk.get('program',  t.program)
            m1 = [0xB0 | t.channel, 0, pc_bank]
            m2 = [0xC0 | t.channel, pc_prog]
            self._out(t).send_message(m1)
            self._out(t).send_message(m2)
            self._mirror(m1)
            self._mirror(m2)
        # CC / Pitch Bend / Pressure: siempre que el cursor pase
        if not t.muted:
            cc_vals = lk.get('cc_vals', {})
            for lane_idx, cv in cc_vals.items():
                ln = t.cc_lanes[int(lane_idx)]
                if ln['num'] >= 0:
                    m = [0xB0 | t.channel, ln['num'], cv]
                    self._out(t).send_message(m)
                    self._mirror(m)
            if 'pitch_bend' in lk:
                pb = lk['pitch_bend']
                m = [0xE0 | t.channel, pb & 0x7F, (pb >> 7) & 0x7F]
                self._out(t).send_message(m)
                self._mirror(m)
            if 'pressure' in lk:
                m = [0xD0 | t.channel, lk['pressure']]
                self._out(t).send_message(m)
                self._mirror(m)
        step_prob = lk.get('prob', t.prob)
        if active and not t.muted and random.random() < step_prob:
            # Notas del step: chord (lista) o nota única
            if 'notes' in lk:
                chord = [int(n) for n in lk['notes']]
            elif 'note' in lk:
                chord = [int(lk['note'])]
            elif 'root' in lk:
                # Root p-lock: genera desde una raíz fija pero respetando escala y spread
                _saved_root = t.root
                t.root = lk['root']
                chord = [t.current_note()]
                t.root = _saved_root
            elif self.xfade_snap and self.xfade_amt > 0:
                notes_b = self.xfade_snap[i].get('tonal_notes', [])
                if notes_b and random.random() < self.xfade_amt:
                    chord = [notes_b[step % len(notes_b)]]
                else:
                    chord = [t.current_note()]
            else:
                chord = [t.current_note()]
            # Escala efectiva: si sigue a otra pista adopta su escala y root
            if t.harmony_src >= 0 and t.harmony_src < len(self.tracks):
                _src = self.tracks[t.harmony_src]
                eff_scale = _src.scale_idx
                eff_root  = _src.root
                # Si el p-lock tiene harm_root (nota grabada con teclado), transponer
                # proporcionalmente al cambio de root del source antes de hacer snap.
                # Esto asegura que las notas grabadas sigan el harmony igual que las generadas.
                _rec_root = lk.get('harm_root')
                if _rec_root is not None:
                    _delta = eff_root - _rec_root
                    chord = [max(0, min(127, n + _delta)) for n in chord]
                # Snap a escala del source (notas generadas y teclado ya transpuesto)
                chord = [_quantize_to_scale(n, eff_scale, eff_root) for n in chord]
            else:
                eff_scale = t.scale_idx
                eff_root  = t.root
            # DENS: voces de acorde en tiempo real (0=1 voz, 1=4 voces)
            if t.density > 0 and not lk.get('notes'):
                n_voices = 1 + int(t.density * 3)  # 1-4 voces
                base_note = chord[0]
                for v in range(1, n_voices):
                    extra = _scale_degree_shift(base_note, v * 2, eff_scale, eff_root)
                    chord.append(max(0, min(127, extra)))
            # Transposición global de la pista: octava + note_offset (NOTE stoch).
            shift = t.octave * 12 + t.note_offset
            if shift:
                chord = [max(0, min(127, n + shift)) for n in chord]
            if t.stoch_enabled.get('NOTE') and t.note_offset != 0:
                chord = [_quantize_to_scale(n, eff_scale, eff_root) for n in chord]
            note = chord[0]
            humanize = int(random.uniform(-5, 5) * t.humanize)
            vel      = max(1, min(127, lk.get('vel', t.velocity) + humanize))
            gate         = lk.get('gate',         t.gate)
            ratchet      = lk.get('ratchet',     t.ratchet)
            spread       = max(0.0, min(2.0, float(lk.get('ratchet_div', t.ratchet_div))))
            note_len     = lk.get('note_len',    t.note_len)
            note_dur     = step_dur * note_len
            micro        = lk.get('micro_time', 0.0)
            swing_offset = (t.swing * step_dur * 0.5 if t.even_step else 0.0) + micro * step_dur
            swing_offset = max(0.0, swing_offset)
            # Intervalo entre hits: fracción del step según spread (0%=flam, 100%=equidistante)
            # Con spread=1.0 y N hits: hits en 0, step/N, 2*step/N … (N-1)*step/N → todos dentro
            if ratchet > 1 and spread > 0.0:
                ratchet_interval = step_dur * spread / ratchet
            else:
                ratchet_interval = 0.0
            ratchet_curve    = lk.get('ratchet_curve', t.ratchet_curve)
            # Dedup: density/harmony/quantize pueden colapsar voces a la misma pitch
            # → evita doble note-on (y doble note-off) por voz duplicada.
            chord = list(dict.fromkeys(chord))
            _gen = self._note_gen
            _port = t.port
            for r in range(ratchet):
                delay = swing_offset + r * ratchet_interval
                # Envolvente de velocidad: -1=fade out · 0=plano · +1=fade in
                if ratchet <= 1 or ratchet_curve == 0.0:
                    hit_vel = vel
                else:
                    t_r = r / (ratchet - 1)          # 0.0 … 1.0 a lo largo de los hits
                    if ratchet_curve > 0:             # fade in: crece de quieto a fuerte
                        factor = 1.0 - ratchet_curve * (1.0 - t_r)
                    else:                             # fade out: decrece de fuerte a quieto
                        factor = 1.0 + ratchet_curve * t_r
                    hit_vel = max(1, min(127, int(vel * max(0.0, factor))))
                for cn in chord:
                    threading.Timer(delay, self._note_on,
                        args=[t.channel, cn, hit_vel, _gen, _port]).start()
                    threading.Timer(delay + note_dur * gate / ratchet,
                        self._note_off, args=[t.channel, cn, _port, _gen]).start()
            if t.delay_on or 'delay_steps' in lk:
                d_steps = lk.get('delay_steps', t.delay_steps)
                d_fb    = lk.get('delay_fb',    t.delay_fb)
                delay_time = swing_offset + step_dur * d_steps
                fb_vel     = max(1, int(vel * d_fb))
                threading.Timer(delay_time, self._note_on,
                    args=[t.channel, note, fb_vel, _gen, _port]).start()
                threading.Timer(delay_time + note_dur * gate,
                    self._note_off, args=[t.channel, note, _port, _gen]).start()
            t.advance_note()
        t.cursor    = (t.cursor + 1) % pat_len
        t.even_step = not t.even_step
        if t.cursor == 0:
            wrapped = True
            t._cycle_start = time.perf_counter()
            # Ejecutar script para mutación automática cada vuelta
            t.loop_count += 1
            t.execute_script()
        return wrapped

    def _export_midi_chain(self):
        """Exporta self.chain como MIDI Type-1 multi-track. Devuelve bytes o None."""
        if not self.chain:
            return None
        TPB = 480          # ticks per beat (quarter note)
        T16 = TPB // 4    # ticks per 1/16th = 120

        tempo_trk  = []                      # track 0: tempo meta events
        note_trks  = [[] for _ in range(8)] # tracks 1-8: un track MIDI por pista

        chain_tick = 0

        for bank_idx, slot_idx in self.chain:
            slot = self.banks[bank_idx].get(slot_idx)
            if slot is None:
                continue
            bpm      = float(slot.get('bpm', self.bpm))
            tempo_us = int(60_000_000 / bpm)
            tb = tempo_us.to_bytes(3, 'big')
            tempo_trk.append((chain_tick, 0xFF, 0x51, 0x03, *tb))

            tracks_data   = slot.get('tracks', [])
            slot_max_ticks = 0   # duración máxima de esta slot en ticks

            for ti, td in enumerate(tracks_data[:8]):
                pattern   = td.get('pattern', [])
                pat_len   = max(len(pattern), 1)
                # step_locks pueden tener claves int (live) o str (snapshot JSON)
                raw_sl    = td.get('step_locks', {})
                step_locks = {int(k): v for k, v in raw_sl.items()}
                tonal_notes = list(td.get('tonal_notes', []))

                root      = int(td.get('root', 60))
                octave    = int(td.get('octave', 0))
                note_off  = int(td.get('note_offset', 0))
                velocity  = int(td.get('velocity', 100))
                gate      = float(td.get('gate', 0.8))
                note_len  = int(td.get('note_len', 1))
                channel   = int(td.get('channel', ti)) & 0xF
                ratchet   = int(td.get('ratchet', 1))
                r_div     = float(td.get('ratchet_div', 1.0))
                r_curve   = float(td.get('ratchet_curve', 0.0))
                swing     = float(td.get('swing', 0.0))
                res_idx   = int(td.get('resolution', 5))
                mul_idx   = int(td.get('multiplier', 1))
                program   = int(td.get('program', 0))
                bank_msb  = int(td.get('bank_msb', 0))
                send_pc   = bool(td.get('send_pc', False))
                cc_lanes  = td.get('cc_lanes', [])
                muted     = bool(td.get('muted', False))

                _, res_m  = RESOLUTIONS.get(res_idx, ("1/16", 1.0))
                _, mul_m  = MULTIPLIERS.get(mul_idx, ("x1",   1.0))
                tps       = T16 * res_m * mul_m   # ticks per step (base)

                slot_max_ticks = max(slot_max_ticks, int(pat_len * tps))
                evs = note_trks[ti]

                # Program change al inicio del slot (solo si program o bank != 0)
                if send_pc and not muted and (program != 0 or bank_msb != 0):
                    evs.append((chain_tick, 0xB0 | channel, 0,       bank_msb & 0x7F))
                    evs.append((chain_tick, 0xC0 | channel, program & 0x7F))

                tonal_idx = 0
                even_step = True

                for step_idx in range(pat_len):
                    active = bool(pattern[step_idx]) if step_idx < len(pattern) else False
                    lk     = step_locks.get(step_idx, {})

                    # Resolución/multiplicador por step (p-lock)
                    _, sr  = RESOLUTIONS.get(int(lk.get('resolution', res_idx)), ("1/16", 1.0))
                    _, sm  = MULTIPLIERS.get(int(lk.get('multiplier',  mul_idx)), ("x1",   1.0))

                    # Posición absoluta en ticks (usando resolución base del track)
                    s_tick = chain_tick + int(step_idx * tps)

                    # Swing (solo steps pares)
                    if even_step:
                        s_tick += int(swing * tps * 0.5)
                    even_step = not even_step

                    # Micro-time nudge
                    micro  = float(lk.get('micro_time', 0.0))
                    s_tick = max(0, s_tick + int(micro * tps))

                    if not muted:
                        # ── CC lanes ─────────────────────────────────────────
                        for lane_k, cv in lk.get('cc_vals', {}).items():
                            li = int(lane_k)
                            if li < len(cc_lanes):
                                cc_n = int(cc_lanes[li].get('num', -1))
                                if cc_n >= 0:
                                    evs.append((s_tick, 0xB0|channel, cc_n&0x7F, int(cv)&0x7F))

                        # ── Pitch bend ───────────────────────────────────────
                        if 'pitch_bend' in lk:
                            pb = int(lk['pitch_bend'])
                            evs.append((s_tick, 0xE0|channel, pb&0x7F, (pb>>7)&0x7F))

                        # ── Aftertouch ───────────────────────────────────────
                        if 'pressure' in lk:
                            evs.append((s_tick, 0xD0|channel, int(lk['pressure'])&0x7F))

                        # ── PC p-lock (solo si program o bank != 0) ──────────
                        if ('program' in lk or 'bank_msb' in lk) and send_pc:
                            _pc_b = int(lk.get('bank_msb', bank_msb)) & 0x7F
                            _pc_p = int(lk.get('program',  program))  & 0x7F
                            if _pc_b != 0 or _pc_p != 0:
                                evs.append((s_tick, 0xB0|channel, 0, _pc_b))
                                evs.append((s_tick, 0xC0|channel,    _pc_p))

                    if not active or muted:
                        continue

                    # ── Determinar notas ──────────────────────────────────────
                    if 'notes' in lk:
                        notes = [int(n) for n in lk['notes']]
                    elif 'note' in lk:
                        notes = [int(lk['note'])]
                    elif tonal_notes:
                        notes = [int(tonal_notes[tonal_idx % len(tonal_notes)])]
                    else:
                        notes = [root]

                    if tonal_notes and 'notes' not in lk and 'note' not in lk:
                        tonal_idx += 1

                    # Octava + transposición + clamp
                    shift = octave * 12 + note_off
                    notes = list(dict.fromkeys(max(0, min(127, n+shift)) for n in notes))

                    vel = int(lk.get('vel',        velocity))
                    nl  = int(lk.get('note_len',   note_len))
                    gt  = float(lk.get('gate',     gate))
                    sr2 = int(lk.get('ratchet',    ratchet))
                    sd  = float(lk.get('ratchet_div',   r_div))
                    sc  = float(lk.get('ratchet_curve', r_curve))

                    note_dur = int(tps * nl * gt)
                    ri = int(tps * sd / sr2) if sr2 > 1 and sd > 0 else 0

                    for r in range(sr2):
                        r_tick = s_tick + r * ri
                        # Envolvente de velocidad
                        if sr2 <= 1 or sc == 0.0:
                            hv = vel
                        else:
                            t_r    = r / max(1, sr2 - 1)
                            factor = (1.0 - sc*(1.0-t_r)) if sc > 0 else (1.0 + sc*t_r)
                            hv     = max(1, min(127, int(vel * max(0.0, factor))))
                        off_t = r_tick + max(1, note_dur // sr2)
                        for note in notes:
                            evs.append((r_tick, 0x90|channel, note&0x7F, hv&0x7F))
                            evs.append((off_t,  0x80|channel, note&0x7F, 0))

            chain_tick += max(slot_max_ticks, T16)

        return _midi_file(TPB, [tempo_trk] + note_trks)

    def _render(self):
        if _APP_MODE: return self._render_sse_only()
        os.system('clear')
        t_a           = self.tracks[self.active]
        scale_name, _ = SCALES[t_a.scale_idx]
        page_name     = PAGES[self.page]
        params        = PAGE_PARAMS[self.page]
        IN = 100

        def row(content):
            pad = IN - 2 - len(content)
            print(f"║ {content}{' ' * max(0, pad)} ║")

        def sep():
            print(f"╠{'─' * IN}╣")

        print(f"╔{'─' * IN}╗")

        transport = "● REC" if self.recording else ("► PLAY" if self.running else "■ STOP")
        row(f"◈  SECUENCIADOR GENERATIVO   "
            f"BPM {self.bpm:<4}  PISTA {self.active+1}/8  "
            f"PAG {page_name}  BANCO {self.current_bank}  STEPS pg{self.step_pg}  {transport}")

        sep()
        row(f"  {'#':<4}{'CH':<5}{'PROG':<10}{'MODO':<13}{'PASOS':<9}"
            f"{'NOTA':<8}{'RESOL':<9}{'PROB':<7}PATRON")
        sep()

        for i, t in enumerate(self.tracks):
            marker = '►' if i == self.active else ' '
            mute_c = 'M' if t.muted else ' '
            tonal  = 'T' if t.tonal_notes else ' '
            rn, _  = RESOLUTIONS[t.resolution]
            mn, _  = MULTIPLIERS[t.multiplier]
            resol  = f"{rn}{mn}"
            _pc_known = (t.port, t.channel) in self._last_pc_sent or (t.program != 0 or t.bank_msb != 0)
            prog_str  = f"{t.bank_msb}:{t.program}" if _pc_known else "?:?"
            prefix = (
                f"  {marker}{mute_c}{i+1:<2}"          # 6
                f"  {t.channel+1:<3}"                   # 5
                f"  {prog_str:<8}"                      # 10
                f"  {PLAY_MODES[t.play_mode]:<11}"      # 13
                f"  {t.steps:2}/{t.pulses:<4}"          # 9
                f"  {tonal}{note_name(t.root):<5}"      # 8
                f"  {resol:<7}"                         # 9
                f"  {int(t.prob*100):3}%  "             # 7
            )
            bar = ""
            for j, a in enumerate(t.pattern):
                if j == t.display_step:
                    bar += "█" if a else "▒"
                elif a:
                    bar += "●"
                else:
                    bar += "·"
            row(f"{prefix} {bar}")

        sep()
        row(f"  Escala {scale_name:<10}  "
            f"Prob {int(t_a.prob*100):3}%  "
            f"Swing {int(t_a.swing*100):3}%  "
            f"Spread {int(t_a.spread*100):3}%  "
            f"Density {int(t_a.density*100):3}%  "
            f"Vel {t_a.velocity:<3}  "
            f"Oct {t_a.octave:+d}  "
            f"Gate {int(t_a.gate*100):3}%  "
            f"Ratchet {t_a.ratchet}x")

        sep()
        row(f"  ▸  {self.last_msg}")

        # Línea de estado estocástico — siempre visible aunque estés en otra página
        stoch_active = [(p, t_a.stoch_amounts.get(p, 0.0))
                        for p, en in t_a.stoch_enabled.items() if en]
        if stoch_active:
            stoch_str = "  ±STOCH: " + "  ".join(
                f"{p}={int(amt*100)}%" for p, amt in stoch_active)
        else:
            stoch_str = "  ±STOCH: —"
        row(stoch_str)

        sep()
        def knob_label(i, lbl):
            stoch_flag = "±" if t_a.stoch_enabled.get(lbl, False) else " "
            if lbl == "PatBnk":
                return f"K{i+1}:{stoch_flag}{lbl}={self.current_bank:<4}"
            if lbl == "Rotac":
                return f"K{i+1}:{stoch_flag}{lbl}={t_a.rotation:<6}"
            if lbl == "MULT":
                name, _ = MULTIPLIERS[t_a.multiplier]
                return f"K{i+1}:{stoch_flag}{lbl}={name:<5}"
            if lbl == "—":
                return f"K{i+1}: —        "
            return f"K{i+1}:{stoch_flag}{lbl:<10}"
        kp = [knob_label(i, lbl) for i, lbl in enumerate(params)]
        row("  " + "  ".join(kp[:4]))
        row("  " + "  ".join(kp[4:]))

        sep()
        row("  GRID: pad=toggle  ColDer=sel pista  Shift+ColDer=mute  Shift+fila activa: Mute Eucl Mutar Dens Tonal Reset Doble Inv")
        row("  SUP: TrkDn TrkUp(+Sh=StepPg±) StepPg- StepPg+ [Shift] BancoDn BancoUp Slot0  | NANO: escena=pagina  knobs=params")

        print(f"╚{'─' * IN}╝")

        self._render_sse_only()

    def _render_sse_only(self):
        """Solo construye y empuja el estado SSE, sin imprimir nada por terminal."""
        t_a           = self.tracks[self.active]
        scale_name, _ = SCALES[t_a.scale_idx]
        page_name     = PAGES[self.page]
        params        = PAGE_PARAMS[self.page]

        # ── Push estado completo al visual web ───────────────────────────────
        rn, _ = RESOLUTIONS[t_a.resolution]
        mn, _ = MULTIPLIERS[t_a.multiplier]
        knobs_data = [{'label': lbl, 'idx': i} for i, lbl in enumerate(params)]
        tracks_data = []
        # Cuando el secuenciador está parado, mostrar t.cursor (posición de grabación)
        # en lugar de t.display_step (última posición de reproducción). Así el
        # indicador de la UI coincide con el step donde irá la próxima nota grabada.
        for ti, t in enumerate(self.tracks):
            pat_len_disp = max(len(t.pattern), 1)
            disp = (t.cursor % pat_len_disp) if not self.running else t.display_step
            locks_set = [i in t.step_locks for i in range(len(t.pattern))]
            tracks_data.append({
                'pulses':  t.pulses, 'steps': t.steps,
                'pattern': t.pattern[:],
                'locks':   locks_set,
                'cursor':  disp,
                'muted':   t.muted,
                'firing':  t.firing,
                'script_id': t.script_id,
            })
        _parts = self.last_msg.split('→')
        _dlbl  = _parts[0].split(None,1)[-1].strip() if len(_parts)==2 else self.last_msg
        _dval  = _parts[1].strip()                    if len(_parts)==2 else ''

        # En detail view, el display lo controlan los handlers (knob, pad, btn).
        # El render solo empuja datos estructurales (tracks, knobs, page_params)
        # pero NO sobreescribe label/value para evitar parpadeo.
        # Si estamos en bank_view, FORZAR page para no mezclar vistas nunca.
        _effective_view_mode = 'page' if self.bank_view else self.view_mode
        _in_detail = (_effective_view_mode == 'detail')

        _extra = {
                'page':    page_name,
                'scale':   scale_name,
                'prob':    int(t_a.prob * 100),
                'swing':   int(t_a.swing * 100),
                'vel':     t_a.velocity,
                'oct':     t_a.octave,
                'gate':    int(t_a.gate * 100),
                'ratchet': t_a.ratchet,
                'spread':  int(t_a.spread * 100),
                'density': int(t_a.density * 100),
                'note':    note_name(t_a.root),
                'resol':   f"{rn}{mn}",
                'knobs':   knobs_data,
                'tracks':  tracks_data,
                'bank':    t_a.bank_msb,
                'slot':         self.active_slot,
                'pending_slot': self.pending_load,
                'pat_bank': self.current_bank,
                'mode':    PLAY_MODES[t_a.play_mode],
                'recording': self.recording,
                'rec_step':  (t_a.cursor % max(1, t_a.steps)) if (self.recording and not self.running and not self.kb_step_focus) else -1,
                'rest_flash': self.rest_flash,
                'prog':    t_a.program,
                'stoch_on': any(t_a.stoch_enabled.values()),
                'xfade_amt': self.xfade_amt,
                'xfade_b':   self.xfade_label if self.xfade_snap else '',
                'view_mode': _effective_view_mode,
                'bank_view': self.bank_view,
                'page_idx':       self.page,
                'kb_param':       self.kb_param,
                'kb_step':        self.kb_step,
                'kb_step_focus':  self.kb_step_focus,
                'kb_enabled':     self.kb_enabled,
                'mapping_mode':    self.mapping_mode,
                'mapping_group':   self.mapping_group,
                'cc_map_current':  self._get_group_ccs(),
                'learn_target': (self.learn_target[2]
                                 if self.learn_target
                                    and self.learn_target[0] == self.mapping_group
                                    and (self.learn_target[0] == 'misc'
                                         or self.learn_target[1] == self.page)
                                 else None),
                'midi_ports': {
                    'midi_out':  (getattr(config, 'MIDI_OUT_PORT',  '') if self.midi_out.is_port_open()  else ''),
                    'midi_out2': (getattr(config, 'MIDI_OUT_PORT2', '') if self.midi_out2.is_port_open() else ''),
                    'lp_port':   (getattr(config, 'LAUNCHPAD_PORT', '') if (self.lp_in and self.lp_in.is_port_open()) else ''),
                    'nk_in':     (getattr(config, 'NK_IN_PORT',     '') if (self.nk_in and self.nk_in.is_port_open())  else ''),
                    'kb_port':   (getattr(config, 'MIDI_KB_PORT',   '') if (self.kb_in and self.kb_in.is_port_open())  else ''),
                },
                'page_params': self._get_page_params(),
                'compact_view':    self.compact_view,
                'all_page_params': self._get_all_page_params() if self.compact_view else None,
                'step_focus': (list(self.step_focus) if self.step_focus else None),
                'step_pg': self.step_pg,
                'step_pg_total': (t_a.steps + 7) // 8,
                # Contador del header: cambia según contexto (p-lock o chord/held)
                'header_counter': self._compute_header_counter(t_a),
                'step_ms': round(t_a.tick_interval(60.0 / self.bpm / 4.0) * 1000),
                'playing': self.running,
                'server_ts': time.time() * 1000,
                # ── Bank View + All-mode (teclado) ──
                'kb_all_mode':  self.kb_all_mode,
                'kb_bank_view': self.kb_bank_view,
                'kb_bank_cursor': self.kb_bank_cursor,
                'banks_grid': [
                    {str(slot): 1 for slot in self.banks[b].keys()}
                    for b in range(8)
                ],
                # Chain de exportación: lista de [bank, slot] con su orden
                'export_chain': [[b, sl] for (b, sl) in self.chain],
                'export_chain_pos': self.chain_pos,
                'bv_status':       self.bv_status,
                'bv_status_warn':  self.bv_status_warn,
                # Morph B slot (para resaltarlo en bank view)
                'morph_b_bank': (int(self.xfade_label[1:].split('.')[0]) - 1
                                 if self.xfade_snap and self.xfade_label else -1),
                'morph_b_slot': (int(self.xfade_label[1:].split('.')[1]) - 1
                                 if self.xfade_snap and self.xfade_label else -1),
        }

        if _in_detail:
            # En detail: empujar solo datos estructurales (tracks, knobs, etc.)
            # SIN tocar label/value — esos los controla el handler que activó detail.
            _extra['track'] = self.active
            _extra['bpm']   = int(self.bpm)
            _extra['playing'] = self.running
            _extra['color'] = TRACK_COLORS[self.active % 8]
            _extra['ts']    = time.time()
            try:
                _display_queue.put_nowait(_extra)
            except _queue_mod.Full:
                pass
        else:
            _push_display(
                _dlbl,
                _dval,
                track   = self.active,
                bpm     = int(self.bpm),
                playing = self.running,
                extra   = _extra
            )
        # rest_flash se emite una sola vez; el JS gestiona el timeout visual
        self.rest_flash = None

    def _tick(self, tick_inc=1.0):
        with self.lock:
            base_interval = 60 / self.bpm / 4
            master_wrapped = False
            # Determinar qué track es el "master": el de longitud más "redonda"
            # (mayor divisibilidad por 2 de sus pasos). En caso de empate, el más largo.
            # Si todos están muteados/vacíos, fallback al track 0.
            def _step_roundness(n):
                if n <= 0: return -1
                c = 0
                while n % 2 == 0:
                    n //= 2
                    c += 1
                return c
            best_i, best_round, best_dur = 0, -1, -1
            for i, t in enumerate(self.tracks):
                if t.muted or not any(t.pattern):
                    continue
                _, res_m = RESOLUTIONS[t.resolution]
                _, mul_m = MULTIPLIERS[t.multiplier]
                dur = t.steps * res_m * mul_m
                rnd = _step_roundness(t.steps)
                if rnd > best_round or (rnd == best_round and dur > best_dur):
                    best_round = rnd
                    best_dur   = dur
                    best_i     = i
            for i, t in enumerate(self.tracks):
                wrapped = self._tick_track(t, base_interval, i, tick_inc)
                if i == best_i:
                    master_wrapped = wrapped
            # Automation continua: interpola y aplica en cada tick
            self._apply_auto(base_interval)
            # Stoch continuo: cada tick (gateado por amount como probabilidad).
            # Estructurales (PULS, ROTA, RESL, MULT) solo en wrap para no romper la estructura.
            self._apply_stoch(structural=False)
            if master_wrapped:
                self._apply_stoch(structural=True)
            if master_wrapped and self.pending_load is not None:
                slot = self.pending_load
                self.pending_load = None
                bank = self.banks[self.current_bank]
                if slot in bank:
                    self._note_gen += 1          # invalida timers de notas en vuelo; las notas ya sonando terminan solas
                    old_pc = [(t.program, t.bank_msb) for t in self.tracks]
                    self._apply_slot(bank[slot])
                    self.active_slot = slot
                    row, col = divmod(slot, 8)
                    self.last_msg = f"Loaded B{self.current_bank+1} r{row+1}c{col+1}"
                    for i, tr in enumerate(self.tracks):
                        if tr.send_pc and (tr.program != 0 or tr.bank_msb != 0) and (tr.program, tr.bank_msb) != old_pc[i]:
                            self._send_program(tr)
            elif master_wrapped and self.chain:
                # Avanzar al siguiente slot del chain
                self.chain_pos = (self.chain_pos + 1) % len(self.chain)
                c_bank, c_slot = self.chain[self.chain_pos]
                bank = self.banks[c_bank]
                if c_slot in bank:
                    self.current_bank = c_bank
                    self._note_gen += 1          # invalida timers de notas en vuelo; las notas ya sonando terminan solas
                    old_pc = [(t.program, t.bank_msb) for t in self.tracks]
                    self._apply_slot(bank[c_slot])
                    self.active_slot = c_slot
                    row, col = divmod(c_slot, 8)
                    self.last_msg = f"Chain {self.chain_pos+1}/{len(self.chain)} B{c_bank+1} r{row+1}c{col+1}"
                    for i, tr in enumerate(self.tracks):
                        if tr.send_pc and (tr.program != 0 or tr.bank_msb != 0) and (tr.program, tr.bank_msb) != old_pc[i]:
                            self._send_program(tr)
            self.tick_count += 1
        _now = time.perf_counter()
        if _now - self._last_leds_ts >= 0.033:    # ~30 fps máximo
            self._update_leds()
            self._last_leds_ts = _now
        if _now - self._last_render_ts >= 0.05:   # ~20 fps máximo
            self._render()
            self._last_render_ts = _now

    def _clock_out_loop(self, gen):
        """Hilo dedicado: envía 0xF8 a 24 PPQN con timing preciso e independiente del loop principal."""
        # Alineamos la fase: el primer pulso sale justo al arrancar
        t_next = time.perf_counter()
        while self.running and self._loop_gen == gen:
            if not self.clock_out or self.ext_sync:
                time.sleep(0.002)
                t_next = time.perf_counter()   # resetear fase al reactivar
                continue
            interval = 60.0 / self.bpm / 24.0  # duración de un pulso (24 PPQN)
            now = time.perf_counter()
            sleep_t = t_next - now
            if sleep_t > 0:
                time.sleep(sleep_t)
            try:
                self.clk_out.send_message([0xF8])
            except Exception:
                pass
            t_next += interval
            # Si nos hemos retrasado demasiado (>2 pulsos), reajustar fase
            if time.perf_counter() - t_next > interval * 2:
                t_next = time.perf_counter() + interval

    def _loop(self):
        import traceback
        my_gen = self._loop_gen     # captura la generación al nacer — si cambia, este hilo es zombi
        t_next = time.perf_counter()  # phase accumulator: objetivo absoluto del siguiente tick
        while self.running and self._loop_gen == my_gen:
            try:
                if self.ext_sync:
                    # ── Modo esclavo: esperar tick externo (0xF8 cada 6 pulsos) ──
                    fired = self._ext_clock_event.wait(timeout=0.5)
                    if not fired or not self.running:
                        t_next = time.perf_counter()   # reset al volver
                        continue
                    self._ext_clock_event.clear()
                    self._tick(1.0)
                    t_next = time.perf_counter()       # en esclavo, no acumulamos
                else:
                    # ── Modo maestro: timer interno con phase accumulator ────────
                    with self.lock:
                        bpm = self.bpm
                    base_interval = 60 / bpm / 4       # duración de 1/16 en segundos
                    min_mult = min(
                        (RESOLUTIONS[t.resolution][1] * MULTIPLIERS[t.multiplier][1]
                         for t in self.tracks),
                        default=1.0)
                    tick_inc = min(min_mult, 1.0)
                    interval = base_interval * tick_inc
                    self._tick(tick_inc)
                    # Avanza el objetivo absoluto (no acumula jitter de sleep).
                    t_next += interval
                    sleep = t_next - time.perf_counter()
                    if sleep > 0:
                        time.sleep(sleep)
                    elif sleep < -interval * 4:
                        # Si nos hemos quedado atrás >4 ticks (freeze, cambio de BPM),
                        # resetear el horizonte para no disparar una ráfaga de catch-up.
                        t_next = time.perf_counter()
            except Exception as e:
                self.last_msg = f"ERROR: {e}"
                with open('/tmp/seq_error.log', 'a') as _f:
                    traceback.print_exc(file=_f)
                time.sleep(0.5)

    def start(self):
        self._loop_gen += 1
        self.running = True
        # Limpiar notas colgadas de instancias anteriores
        self._midi_panic()
        threading.Thread(target=_display_worker,     daemon=True).start()
        threading.Thread(target=lambda: _start_visual(5001), daemon=True).start()
        threading.Thread(target=self._loop,          daemon=True).start()
        threading.Thread(target=self._clock_out_loop,
                         args=[self._loop_gen],      daemon=True).start()

    def _midi_panic(self):
        """MIDI Panic: All Notes Off + All Sound Off en los 16 canales de todos los puertos.
        Más agresivo que _silence_all_tracks — cubre canales no asignados a ninguna pista."""
        self._note_gen += 1   # invalida todos los note_on de timers en vuelo
        for out in self.midi_outs:
            for ch in range(16):
                try:
                    out.send_message([0xB0 | ch, 123, 0])  # All Notes Off
                    out.send_message([0xB0 | ch, 120, 0])  # All Sound Off
                except Exception:
                    pass
        # También el puerto principal por si no está en midi_outs
        for ch in range(16):
            try:
                self.midi_out.send_message([0xB0 | ch, 123, 0])
                self.midi_out.send_message([0xB0 | ch, 120, 0])
            except Exception:
                pass

    def pause(self):
        """Para el loop pero mantiene el proceso y los LEDs."""
        self.running   = False
        self.recording = False
        self._nk_led(44, False)
        if self.clock_out:
            self.clk_out.send_message([0xFC])   # MIDI Stop
        self._midi_panic()
        self.last_msg = "■ STOP"
        self._led_cache.clear()   # fuerza reenvío completo — el Launchpad puede haber
        self._update_leds()       # perdido estado (MIDI thru, reset, etc.)
        self._render()

    def resume(self):
        """Arranca o reanuda el loop."""
        if not self.running:
            self._loop_gen += 1     # invalida cualquier hilo _loop zombi anterior
            # Panic ANTES de arrancar: limpia note-offs pendientes que podrían
            # solaparse con el primer tick y producir notas dobles al reanudar.
            self._note_gen += 1
            for out in self.midi_outs:
                for ch in range(16):
                    try:
                        out.send_message([0xB0 | ch, 123, 0])
                    except Exception:
                        pass
            self.running = True
            self._clock_acc = 0.0
            if self.clock_out:
                self.clk_out.send_message([0xFA])   # MIDI Start
            threading.Thread(target=self._loop,          daemon=True).start()
            threading.Thread(target=self._clock_out_loop,
                             args=[self._loop_gen],      daemon=True).start()
            self.last_msg = "► PLAY"

    def stop(self):
        """Hard stop — apaga todo y termina (Ctrl+C)."""
        self.running = False
        if self.clock_out:
            self.clk_out.send_message([0xFC])   # MIDI Stop
        self._midi_panic()
        for ti in range(8):
            for col in range(8):
                self._set_pad(lp_grid(ti, col), LP_OFF)
            self._set_pad(lp_right(ti), LP_OFF)
        for cc in LP_TOP:
            self._set_top(cc, LP_OFF)
        os.system('clear')
        print("Secuenciador parado.")


if __name__ == "__main__":
    seq = Sequencer()
    seq.start()
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        seq.stop()
