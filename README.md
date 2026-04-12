# Mastodon.py Examples

This repository contains several small example apps for Mastodon.py, to allow you to get a better understanding of how to use the library in practice. They are all flask apps - to run them, install the requiremets from requirements.txt, modify the main script in the folder you are interested in so it matches what port and address you want the app to be listening on, and run it. You can find a sample deployment of all of them on https://mastolab.kal-tsit.halcy.de/

Repository structure:
* **tooling/login_oauth.py** - Example of a console script copy-code-from-webbrowser oauth login flow.
* **tooling/secret_registry.py** - A credential file registry. Stores auth token info for users. NB: If you're writing a big production app, storing such credentials in a proper database and possibly with encryption in rest would be advisable - this is just a simple example.
* **01_latency_observatory/** - Obsolete, consider **02_nicer_latency_observatory/** instead
* **02_nicer_latency_observatory/** - https://mastolab.kal-tsit.halcy.de/day02/ - A tool measuring end-to-end client-sends-to-other-client-receives latency between different mastodon servers. Static logins, basic posting and looking for posts, background processing.
* **03_clippy/** - https://mastolab.kal-tsit.halcy.de/day03/ - An (opt-in) "similar users" finder. Full web based oauth login flow, lists. (nb: Written before Exclusive Lists existed, could be improved using Exclusive Lists now)
* **04_alphant/** - https://mastolab.kal-tsit.halcy.de/day04/ - A client that shows new post by users in alphabetic order. Basic client functionality.
* **05_toot_it_forward/** - https://mastolab.kal-tsit.halcy.de/day05/ - A client that lets you post for the next user. State keeping between different user sessions.
* **06_mastomash/** - https://mastolab.kal-tsit.halcy.de/day06/ - A client with randomized UI strings. Fully usable (though not complete) client functionality.
* **07_florps/** - https://mastolab.kal-tsit.halcy.de/day07/ - Tweets are now this. Timeline goes sideways. Fully usable (though not complete) client functionality.
* **08_trunkshow/** - https://mastolab.kal-tsit.halcy.de/day08/ - Tag / user media viewer with ken burns effect. Non-authed access, tags, media access.
* **09_hellomotoot/** - https://mastolab.kal-tsit.halcy.de/day09/ - A PSTN client. Basic client functionality integrated with an external service (Twilio). More complex login flows and state. Hosted version often nonfunctional (depends on Twilio account being funded).

Some other examples, outside of this repository:
* https://kal-tsit.halcy.de/client_side_mastopy/web_client.htm - A fully client-side (running python in the browser) basic functionality mastodon client. in-browser python, fully client-side oauth login flow. To get source code, right-click -> view source (no hosted components!)
* https://kal-tsit.halcy.de/client_side_mastopy/api_explorer.htm - A fully client-side (running python in the browser) mastodon.py based API playground. Explore endpoints, docs, try different calls, on your actual account! Best enjoyed while also perusing the documentation: https://mastodonpy.readthedocs.io/en/stable/ .  To get source code, right-click -> view source (no hosted components!)
* https://mastolab.kal-tsit.halcy.de/mastofuse.py - A file system based Mastodon client using FUSE - mount your account!
* https://github.com/halcy/Mastodon-LaTeXclient - A Mastodon client that generates PDFs using LaTeX - look like you're doing science while browsing your timeline!
* https://github.com/halcy/tootmage - A terminal multicolumn client. In a semi-broken alpha state. Lots of UI work in progress, an ongoing experiment to figure out how to make it pleasant to interact with Mastodon in a TUI.
