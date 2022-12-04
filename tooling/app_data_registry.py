import secret_registry
import os

appdata_dir = os.path.abspath(secret_registry.db_path) + "/appdata/"
if not os.path.exists(appdata_dir):
    os.makedirs(appdata_dir)

def set_flask_session_info(app, app_prefix):
    """
    Set up a flask session with a certain session dir, and set the secret to a value read from env variables
    """
    session_dir = appdata_dir + "session_" + app_prefix
    if not os.path.exists(session_dir):
        os.makedirs(session_dir)
    app.secret_key = os.environ["FLASK_SECRET"]
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["SESSION_FILE_DIR"] = session_dir
    
def get_db_file(app_prefix):
    """
    Return a file name for a database file
    """
    return appdata_dir + "db_" + app_prefix + ".db"
