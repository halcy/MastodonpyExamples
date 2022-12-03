import sys
import os
import time
import logging
import threading
import numpy as np
import matplotlib.pyplot as plt
from functools import partial
import datetime
import matplotlib as mpl
import matplotlib.dates as mdates
import io
import copy

sys.path.append("../tooling/")
import secret_registry

from mastodon import Mastodon, streaming

# Hardcoded settings
TIME_BETWEEN_PINGS = 60 * 5
TIME_BEFORE_DELETE = 10
SECRET = os.environ["MASTODON_SECRET"]
LATENCY_TEST_FRAME = "____________LATENCYPING____________"
LATENCY_CACHE_MAX = 7 * 24 * (60 * 60) // TIME_BETWEEN_PINGS
LATENCY_CACHE_MEAN_OVER_LAST = 3 * (60 * 60) // TIME_BETWEEN_PINGS

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
        self.latency_info_cached = {}
        self.latency_plot_cached = {}
        self.latency_dirty = {}
        self.latency_dirty_any = True
        for account in self.accounts:
            self.latency_info[account] = {}
            self.latency_info_cached[account] = {}
            self.latency_dirty[account] = {}
            self.latency_plot_cached[account] = {}
            for account2 in self.accounts:
                self.latency_info[account][account2] = []
                self.latency_info_cached[account][account2] = (10000, 0)
                self.latency_dirty[account][account2] = True
                self.latency_plot_cached[account][account2] = None

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
                        self.latency_info[account][account2].append((latency, now))
                        self.latency_info[account][account2] = self.latency_info[account][account2][-LATENCY_CACHE_MAX:]
                        self.latency_dirty[account][account2] = True
                        self.latency_dirty_any = True
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
        # Update cache if dirty
        if self.latency_dirty_any:
            self.latency_dirty_any = False
            for account in self.accounts:
                for account2 in self.accounts:
                    if len(self.latency_info[account][account2]) > 0:
                        self.latency_info_cached[account][account2] = (
                                np.mean(list(map(lambda x: x[0], self.latency_info[account][account2][-LATENCY_CACHE_MEAN_OVER_LAST:]))),
                                self.latency_info[account][account2][-1][1]
                        )
                    else:
                        self.latency_info_cached[account][account2] = (10000, 0)
        
        # Return cached
        return self.latency_info_cached

    def get_latencies_graph(self, account, account2):
        if self.latency_dirty[account][account2]:
            # Refresh cache if needed
            COLOR = 'white'
            mpl.rcParams['text.color'] = COLOR
            mpl.rcParams['axes.labelcolor'] = COLOR
            mpl.rcParams['xtick.color'] = COLOR
            mpl.rcParams['ytick.color'] = COLOR
            mpl.rcParams['axes.edgecolor'] = COLOR

            latency_val = self.get_latencies()[account][account2][0]
            self.latency_dirty[account][account2] = False
            latency_copy = copy.deepcopy(self.latency_info)
            
            data_x = list(map(lambda x: datetime.datetime.fromtimestamp(x[1]).astimezone(datetime.timezone.utc), latency_copy[account][account2]))
            data_y = list(map(lambda x: x[0] * 1000, latency_copy[account][account2]))

            # Basic matplotlib plot
            fig = plt.figure(figsize=(3.5, 3.5))
            ax = fig.add_subplot(111)
            ax.plot(data_x, data_y, marker="o", color=COLOR)
            ax.set_title("{} ->\n{}\nMean[50]: {}ms".format(account[1], account2[1], str(round(latency_val * 1000, 2)) + "ms" ))
            ax.set_xlabel("Time")
            ax.set_ylabel("Latency [ms]")
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%b%d\n%H:%M'))
            ax.set_xticks(list(map(
                lambda x: datetime.datetime.fromtimestamp(x).astimezone(datetime.timezone.utc),
                np.linspace(data_x[0].timestamp(), data_x[-1].timestamp(), 5)
            )))
            ax.set_xlim(data_x[0], data_x[-1])
            fig.tight_layout()

            # Save the figure to a PNG file in memory
            buf = io.BytesIO()
            fig.savefig(buf, format='png', transparent=True, bbox_inches=0)

            # Get the PNG image data from the BytesIO object
            self.latency_plot_cached[account][account2] = buf.getvalue()
        return self.latency_plot_cached[account][account2]