# Contributing to rrresponseq

First off, thanks for being interested! This project is all about exploration and creativity, so contributions are super welcome.

## What can you contribute?

### 🎵 Pattern Scripts
The easiest and most fun way to contribute. Write a new generative script that mutates patterns or parameters.

**How:**
1. Create a new script in the SCR tab (Settings → Ctrl+M → SCR)
2. Test it live with your hardware/keyboard
3. When you like it, share it:
   - Open an issue with the script name, description, and code
   - Or fork + add to `scripts.json` and submit a PR

**Example:**
```json
{
  "id": "your_script_id",
  "name": "Your Script Name",
  "desc": "One line description",
  "code": "velocity = int(90 + 30 * sin(loop_count * 0.5))\npattern = wobble(pattern, 0.15)"
}
```

**Script variables available:**
- Pattern: `pattern`, `steps`, `pulses`, `euclidean()`, `rotate()`, `mirror()`, `wobble()`, `drunk_walk()`, `stutter()`, `polyrhythm()`, etc.
- Tonal: `velocity`, `gate`, `ratchet`, `octave`, `interval`, `prob`, `swing`, `note_len`, `spread`, `density`, `humanize`, `play_mode`
- State: `loop_count`
- Math: `random()`, `sin()`, `cos()`, `choice()`, `randint()`, `min()`, `max()`, `abs()`

[See existing scripts for inspiration](scripts.json)

### 🐛 Bug Reports

Found something broken? Open an issue with:
- What you did
- What you expected
- What happened instead
- Your setup (OS, hardware, Python version)

Example:
```
**Issue:** Cursor doesn't move when using Launchpad

**Setup:** macOS 14.2, Launchpad MK1, nanoKONTROL

**Steps:**
1. Assign Launchpad as input in Settings
2. Press play
3. Play a note on Launchpad

**Expected:** Cursor advances on track grid

**Actual:** Cursor stays at step 0
```

### 💡 Feature Ideas

Have an idea but don't want to code it? Open an issue labeled `enhancement`:

- New parameter mode
- Hardware support
- UI improvement
- Script library feature
- Anything else

We'll discuss it!

### 🔧 Code Contributions

Want to fix a bug or add a feature? Great!

**Setup:**
```bash
git clone https://github.com/yourusername/rrresponseq.git
cd rrresponseq
pip install -r requirements.txt
python3 sequencer.py
```

**Workflow:**
1. Create a branch: `git checkout -b fix/your-issue` or `feature/your-idea`
2. Make changes
3. Test thoroughly (especially if touching MIDI code)
4. Commit with clear message: `Fix cursor not advancing with Launchpad`
5. Push and open a Pull Request

**Code style:**
- 4 spaces, no tabs
- Comments for complex logic
- Keep functions under 50 lines when possible
- Name things clearly

### 📖 Documentation

- Improve README, add examples
- Write tutorials for specific hardware
- Create demo videos/GIFs
- Translate docs to other languages

### 🎮 Hardware Support

Want to add support for your controller?

1. Figure out the CC numbers or MIDI messages it sends
2. Add a preset function (like `_default_cc_map()`) in sequencer.py
3. Test with the existing UI
4. Open a PR with the new mapping

## Process

1. **Fork** the repo (top right on GitHub)
2. **Clone** your fork locally
3. **Create a branch** for your change
4. **Make changes** and test
5. **Commit** with a clear message
6. **Push** to your fork
7. **Open a Pull Request** to main with description

**What happens next:**
- I'll review your PR (may take a few days)
- You might get feedback or requests for changes
- Once approved, it merges!
- You're in the credits ✨

## Code of Conduct

Be respectful. This is a creative space. No harassment, discrimination, or malice.

## Questions?

- 💬 Open an issue labeled `question`
- 📧 Check the README for contact info
- 🎵 Join the conversation in PRs/issues

## What Won't Be Accepted

- Scripts/code that break existing functionality
- Code without testing
- Major features without discussion first (open an issue first!)
- Anything that violates AGPL v3 terms

## Remember

- This is creative first. Weird and experimental is good.
- Your first PR doesn't have to be perfect.
- No silly contributions — only good contributions exist.
- If you ship a script, you're part of the music community now.

---

**Thanks for contributing to rrresponseq.** Make weird patterns. Share them. Let's make generative music together. 🎛️✨
