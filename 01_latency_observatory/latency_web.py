from flask import Flask
import datetime

from latencies import LatencyWatcher

# Settings
accounts = [
    ("halcy", "mastodon.social"),
    ("halcy", "glitch.social"),
    ("latencyobs", "icosahedron.website"),
]

watcher = LatencyWatcher(accounts)
watcher.start()

HEAD = """
<html>
<head>
<title>Mastodon Latency Observatory</title>
</head>
<body>
<style type="text/css">
table {
	border-collapse: collapse;
    font-family: Tahoma, Geneva, sans-serif;
}
table td {
	padding: 15px;
}
table thead td {
	background-color: #54585d;
	color: #ffffff;
	font-weight: bold;
	font-size: 13px;
	border: 1px solid #54585d;
}
table tbody td {
	color: #636363;
	border: 1px solid #dddfe1;
}
table tbody tr {
	background-color: #f9fafb;
}
table tbody tr:nth-child(odd) {
	background-color: #ffffff;
}
</style>
<h1>Mastodon Latency Observatory</h1>
"""

FOOT = """
<a href="https://github.com/halcy/MastodonpyExamples/tree/master/01_latency_observatory">source code</a>
</body></html>
"""
app = Flask(__name__)
@app.route('/')
def base_page():
    # Get info from watcher
    latency_info = watcher.get_latencies()

    # Head
    response = "<table><tr><td></td>"
    for account in accounts:
        response += "<td>to " + account[1] + "</td>"
    response += "</tr>"

    # Rows
    for account in accounts:
        response += "<tr><td>from " + account[1] + "</td>"
        for account2 in accounts:
            if account == account2:
                response += "<td></td>"
            else:
                latency_data = latency_info[account2][account]
                lat_str = str(round(latency_data[0] * 1000, 2)) + "ms"
                lat_date = datetime.datetime.fromtimestamp(latency_data[1])
                lat_date_str = lat_date.astimezone(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                response += "<td>" + lat_str + "<br/>at " + lat_date_str + " UTC</td>"
        response += "</tr>"

    # Foot
    response += "</table>"
    return HEAD + response + FOOT

if __name__ == '__main__':
    app.run()
