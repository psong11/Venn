import os
import datetime

from cs50 import SQL
import flask
from flask import Flask, flash, redirect, render_template, request, session
from flask_session import Session
import requests
from tempfile import mkdtemp
from werkzeug.exceptions import default_exceptions, HTTPException, InternalServerError
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery

from helpers import login_required, check_time, check_chronology, credentials_to_database, credentials_to_dict, update_credentials

# Configure application
app = Flask(__name__)

# Ensure templates are auto-reloaded
app.config["TEMPLATES_AUTO_RELOAD"] = True


# Ensure responses aren't cached
@app.after_request
def after_request(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response


# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_FILE_DIR"] = mkdtemp()
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Configure CS50 Library to use SQLite database
db = SQL("sqlite:///venn.db")

# Google Oauth routes (authorize, oatuh2callback) from https://developers.google.com/identity/protocols/oauth2/web-server
# This variable specifies the name of a file that contains the OAuth 2.0
# information for this application, including its client_id and client_secret.
CLIENT_SECRETS_FILE = "../../../client_secret_544895891085-c0ee6mvu0fgpl9ki745i06crhlc67o2o.apps.googleusercontent.com.json"

# This OAuth 2.0 access scope allows for full read/write access to the
# authenticated user's account and requires requests to use an SSL connection.
SCOPES = ['https://www.googleapis.com/auth/calendar']
API_SERVICE_NAME = 'calendar'
API_VERSION = 'v3'

@app.route("/")
@login_required
def index():

    name = db.execute("SELECT name FROM users WHERE id=?", session["user_id"])[0]["name"]

    events = db.execute("SELECT name, events.id FROM events JOIN members ON events.id=members.event_id WHERE members.user_id=?", session["user_id"])

    for event in events:
        event["view"] = flask.url_for('view', _external=True, id=str(event["id"]))
        event["link"] = flask.url_for('join', _external=True, id=str(event["id"]))

    return render_template("index.html", events=events, name=name)


@app.route("/login", methods=["GET", "POST"])
def login():
    """Ask user to login"""

     # User reached route via POST (as by submitting a form via POST)
    if request.method == "POST":

        rows = db.execute("SELECT * FROM users WHERE name=? AND email=?", request.form.get("name"), request.form.get("email"))

        if len(rows) == 0:
            db.execute("INSERT INTO users (name, email) VALUES(?,?)", request.form.get("name"), request.form.get("email"))
            rows = db.execute("SELECT * FROM users WHERE name=? AND email=?", request.form.get("name"), request.form.get("email"))

        session["user_id"] = rows[0]["id"]

        return redirect("/")
    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""

    # Forget any user_id
    session.clear()

    # Redirect user to login form
    return redirect("/")


@app.route('/authorize')
def authorize():
    # Create flow instance to manage the OAuth 2.0 Authorization Grant Flow steps.
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE, scopes=SCOPES)

    # The URI created here must exactly match one of the authorized redirect URIs
    # for the OAuth 2.0 client, which you configured in the API Console. If this
    # value doesn't match an authorized URI, you will get a 'redirect_uri_mismatch'
    # error.
    tmp_url = flask.url_for('oauth2callback', _external=True)
    if "http:" in tmp_url:
        tmp_url = "https:" + tmp_url[5:]
    flow.redirect_uri = tmp_url

    tmp_url, state = flow.authorization_url(
        # Enable offline access so that you can refresh an access token without
        # re-prompting the user for permission. Recommended for web server apps.
        access_type='offline'
    )

    # http to https from https://github.com/requests/requests-oauthlib/issues/287
    if "http:" in tmp_url:
        tmp_url = "https:" + tmp_url[5:]
    authorization_url = tmp_url

    # Store the state so the callback can verify the auth server response.
    flask.session['state'] = state

    return flask.redirect(authorization_url)


@app.route('/oauth2callback')
def oauth2callback():
    # Specify the state when creating the flow in the callback so that it can
    # verified in the authorization server response.
    state = flask.session['state']

    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
      CLIENT_SECRETS_FILE, scopes=SCOPES, state=state)
    tmp_url = flask.url_for('oauth2callback', _external=True)
    if "http:" in tmp_url:
        tmp_url = "https:" + tmp_url[5:]
    flow.redirect_uri = tmp_url

    # Use the authorization server's response to fetch the OAuth 2.0 tokens.
    tmp_url = flask.request.url
    if "http:" in tmp_url:
        tmp_url = "https:" + tmp_url[5:]
    authorization_response = tmp_url
    flow.fetch_token(authorization_response=authorization_response)

    # Store credentials in the session.
    # ACTION ITEM: In a production app, you likely want to save these
    #              credentials in a persistent database instead.
    credentials = flow.credentials
    credentials_to_database(credentials, session["user_id"])

    return flask.redirect("/")


@app.route("/create", methods=["GET", "POST"])
@login_required
def create():
    """ Create A New Event"""
    if request.method == "POST":
        name = request.form.get("name")

        password = generate_password_hash(request.form.get("password"))

        duration = int(request.form.get("duration"))

        timezone = int(request.form.get("timezone"))

        daterange = request.form.get("daterange").split("-")
        start_date = daterange[0].strip().split("/")
        start_date = datetime.date(int(start_date[2]), int(start_date[0]), int(start_date[1]))
        end_date = daterange[1].strip().split("/")
        end_date = datetime.date(int(end_date[2]), int(end_date[0]), int(end_date[1]))

        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")

        if not check_time(start_time) or not check_time(end_time):
            return render_template("apology.html")

        if not check_chronology(start_time, end_time):
            return render_template("apology.html")

        # start_time += ":00"
        # end_time += ":00"

        event_id = db.execute("INSERT INTO events (name, hash, start_date, end_date, start_time, end_time, timezone, duration) VALUES(?,?,?,?,?,?,?,?)", name, password, start_date, end_date, start_time, end_time, timezone, duration)
        db.execute("INSERT INTO members (event_id, user_id, host) VALUES(?,?,?)", event_id, session["user_id"], True)

        url = flask.url_for("join", _external=True) + "?id=" + str(event_id)

        return render_template("created.html", URL=url, PASSWORD=request.form.get("password"))

    return render_template("create.html")


