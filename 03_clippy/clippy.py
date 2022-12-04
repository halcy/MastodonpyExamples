import os
import sys
import time
import re
import random
import logging
import threading

from flask import Flask, session, render_template, redirect, request, abort, g
import sqlite3

import numpy as np
import torch
from torch.nn.functional import cosine_similarity
from fast_pytorch_kmeans import KMeans
from transformers import CLIPProcessor, CLIPModel

sys.path.append("../tooling/")
import secret_registry
import app_data_registry
import validators

from mastodon import Mastodon

# Settings
CLIENT_NAME = "Clippy"
APP_PREFIX = "day03_clippy"
MASTO_SECRET = os.environ["MASTODON_SECRET"]
SCOPES_TO_REQUEST = ["read:accounts", "read:statuses", "read:lists", "write:lists", "read:follows", "write:follows", "read:search"]
OAUTH_TARGET_URL = "https://mastolab.kal-tsit.halcy.de/day03/auth"
APP_BASE_URL = "/day03/"
SIMILAR_USERS_COUNT = 7
SECONDS_BETWEEN_REFRESH = 60 * 60 * 3


# Logging setup
logging.basicConfig(
    stream = sys.stdout, 
    format = "%(levelname)s %(asctime)s - %(message)s", 
    level = logging.INFO
)

# Set the secret key to a random value
app = Flask(__name__)
app_data_registry.set_flask_session_info(app, APP_PREFIX)

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
table_exists = query_db("SELECT name FROM sqlite_master WHERE type='table' AND name='users'", single = True) is not None
if not table_exists:
    query = """
    CREATE TABLE users (
        account TEXT,
        last_update NUMERIC,
        embed1 TEXT,
        embed2 TEXT,
        embed3 TEXT
    )
    """
    query_db(query)

table_exists = query_db("SELECT name FROM sqlite_master WHERE type='table' AND name='suggestions'", single = True) is not None
if not table_exists:
    query = """
    CREATE TABLE suggestions (
        account TEXT,
        suggestions TEXT,
        mode TEXT
    )
    """
    query_db(query)

# User insert / update / delete
def db_insert_user(account):
    query = """
    INSERT INTO users (account, last_update, embed1, embed2, embed3)
    VALUES (?, 0, "", "", "")
    """
    query_db(query, (account,))
    query = """
    INSERT INTO suggestions (account, suggestions, mode)
    VALUES (?, "", "show")
    """
    query_db(query, (account,))

def db_update_user(account, embed1, embed2, embed3, reset=False):
    query = """
    UPDATE users
    SET last_update = ?, embed1 = ?, embed2 = ?, embed3 = ?
    WHERE account = ?
    """
    embed1 = " ".join(map(str, embed1))
    embed2 = " ".join(map(str, embed2))
    embed3 = " ".join(map(str, embed3))
    update_time = int(time.time())
    if reset == True:
        update_time = 0
    query_db(query, (update_time, embed1, embed2, embed3, account))

def db_delete_user(account):
    query = "DELETE FROM users WHERE account = ?"
    query_db(query, (account,))
    query = "DELETE FROM suggestions WHERE account = ?"
    query_db(query, (account,))

def db_update_suggestions(account, suggestions):
    query = """
    UPDATE suggestions
    SET suggestions = ?
    WHERE account = ?
    """
    query_db(query, (" ".join(suggestions), account))

# Select a user for update
def db_next_update_user():
    query = """
    SELECT account
    FROM users
    WHERE last_update < ?
    ORDER BY RANDOM()
    LIMIT 1
    """
    the_past = int(time.time()) - SECONDS_BETWEEN_REFRESH
    update_user = query_db(query, (the_past,), single = True)
    if update_user is None:
        return None
    else:
        return update_user[0]

# Get embeds, denormalized
def db_get_other_users_embeds(account):
    query = """
    SELECT account, embed1 AS embed
    FROM users
    WHERE account != ? AND embed1 != ''
    UNION ALL
    SELECT account, embed2
    FROM users
    WHERE account != ? AND embed1 != ''
    UNION ALL
    SELECT account, embed3
    FROM users
    WHERE account != ? AND embed1 != ''
    """
    results = query_db(query, (account, account, account))
    users = []
    embeds = []
    for result in results:
        users.append(result[0])
        embeds.append(result[1])
    return users, np.array(list(map(lambda x: list(map(float, x.split(" "))), embeds)))

