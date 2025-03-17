##
# HelloMoToot - A PSTN Mastodon Client
##

from flask import abort, Flask, request, render_template, flash, redirect, session, g, send_file, send_from_directory
from functools import wraps
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import Gather, VoiceResponse, Say, Play
from mastodon import Mastodon
from io import BytesIO
import urllib.parse
import os
import sqlite3
import random
import string
import qrcode
import re

##
# Config
##

# Super secret values
TWILIO_NUMBER = "+12318668464"
TWILIO_NUMBER_DISP = "+1 (231) TOOTING / +1 (231) 866 8464"
DB_FILE = "users.db"
BASE_URL = "http://mastolab.kal-tsit.halcy.de/day09"

# load secret values from a file excluded from git
with open("twilio.secret", 'r') as f:
    lines = f.readlines()
TWILIO_AUTH_TOKEN = lines[0]
SECRET_KEY = lines[1]

##
# Basic tooling
##

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY

# ensure DB exists
if not os.path.exists(DB_FILE):
    connection = sqlite3.connect(DB_FILE)
    cur = connection.cursor()
    cur.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            masto_username TEXT,
            masto_token TEXT,
            masto_app_url TEXT,
            phone_number TEXT,
            phone_reg_code TEXT
        );
    """)
    cur.execute("""
            CREATE TABLE app_creds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            masto_app_url TEXT,
            masto_client_id TEXT,
            masto_client_secret TEXT
        );
    """);
    connection.commit()
    connection.close()
    
# flask session-global DB getter
def db():
    conn = getattr(g, '_database', None)
    if conn is None:
        conn = g._database = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
    return conn

@app.teardown_appcontext
def close_connection(exception):
    conn = getattr(g, '_database', None)
    if conn is not None:
        conn.close()

# oauth callback URL generator
def get_oauth_callback_url(masto_app_url):
    return BASE_URL + "oauth?for=" + urllib.parse.quote(masto_app_url, safe="")

# masto unauthed API getter, creates app if needed
def get_masto_api_unauthed(masto_app_url):
    # get db
    conn = db()
    cur = conn.cursor()
    
    # okay, see if we need to make an app
    app_data = cur.execute("SELECT masto_client_id, masto_client_secret FROM app_creds WHERE masto_app_url = ?", (masto_app_url, )).fetchall()
    if len(app_data) == 0:
        masto_client_id, masto_client_secret = Mastodon.create_app("HelloMoToot", api_base_url=masto_app_url, redirect_uris = get_oauth_callback_url(masto_app_url))
        cur.execute("INSERT INTO app_creds (masto_app_url, masto_client_id, masto_client_secret) VALUES (?, ?, ?)", (masto_app_url, masto_client_id, masto_client_secret))
        conn.commit()
    else:
        masto_client_id = app_data[0]["masto_client_id"]
        masto_client_secret = app_data[0]["masto_client_secret"]
    
    # return unauthed API instance
    return Mastodon(
        api_base_url = masto_app_url,
        client_id = masto_client_id,
        client_secret = masto_client_secret
    )

# user info getter / updater given masto data
def get_user_phone_info(masto_user, masto_app_url, access_token):
    # get db
    conn = db()
    cur = conn.cursor()
    
    # check if user record exists
    user_data = cur.execute("SELECT masto_token, phone_number, phone_reg_code FROM users WHERE masto_username = ? AND masto_app_url = ?", (masto_user, masto_app_url)).fetchall()
    if len(user_data) == 0:
        # no? create it
        phone_number = ""
        phone_reg_code = masto_user + "-" + masto_app_url + "-" + ''.join(random.sample(string.ascii_letters+string.digits, 20)).lower()
        cur.execute("INSERT INTO users (masto_username, masto_token, masto_app_url, phone_number, phone_reg_code) VALUES (?, ?, ?, ?, ?)", (masto_user, access_token, masto_app_url, "", phone_reg_code))
        conn.commit()
    else:
        # yes? update if needed and return
        access_token_stored = user_data[0]["masto_token"]
        phone_number = user_data[0]["phone_number"]
        phone_reg_code = user_data[0]["phone_reg_code"]
        
        if access_token != access_token_stored:
            cur.execute("UPDATE users SET masto_token = ? WHERE masto_username = ? AND masto_app_url = ?", (access_token, masto_user, masto_app_url))
            conn.commit()
            
    return phone_number, phone_reg_code

# literally yoinked from twilio tutorial
def validate_twilio_request(f):
    """Validates that incoming requests genuinely originated from Twilio"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Create an instance of the RequestValidator class
        validator = RequestValidator(TWILIO_AUTH_TOKEN)

        # Validate the request using its URL, POST data,
        # and X-TWILIO-SIGNATURE header
        request_valid = validator.validate(
            request.url,
            request.form,
            request.headers.get('X-TWILIO-SIGNATURE', ''))

        # Continue processing the request if it's valid, return a 403 error if
        # it's not
        if request_valid:
            return f(*args, **kwargs)
        else:
            return abort(403)
    return decorated_function

