import asyncio
from dataclasses import dataclass

@dataclass
class GlobalState:
    paused: bool = False
    domain_paused: dict = None
    
    # Event to notify workers when state changes (optional, but good for blocking waits)
    _pause_event: asyncio.Event = None

    def __post_init__(self):
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Set means "running", clear means "paused"
        if self.domain_paused is None:
            self.domain_paused = {
                "stocks": False,
                "events": False,
                "sports": False,
            }

    @property
    def is_paused(self) -> bool:
        return self.paused

    def set_paused(self, paused: bool):
        self.paused = paused
        if paused:
            self._pause_event.clear()
        else:
            self._pause_event.set()

    def set_domain_paused(self, domain: str, paused: bool):
        if domain not in self.domain_paused:
            raise ValueError(f"Unknown domain: {domain}")
        self.domain_paused[domain] = paused

    def is_domain_paused(self, domain: str) -> bool:
        return bool(self.domain_paused.get(domain, False))

    async def wait_if_paused(self):
        """Block execution if paused until resumed."""
        await self._pause_event.wait()

state = GlobalState()
