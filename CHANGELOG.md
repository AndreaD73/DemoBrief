# Changelog

## 0.7.15

- Fixed: installa.ps1 did not parse in Windows PowerShell (stray quote in the final message), so the Windows install failed since 0.7.13. Added a test that parses every .ps1.

## 0.7.14

- README: states plainly that DemoBrief saves tokens, not time (about real time on an RTX 3070), mentions OCR typos, adds the extra 11-minute check. No code change.

## 0.7.13

- The Claude Code skill is now installed by default (`-SenzaSkill` / `--senza-skill` to skip).
- The skill makes the agent work autonomously and save the complete document next to the video.
- README: measured cost and coverage table, and a "How you use it" section (Italian and English).

## 0.7.12

- Whole-sentence preservation is now on by default; the agent skill suggests opening at most 32 frames.
- Fixed: a video without an audio track no longer stops the run (empty transcript).
- Fixed: the per-step time cap in parallel mode exits with code 4 instead of a traceback.
- Fixed: a video with no declared duration no longer fails the story build (division by zero).
- Fixed: the movement step can no longer stall on a full ffmpeg stderr pipe.
- Fixed: reading a frame past the end of the video returns nothing instead of failing the eye step.
- Fixed: the parallel OCR phases tolerate the shared cache file being locked.
- Fixed: `-Uscita` ending with a backslash is passed correctly by leggi-video.ps1.
- Fixed: broken accented characters in two installa.sh messages.
- Fixed: downloads refuse live streams and files over 4 GB, with timeouts (60 s title, 2 h download).
- Fixed: when the eye is switched off in config.json, the story header says so.

## 0.7.10

- Added tables read from SCREEN, whole-sentence preservation and table frames, all off by default.

## 0.7.9

- Added VALUES and WHERE sections, off by default.

## 0.7.8

- Ran independent movement, transcription and OCR phases concurrently when memory permits.
- Preserved sequential execution through the `parallelo` setting and on systems with less than 6 GB available.
- Stopped all remaining phases after a failure or per-phase timeout.

## 0.7.7

- Decoded sampled movement frames through ffmpeg while preserving cv2-identical output.
- Retained only the first color frame and added automatic cv2 fallback.

## 0.7.6

- Prevented OCR phases from blocking indefinitely while closing CUDA worker pools.
- Flushed RapidOCR worker timing data after every completed task.

## 0.7.5

- Faster OCR discovery with vectorized widget matching and zone fusion.
- Complete timing log for OCR workers, Whisper loading and transcription, cursor matching, optical flow and connected components.

## 0.7.4

- Run public-package OCR on CPU by default and document experimental GPU OCR.
- Added a configurable per-phase timeout with process termination and exit code 4.
- Added a CUDA OCR startup probe with automatic CPU fallback.
- Added the "Why DemoBrief" workflow, measured token use and agent example.

## 0.7.3

- Renamed the public product and repository to DemoBrief.

## 0.7.2

- Review polish: early channel validation, Windows channel forwarding, clearer documentation and neutral source comments.

## 0.7.1

- Review fixes before publication: portability, process cleanup, documentation, licensing and packaging.

## 0.7.0

- First public release.
- Added Linux and macOS support.