# Get current suggestions
def db_current_suggestions(account):
    query = """
    SELECT suggestions
    FROM suggestions
    WHERE account = ?
    LIMIT 1
    """
    suggestions = query_db(query, (account,), single = True)
    if suggestions is None:
        return None
    suggestions = suggestions[0].strip()
    if len(suggestions) == 0:
        return None
    suggestions = suggestions.split(" ")
    if len(suggestions) == 0:
        return None
    return suggestions

# "show" or "follow" mode
def db_get_follow_mode(account):
    query = """
    SELECT mode
    FROM suggestions
    WHERE account = ?
    LIMIT 1
    """
    mode = query_db(query, (account,), single = True)
    return mode[0]

def db_set_follow_mode(account, mode):
    query = """
    UPDATE suggestions
    SET mode = ?
    WHERE account = ?
    """
    mode = query_db(query, (mode, account, ))

# Embed cosine similarity calculator
def cosine_sim(user_embed, other_embeds):
    with torch.no_grad():
        user_embed = torch.tensor(user_embed)
        other_embeds = torch.tensor(other_embeds)
        cosine_similarity_data = cosine_similarity(user_embed, other_embeds)
    return cosine_similarity_data.squeeze().cpu().numpy()

# Pytorch stuff
def get_model():
    model_data = getattr(g, '_clip_model_data', None)
    if model_data is None:
        clip_model = CLIPModel.from_pretrained("openai/clip-vit-base-patch32")
        clip_processor = CLIPProcessor.from_pretrained("openai/clip-vit-base-patch32")
        g._clip_model_data = (clip_model, clip_processor)
    else:
        clip_model, clip_processor = model_data
    return clip_model, clip_processor

def get_clustered_clip_encodings(text_list):
    with app.app_context():
        model, processor = get_model()
        with torch.no_grad():
            inputs = processor(text = text_list, return_tensors="pt", padding=True, truncation=True)
            clip_data = model.get_text_features(**inputs)
            clustering = KMeans(n_clusters=8, mode='cosine', max_iter = 100)
            clustering.fit(clip_data)
            clusters = clustering.centroids.cpu().numpy()
        return clusters

def get_client_credential(instance):
    # Validate
    if not instance.startswith("http://") or instance.startswith("https://"):
        instance = "https://" + instance
    if not validators.url(instance):
        abort(500)

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

# User updater code
def insecure_strip_html(html):
    return re.sub('<[^<]+?>', '', html)

def update_account(username, instance):
    # Get login
    user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", username)
    api = Mastodon(access_token = user_credential, request_timeout = 10)
    
    # Fetch statuses
    me = api.me()
    statuses_fetch = api.account_statuses(me, limit = 40)
    statuses = []
    for i in range(10):
        for status in statuses_fetch:
            status_text = insecure_strip_html(status.content).strip()
            if len(status_text) > 0:
                statuses.append(status.content)
            if len(statuses) >= 100:
                break
        if len(statuses) < 100:
            statuses_fetch = api.fetch_next(statuses_fetch)
        else:
            break
    if len(statuses) < 3:
        return

    # Embed and cluster
    embeds = get_clustered_clip_encodings(statuses)
    account = "{}@{}".format(username, instance)

    # Now, calculate cosine similarity with all other embeds
    other_users, other_embeds = db_get_other_users_embeds(account)
    if len(other_users) == 0:
        db_update_user(account, embeds[0, :], embeds[1, :], embeds[2, :])
        return

    user_list = other_users + other_users + other_users
    distance_list = []
    for i in range(3):
        distance_list.extend(cosine_sim(embeds[i, :], other_embeds))

    # Select n random users
    similar_user_list = set()
    user_choices = random.choices(user_list, weights = distance_list, k = SIMILAR_USERS_COUNT * 9 * 2)
    for user in user_choices:
        similar_user_list.add(user)
        if len(similar_user_list) >= SIMILAR_USERS_COUNT:
            break
    similar_user_list = list(similar_user_list)

    # To db
    db_update_suggestions(account, similar_user_list)
    reset = False
    if len(similar_user_list) < SIMILAR_USERS_COUNT:
        reset = True
    db_update_user(account, embeds[0, :], embeds[1, :], embeds[2, :], reset)

    # Try following, if requested
    follow_mode = db_get_follow_mode(account)
    if follow_mode == "follow":
        masto_list_id = None
        for masto_list in api.lists():
            if masto_list.title == "clippy users":
                masto_list_id = masto_list.id
                break
        if masto_list_id is None:
            masto_list_id = api.list_create("clippy users")
        for user in similar_user_list:
            try:
                user = api.search("@" + user, result_type="accounts").accounts[0]
            except Exception as e:
                logging.warning("Could not find user: " + str(e))
            try:
                api.account_follow(user)
            except Exception as e:
                logging.warning("Could not follow user: " + str(e))
            try:
                api.list_accounts_add(masto_list_id, user)
            except Exception as e:
                if not "Account has already been taken" in str(e):
                    logging.warning("Could not list user: " + str(e))

