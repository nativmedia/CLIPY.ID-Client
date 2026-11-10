"""Run the development API until its desktop launcher closes the stdin pipe."""

import os
import sys
from threading import Thread

import uvicorn


def main():
    server = uvicorn.Server(uvicorn.Config("app:app", host="127.0.0.1", port=4758))

    def watch_launcher():
        try:
            os.read(sys.stdin.fileno(), 1)
        finally:
            server.should_exit = True

    Thread(target=watch_launcher, daemon=True).start()
    server.run()
    if not server.started and not server.should_exit:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
