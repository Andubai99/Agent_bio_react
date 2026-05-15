from __future__ import annotations

from datetime import datetime

from agent_server.schemas import OmniParserStatus
from modules.ui_parser.omniparser import OmniParserConfig, OmniParserService


class OmniParserManager:
    def __init__(self, *, config: OmniParserConfig | None = None):
        self.config = config or OmniParserConfig.from_env()
        self.service = OmniParserService(self.config)
        self._state = "stopped"
        self._message = "OmniParser is stopped."
        self._last_probe_at: str | None = None

    def status(self) -> OmniParserStatus:
        probe_ok = self._probe()
        process = self.service.process
        pid = process.pid if process is not None and process.poll() is None else None
        owned = self.service.started_by_us and pid is not None

        if probe_ok:
            state = "running"
            message = "OmniParser API ready."
        elif self._state in {"starting", "stopping"}:
            state = self._state
            message = self._message
        elif process is not None and process.poll() is not None and self.service.started_by_us:
            state = "failed"
            message = f"Owned OmniParser process exited with code {process.returncode}."
        else:
            state = "stopped"
            message = "OmniParser is stopped or unreachable."

        self._state = state
        self._message = message
        return self._status(state=state, probe_ok=probe_ok, owned=owned, pid=pid, message=message)

    def start(self) -> OmniParserStatus:
        current = self.status()
        if current.probe_ok:
            return current

        self._state = "starting"
        self._message = "Starting OmniParser service."
        try:
            self.service.ensure_running()
        except Exception as exc:
            self._state = "failed"
            self._message = str(exc)
            return self.status()

        self._state = "running"
        self._message = "OmniParser API ready."
        return self.status()

    def stop(self) -> OmniParserStatus:
        self._state = "stopping"
        self._message = "Stopping OmniParser service."
        self.service.shutdown_if_owned()
        self._state = "stopped"
        self._message = "OmniParser is stopped."
        return self.status()

    def _probe(self) -> bool:
        self._last_probe_at = datetime.now().astimezone().isoformat(timespec="seconds")
        return self.service.probe(timeout=2.0)

    def _status(
        self,
        *,
        state: str,
        probe_ok: bool,
        owned: bool,
        pid: int | None,
        message: str,
    ) -> OmniParserStatus:
        return OmniParserStatus(
            state=state,  # type: ignore[arg-type]
            probe_ok=probe_ok,
            owned=owned,
            pid=pid,
            host=self.config.host,
            port=self.config.port,
            root=str(self.config.root),
            last_probe_at=self._last_probe_at,
            message=message,
        )
