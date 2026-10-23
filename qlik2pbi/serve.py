"""Servidor local de la misma página que se despliega en Vercel."""

from __future__ import annotations

from http.server import ThreadingHTTPServer

from qlik2pbi.http_api import handler


def crear_servidor(host: str = "127.0.0.1", puerto: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, puerto), handler)


def main() -> None:
    servidor = crear_servidor()
    host, puerto = servidor.server_address[:2]
    print(f"qlik2pbi en http://{host}:{puerto}")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.server_close()


if __name__ == "__main__":
    main()
