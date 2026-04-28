# rrresponseq — Generative MIDI Sequencer

A real-time generative MIDI sequencer built for creative exploration and live performance. Control 8 independent tracks with Euclidean patterns, stochastic parameters, and evolving scripts.

## Features

- **8 generative tracks** — Each with independent Euclidean patterns, play modes (forward, reverse, bounce, random, snake, drunk), and per-step locks
- **4 parameter pages** (32 total parameters):
  - **A·SEQ**: Pulses, steps, probability, mode, velocity, swing, resolution, rotation
  - **B·NOTE**: Note, scale, octave, density, spread, harmony, interval, note length
  - **C·FX**: Delay, delay time, feedback, decay, speed, ratchet, gate, CC
  - **D·CONF**: Program, bank, pattern bank, pattern, channel, clock out, port, **scripts**

- **31 built-in scripts** — Algorithmic pattern/parameter mutations (Euclidean morphing, melodic walks, tidal breathing, tension arcs, chaos bursts, etc.)
- **Stochastic control** — Any parameter can toggle stochastic mode for evolving randomness
- **16 pattern banks** — 8 slots each for organizing ideas
- **Hardware support**:
  - Launchkey MK4 49 (8 knobs, pads, navigation)
  - nanoKONTROL (8 knobs, 8 faders, 16 buttons)
  - Launchpad MK1 (16×8 grid, shift control, velocity)
  - Generic MIDI CC mapping

- **Compact view** (key `9`) — See all 8 tracks + 4 parameter columns simultaneously
- **Live web UI** — Real-time browser interface with SSE state streaming
- **MIDI Clock sync** — Send/receive clock for DAW integration
- **Pattern scripting** — JavaScript-like Python code runs every loop, modulating patterns and parameters

## Installation

### macOS App (easiest)

Download and run the compiled app:
```bash
curl -o rrresponseq-macOS.zip https://[your-release-url]
unzip rrresponseq-macOS.zip
open rrresponseq-macOS/rrresponseq.app
```

Then navigate to `http://localhost:5001` in your browser.

### From Source

Requires Python 3.12+, pygame, flask, mido.

```bash
git clone https://github.com/yourusername/rrresponseq.git
cd rrresponseq
pip install -r requirements.txt
python3 sequencer.py
```

Then open `http://localhost:5001`.

## Quick Start

1. **Launch** — Run the app or `python3 sequencer.py`
2. **Connect hardware** (optional) — Click SETTINGS in Ctrl+M menu to select MIDI ports
3. **Start playback** — Press Space or click Play
4. **Edit parameters** — Use knobs, faders, or keyboard shortcuts:
   - Arrow keys: navigate track/parameter
   - `1-8`: select track
   - `0`: select parameter page
   - `M`: toggle stochastic mode on current param
   - `E`: randomize current parameter
   - `S`: assign pattern script
   - `Shift+S`: toggle script on/off
5. **Assign scripts** — Press Ctrl+M, go to SCR tab, click a script to activate

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Space` | Play/stop |
| `R` | Record mode |
| `M` | Toggle stochastic on parameter |
| `E` | Randomize parameter |
| `S` | Select script for current track |
| `Shift+S` | Toggle script on/off |
| `9` | Compact view (all tracks) |
| `Ctrl+B` | Bank view |
| `Ctrl+M` | Settings/MIDI config |
| `Ctrl+Z` / `Ctrl+Y` | Undo/redo |
| `Shift+C` | Clear track |
| `↑↓←→` | Navigate |

## Pattern Scripts

Scripts run at the end of each loop cycle and can mutate:

**Pattern functions:** `euclidean()`, `rotate()`, `mirror()`, `invert()`, `wobble()`, `drunk_walk()`, `stutter()`, `polyrhythm()`, etc.

**Tonal parameters:** `velocity`, `gate`, `ratchet`, `octave`, `interval`, `prob`, `swing`, `note_len`, `spread`, `density`, `humanize`

**Available variables:** `pattern`, `steps`, `pulses`, `loop_count`, `random`, `sin`, `cos`, `choice()`, `randint()`, etc.

### Example Scripts

```python
# Tidal breathing — everything oscillates together
tide = sin(loop_count * 0.1)
velocity = int(80 + 40 * tide)
gate = 0.4 + 0.5 * abs(tide)
octave = int(round(tide))
interval = int(round(3 * tide))

# Harmonic rise — interval climbs scale
interval = (loop_count % 8) - 4
if loop_count % 8 == 7:
    pattern = mirror(pattern)
velocity = int(65 + 50 * abs(sin(loop_count * 0.55)))

# Ratchet fever — ratchets escalate every 8 loops
r = 1 + (loop_count % 8) // 2
ratchet = r
if loop_count % 8 == 7:
    pattern = [random() > 0.3 for _ in range(steps)]
```

Create custom scripts in Settings → SCR tab.

## Hardware Setup

### nanoKONTROL
- Knobs CC 11-18 → Page A/B/C/D params 1-8
- Faders CC 1-8 → Page A/B/C/D params 1-8
- Buttons S CC 21-28 → Stochastic toggle
- Buttons M CC 31-38 → Stochastic randomize

### Launchkey MK4 49
- Knobs CC 21-28 → Current page params
- Pads → Keyboard note input
- NEXT/PREV → Navigate parameters
- SHIFT → Rest step when recording

### Launchpad MK1
- 16×8 grid → Keyboard note input
- Right column → Track select + mute
- Top row → View modes
- Shift (CC 108) → Rest step

## Configuration

Edit `config.py` to customize:
- MIDI port names and numbers
- Default BPM (120)
- Default scale and play mode
- Knob/fader CC mappings

## Documentation

Full documentation with rendered UI examples: `http://localhost:5001/docs`

Or read `/docs/index.html` for offline reference.

## Contributing

Bug reports, feature ideas, and script submissions welcome via:
- GitHub Issues
- Pull requests (fork and modify under AGPL v3)

## License

**GNU Affero General Public License v3** — See [LICENSE](LICENSE)

In short:
- ✓ Free to use, modify, distribute
- ✓ Free to use commercially
- ✗ Must share modifications under same license
- ✗ Must provide source code to service users

[Read full AGPL v3 terms](https://www.gnu.org/licenses/agpl-3.0.html)

## Credits

Built by [your name/handle]  
Inspired by Euclidean rhythms, generative music, and hardware sequencers.

Font: [Disket Mono](https://github.com/romeovs/disket) by Romeo Van Snick

## Support

- 📖 Docs: `http://localhost:5001/docs`
- 🎛️ Settings: Ctrl+M in the app
- 💬 Issues: GitHub Issues
- 🎵 Scripts: Check the SCR tab in Settings for examples

---

**Make generative music. Break the rules. Share your patterns.**
