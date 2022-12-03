from flask import Flask, session, render_template, redirect, request
from mastodon import Mastodon
import os
import sys
sys.path.append("../tooling/")
import secret_registry

CLIENT_NAME = "Clippy"
CRED_PREFIX = "day03_clippy"
MASTO_SECRET = os.environ["MASTODON_SECRET"]
FLASK_SECRET = os.environ["FLASK_SECRET"]
SCOPES_TO_REQUEST = ["read:accounts", "write:lists"]
app = Flask(__name__)

# Set the secret key to a random value
app.secret_key = b'_5#y2L"F4Q8z\n\xec]/'

def get_client_credential(instance):
    # Lock credential DB
    secret_registry.lock_credentials()

    # Get app credential, register new if needed
    client_credential = secret_registry.get_name_for(CRED_PREFIX, MASTO_SECRET, instance, "client")
    if not secret_registry.have_credential(client_credential):
        Mastodon.create_app(
            CLIENT_NAME,
            api_base_url = instance,
            scopes = SCOPES_TO_REQUEST,
            to_file = client_credential,
            redirect_uris = [
                "http://mastolab.kal-tsit.halcy.de/day03/auth",
                "https://mastolab.kal-tsit.halcy.de/day03/auth",
            ]
        )
        secret_registry.update_meta_time(client_credential)

    # Unlock credential DB
    secret_registry.release_credentials()

@app.route('/')
def index():
    # Check if the user is already logged in
    if 'user' not in session:
        # Render the login page if the user is not logged in
        return render_template('templates/login.htm')
    else:
        # Retrieve the user data from the session
        user = session['user']

        return 'Hello, {}! You are already logged in.'.format(user['name'])


@app.route('/login', methods=['POST'])
def login():
    # Retrieve the user data from the form
    user_data = request.form

    # Split the username into the username and instance
    username, instance = user_data['name'].split('@')

    # Create a Mastodon instance
    mastodon = Mastodon(
        client_id=client_id,
        client_secret=client_secret,
        api_base_url='https://{}'.format(instance)
    )

    # Create an OAuth login URL
    login_url = mastodon.auth_request_url(
        redirect_uris='http://localhost:5000/auth')

    # Store the user data in the session
    session['user'] = user_data

    # Redirect the user to the OAuth login URL
    return redirect(login_url)

if __name__ == '__main__':
    app.run()
