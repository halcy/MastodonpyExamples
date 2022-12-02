import sys
import os
import time
import logging
import threading
import pickle
from functools import partial

sys.path.append("../tooling/")
import secret_registry

from mastodon import Mastodon, streaming

# Hardcoded settings
TIME_BETWEEN_PINGS = 60 * 5
TIME_BEFORE_DELETE = 10
SECRET = os.environ["MASTODON_SECRET"]
LATENCY_TEST_FRAME = "____________LATENCYPING____________"

class LatencyWatcher():
    def __init__(self, accounts):
        # Store parameters
        self.accounts = accounts

        # Logging setup
        logging.basicConfig(
            stream = sys.stdout, 
            format = "%(levelname)s %(asctime)s - %(message)s", 
            level = logging.INFO
        )

        # Storage for latencies
        self.latency_info = {}
        for account in self.accounts:
            self.latency_info[account] = {}
            for account2 in self.accounts:
                self.latency_info[account][account2] = (100000, 0)

        # Log in
        self.apis = {}
        self.mention_strings = {}
        for account in self.accounts:
            cred_file = secret_registry.get_name_for("day01latencyobs", SECRET, account[1], "user", account[0])
            self.apis[account] = Mastodon(access_token = cred_file)

            mention_str = ""
            for account2 in self.accounts:
                if account2 != account:
                    mention_str = mention_str + "@" + account2[0] + "@" + account2[1] + " "
            self.mention_strings[account] = mention_str

            logging.info("Logged into " + str(account) + " = " + self.apis[account].me().acct)

    def start(self):
        # Write workers
        def write_worker(account):
            api = self.apis[account]
            mention_str = self.mention_strings[account]
            while True:
                prev_status = None
                try:
                    logging.info("Attempting post for " + str(account))
                    prev_status = api.status_post(
                        status = mention_str + LATENCY_TEST_FRAME + str(time.time()) + LATENCY_TEST_FRAME,
                        visibility = "direct"
                    )
                except Exception as e:
                    logging.warn("Status post failed for " + str(account) + ", reason was " + str(e))
                time.sleep(TIME_BEFORE_DELETE)
                try:
                    api.status_delete(prev_status)
                except Exception as e:
                    logging.warn("Status delete failed for " + str(account) + ", reason was " + str(e))
                time.sleep(TIME_BETWEEN_PINGS)

        # Latency logger using streaming API
        def log_latency(account, notification):
            try:
                now = time.time()
                if notification.type == "mention":
                    text = notification.status.content
                    if LATENCY_TEST_FRAME in text:
                        latency_time = float(text.split(LATENCY_TEST_FRAME)[1])
                        latency = now - latency_time
                        account2 = tuple(notification.status.account.acct.split("@"))
                        logging.info("New read for " + str(account) + " from " + str(account2) + " -> " + str(latency))
                        self.latency_info[account][account2] = (latency, now)
            except Exception as e:
                logging.warn("Failed to log latency for " + str(account) + ", reason was " + str(e))

        # Start readers
        self.readers = {}
        for account in self.accounts:
            logging.info("Starting reader for " + str(account))
            listener = streaming.CallbackStreamListener(
                notification_handler = partial(log_latency, account)
            )
            self.readers[account] = self.apis[account].stream_user(
                listener,
                run_async = True,
                reconnect_async = True
            )

        # Wait a moment
        time.sleep(1)

        # Start writers
        self.writers = {}
        for account in self.accounts:
            self.writers[account] = threading.Thread(target=write_worker, args=(account,))
            self.writers[account].start()

    def get_latencies(self):
        return self.latency_info
