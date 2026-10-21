"""A real Radicale CalDAV server in a background thread, for integration tests."""

import socket
import threading
from contextlib import contextmanager
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import radicale
from radicale import config as rconfig


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):  # keep test output clean
        pass


class _Threaded(WSGIServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread, args=(request, client_address))
        t.daemon = True
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def radicale_server(folder: str):
    """Yields the base URL. Any username/password is accepted (auth type none)."""
    configuration = rconfig.load(())
    configuration.update(
        {
            "storage": {"filesystem_folder": folder},
            "auth": {"type": "none"},
            "rights": {"type": "owner_only"},
            "logging": {"level": "warning"},
        },
        "tahgul-tests",
    )
    app = radicale.Application(configuration)
    port = _free_port()
    server = make_server("127.0.0.1", port, app, server_class=_Threaded, handler_class=_Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        server.shutdown()
        server.server_close()
