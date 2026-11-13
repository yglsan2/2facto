import logging
import os
import threading
import webbrowser

from folio.web import create_app


def main():
    logging.getLogger("facturx").setLevel(logging.ERROR)
    app = create_app()
    port = int(os.environ.get("FOLIO_PORT", "8765"))
    url = f"http://127.0.0.1:{port}"
    if os.environ.get("FOLIO_OPEN") == "1":
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    print(f"2facto — {url}")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