def refresh_worker():
    # Background user refresh worker
    while True:
        next_user = db_next_update_user()

        if next_user is not None:
            try:
                logging.info("Running update for " + next_user)
                username, instance = next_user.split("@")
                update_account(username, instance)
                time.sleep(5)
            except Exception as e:
                logging.warning("Error on user update for " + next_user + ": " + str(e))

@app.route('/switchfollow')
def switchfollow():
    account = "{}@{}".format(session["user_name"], session["instance"])
    follow_mode = db_get_follow_mode(account)
    if follow_mode == "show":
        db_set_follow_mode(account, "follow")
    else:
        db_set_follow_mode(account, "show")
    return redirect(APP_BASE_URL)

@app.route('/')
def index():
    # Check if the user is already logged in
    if not "logged_in" in session or session["logged_in"] == False:
        # Render the login page if the user is not logged in
        return render_template('login.htm')
    else:
        # See if we have suggestions available
        account = "{}@{}".format(session["user_name"], session["instance"])
        suggestions = db_current_suggestions(account)

        # Show suggestions or processing indicator
        follow_mode = db_get_follow_mode(account)
        follow_mode_switch = "on"
        if follow_mode == "follow":
            follow_mode_switch = "off"
        if suggestions is None:
            return render_template(
                'authed.htm', 
                account=account,
                processing = True,
                followmode = follow_mode_switch
            )
        else:
            account_links = ["https://" + session["instance"] + "/@" + x for x in suggestions]
            links = list(zip(suggestions, account_links))
            return render_template(
                'authed.htm', 
                account="{}@{}".format(session["user_name"], session["instance"]),
                processing = False,
                links = links,
                followmode = follow_mode_switch
            )

@app.route('/revoke')
def revoke():
    # Lock credential DB
    secret_registry.lock_credentials()

    # Revoke token
    try:
        user_credential = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, session["instance"], "user", session["user_name"])
        secret_registry.revoke_credential(user_credential)
    except:
        pass
    
    # Remove from DB
    db_delete_user(session["user_name"] + "@" + session["instance"])

    # Also clear the session
    session["logged_in"] = False
    del session["user_name"] 
    del session["instance"] 

    # Unlock credential DB
    secret_registry.release_credentials()

    # Back to root
    return redirect(APP_BASE_URL)

@app.route('/auth')
def auth():
    # Get the oauth code
    oauth_code = request.args.get('code')
    instance = request.args.get('state')

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
        user_name = api.me().acct
        user_credential_final = secret_registry.get_name_for(APP_PREFIX, MASTO_SECRET, instance, "user", user_name)
        secret_registry.move_credential(user_credential_temp, user_credential_final)

        # Add to DB
        db_insert_user(user_name + "@" + instance)
    
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
        instance = request.form['instance']
        client_credential = get_client_credential(instance)

        # Create a Mastodon api instance and generate oauth URL
        api = Mastodon(client_id = client_credential)
        login_url = api.auth_request_url(
            scopes = SCOPES_TO_REQUEST,
            redirect_uris = OAUTH_TARGET_URL,
            state=instance
        )

        # Redirect the user to the OAuth login URL
        return redirect(login_url)
    except:
        # Just clear credentials and redirect to root
        session["logged_in"] = False
        del session["user_name"] 
        del session["instance"]
        return redirect(APP_BASE_URL)

if __name__ == '__main__':
    # Run update worker
    worker_thread = threading.Thread(target=refresh_worker)
    worker_thread.start()

    # Run webapp
    app.run()
