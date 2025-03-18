import os
import sys
import time
import json
import logging
import traceback
import uuid
import pickle

from flask import Flask, session, render_template, redirect, request, abort, g
import sqlite3

sys.path.append("../tooling/")
import secret_registry
import app_data_registry
import validators

from mastodon import Mastodon, streaming

# Settings
CLIENT_NAME = "TootItForward"
APP_PREFIX = "day05_tootitforward_r1"
MASTO_SECRET = os.environ["MASTODON_SECRET"]
SCOPES_TO_REQUEST = ["read:accounts", "read:statuses", "write:statuses"]
SCOPES_FALLBACK = ["read", "write"]
OAUTH_TARGET_URL = "https://mastolab.kal-tsit.halcy.de/day05/auth"
APP_BASE_URL = "/day05/"

# Logging setup
logging.basicConfig(
    stream = sys.stdout, 
    format = "%(levelname)s %(asctime)s - %(message)s", 
    level = logging.INFO
)

# Set the secret key to a random value
app = Flask(__name__)
app_data_registry.set_flask_session_info(app, APP_PREFIX, True)

# Globals
state_file = app_data_registry.get_state_file(APP_PREFIX)
if os.path.exists(state_file):
    with open(state_file, 'rb') as f:
        post_register, instance_register, current_post_uuid = pickle.load(f)
else:
    post_register = {
        "initial": ("halcy@icosahedron.website", "First post!", {
            "acct": "halcy@icosahedron.website",
            "display_name": "halcy",  
            "avatar": "https://icosahedron.website/system/accounts/avatars/000/000/001/original/media.jpg"
        })
    }
    instance_register = {}
    current_post_uuid = "initial"

# DB stuff
def get_db():
    # Connect to DB
    db = getattr(g, '_database', None)
    if db is None:
        db = sqlite3.connect(app_data_registry.get_db_file(APP_PREFIX))
        db.row_factory = sqlite3.Row
        g._database = db
    return db

def query_db(query, args=(), single = False):
    with app.app_context():
        # SQL query function
        db =  get_db()
        cursor = db.execute(query, args)
        data = cursor.fetchall()
        cursor.close()
        db.commit()
        return (data[0] if data else None) if single else data

@app.teardown_appcontext
def close_connection(exception):
    # Close DB connection on teardown
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# Set up the DB initially, if empty
table_exists = query_db("SELECT name FROM sqlite_master WHERE type='table' AND name='posts'", single = True) is not None
if not table_exists:
    query = """
    CREATE TABLE posts (
        account_from TEXT,
        account_to TEXT,
        post TEXT,
        time NUMERIC
    )
    """
    query_db(query)

# Update post in the DB
def db_add_post(account, other_account, post):
    # Convert arguments to DB form
    post_json = json.dumps(post, default=str)
    other_account = other_account.lower()

    # Insert or update, depending
    query = """
    INSERT INTO posts (account_from, account_to, post, time)
    VALUES (?, ?, ?, ?)
    """
    query_db(query, (account, other_account, post_json, time.time()))

# Get current posts from DB
def db_get_posts():
    query = """
        SELECT post
        FROM posts
        WHERE time > ? AND time < ?
        ORDER BY time
        DESC
        """
    posts_json = query_db(query, (time.time() - 60 * 60 * 24 * 2, time.time() - 60 * 60 * 24))
    posts = [json.loads(x[0], object_hook=Mastodon._Mastodon__json_hooks) for x in posts_json]
    return posts

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

    fallback_scopes = False
    try:
        # Get app credential, register new if needed
        client_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "client")
        if not secret_registry.have_credential(client_credential):
            try:
                Mastodon.create_app(
                    CLIENT_NAME,
                    api_base_url = instance,
                    scopes = SCOPES_TO_REQUEST,
                    to_file = client_credential,
                    redirect_uris = [OAUTH_TARGET_URL]
                )
            except:
                # Special akkoma fallback
                Mastodon.create_app(
                    CLIENT_NAME,
                    api_base_url = instance,
                    scopes = SCOPES_FALLBACK,
                    to_file = client_credential,
                    redirect_uris = [OAUTH_TARGET_URL]
                )
                fallback_scopes = True
            secret_registry.update_meta_time(client_credential)
    except:
        client_credential = None

    # Unlock credential DB
    secret_registry.release_credentials()

    return client_credential, fallback_scopes