@app.route("/join", methods=["GET", "POST"])
@login_required
def join():
    """Join an Event"""
    if request.method == "POST":

        rows = db.execute("SELECT * FROM events WHERE id=?", request.form.get("id"))

        if len(rows) != 1 or not check_password_hash(rows[0]["hash"], request.form.get("password")):
            flash("Incorrect ID or Password. Please Try Again.")
            return render_template("join.html", ID=request.form.get("id"))

        rows = db.execute("SELECT * FROM members WHERE event_id=? AND user_id=?", int(request.form.get("id")), session["user_id"])
        # If user already joined, then just redirect to /view
        if len(rows) != 0:
            return redirect("/")

        db.execute("INSERT INTO members (event_id, user_id) VALUES(?,?)", int(request.form.get("id")), session["user_id"])

        return redirect(flask.url_for("view", id=request.form.get("id")))

    # Redirect user to login form
    return render_template("join.html", ID=request.args.get("id"))


@app.route("/view", methods=["GET", "POST"])
@login_required
def view():

    if request.method == "POST": #AKA If they submit the form to add their GCal...

        creds = db.execute("SELECT token, refresh_token, token_uri, client_id, client_secret, scopes FROM credentials WHERE user_id=?", session["user_id"])
        if len(creds) != 1:
            return flask.redirect('authorize')

        creds = credentials_to_dict(creds[0])

        # Load credentials from the session.
        credentials = google.oauth2.credentials.Credentials(
          **creds)

        # Save credentials back to session in case access token was refreshed.
        # ACTION ITEM: In a production app, you likely want to save these
        #              credentials in a persistent database instead.
        update_credentials(credentials, session["user_id"])

        service = googleapiclient.discovery.build(
            API_SERVICE_NAME, API_VERSION, credentials=credentials, cache_discovery=False)

        # Gather all calendar IDs
        calendars = []

        page_token = None
        while True:
            calendar_list = service.calendarList().list(pageToken=page_token).execute()
            for calendar_list_entry in calendar_list['items']:
                calendars.append(calendar_list_entry['id'])
            page_token = calendar_list.get('nextPageToken')
            if not page_token:
                break

        event = db.execute("SELECT * FROM events WHERE id=?", request.form.get("id"))

        if len(event) == 0:
            flash("Not a valid event")
            return redirect("/")

        if event[0]["timezone"] == 0:
            start_date = event[0]["start_date"] + "T00:00:00Z"
            end_date = event[0]["end_date"] + "T00:00:00Z"
        else:
            #["", "+"][total > 0] from https://stackoverflow.com/questions/2763432/how-to-print-the-sign-of-a-digit-for-positive-numbers-in-python
            start_date = event[0]["start_date"] + "T00:00:00" + ["", "+"][event[0]["timezone"] > 0] + str(event[0]["timezone"]).zfill(2) + ":00"
            end_date = event[0]["end_date"] + "T23:59:59" + ["", "+"][event[0]["timezone"] > 0] + str(event[0]["timezone"]).zfill(2) + ":00"

        print(start_date)

        for calendar in calendars:
            events_result = service.events().list(calendarId=calendar, timeMax=end_date, timeMin=start_date, singleEvents=True, orderBy='startTime').execute()
            events = events_result.get('items', [])

            # "start": { # The (inclusive) start time of the event. For a recurring event, this is the start time of the first instance.
            # "dateTime": "A String", # The time, as a combined date-time value (formatted according to RFC3339). A time zone offset is required unless a time zone is explicitly specified in timeZone.
            # "date": "A String", # The date, in the format "yyyy-mm-dd", if this is an all-day event.
            # "timeZone": "A String", # The time zone in which the time is specified. (Formatted as an IANA Time Zone Database name, e.g. "Europe/Zurich".) For recurring events this field is required and specifies the time zone in which the recurrence is expanded. For single events this field is optional and indicates a custom time zone for the event start/end.
            # },
            if not events:
                print('No upcoming events found.')
            for event in events:
                start = event['start'].get('dateTime', event['start'].get('date'))
                end = event['end'].get('dateTime', event['end'].get('date'))
                print(start, end, event['summary'])
                # Put event into database
                # db.execute("INSERT INTO conflicts (event_id, user_id, start_time, end_time) VALUES(?,?,?,?)", event[0]['id'], session["user_id"], start, end)

        return redirect(flask.url_for("view", id=request.form.get("id")))

    event = db.execute("SELECT * FROM events WHERE id=?", request.args.get("id"))

    if len(event) == 0:
        flash("Not a valid event")
        return redirect("/")
    # Have to pass in the join url
    # Have to pass through the best event times by GET
    return render_template("view.html", event=event[0])

@app.route("/test")
def test():
    creds = db.execute("SELECT user_id, token, refresh_token, token_uri, client_id, client_secret, scopes FROM credentials WHERE user_id=?", session["user_id"])
    creds = credentials_to_dict(creds[0])
    for test in creds:
        print(test)
        print(creds[test])
        print(type(creds[test]))
    return redirect ("/")