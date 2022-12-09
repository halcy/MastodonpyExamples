import os
import sys
import json
import logging
import traceback
import random
import datetime

from flask import Flask, session, render_template, redirect, request, abort, g

sys.path.append("../tooling/")
import secret_registry
import app_data_registry
import validators

from mastodon import Mastodon, streaming

# Settings
CLIENT_NAME = "Florps"
APP_PREFIX = "day07_florps"
MASTO_SECRET = os.environ["MASTODON_SECRET"]
SCOPES_TO_REQUEST = ["read", "write"]
OAUTH_TARGET_URL = "https://mastolab.kal-tsit.halcy.de/day07/auth"
APP_BASE_URL = "/day07/"

with open("ui_data.json", "rb") as f:
    ui_strings = json.load(f)

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

# Grabs data from session
def get_session_user():
    user_name = session["user_name"]
    instance = norm_instance_url(session["instance"])
    account = "{}@{}".format(user_name, instance)
    return user_name, instance, account

# Cleans out the session
def log_off_session():
    session["logged_in"] = False
    try:
        del session["user_name"] 
    except:
        pass
    try:
        del session["instance"] 
    except:
        pass

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
                redirect_uris = [OAUTH_TARGET_URL]
            )
            secret_registry.update_meta_time(client_credential)
    except:
        client_credential = None

    # Unlock credential DB
    secret_registry.release_credentials()

    return client_credential

def process_emoji(text, emojis):
    for emoji in emojis:
        text = text.replace(":{}:".format(emoji.shortcode), '<img src="{}" style="height: 16px;" alt="{}" />'.format(emoji.url, emoji.shortcode))
    return text
app.jinja_env.globals.update(process_emoji = process_emoji)

def ui(string):
    return random.choice(ui_strings[string])
app.jinja_env.globals.update(ui = ui)

def dateformat(datetime_object):
    format = random.choice(ui_strings["time_formats"])
    if format == "SEPTEMBER":
        sept1st1993 = datetime.datetime(1993, 9, 1, tzinfo = datetime.timezone.utc)
        delta = datetime_object - sept1st1993
        return "September {}, 1993, {}:{}".format(delta.days, datetime_object.hour, datetime_object.minute)
    else:
        return datetime_object.strftime(format)
app.jinja_env.globals.update(dateformat = dateformat)

@app.route('/')
def index():
    # Check if the user is already logged in
    if not "logged_in" in session or session["logged_in"] == False:
        # Render the login page if the user is not logged in
        return render_template('login.htm')
    else:
        # Get user data from session
        user_name, instance, account = get_session_user()

        # Get args
        min_id = request.args.get('min_id')
        max_id = request.args.get('max_id')

        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        api = Mastodon(access_token = user_credential, request_timeout = 10)
        posts = api.timeline_home(
            limit = 40,
            min_id = min_id,
            max_id = max_id
        )

        # Render
        next_id = posts._pagination_prev["min_id"] + 1
        return render_template(
            'authed.htm', 
            account = account,
            posts = posts,
            next_id = next_id
        )

@app.route('/posts')
def posts():
    # Check if the user is logged in
    if not "logged_in" in session or session["logged_in"] == False:
        # Back to root
        return redirect(APP_BASE_URL)
    else:
        # Get user data from session
        user_name, instance, account = get_session_user()

        # Get args
        min_id = request.args.get('min_id')
        max_id = request.args.get('max_id')

        # Get posts
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        api = Mastodon(access_token = user_credential, request_timeout = 10)
        posts = api.timeline_home(
            limit = 40,
            min_id = min_id,
            max_id = max_id
        )

        # Render
        if len(posts) == 0:
            next_id = min_id
        else:
            next_id = posts._pagination_prev["min_id"] + 1
        if max_id is not None:
            next_id = 0
        
        return render_template(
            'post_list.htm', 
            account = account,
            posts = posts,
            next_id = next_id
        )

@app.route('/post_status', methods=["POST"])
def post_status():
    # Get user info
    user_name, instance, _ = get_session_user()

    # Send post
    post_text = request.form['text']
    try:
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        api = Mastodon(access_token = user_credential, request_timeout = 10)
        api.status_post(post_text)
    except:
        pass
    
    # Back to root
    return redirect(APP_BASE_URL)