def process_emoji(text, emojis):
    for emoji in emojis:
        text = text.replace(":{}:".format(emoji.shortcode), '<img src="{}" style="height: 16px;" alt="{}" />'.format(emoji.url, emoji.shortcode))
    return text
app.jinja_env.globals.update(process_emoji = process_emoji)

@app.route('/')
def index():
    # Get the posts
    posts = db_get_posts()

    # Render the login page if the user is not logged in
    return render_template('login.htm', posts = posts)

@app.route('/auth')
def auth():
    # Global vars
    global post_register
    global instance_register
    global current_post_uuid
    global state_file

    # Get the oauth code
    uuid_str = request.args.get('state')
    oauth_code = request.args.get('code')
    instance = norm_instance_url(instance_register[uuid_str])
    del instance_register[uuid_str]

    # Get client credential and create API
    client_credential, _ = get_client_credential(instance)
    api = Mastodon(client_id = client_credential)

    # Lock credential DB (we also use this lock for everything else)
    secret_registry.lock_credentials()

    # Log in
    try:
        # Authenticate
        try:
            api.log_in(
                code = oauth_code,
                scopes = SCOPES_TO_REQUEST,
                redirect_uri = OAUTH_TARGET_URL,
            )
        except:
            # Special akkoma fallback
            api.log_in(
                code = oauth_code,
                scopes = SCOPES_FALLBACK,
                redirect_uri = OAUTH_TARGET_URL,
            )
        account = api.me().acct

        # Send the post
        current_post_user, current_post, current_post_account = post_register[current_post_uuid]
        post = api.status_post(current_post)
        post["account"]["acct"] += instance
        del post_register[current_post_uuid]
        post_register[uuid_str] = (account, post_register[uuid_str][1], post.account)
        current_post_uuid = uuid_str
        
        # Dump state
        with open(state_file, 'wb') as f:
            pickle.dump((post_register, instance_register, current_post_uuid), f)

        # Store in DB
        post["post_from"] = current_post_account
        db_add_post(current_post_user, account, post)

        # Revoke credential immediately
        api.revoke_access_token()
    except Exception as e:
        # Error handling (showing the user that something went wrong) is future work
        logging.warning("Auth error" + str(e))

    # Unlock credential DB
    secret_registry.release_credentials()

    return redirect(APP_BASE_URL)

@app.route('/login', methods=['POST'])
def login():
    # Sanity check post
    new_post = request.form['new_post'].strip()
    new_post.replace("@", " ").strip()
    new_post.replace("http://", "").strip()
    new_post.replace("https://", "").strip()
    if len(new_post) >= 499 or len(new_post) == 0:
        return redirect(APP_BASE_URL)

    try:
        # Get a client
        instance = norm_instance_url(request.form['instance'])
        client_credential, fallback_scopes = get_client_credential(instance)

        # Post store UUID
        uuid_str = str(uuid.uuid1())
        
        # Create a Mastodon api instance and generate oauth URL
        api = Mastodon(client_id = client_credential)
        if not fallback_scopes:
            login_url = api.auth_request_url(
                scopes = SCOPES_TO_REQUEST,
                redirect_uris = OAUTH_TARGET_URL,
                state = uuid_str
            )
        else:
            # Special akkoma fallback
            login_url = api.auth_request_url(
                scopes = SCOPES_FALLBACK,
                redirect_uris = OAUTH_TARGET_URL,
                state = uuid_str
            )

        # Store data
        post_register[uuid_str] = (None, new_post)
        instance_register[uuid_str] = instance

        # Redirect the user to the OAuth login URL
        return redirect(login_url)
    except:
        traceback.print_exc()
        return redirect(APP_BASE_URL)

if __name__ == '__main__':
    # Run webapp
    app.run("0.0.0.0", debug = True)
