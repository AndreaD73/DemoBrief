# Changelog

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
