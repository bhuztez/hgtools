#!/usr/bin/env python2

import os
import os.path
import sys
try:
    import cPickle as pickle
except ImportError:
    import pickle
from functools import wraps
from base64 import urlsafe_b64encode as b64encode
from mercurial import ui, hg, bookmarks, commands, encoding, error
from mercurial.context import memctx, memfilectx
from flask import Flask, request, abort, redirect, render_template, url_for, sessions
from beaker.middleware import SessionMiddleware
from beaker.crypto.pbkdf2 import crypt


def generate_csrf_token():
    return b64encode(os.urandom(40))


def CSRFMiddleware(application):

    @wraps(application)
    def wrapper(environ, start_response):
        session = environ['beaker.session']
        if 'csrf-token' not in session:
            session['csrf-token'] = generate_csrf_token()

        return application(environ, start_response)

    return wrapper


def check_csrf_token_and_refresh(session, token):
    if token != session['csrf-token']:
        abort(403)

    session['csrf-token'] = generate_csrf_token()


PWD = os.getcwd()
REPO_PATH = PWD
BOOKMARK = 'public'
PASSWD_DIR = os.path.join(PWD, 'passwd')
SESSION_DIR = os.path.join(PWD, 'session')

SESSION_OPTS = {
    'session.type': 'file',
    'session.data_dir': SESSION_DIR,
    'session.key': 'session.id',
    'session.auto': True
}

app = application = Flask(__name__)
app.wsgi_app = SessionMiddleware(CSRFMiddleware(app.wsgi_app), SESSION_OPTS)


def get_file_node(repo, rev, filename):
    try:
        return repo[rev][filename].node()
    except error.LookupError:
        return


def get_file_data(repo, rev, filename):
    try:
        return repo[rev][filename].data()
    except error.LookupError:
        return ''


def sanitize_newline(content):
    content = content.replace("\r\n", "\n")

    if not content.endswith("\n"):
        content += "\n"

    return content


def commit_one_file(repo, parent, filename, content, username):
    ctx = memctx(
        repo=repo,
        parents=(parent, None),
        text="commit message",
        files=[filename],
        filectxfn=lambda repo, memctx, path: memfilectx(path, content, False, False, None),
        user=username)
    return repo.commitctx(ctx)


@app.route("/")
def home_view():
    return redirect(url_for('article_detail_view', path='HOME'))


@app.route("/edit/wiki/<path:path>", methods=['GET', 'POST'])
def article_edit_view(path):
    session = request.environ['beaker.session']
    email = session.get("email", None)
    fullname = session.get("fullname", None)

    if email is None or fullname is None:
        return redirect(url_for("login_view", redirect_to=request.base_url))

    filename = path.encode('utf-8')

    if request.method == 'POST':
        check_csrf_token_and_refresh(session, request.form.get("csrf-token", ""))

        parent = request.form['parent'].encode('utf-8')

        content = sanitize_newline(
            request.form['content'].encode('utf-8'))

        w = repo.wlock()

        try:
            current = bookmarks.listbookmarks(repo).get(BOOKMARK, '')

            if current != parent:
                if get_file_node(repo, parent, filename) == get_file_node(repo, current, filename):
                    parent = current

            if current == parent:
                n = commit_one_file(repo, current, filename, content, "%s <%s>"%(fullname.encode("UTF-8"), email))
                bookmarks.pushbookmark(repo, BOOKMARK, current, n)
                return redirect(url_for('article_detail_view', path=path))
        finally:
            w.release()

        data = content
        diff = [
            get_file_data(repo, parent, filename),
            get_file_data(repo, current, filename),
        ]
    else:
        current = bookmarks.listbookmarks(repo)[BOOKMARK]
        data = get_file_data(repo, current, filename).encode("utf-8")
        diff = None

    return render_template(
        'edit.html',
        csrf_token=session['csrf-token'],
        parent=current,
        path=path,
        title=path,
        diff=diff,
        content=data)


@app.route("/wiki/<path:path>.html", methods=['GET'])
def article_detail_view(path):
    nodeid = bookmarks.listbookmarks(repo)[BOOKMARK]
    data = get_file_data(repo, nodeid, path)

    return render_template(
        'wiki.html',
        path=path,
        title=path,
        content=data.decode('utf-8'))


def validate_login_form(form):
    email = request.form.get('email', None)
    password = request.form.get('password', None)

    if email is not None and password is not None:
        filename = os.path.join(PASSWD_DIR, email)

        if os.path.exists(filename):
            with open(filename, 'r') as f:
                d = pickle.load(f)

            if crypt(password, d["password"]) == d["password"]:
                return email, d

    return email, None


@app.route("/accounts/login", methods=['GET', 'POST'])
def login_view():
    session = request.environ['beaker.session']

    if request.method == "POST":
        check_csrf_token_and_refresh(session, request.form.get("csrf-token", ""))

        redirect_to = request.form.get(
            "redirect_to",
            url_for("home_view"))

        email, d = validate_login_form(request.form)

        print(email)
        print(d)

        if d is not None:
            session["email"] = email
            session["fullname"] = d["fullname"]

            print(redirect_to)

            return redirect(redirect_to)
    else:
        email = None
        redirect_to = request.args.get(
            "redirect_to",
            url_for("home_view"))

    return render_template(
        'login.html',
        csrf_token = session['csrf-token'],
        email=email or '',
        redirect_to=redirect_to)


@app.route("/accounts/logout", methods=['GET', 'POST'])
def logout_view():
    pass


def init(*argv):
    if os.path.exists(os.path.join(os.path.expanduser(REPO_PATH), '.hg')):
        return
    u = ui.ui()
    commands.init(u, REPO_PATH)
    repo = hg.repository(u, REPO_PATH)
    n = commit_one_file(repo, None, 'Home', "Hello, world!", u.username())
    bookmarks.pushbookmark(repo, BOOKMARK, '', n)


def run(*argv):
    global repo
    repo = hg.repository(ui.ui(), REPO_PATH)
    app.run(port=8000, debug=True, use_reloader=False)


def add_user(email, fullname):
    from getpass import getpass
    import locale
    codec = locale.getpreferredencoding()
    fullname = fullname.decode(codec)

    filename = os.path.join(PASSWD_DIR, email)

    try:
        os.makedirs(PASSWD_DIR)
    except OSError:
        pass

    assert os.path.exists(PASSWD_DIR)
    assert not os.path.exists(filename)

    password = crypt(getpass("Password: "))

    fd = os.open(filename, os.O_WRONLY|os.O_CREAT|os.O_EXCL)
    with os.fdopen(fd, 'w') as f:
        pickle.dump({"password": password, "fullname": fullname}, f)
        f.flush()
        os.fsync(fd)


if __name__ == '__main__':
    cmds = {
        "init": init,
        "run": run,
        "add-user": add_user}
    cmds[sys.argv[1]](*sys.argv[2:])