# Error helper
def error(text):
    flash(text)
    return redirect("/")

##
# Web UI
##

# logged auth user landing
@app.route("/")
def index():
    if "masto_app_url" in session and "access_token" in session:
        return redirect("/manage")
    return(render_template('index.html'))

# Oauth flow start
@app.route("/login", methods=['POST'])
def login():
    try:
        # get db
        conn = db()
        cur = conn.cursor()
        
        # get request params
        masto_fulluser = request.form['username']
        masto_user = "@".join(masto_fulluser.split("@")[:-1])
        masto_domain = masto_fulluser.split("@")[-1]
        masto_app_url = masto_domain
        
        # check some basic things
        if (not "@" in masto_fulluser) or (len(masto_user) == 0) or (len(masto_domain) == 0) or (len(masto_fulluser) > 255):
            return error("Please enter something sensible.")
        
        api_unauthed = get_masto_api_unauthed(masto_app_url)
        oauth_redirect = api_unauthed.auth_request_url(redirect_uris = get_oauth_callback_url(masto_app_url))
        
        return redirect(oauth_redirect)
    except:
        return error("Something broke in oauth flow start, it is unclear what.")

# Oauth flow end
@app.route("/oauth", methods=['GET'])
def oauth():
    try:
        masto_app_url = request.args.get('for')
        code = request.args.get('code')
        api_unauthed = get_masto_api_unauthed(masto_app_url)
        access_token = api_unauthed.log_in(code = code, redirect_uri = get_oauth_callback_url(masto_app_url))
        session["masto_app_url"] = masto_app_url
        session["access_token"] = access_token
        return redirect("/")
    except:
        return error("Something broke in oauth flow end, it is unclear what.")


# qr code generator for registration SMS
@app.route("/qr")
def qr():
    try:
        phone_reg_code = request.args.get('phone_reg_code')
        boring = request.args.get('boring', 0, type=int)
        qr_code_data = "SMSTO:" + TWILIO_NUMBER + ":--register-" + phone_reg_code
        qr_code = qrcode.QRCode()
        qr_code.add_data(qr_code_data)
        qr_code.make()
        if boring != 1:
            qr_img = qr_code.make_image(fill_color=(254, 254, 254), back_color=(15, 6, 41))
        else:
            qr_img = qr_code.make_image(fill_color=(0, 0, 0), back_color=(255, 255, 255))            
        img_io = BytesIO()
        qr_img.save(img_io, 'PNG')
        img_io.seek(0)
        return send_file(img_io, mimetype='image/png')
    except Exception as e:
        print(e)
        return error("Something broke in QR code gen, it is unclear what.")
    
# authed user landing
@app.route("/manage")
def manage():
    try:
        masto_app_url = session["masto_app_url"]
        access_token = session["access_token"]
        api = Mastodon(
            api_base_url = masto_app_url,
            access_token = access_token,
        )
        masto_user = api.me().acct
        
        # get phone info
        phone_number, phone_reg_code = get_user_phone_info(masto_user, masto_app_url, access_token)
        return(render_template('manage.html',
            masto_user_full = masto_user + "@" + masto_app_url,
            phone_number = phone_number,
            phone_reg_code = phone_reg_code,
            phone_reg_number = TWILIO_NUMBER_DISP
        ))
    except:
        return error("Something broke, it is unclear what.")
        
