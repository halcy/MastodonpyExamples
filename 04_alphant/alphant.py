import os
import sys
import time
import json
import logging
import threading
import traceback

from flask import Flask, session, render_template, redirect, request, abort, g
import sqlite3

sys.path.append("../tooling/")
import secret_registry
import app_data_registry
import validators

from mastodon import Mastodon, streaming

# Settings
CLIENT_NAME = "Alphant"
APP_PREFIX = "day04_alphant"
MASTO_SECRET = os.environ["MASTODON_SECRET"]
SCOPES_TO_REQUEST = ["read:accounts", "read:statuses", "read:follows", "write:follows", "write:statuses"]
OAUTH_TARGET_URL = "https://mastolab.kal-tsit.halcy.de/day04/auth"
APP_BASE_URL = "/day04/"

# Logging setup
logging.basicConfig(
    stream = sys.stdout, 
    format = "%(levelname)s %(asctime)s - %(message)s", 
    level = logging.INFO
)

# Set the secret key to a random value
app = Flask(__name__)
app_data_registry.set_flask_session_info(app, APP_PREFIX, True)

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
        account TEXT,
        other_account TEXT,
        post TEXT,
        post_id NUMERIC
    )
    """
    query_db(query)

# Update post in the DB
def db_update_post(account, other_account, post):
    # Don't want reblogs
    if post.reblog is not None:
        return

    # Convert arguments to DB form
    post_json = json.dumps(post, default=str)
    other_account = other_account.lower()

    # Check if a post exists that would match here
    query = """
    SELECT post FROM posts 
    WHERE account = ? AND other_account = ?
    """
    post_exists = query_db(query, (account, other_account), single = True) is not None

    # Insert or update, depending
    if not post_exists:
        query = """
        INSERT INTO posts (account, other_account, post, post_id)
        VALUES (?, ?, ?, ?)
        """
        query_db(query, (account, other_account, post_json, post.id))
    else:
        query = """
        UPDATE posts
        SET post = ?, post_id = ?
        WHERE account = ? AND other_account = ?
        """
        query_db(query, (post_json, post.id, account, other_account))

# Get current posts from DB
def db_get_posts(account):
    ascdesc = "ASC"
    if int(time.time() / 60 * 60) % 2 == 0:
        ascdesc = "DESC"
    query = """
        SELECT post
        FROM posts
        WHERE account = ?
        ORDER BY other_account
        """
    query = query + " " + ascdesc
    posts_json = query_db(query, (account, ))
    posts = [json.loads(x[0], object_hook=Mastodon._Mastodon__json_hooks) for x in posts_json]
    return posts

# Get user list
def db_get_accounts():
    query = """
        SELECT DISTINCT account
        FROM posts
        """
    return [x[0] for x in query_db(query)]

# Delete all posts associated with a user
def db_delete_posts(account):
    query = "DELETE FROM posts WHERE account = ?"
    query_db(query, (account,))

# Delete a single post
def db_delete_one_post(id):
    query = "DELETE FROM posts WHERE post_id = ?"
    query_db(query, (id,))

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

# Background processing
def post_to_db_resilient(account, post):
    try:
        db_update_post(account, post.account.acct, post)
    except Exception as e:
        logging.warning("Could not store post:", str(e))

def delete_from_db_resilient(id):
    try:
        db_delete_one_post(id)
    except Exception as e:
        logging.warning("Could not delete post:", str(e))

streams = {}
def refresh_worker():
    while True:
        try:
            # Start new streams where needed
            accounts = db_get_accounts()
            for account in accounts:
                if not account in streams:
                    try:
                        # Get login
                        logging.info("Starting stream for " + account)
                        user_name, instance = account.split("@")
                        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
                        api = Mastodon(access_token = user_credential, request_timeout = 10)

                        # Stream
                        listener = streaming.CallbackStreamListener(
                            update_handler = lambda status, account_bind = account: post_to_db_resilient(account_bind, status),
                            status_update_handler = lambda status, account_bind = account: post_to_db_resilient(account_bind, status),
                            delete_handler = delete_from_db_resilient
                        )
                        streams[account] = api.stream_user(listener, run_async = True, reconnect_async = True)
                    except Exception as e:
                        logging.warning("Could not start stream:" + str(e))

            # Reap old streams where not
            keys = streams.keys()
            for stream in keys:
                if not stream in accounts:
                    try:
                        # Kill stream (should already be dead, but be extra paraoid)
                        logging.info("Reaping stream for" + stream)
                        try:
                            streams[account].close()
                        except:
                            pass
                        del streams[account]
                    except Exception as e:
                        logging.warning("Could not reap stream:" + str(e))
        except:
            pass
        time.sleep(10)

def process_emoji(text, emojis):
    for emoji in emojis:
        text = text.replace(":{}:".format(emoji.shortcode), '<img src="{}" style="height: 16px;" alt="{}" />'.format(emoji.url, emoji.shortcode))
    return text
app.jinja_env.globals.update(process_emoji = process_emoji)

@app.route('/')
def index():
    # Check if the user is already logged in
    if not "logged_in" in session or session["logged_in"] == False:
        # Render the login page if the user is not logged in
        return render_template('login.htm')
    else:
        # Get user data from session
        user_name, instance, account = get_session_user()

        # If there are no posts in DB: Get posts, insert
        posts = db_get_posts(account)
        if len(posts) == 0:
            user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
            api = Mastodon(access_token = user_credential, request_timeout = 10)
            posts_new = api.timeline_home()
            for post_new in posts_new:
                db_update_post(account, post_new.account.acct, post_new)
            posts = db_get_posts(account)

        # Render
        return render_template(
            'authed.htm', 
            account="{}@{}".format(user_name, instance),
            posts = posts
        )

@app.route('/post_status', methods=["POST"])
def post_status():
    # Get user info
    user_name, instance, account = get_session_user()

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
    return redirect(APP_BASE_URL)

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
    
    # Kill stream, if exists
    try:
        streams[account].close()
    except:
        pass

    # Remove from DB
    db_delete_posts(user_name + "@" + instance)

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

# Run update worker
worker_thread = threading.Thread(target=refresh_worker)
worker_thread.start()

if __name__ == '__main__':
    # Run webapp
    app.run("0.0.0.0", debug = True)
