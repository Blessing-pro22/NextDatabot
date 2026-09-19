import os
from threading import Thread
from flask import Flask

app = Flask(__name__)


@app.route('/')
def home():
  return 'Bot is online and running!'


def run():
  # Render automatically injects the PORT variable
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)


def keep_alive():
  # Runs the Flask server on a background thread
  t = Thread(target=run)
  t.daemon = True
  t.start()