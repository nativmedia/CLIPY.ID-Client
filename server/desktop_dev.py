"""Run the development API until its desktop launcher closes the stdin pipe."""

import os
import sys
from threading import Thread

import uvicorn


class DesktopServer(uvicorn.Server):
    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            # Load NumPy and other native libraries before reading the pipe.
            # On Windows they can stall if stdin is already blocked in a reader.
            Thread(target=self.watch_launcher, daemon=True).start()

    def watch_launcher(self):
        try:
            os.read(sys.stdin.fileno(), 1)
        finally:
            self.should_exit = True


if __name__ == "__main__":
    server = DesktopServer(uvicorn.Config("app:app", host="127.0.0.1", port=4758))
    server.run()
    if not server.started:
        raise SystemExit(1)