@app.route('/reply', methods=["POST"])
def reply():
    # Get user info
    user_name, instance, account = get_session_user()

    # Get params
    reply_to = int(request.form['to'])
    reply_text = request.form['text']

    # Send reply
    try:
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        api = Mastodon(access_token = user_credential, request_timeout = 10)
        reply_to_post = api.status(reply_to)
        api.status_reply(reply_to_post, reply_text)
    except:
        pass

    # Back to root
    return ""

@app.route('/boost', methods=["POST"])
def boost():
    # Get user info
    user_name, instance, account = get_session_user()

    # Get params
    which = int(request.form['which'])

    # Send reply
    post_updated = None
    try:
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        api = Mastodon(access_token = user_credential, request_timeout = 10)
        post_updated = api.status_reblog(which)
    except:
        pass

    # Back to root
    return render_template('button_boost.htm', post = post_updated)

@app.route('/unboost', methods=["POST"])
def unboost():
    # Get user info
    user_name, instance, account = get_session_user()

    # Get params
    which = int(request.form['which'])

    # Send reply
    post_updated = None
    try:
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        api = Mastodon(access_token = user_credential, request_timeout = 10)
        post_updated = api.status_unreblog(which)
    except:
        pass

    # Back to root
    return render_template('button_boost.htm', post = post_updated)

@app.route('/fav', methods=["POST"])
def fav():
    # Get user info
    user_name, instance, account = get_session_user()

    # Get params
    which = int(request.form['which'])

    # Send reply
    post_updated = None
    try:
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        api = Mastodon(access_token = user_credential, request_timeout = 10)
        post_updated = api.status_favourite(which)
    except:
        pass

    # Back to root
    return render_template('button_fav.htm', post = post_updated)

@app.route('/unfav', methods=["POST"])
def unfav():
    # Get user info
    user_name, instance, account = get_session_user()

    # Get params
    which = int(request.form['which'])

    # Send reply
    post_updated = None
    try:
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        api = Mastodon(access_token = user_credential, request_timeout = 10)
        post_updated = api.status_unfavourite(which)
    except:
        pass

    # Back to root
    return render_template('button_fav.htm', post = post_updated)

@app.route('/revoke')
def revoke():
    # Get user info
    user_name, instance, account = get_session_user()

    # Lock credential DB
    secret_registry.lock_credentials()

    # Revoke token
    try:
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        secret_registry.revoke_credential(user_credential)
    except:
        pass
    
    # Also clear the session
    log_off_session()

    # Unlock credential DB
    secret_registry.release_credentials()

    # Back to root
    return redirect(APP_BASE_URL)

@app.route('/auth')
def auth():
    # Get the oauth code
    oauth_code = request.args.get('code')
    instance = norm_instance_url(request.args.get('state'))
    
    # Get client credential and create API
    client_credential = get_client_credential(instance)
    api = Mastodon(client_id = client_credential)

    # Lock credential DB
    secret_registry.lock_credentials()

    # Log in
    try:
        user_credential_temp = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", oauth_code)
        api.log_in(
            code = oauth_code,
            to_file = user_credential_temp,
            scopes = SCOPES_TO_REQUEST,
            redirect_uri = OAUTH_TARGET_URL,
        )
        secret_registry.update_meta_time(user_credential_temp)

        # Move to final place
        user_name = api.me().acct.split("@")[0]
        user_credential_final = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        secret_registry.move_credential(user_credential_temp, user_credential_final)
    
        # Store session data
        session["logged_in"] = True
        session["user_name"] = user_name
        session["instance"] = instance
    except Exception as e:
        # Error handling (showing the user that something went wrong) is future work
        logging.warning("Auth error" + str(e))

    # Unlock credential DB
    secret_registry.release_credentials()

    return redirect(APP_BASE_URL)

@app.route('/login', methods=['POST'])
def login():
    try:
        # Get a client
        instance = norm_instance_url(request.form['instance'])
        client_credential = get_client_credential(instance)
        print(client_credential)

        # Try to be permissive
        if "://" in instance:
            instance = "://".join(instance.split("://")[1:])
        if "/" in instance:        
            instance = instance.split("/")[0]

        # Create a Mastodon api instance and generate oauth URL
        api = Mastodon(client_id = client_credential)
        login_url = api.auth_request_url(
            scopes = SCOPES_TO_REQUEST,
            redirect_uris = OAUTH_TARGET_URL,
            state = instance
        )

        # Redirect the user to the OAuth login URL
        return redirect(login_url)
    except:
        traceback.print_exc()
        log_off_session()
        return redirect(APP_BASE_URL)

if __name__ == '__main__':
    # Run webapp
    app.run("0.0.0.0", debug = True)
