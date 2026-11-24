# Το Νησί των Λέξεων: a whole-Studio test

One prompt that uses nearly every part of Strata Studio: questions, the plan card, Blender, ComfyUI (textures and
pictures), the voice cast, Greek spell-checking, tests, a live web page checked with view_page (desktop, phone,
held keys), the Live tab, the video timeline with narration, skills and Git.

Run it in a new, empty folder with the **Sandbox off** (Blender and the voice engine write files by full path on
this machine), with the blender, comfyui, voices, video and greek-check tool servers running, and expect a long
run (one to three hours). Answer the agent's questions when it asks; approve or turn on auto-run as you prefer.

---

## The prompt

Build **"Το Νησί των Λέξεων"** (The Island of Words): an interactive 3D web page where children aged 6–9, including
children with dyslexia, explore a small island and learn six Greek words. Clicking an object on the island shows its
word, a picture and a sentence, reads them aloud, and highlights each syllable as it is spoken.

**Before you start**
1. Ask me (with your question tool, not in plain text) three things at once: the island's theme (for example
   seaside village, forest, or farm), which two voices from the cast to use (list them first with list_voices), and
   whether the page should start in Greek only or offer an English switch.
2. Make a plan with the plan tool, one step per stage below, and keep its progress up to date.

**Assets**
3. Choose six concrete, easy nouns that fit the theme (for example βάρκα, φάρος, δέντρο). For each one write a short
   sentence a child can read (at most 8 words) and split the word into syllables (βάρ-κα). Keep them in
   `data/words.json`. Check every Greek string with check_text and fix what it finds.
4. With ComfyUI make:
   - one seamless texture per object (wood, stone, leaves…), 1024×1024, and one for the island's ground and the sea,
   - one friendly, simple illustration per word for its card (no text in the pictures),
   - a wide sky picture for the background.
   Look at each result; make it again if it is wrong. Save them into `assets/textures/` and `assets/cards/` with
   save_picture.
5. In Blender, build the island and the six objects: low-poly and readable from far away, each a separate object with
   a clear name. Put the ComfyUI textures on them (use the full paths of the saved files), check the scene with a
   viewport screenshot, and export each object and the island as `.glb` files into `assets/models/`. Tell me if
   Blender cannot reach a texture and how you solved it.
6. With the voices tools, record for each word: the word alone, read slowly by the first voice; the sentence, read by
   the second voice. Save them as MP3 in `assets/audio/` (for example `varka-word.mp3`, `varka-sentence.mp3`). Use
   probe_media to get each file's exact length and store it in `data/words.json`.

**The page**
7. Build the page with Three.js **stored in the project** (no CDN: Studio's Live tab and view_page block outside
   addresses). Requirements:
   - the island and the six objects from the `.glb` files, the sky behind, soft light;
   - move with the arrow keys or WASD, turn by dragging, and on phones with touch (drag to look, tap to choose);
   - hovering or tapping an object highlights it and shows its name;
   - clicking opens a card: the picture, the word in large letters split into syllables, the sentence, and two buttons
     (Ξανά / Επόμενο). The word is read, then the sentence; each syllable lights up in time with the audio (spread
     the audio length over the syllables, weighted by their letters);
   - a settings panel: larger text, a dyslexia-friendly font (stored in the project), extra letter spacing, a
     reading speed control (0.75× to 1.25×, applied to the audio), and the English switch if I asked for it;
   - a progress bar: words found out of six, with a short celebration when all six are found;
   - it must work at phone width (390 pixels) without anything cut off.
8. Write tests (any framework that runs offline) that check `data/words.json`: six words, each with syllables that
   join to the word, a sentence of at most 8 words, and existing model, texture, card and audio files with stored
   lengths. Run them until they pass.

**Check it like a user would**
9. Start a local server for the page, then with view_page:
   - look at it on a desktop (1280) and on a phone (width 390, full page);
   - hold the arrow keys or W for a second to walk, and check the view moved;
   - click an object and confirm the card, the syllables and the audio controls appear;
   - read the console errors and failed requests, and fix every one.
   Then open it for me in the Live tab with open_preview.

**A trailer**
10. Make a 20–30 second trailer as a video timeline (`video/trailer.timeline.json`): the sky picture and the card
    illustrations, short Greek titles, and narration you record with the first voice (one or two sentences inviting
    children to the island). Render it to `video/trailer.mp4` and check a few frames with video_frames.

**Finish**
11. Save one skill that would help next time (for example the steps from ComfyUI texture to textured Blender model
    to `.glb`), then commit everything to Git with a clear message.
12. Finish with a short summary in Greek: what you built, where each kind of file is, how to open the page, and
    anything you could not do or did differently from this request.

---

## What to look for

| Feature | Seen in |
|---|---|
| Questions | Step 1 asks with the question card, three questions at once, after listing the voices |
| Plan card | A plan of about 12 steps, moved along during the run |
| Greek check | check_text calls on `data/words.json`; corrections made |
| ComfyUI | 14–15 pictures; at least one checked and remade; saved with save_picture |
| Blender | Six named objects plus the island, textured from those files, a viewport screenshot, `.glb` exports |
| Voices | `list_voices`, 12 MP3 files, two voices; lengths from probe_media |
| Page | Local Three.js, no CDN; models load; syllables light up in time; settings work; phone layout |
| Tests | A real test run that passes |
| view_page | Desktop and width 390 with full_page; a held key (hold_ms); a click; errors fixed |
| Live tab | open_preview at the end |
| Video | A timeline with pictures, titles and narration; rendered; frames checked |
| Skill and Git | One saved skill; one commit |
| Language | The final summary in Greek, Greek only in the Greek alphabet |

Weak spots to watch: the agent forgetting the no-CDN rule; Blender texture paths; syllable timing that ignores the
audio length; the phone layout; voices waiting while ComfyUI holds the graphics card (readings say "waiting for the
graphics card" and continue on their own).
