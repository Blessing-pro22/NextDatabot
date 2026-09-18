"""
Run this to start everything:

    python run.py

Starts the Flask webhook server (Paystack + Hubnet callbacks) in a
background thread, then runs the Telegram bot's polling loop in the main
thread.

For production, consider running webhook_server.py under a real WSGI
server (gunicorn/uwsgi) behind Nginx instead of Flask's dev server, and
running bot.py as a separate systemd service - the threaded setup here is
meant to get you running quickly and works fine for small-to-medium load.
"""

import logging
import threading

import main
import webhook_server

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    server_thread = threading.Thread(target=webhook_server.run, daemon=True, name="webhook-server")
    server_thread.start()

    main.run_polling()
