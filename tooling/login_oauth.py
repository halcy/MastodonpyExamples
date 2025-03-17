# Basic login example
# Not at all performant, but simple

import secret_registry
import webbrowser
import time
import sys
from mastodon import Mastodon

# Usage printer
if len(sys.argv) != 4:
    print("Usage: " + sys.argv[0] + " <client name> <credential prefix> <secret>")
    sys.exit()
    
# Settings
SCOPES_TO_REQUEST = ['read', 'write', 'follow', 'push']
CLIENT_NAME = sys.argv[1]
CRED_PREFIX = sys.argv[2]
SECRET = sys.argv[3]

# Read instance from user
instance = input("Enter mastodon instance name: ")

# Lock credential DB
secret_registry.lock_credentials()

# Get app credential, register new if needed
client_credential = secret_registry.get_name_for(CRED_PREFIX, SECRET, instance, "client")
if not secret_registry.have_credential(client_credential):
    Mastodon.create_app(
        CLIENT_NAME,
        api_base_url = instance,
        scopes = SCOPES_TO_REQUEST,
        to_file = client_credential
    )
    secret_registry.update_meta_time(client_credential)

# Unlock credential DB
secret_registry.release_credentials()

# Start oauth flow
api = Mastodon(client_id = client_credential)
oauth_url = api.auth_request_url(scopes = SCOPES_TO_REQUEST)
print(oauth_url)
webbrowser.open_new(oauth_url)
time.sleep(3)
print("\n\n")

# Get oauth code from user and log in
oauth_code = input("After logging in, enter the code you received: ")

# Lock credential DB
secret_registry.lock_credentials()

# Log in
user_credential_temp = secret_registry.get_name_for(CRED_PREFIX, SECRET, instance, "user", oauth_code)
api.log_in(
    code = oauth_code,
    to_file = user_credential_temp
)
secret_registry.update_meta_time(user_credential_temp)

# Move to final place
user_name = api.me().acct
user_credential_final = secret_registry.get_name_for(CRED_PREFIX, SECRET, instance, "user", user_name)
secret_registry.move_credential(user_credential_temp, user_credential_final)

# Unlock credential DB
secret_registry.release_credentials()