# session emptier
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

###
# Messaging
###
# twilio webhook landing for SMS
@app.route('/message', methods=['POST'])
@validate_twilio_request
def incoming_message():
    try:
        message = request.values["Body"]
        phone_number = request.values["From"]
        if message.startswith("--register-"):
            handle_sms_reg(phone_number, message[11:])
            resp = MessagingResponse()
            resp.message("Thank you for registering - you are now ready to post. Simply send your messages to this phone number!")
            return str(resp)
        else:
            success = handle_sms_post(phone_number, message)
            if not success:
                resp = MessagingResponse()
                resp.message("Something went wrong - did you register properly? You can check on " + BASE_URL)
                return resp
            else:
                return ""
    except:
        resp = MessagingResponse()
        resp.message("Something went wrong, it is unclear what.")
        return resp

# phone number reg handler
def handle_sms_reg(phone_number, code):
    # get db
    conn = db()
    cur = conn.cursor()
    
    # update user from code
    cur.execute("UPDATE users SET phone_number = ? WHERE phone_reg_code = ?", (phone_number, code))
    conn.commit()
    
# posting handler
def handle_sms_post(phone_number, message):
    # get db
    conn = db()
    cur = conn.cursor()
    
    # get token given phone number
    user_data = cur.execute("SELECT masto_token, masto_app_url FROM users WHERE phone_number = ?", (phone_number,)).fetchall()
    if len(user_data) != 0:
        access_token = user_data[0]["masto_token"]
        masto_app_url = user_data[0]["masto_app_url"]
        api = Mastodon(
            api_base_url = masto_app_url,
            access_token = access_token,
        )
        api.toot(message)
        return True
    else:
        return False
    
##
# Voice
##

# audio files server
@app.route('/audio/<path:path>')
def audio(path):
    if ".." in path or not os.path.exists("audio/" + path): # Bonus paranoia
        return abort(403)
    else:
        return send_from_directory('audio', path)

# twilio webhook landing for incoming call
@app.route("/call", methods=['GET', 'POST'])
@validate_twilio_request
def call():
    resp = VoiceResponse()
    
    # get db
    conn = db()
    cur = conn.cursor()
    
    # get token given phone number
    phone_number = request.values["From"]
    user_data = cur.execute("SELECT masto_token, masto_app_url FROM users WHERE phone_number = ?", (phone_number,)).fetchall()
    if len(user_data) == 0:
        # play signup reminder and stop
        resp.play(BASE_URL + "/audio/pleaseregister.mp3", loop=1)
        resp.hangup()
    else:
        # gather either normally, or with the (fake) waiting queue (30% chance)
        if random.random() < 0.3:
            gather = Gather(num_digits=1, action='/menu_option', timeout=1)
            gather.play(BASE_URL + "/audio/pleasehold.mp3", loop=1)
        else:
            gather = Gather(num_digits=1, action='/menu_option', timeout=1)
            gather.play(BASE_URL + "/audio/intro.mp3", loop=1)
        resp.append(gather)

        # if the user doesn't select an option, go directly to the questionaire
        resp.redirect('/questionaire')

    return str(resp)

