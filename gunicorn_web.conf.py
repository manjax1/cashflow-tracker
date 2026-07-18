"""Gunicorn config for the web agent service (Railway).

Reads PORT from the environment in Python — immune to the shell-expansion
issue where an exec-form start command passes a literal '$PORT' string.

Start command:  gunicorn -c gunicorn_web.conf.py src.agent.web:app
"""

import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5555')}"
workers = 1          # sessions, rate limiter, and agent cache are in-memory
threads = 4          # allow a few concurrent requests within the worker
timeout = 120        # agent chat turns can take a while
accesslog = "-"      # request logs to Railway's log stream
