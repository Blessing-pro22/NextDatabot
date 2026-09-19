import os
from threading import Thread
from flask import Flask

app = Flask(__name__)


@app.route('/')
@app.route('/health')
def home():
  return 'Bot is online and running!', 200


def run():
  port = int(os.environ.get('PORT', 8080))
  app.run(host='0.0.0.0', port=port)


def keep_alive():
  t = Thread(target=run)
  t.daemon = True
  t.start()