# twilio voice menu selection
@app.route('/menu_option', methods=['GET', 'POST'])
@validate_twilio_request
def menu_option():
    try:
        resp = VoiceResponse()        
        
        if 'Digits' in request.values:
            choice = request.values['Digits']

            if choice == '1':
                resp.play(BASE_URL + "/audio/1_recordmessage.mp3")
                resp.play(BASE_URL + "/audio/beep.mp3")
                resp.gather(input="speech", action="/speechpost", speech_timeout = 3, profanity_filter = False)
            elif choice == '2':
                resp.play(BASE_URL + "/audio/2_mentions.mp3")
                
                # get db
                conn = db()
                cur = conn.cursor()
                
                # get token given phone number
                phone_number = request.values["From"]
                user_data = cur.execute("SELECT masto_token, masto_app_url FROM users WHERE phone_number = ?", (phone_number,)).fetchall()
                
                # fetch mentions
                access_token = user_data[0]["masto_token"]
                masto_app_url = user_data[0]["masto_app_url"]
                api = Mastodon(
                    api_base_url = masto_app_url,
                    access_token = access_token,
                )
                mentions = api.notifications(limit=10, exclude_types = ["follow", "favourite", "reblog", "poll", "follow_request"])
                
                # Say mentions
                for mention in mentions:
                    text_content = re.sub('<[^<]+?>', '', mention.status.content)
                    resp.say("From {} at {}: {}.".format(mention.status.account.acct, mention.status.created_at.strftime("%B %d, %H:%M o'clock"), text_content))
                    resp.pause(length=2)
                                    
            elif choice == '3':
                resp.play(BASE_URL + "/audio/3_boopsound.mp3")
                resp.play(BASE_URL + "/audio/boop.mp3")
            else:
                resp.play(BASE_URL + "/audio/error.mp3")
                resp.hangup()
                return str(resp)
            
        # go to questionaire after unless we're now in a different handler
        resp.redirect('/questionaire')

        return str(resp)
    except:
        resp = VoiceResponse()   
        resp.play(BASE_URL + "/audio/error.mp3")
        resp.hangup()
        return str(resp)
    
# twilio speech rec post
@app.route('/speechpost', methods=['GET', 'POST'])
@validate_twilio_request
def speechpost():
    try:
        resp = VoiceResponse()          
        post_text = request.values["SpeechResult"]
        
        # get db
        conn = db()
        cur = conn.cursor()
        
        # get token given phone number
        phone_number = request.values["From"]
        user_data = cur.execute("SELECT masto_token, masto_app_url FROM users WHERE phone_number = ?", (phone_number,)).fetchall()

        access_token = user_data[0]["masto_token"]
        masto_app_url = user_data[0]["masto_app_url"]
        api = Mastodon(
            api_base_url = masto_app_url,
            access_token = access_token,
        )
        api.toot(post_text)
        
        resp = VoiceResponse()    
        resp.redirect('/questionaire')
        return str(resp)
    except:
        resp = VoiceResponse()
        resp.play(BASE_URL + "/audio/error.mp3")
        resp.hangup()
        return str(resp)
    
# twilio questionaire
@app.route('/questionaire', methods=['GET', 'POST'])
@validate_twilio_request
def questionaire():
    try:
        resp = VoiceResponse()

        gather = Gather(num_digits=1, action='/questionaire_resp', timeout=1)
        gather.play(BASE_URL + "/audio/pleaserate.mp3", loop=1)
        resp.append(gather)
        
        # no answer? go to response anyways
        resp.redirect('/questionaire_resp')
        return str(resp)
    except:
        resp = VoiceResponse()
        resp.play(BASE_URL + "/audio/error.mp3")
        resp.hangup()
        return str(resp)

# twilio questionaire response
@app.route('/questionaire_resp', methods=['GET', 'POST'])
@validate_twilio_request
def questionaire_resp():
    try:
        resp = VoiceResponse()

        if 'Digits' in request.values:
            choice = request.values['Digits']

            if choice in ['1', '2', '3', '4', '5']:
                # regular goodbye. could in theory save ratings but, you know.
                resp.play(BASE_URL + "/audio/correctrating.mp3")
            else:
                # annoyed goodbye
                resp.play(BASE_URL + "/audio/wrongrating.mp3")
        else:
            # no answer? annoyed goodbye.
            resp.play(BASE_URL + "/audio/wrongrating.mp3")
            
        resp.hangup()
        return str(resp)
    except:
        resp = VoiceResponse()
        resp.play(BASE_URL + "/audio/error.mp3")
        resp.hangup()
        return str(resp)
    
# favicon
@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

if __name__ == '__main__':
    app.run(port=5555, host="0.0.0.0", debug=True)

