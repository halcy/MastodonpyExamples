from flask import Flask, session, render_template, redirect, request, abort
from mastodon import Mastodon
import os
import sys
sys.path.append("../tooling/")
import secret_registry
import validators

CLIENT_NAME = "Clippy"
CRED_PREFIX = "day03_clippy"
MASTO_SECRET = os.environ["MASTODON_SECRET"]
FLASK_SECRET = os.environ["FLASK_SECRET"]
SCOPES_TO_REQUEST = ["read:accounts", "write:lists"]
OAUTH_TARGET_URL = "https://mastolab.kal-tsit.halcy.de/day03/auth"
APP_BASE_URL = "/day03/"
app = Flask(__name__)

# Set the secret key to a random value
app.secret_key = FLASK_SECRET

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
        client_credential = secret_registry.get_name_for(CRED_PREFIX, MASTO_SECRET, instance, "client")
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

@app.route('/')
def index():
    # Check if the user is already logged in
    if not "logged_in" in session or session["logged_in"] == False:
        # Render the login page if the user is not logged in
        return render_template('login.htm')
    else:
        return render_template(
            'authed.htm', 
            account="{}@{}".format(session["account"], session["instance"])
        )

@app.route('/revoke')
def revoke():
    # Lock credential DB
    secret_registry.lock_credentials()

    # Revoke token
    try:
        user_credential = secret_registry.get_name_for(CRED_PREFIX, MASTO_SECRET, session["instance"], "user", session["account"])
        secret_registry.revoke_credential(user_credential)
    except:
        pass
    
    # Also clear the session
    session["logged_in"] = False
    del session["account"] 
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
        user_credential_temp = secret_registry.get_name_for(CRED_PREFIX, MASTO_SECRET, instance, "user", oauth_code)
        api.log_in(
            code = oauth_code,
            to_file = user_credential_temp,
            scopes = SCOPES_TO_REQUEST,
            redirect_uri = OAUTH_TARGET_URL,
        )
        secret_registry.update_meta_time(user_credential_temp)

        # Move to final place
        user_name = api.me().acct
        user_credential_final = secret_registry.get_name_for(CRED_PREFIX, MASTO_SECRET, instance, "user", user_name)
        secret_registry.move_credential(user_credential_temp, user_credential_final)

    
        # Store session data
        session["logged_in"] = True
        session["account"] = user_name
        session["instance"] = instance
    except:
        # Error handling (showing the user that something went wrong) is future work
        pass

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
        del session["account"] 
        del session["instance"]
        return redirect(APP_BASE_URL)

if __name__ == '__main__':
    app.run("0.0.0.0", debug=True)
