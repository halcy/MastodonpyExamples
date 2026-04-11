# Simple file based manager for mastodon secrets
# Note that these are still stored in plaintext here, which is maybe not ideal if you're hosting a real service
# This is more intended for experimental stuff

import time
import hashlib
import os
import logging
import glob
import locket

from mastodon import Mastodon

global_secret = os.environ.get("MASTODON_GLOBAL_SECRET", None)
if global_secret is None or len(global_secret.strip()) == 0:
    raise Exception("Need to have a MASTODON_GLOBAL_SECRET env var")

db_path = os.path.abspath(os.path.dirname(os.path.realpath(__file__)) + "/../../") + "/secrets/"
if not os.path.exists(db_path):
    os.makedirs(db_path)

credential_lock_file = db_path + ".lock"
if not os.path.exists(credential_lock_file):
    with open(credential_lock_file, 'w') as f:
        f.write("")
credential_lock = locket.lock_file(credential_lock_file)

def get_name_for(prefix, secret, instance, cred_type, user = None):
    """
    Get a name for a credential file for the given object
    """
    if instance.startswith("http://"):
        instance = "http://".join(instance.split("http://")[1:])
    elif instance.startswith("https://"):
        instance = "https://".join(instance.split("https://")[1:])

    if not cred_type in ["client", "user"]:
        raise Exception("Type must be Client or User")

    if cred_type == "client" and user is not None:
        raise Exception("Client credential must not have user")
    
    if cred_type == "user" and user is None:
        raise Exception("User credential must have user")

    if "@@" in instance or (user is not None and "@@" in user):
        raise Exception("No double-@ allowed in instance or user.")

    if len(secret) == 0 or len(instance) == 0 or (user is not None and len(user) == 0):
        raise Exception("No zero length strings allowed")

    key = global_secret + "@@@\n" + secret + "@@@\n" + cred_type + "@@@\n" + instance
    if cred_type == "user":
        key += "@@@\n" + user
    
    return db_path + prefix + "_" + cred_type + "_" + hashlib.sha256(key.encode("utf-8")).hexdigest() + ".secret"

def lock_credentials():
    """
    Acquire database lock
    """
    credential_lock.acquire()

def release_credentials():
    """
    Release database lock
    """
    credential_lock.release()

def move_credential(from_name, to_name):
    """
    Move a credential file
    """
    if not from_name.startswith(db_path) or not from_name.endswith(".secret"):
        raise Exception("Invalid name")
    if not to_name.startswith(db_path) or not to_name.endswith(".secret"):
        raise Exception("Invalid name")
    os.rename(from_name, to_name)
    os.rename(from_name + "_meta.secret", to_name + "_meta.secret")

def have_credential(name):
    """
    Just test if file exists
    """
    if not name.startswith(db_path) or not name.endswith(".secret"):
        raise Exception("Invalid name")
    return os.path.isfile(name)

def revoke_credential(name):
    """
    Log in with a credential, revoke it if possible, delete the file
    """
    if not name.startswith(db_path) or not name.endswith(".secret"):
        raise Exception("Invalid name")    
    try:
        api = Mastodon(access_token = name)
        api.revoke_access_token()
    except:
        logging.warn(f"Could not revoke token {name}")
    try:
        os.remove(name)
        os.remove(name + "_meta.secret")
    except:
        logging.warn(f"Error while deleting token {name}")

def revoke_all_older_than(prefix, cred_type, when = None):
    """
    Revoke all credentials of a certain type older than some time
    """
    if not cred_type in ["client", "user"]:
        raise Exception("Type must be Client or User")
    
    # No time given -> revoke all before now, i.e. all
    if when is None:
        when = time.time()

    # Go through files and revoke
    names = glob.glob(db_path + prefix + "_" + cred_type + "*.secret")
    for name in names:
        if name.endswith("_meta.secret"):
            continue
        meta_name = name + "_meta.secret"
        mod_time = 0

        # Extra paranoid programming
        try:
            with open(meta_name, 'r') as f:
                mod_time = float(f.read().replace("\n", "").strip())
        except:
            mod_time = 0
        should_revoke = True
        try:
            should_revoke = mod_time is None or mod_time < when
        except:
            pass
        if should_revoke:
            try:
                revoke_credential(name)
            except:
                logging.warn(f"Error when revoking credential {name}")

def update_meta_time(name):
    """
    Set "creation" time for a credential file
    """
    with open(name + "_meta.secret", 'w') as f:
        f.write(str(time.time()))
