---
title: Adona Style Generator
emoji: 🎹
colorFrom: purple
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
short_description: Turn a song's drum groove into a Yamaha keyboard style
---

# Adona Style Generator

Upload a song (MP3, WAV, FLAC or OGG). The app finds its tempo, key and the drummer's actual groove,
then builds a keyboard style for Yamaha PSR-SX, Genos, PSR-S, PSR-E and PSR-I keyboards.

- **The song's own beat.** Kick, snare, hi-hat and cymbal hits keep their timing, swing and
  dynamics. Fills, intro and ending come from the song itself.
- **Fits your keyboard.** Every model gets SFF1 `.sty` files within its size limit. Keyboards
  with only A/B variation buttons get the song's grooves as two styles.
- **You decide.** Choose which song groove plays on each variation button. Preview every section
  before you download.

**Current limits**

- Styles contain the **rhythm (drum) parts only**. Bass and chord parts aren't generated yet.
- Uploads are deleted after a few days. Download your styles when you're done.
- Songs without a real drum kit (solo piano, orchestra) are refused rather than given a fake groove.

## Run it on your own computer

Windows: double-click `start.bat` (needs Python 3.11 or newer), then open http://127.0.0.1:8765.
