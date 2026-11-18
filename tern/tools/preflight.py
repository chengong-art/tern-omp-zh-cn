"""Preflight check of the daily automation: start a session only for a new Tern build.

Runs on the automation's own small VM before any session starts. It holds the Stencil
sign-in, because a refresh token works once and the next one has to be kept somewhere
that outlives the run: $STATE_FILE. The session never sees a token; it gets a download
link for the one zip, valid for a short time.

State: {"refresh_token", "attempt": {build: sessions started for it}}. A build is handled
once a session has pushed its "<version> <build>" to tern/VERSION on main.
$STENCIL_REFRESH_TOKEN (an org secret) only seeds an automation with no state yet.

Never test-run this with the secret attached: a test run uses the token up and does
not save the state, so the sign-in is lost and has to be approved again.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

AUTH, BUILD, CLIENT = "https://auth.stencil.so", "https://build.stencil.so", "tern"
LATEST = BUILD + "/api/products/tern/latest/macos-arm64?channel=stable"
DONE = "https://raw.githubusercontent.com/chengong-art/tern-omp-zh-cn/main/tern/VERSION"
UA = {"User-Agent": "tern-zh-cn-preflight"}
TRIES = 3  # sessions started for one build before giving up on it


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def load(name):
    try:
        with open(os.environ[name]) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (KeyError, OSError, ValueError):
        return {}


def call(url, form=None, token=None):
    head = dict(UA, Accept="application/json")
    if token:
        head["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, urllib.parse.urlencode(form).encode() if form else None, head)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except ValueError:
            err = None
        return err if isinstance(err, dict) and "error" in err else {"error": f"HTTP {e.code}"}


def find_zip(node):
    if isinstance(node, dict):
        # A sibling "url" key wins over a bare file "name", which downloads as a 404.
        for k, v in node.items():
            if k == "url" and isinstance(v, str) and v.endswith("macos-arm64.zip"):
                return v, next((x for k, x in node.items() if "sha" in k.lower() and isinstance(x, str)), None)
        for v in node.values():
            if isinstance(v, str) and v.endswith("macos-arm64.zip"):
                return v, next((x for k, x in node.items() if "sha" in k.lower() and isinstance(x, str)), None)
    for v in (node.values() if isinstance(node, dict) else node if isinstance(node, list) else ()):
        hit = find_zip(v)
        if hit:
            return hit
    return None


def signed_url(url, token):
    """Follow the build service to the storage link, which needs no token."""
    opener = urllib.request.build_opener(NoRedirect)
    try:
        opener.open(urllib.request.Request(url, headers=dict(UA, Authorization="Bearer " + token)), timeout=20).close()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return urllib.parse.urljoin(url, e.headers["Location"])
        raise
    raise RuntimeError("the build service did not redirect to a storage link")


def running(item_id):
    """Whether the session started for an item emitted earlier has not finished yet."""
    for it in load("LAST_RUN_FILE").get("items") or []:
        if isinstance(it, dict) and str(it.get("id") or it.get("item_id") or "") == item_id:
            status = str(it.get("status") or it.get("state") or it.get("outcome") or "").lower()
            return any(w in status for w in ("running", "pending", "queued", "progress", "started"))
    return False


def handled():
    """tern/VERSION on main: the "<version> <build>" a session pushed last, None before the first one."""
    try:
        with urllib.request.urlopen(urllib.request.Request(DONE, headers=UA), timeout=20) as r:
            return " ".join(r.read().decode().split())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def decide(state, token):
    info = call(LATEST, token=token)
    hit = None if "error" in info else find_zip(info)
    bld = info.get("build") if isinstance(info.get("build"), dict) else {}
    ver = info.get("version") or bld.get("version")
    if not hit or not hit[1] or not ver:
        return {"run": False, "reason": f"build service: {json.dumps(info)[:200]}"}
    version = str(ver)
    build = str(info.get("buildId") or info.get("build_id") or info.get("id") or bld.get("id") or hit[1][:12])
    key = f"tern-{version}-{build}"
    done = handled()
    if done is None:
        return {"run": False, "reason": "tern/VERSION is not on main yet: the tools are not merged"}
    if done == f"{version} {build}":
        state["attempt"] = {}
        return {"run": False, "reason": f"Tern {version} ({build}) already handled"}
    if running(key):
        return {"run": False, "reason": f"the session for Tern {version} is still running"}
    tries = state.setdefault("attempt", {})
    if tries.get(key, 0) >= TRIES:
        return {"run": False, "reason": f"Tern {version} ({build}) failed {TRIES} times; not starting another session"}
    url = signed_url(urllib.parse.urljoin(BUILD, hit[0]), token)
    state["attempt"] = {key: tries.get(key, 0) + 1}
    return {"items": [{"id": key, "version": state["attempt"][key], "tern_version": version, "build": build,
                       "zip_url": url, "sha256": hit[1]}]}


def main():
    state = load("STATE_FILE")
    seeds = [t for t in dict.fromkeys((state.get("refresh_token"), os.environ.get("STENCIL_REFRESH_TOKEN"))) if t]
    endpoint = call(AUTH + "/.well-known/openid-configuration").get("token_endpoint")
    if not endpoint:
        sys.exit("auth.stencil.so is unreachable")
    why = "no Stencil sign-in: STENCIL_REFRESH_TOKEN is not set"
    for seed in seeds:
        tok = call(endpoint, {"grant_type": "refresh_token", "refresh_token": seed, "client_id": CLIENT})
        if "access_token" in tok:
            break
        why = f"Stencil sign-in expired ({tok.get('error_description') or tok.get('error')}): it needs a new approval"
    else:
        sys.exit(why)  # a failed run is reported; a skipped one is not
    # From here on the run must end normally so that the state is saved: the old token is dead.
    state["refresh_token"] = tok.get("refresh_token") or seed
    try:
        decision = decide(state, tok["access_token"])
    except Exception as e:
        decision = {"run": False, "reason": f"{type(e).__name__}: {e}"[:300]}
    if os.environ.get("STATE_FILE"):
        with open(os.environ["STATE_FILE"], "w") as f:
            json.dump(state, f)
    with open(os.environ["OUTPUT_FILE"], "w") as f:
        json.dump(decision, f)
    print(json.dumps(decision if "items" not in decision else
                     {"items": [dict(i, zip_url=i["zip_url"].split("?")[0]) for i in decision["items"]]}))


main()
