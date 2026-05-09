# Error Handling Layer TODO

The new loop intentionally has no recovery strategy yet. Future work should add
an error handling layer around these stages:

- OmniParser service startup and probe.
- Window connection and activation.
- Screenshot capture.
- OmniParser UI parsing.
- Reasoner decision generation.
- Element-index action execution.
- Post-action visual verification.

For the current refactor, failures should return explicit error codes and stop
the run instead of attempting recovery.
