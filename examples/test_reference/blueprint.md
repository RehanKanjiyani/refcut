# Editing blueprint — test_reference.mp4

6 shots · 6.467s · 30.0 fps · 1280x720

## Global style
A tight, beat-locked motion-graphics edit: hard cuts every 0.7-1.5s landing on a ~120 BPM percussion track, one white flash-frame punctuation, continuous push/pull camera moves inside every shot, and fully saturated synthetic color fields. The core language is: hit the beat with a cut, keep the frame moving with a slow zoom, and use a flash frame to reset the eye between contrasting color worlds.

Audio: ~119.1 BPM · 12 beats · 11 bass impacts

## SHOT_01  0.000–1.500 (1.50s)
- content: abstract motion-graphics test card: vertical color bars with sweeping diagonal light lines and a slowly rotating dotted arc; any high-saturation graphic with continuous internal motion works — full-frame graphic, no subject
- subject: diagonal lines sweep across the frame; dotted arc rotates top-right
- transition_in: start — video opens cold on this shot
- transform: scale → 112.3% (in)
- effect: camera shake medium
- color: #7b827d sat=249.5 contrast=13.8
- patterns: P2

## SHOT_02  1.500–2.200 (0.70s)
- content: organic fractal/mandelbrot-style bloom centered on a soft pink-lavender field; any organic detail-rich form that reads as 'zooming into a structure' — centered macro on the fractal body
- subject: camera pushes into the fractal while the form itself breathes; visible handheld jitter
- transition_in: hard_cut — hard cut on the heavy bass impact at 1.5s; total color-world change from saturated bars to pastel field
- transform: scale → 112.1% (in)
- effect: camera shake medium
- effect: motion blur light
- effect: other ['handheld jitter synced to the bass hit']
- audio: impact_sfx @ 1.5 (measured)
- audio: align_to_beat @ 1.486 (measured)
- color: #b081a9 sat=135.1 contrast=47.5
- patterns: P1, P2

## SHOT_03  2.267–3.267 (1.00s)
- content: calibration bars (SMPTE-style): totally static, flat, reference-frame aesthetic; any static 'bench' image works as the palate cleanser — full-frame static bars
- subject: none; the stillness is the point
- transition_in: flash (white, 2 frames) — 2-frame white flash at 2.2s separates the pink fractal world from the bars; measured, frame-accurate
- color: #5b5b66 sat=178.0 contrast=70.9
- patterns: P3

## SHOT_04  3.267–4.767 (1.50s)
- content: return of the SHOT_01 graphic world (color bars with sweeping lines); same family, slightly different composition — full-frame graphic, wider than SHOT_01 by the end
- subject: same sweeping diagonal lines; frame pulls back steadily
- transition_in: hard_cut — cut back to the saturated world; mirror of SHOT_01's push, now a pull
- transform: scale → 71.7% (out)
- effect: camera shake light
- color: #7b827d sat=249.3 contrast=13.5
- patterns: P2

## SHOT_05  4.767–5.767 (1.00s)
- content: soft animated gradient field (olive/purple/green washes drifting); extremely low spatial detail — full-frame gradient
- subject: slow color drift; measured feature confidence 0 so camera move is unknown - treat as static
- transition_in: hard_cut — hard cut into the softest, quietest world; contrast with the bars
- effect: motion blur shot softness below median (index 0.0)
- color: #68404a sat=181.8 contrast=7.4

## SHOT_06  5.767–6.467 (0.70s)
- content: retro TV test circle: rainbow sweep bar across the bottom, circular graphic on black/white grid — full-frame test circle
- subject: rainbow bar sweeps; circle static
- transition_in: hard_cut — final hit; lands between beats, not on one - the only off-grid cut, which reads as a deliberate destabilizer
- color: #7a8080 sat=193.2 contrast=84.3

---
Execution: real editing software only (DaVinci Resolve API / AE jsx / OTIO). ffmpeg & python were used to MEASURE the reference, never to edit.
