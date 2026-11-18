"""Find and download the current official Tern build from build.stencil.so.

usage: fetch.py login                  sign in once: prints a link to approve in a browser
       fetch.py latest                 print the newest stable macOS arm64 build as JSON
       fetch.py download OUT_DIR       download it, check its SHA-256, unpack OUT_DIR/Tern.app

Tern builds are invite-only, so every call carries an access token of a Stencil
account that has Tern. The sign-in is the OAuth device flow the Tern app itself uses
(no password). Stencil refresh tokens work once: each use returns the next one, which
is written to the state file ($TERN_ZH_STATE, default ~/.config/tern-zh/stencil.json)
before anything else happens. $STENCIL_REFRESH_TOKEN seeds a machine with no state file.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH, BUILD, CLIENT = "https://auth.stencil.so", "https://build.stencil.so", "tern"
LATEST = BUILD + "/api/products/tern/latest/macos-arm64?channel=stable"
STATE = os.environ.get("TERN_ZH_STATE") or os.path.expanduser("~/.config/tern-zh/stencil.json")
UA = {"User-Agent": "tern-zh-cn-fetch"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(url, form=None, token=None):
    """JSON from a GET, or from a form POST. OAuth errors come back as {"error": ...}."""
    head = dict(UA, Accept="application/json")
    if token:
        head["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, urllib.parse.urlencode(form).encode() if form else None, head)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            err = json.loads(body)
        except ValueError:
            err = None
        if not isinstance(err, dict) or "error" not in err:
            sys.exit(f"{url}: HTTP {e.code} {body[:200]!r}")
        return err


def oidc():
    return call(AUTH + "/.well-known/openid-configuration")


def save(refresh):
    os.makedirs(os.path.dirname(os.path.abspath(STATE)), exist_ok=True)
    tmp = STATE + ".tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump({"refresh_token": refresh, "saved": int(time.time())}, f)
    os.replace(tmp, STATE)


def login():
    conf = oidc()
    dev = call(conf["device_authorization_endpoint"], {"client_id": CLIENT, "scope": "openid offline_access"})
    if "device_code" not in dev:
        sys.exit(f"device authorization refused: {dev}")
    print(f"Approve in a browser: {dev.get('verification_uri_complete') or dev['verification_uri']}"
          f"   (code {dev['user_code']}, valid {dev['expires_in'] // 60} min)", flush=True)
    wait, end = dev.get("interval", 5), time.time() + dev["expires_in"]
    while time.time() < end:
        time.sleep(wait)
        tok = call(conf["token_endpoint"], {"grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                                            "device_code": dev["device_code"], "client_id": CLIENT})
        if "refresh_token" in tok:
            save(tok["refresh_token"])
            print(f"signed in; refresh token saved to {STATE}")
            return
        if tok.get("error") == "slow_down":
            wait += 5
        elif tok.get("error") != "authorization_pending":
            sys.exit(f"sign-in failed: {tok}")
    sys.exit("the link expired before it was approved")


def access_token():
    seeds = []
    try:
        seeds.append(json.load(open(STATE))["refresh_token"])
    except (OSError, ValueError, KeyError):
        pass
    if os.environ.get("STENCIL_REFRESH_TOKEN") and os.environ["STENCIL_REFRESH_TOKEN"] not in seeds:
        seeds.append(os.environ["STENCIL_REFRESH_TOKEN"])
    if not seeds:
        sys.exit("not signed in: run `fetch.py login` or set STENCIL_REFRESH_TOKEN")
    endpoint, tok = oidc()["token_endpoint"], {}
    for seed in seeds:
        tok = call(endpoint, {"grant_type": "refresh_token", "refresh_token": seed, "client_id": CLIENT})
        if "access_token" in tok:
            if tok.get("refresh_token"):
                save(tok["refresh_token"])  # the old one is dead from here on
            return tok["access_token"]
    sys.exit(f"sign-in expired ({tok.get('error_description') or tok.get('error')}): run `fetch.py login` again")


def find_zip(node):
    """The arm64 zip of the build description: (path or URL, sha256 or None).
    A sibling "url" key wins over a bare file "name", which downloads as a 404."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "url" and isinstance(v, str) and v.endswith("macos-arm64.zip"):
                sha = next((x for k, x in node.items() if "sha" in k.lower() and isinstance(x, str)), None)
                return v, sha
        for v in node.values():
            if isinstance(v, str) and v.endswith("macos-arm64.zip"):
                sha = next((x for k, x in node.items() if "sha" in k.lower() and isinstance(x, str)), None)
                return v, sha
    for v in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()):
        hit = find_zip(v)
        if hit:
            return hit
    return None


def latest(token):
    info = call(LATEST, token=token)
    if "error" in info:
        sys.exit(f"build service: {info}")
    hit = find_zip(info)
    if not hit:
        sys.exit(f"no macOS arm64 zip in the build description: {json.dumps(info)[:400]}")
    build = info.get("build") if isinstance(info.get("build"), dict) else {}
    return {"version": info.get("version") or build.get("version"),
            "build": info.get("buildId") or info.get("build_id") or info.get("id") or build.get("id"),
            "zip": urllib.parse.urljoin(BUILD, hit[0]), "sha256": hit[1]}


def download(token, out):
    b = latest(token)
    os.makedirs(out, exist_ok=True)
    dst = os.path.join(out, os.path.basename(urllib.parse.urlparse(b["zip"]).path))
    # The build service answers with a redirect to a signed storage URL, which must
    # not be sent the bearer token.
    opener = urllib.request.build_opener(NoRedirect)
    url, head = b["zip"], dict(UA, Authorization="Bearer " + token)
    for _ in range(5):
        try:
            r = opener.open(urllib.request.Request(url, headers=head), timeout=120)
            break
        except urllib.error.HTTPError as e:
            if e.code not in (301, 302, 303, 307, 308):
                sys.exit(f"{url.split('?')[0]}: HTTP {e.code}")
            url, head = urllib.parse.urljoin(url, e.headers["Location"]), UA
    else:
        sys.exit("too many redirects")
    sha = hashlib.sha256()
    with r, open(dst, "wb") as f:
        while chunk := r.read(1 << 20):
            sha.update(chunk)
            f.write(chunk)
    if not b["sha256"]:
        sys.exit("the build service sent no SHA-256 for the zip")
    if sha.hexdigest() != b["sha256"].lower():
        sys.exit(f"SHA-256 mismatch: got {sha.hexdigest()}, expected {b['sha256']}")
    app = os.path.join(out, "Tern.app")
    subprocess.run(["rm", "-rf", app], check=True)
    subprocess.run(["ditto", "-x", "-k", dst, out], check=True)
    if not os.path.isdir(app):
        sys.exit(f"{dst} holds no Tern.app")
    b["app"] = app
    return b


if __name__ == "__main__":
    cmd = sys.argv[1:]
    if cmd == ["login"]:
        login()
    elif cmd == ["latest"]:
        print(json.dumps(latest(access_token())))
    elif len(cmd) == 2 and cmd[0] == "download":
        print(json.dumps(download(access_token(), cmd[1])))
    else:
        sys.exit(__doc__)
