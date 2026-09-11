# Sada eval dataset

Sada needs a real audio file for Local Whisper, so it is not part of the
generic JSON-driven runner (`scripts/run_evals.py`). Exercise it with:

```
python scripts/smoke_lab_characters.py --audio path/to/sample.wav
```

A dedicated `evals/datasets/sada/*.wav` fixture set (clear + noisy Arabic
audio, per spec section 19's WER targets) is a release blocker before A2/A3,
not something this seed harness fabricates.
