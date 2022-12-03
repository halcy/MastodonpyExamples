from flask import Flask, make_response, request
import datetime

from latencies import LatencyWatcher

# Settings
accounts = [
    ("halcy", "mastodon.social"),
    ("halcy", "glitch.social"),
    ("latencyobs", "icosahedron.website"),
    ("latencyobs", "botsin.space"),
    ("halcy", "hachyderm.io"),
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
body {
    color: #FFFFFF;
    background: #102;
    font-size: 20px;
    font-family: Tahoma, Geneva, sans-serif;
    padding-left: 20px
}
table {
	border-collapse: collapse;
    font-family: Tahoma, Geneva, sans-serif;
}
table td {
	padding: 10px;
}
table tbody td {
	color: #FFFFFF;
	border: 1px solid #FFFFFF;
    font-weight: bold;
	font-size: 20px;
}
table tbody tr {
	background-color: #203;
}
table tbody tr:nth-child(odd) {
	background-color: #305;
}
a {
    color: #FFFFFF;
}
</style>
<h1>⟴ Mastodon Latency Observatory <span style="font-size:17px"><a href="https://github.com/halcy/MastodonpyExamples/tree/master/01_latency_observatory">on github</a></h1>
"""

FOOT = """
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
    for id1, account in enumerate(accounts):
        response += "<tr><td>from " + account[1] + "</td>"
        for id2, account2 in enumerate(accounts):
            if account == account2:
                response += "<td></td>"
            else:
                latency_data = latency_info[account2][account]
                lat_str = str(round(latency_data[0] * 1000, 2)) + "ms"
                lat_date = datetime.datetime.fromtimestamp(latency_data[1])
                lat_date_str = lat_date.astimezone(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')
                alt_text = lat_str + " at " + lat_date_str
                response += '<td><img src="plot?acc1={}&acc2={}" alt="{}"</td>'.format(str(id1), str(id2), alt_text)
        response += "</tr>"

    # Foot
    response += "</table>"
    return HEAD + response + FOOT

@app.route('/plot', methods=['GET'])
def send_png():
    args = request.args
    account = accounts[min(max(0, int(args.get("acc1"))), len(accounts) - 1)]
    account2 = accounts[min(max(0, int(args.get("acc2"))), len(accounts) - 1)]
    binary_data = watcher.get_latencies_graph(account, account2)
    resp = make_response(binary_data)
    resp.headers['Content-Type'] = "image/png"
    return resp

if __name__ == '__main__':
    app.run()
