# Changelog

All notable changes to rrresponseq are documented here.

## [0.1.0] — 2025-04-28

### Initial Public Release

**Features:**
- 8 independent generative tracks with Euclidean pattern generation
- 4 parameter pages (32 total controllable parameters per track)
- 16 pattern banks with 8 slots each
- 31 built-in pattern scripts (algorithmic mutations every loop)
- Stochastic control — any parameter can evolve randomly
- 6 play modes: forward, reverse, bounce, random, snake, drunk
- Hardware support: Launchkey MK4 49, nanoKONTROL, Launchpad MK1
- MIDI Clock sync (send/receive) for DAW integration
- Compact view (key `9`) — see all 8 tracks simultaneously
- Web-based real-time UI with SSE streaming
- Per-step locks (p-locks) for parameter overrides
- Undo/redo stack
- Bank/pattern management with snapshots
- Full keyboard shortcuts for live performance
- Custom MIDI CC mapping (learn mode)
- Documentation with rendered UI examples

**Scripts included:**
- Pattern generation: euclidean_random, polyrhythm_wobble, drunk_walk, chaos_burst, etc.
- Melodic: melodic_walk, harmonic_rise, drunk_melody, interval_climb
- Tonal: octave_jumper, accent_dance, gate_pulse, ratchet_fever
- Evolving: tidal, pendulum, breath, phase_drift, tension_arc
- [31 total — see scripts.json]

### Architecture

- **Single-file design**: sequencer.py contains all Python backend + HTML/CSS/JS
- **Flask server**: Real-time browser UI, SSE for state streaming
- **Hardware MIDI**: pygame + mido for multi-port MIDI I/O
- **No external databases**: State stored in JSON snapshots
- **py2app compiled app**: Self-contained macOS bundle (rrresponseq-macOS.zip)

### Known Limitations

- Pattern scripts are silent on execution errors (by design, to avoid crashes)
- Swing only affects the currently playing track
- CC Lane display doesn't animate during recording
- Cursor position persists when stopping playback

### Testing

- `test_pattern_script.py`: 9 tests covering script execution, pattern manipulation, parameter clamping
- Manual testing with Launchkey MK4, nanoKONTROL, Launchpad MK1

---

## Future Roadmap (Ideas)

- [ ] Step-by-step pattern recording with step sounds
- [ ] Visual pattern editor (click to toggle steps)
- [ ] More script templates/wizard
- [ ] Time-stretching scripts (tempo-synced pattern morphing)
- [ ] Note quantizer / scale-aware pattern generation
- [ ] Export patterns to MIDI files
- [ ] Multi-project management
- [ ] Community script gallery / sharing platform
- [ ] iOS/Android companion app for parameters
- [ ] Linux port (Flatpak)
- [ ] Dark mode toggle

---

## Version History

### 0.1.0-beta (2025-04)
- Initial private beta with 20 scripts
- Added 11 multi-parameter creative scripts (tidal, pendulum, harmonic_rise, etc.)
- Fixed SSE bandwidth issues (scripts_list removed from streaming)
- Fixed missing cf-tab-script HTML element (UI freeze bug)
- Added SCRI parameter to D·CONF page (script toggle)
- Keyboard shortcut M for script on/off toggle

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to:
- Submit pattern scripts
- Report bugs
- Suggest features
- Contribute code

## License

GNU Affero General Public License v3 — See [LICENSE](LICENSE)

---

**Latest release:** v0.1.0  
**Release date:** April 28, 2025  
**Status:** Stable, accepting contributions
