import os
import sys
import json
import logging

from flask import Flask, session, render_template, redirect, request, abort

sys.path.append("../tooling/")
import secret_registry
import app_data_registry
import validators

from mastodon import Mastodon

# Settings
CLIENT_NAME = "Trunkshow"
APP_PREFIX = "day08_trunkshow"
MASTO_SECRET = os.environ["MASTODON_SECRET"]
SCOPES_TO_REQUEST = ["read", "write"]
APP_BASE_URL = "/day08/"

# Logging setup
logging.basicConfig(
    stream = sys.stdout, 
    format = "%(levelname)s %(asctime)s - %(message)s", 
    level = logging.INFO
)

# Set the secret key to a random value
app = Flask(__name__)
app_data_registry.set_flask_session_info(app, APP_PREFIX, True)

# Instance URL normalizer
def norm_instance_url(instance):
    # Try to be permissive but also paranoid
    if "://" in instance:
        instance = "://".join(instance.split("://")[1:])
    if "/" in instance:        
        instance = instance.split("/")[0]
    if not validators.url("https://" + instance):
        abort(500)
    return instance

# Gets a client credential, creates app if needed
def get_client_credential(instance):
    # For registry, add https://
    instance = "https://" + instance

    # Lock credential DB
    secret_registry.lock_credentials()

    try:
        # Get app credential, register new if needed
        client_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "client")
        if not secret_registry.have_credential(client_credential):
            Mastodon.create_app(
                CLIENT_NAME,
                api_base_url = instance,
                scopes = SCOPES_TO_REQUEST,
                to_file = client_credential,
            )
            secret_registry.update_meta_time(client_credential)
    except:
        client_credential = None

    # Unlock credential DB
    secret_registry.release_credentials()

    return client_credential

@app.route('/')
def index():
    # Get args
    instance = request.args.get('instance')
    what = request.args.get('what')
    
    # Check if the user is already logged in
    if instance is None or what is None:
        return render_template('enter.htm')
    else:
        session["instance"] = instance
        session["what"] = what
        return render_template('gallery.htm')

@app.route('/posts')
def posts():
    # Get api
    instance = session["instance"]
    api = Mastodon(client_id = get_client_credential(instance))
    
    # Get posts
    what = session["what"]
    is_tag = False
    if what.startswith("#"):
        what = what[1:]
        is_tag = True
    elif what.startswith("@"):
        what = what[1:]
        is_tag = False
    else:
        is_tag = True
    if is_tag:
        posts = api.timeline_hashtag(what, only_media = True, local = True)
    else:
        account = api.account_lookup(what)
        posts = api.account_statuses(account, only_media = True)
    return render_template(
        'post_list.htm', 
        posts = posts,
        first_load = request.args.get('first_load')
    )

if __name__ == '__main__':
    # Run webapp
    app.run("0.0.0.0", debug = True)
