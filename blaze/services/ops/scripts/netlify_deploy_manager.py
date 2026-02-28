#!/usr/bin/env python3
"""
Netlify Deployment Manager — Blaze V4
Integrated site deployment and troubleshooting.
"""
import os, sys, subprocess, json

def _load_token():
    env_file = Path.home() / ".blaze_env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("NETLIFY_AUTH_TOKEN="):
                return line.split("=", 1)[1].strip()
    import os
    return os.environ.get("NETLIFY_AUTH_TOKEN", "")

TOKEN = _load_token()
SITE_ID = "cb4ac01b-00ca-47ca-b9d1-f89903cca2d1"
WEBSITE_PATH = "/tmp/acs-website"

def run_netlify(args):
    env = os.environ.copy()
    env["NETLIFY_AUTH_TOKEN"] = TOKEN
    cmd = ["/tmp/netlify-bin/node_modules/.bin/netlify"] + args
    return subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=WEBSITE_PATH)

def deploy():
    print(f"🚀 Pushing production deploy for {SITE_ID}...")
    # Force production deploy from current directory
    res = run_netlify(["deploy", "--prod", "--dir", "."])
    if res.returncode == 0:
        print("✅ Deploy successful.")
        print(res.stdout)
    else:
        print("❌ Deploy failed.")
        print(res.stderr)

def status():
    res = run_netlify(["status"])
    print(res.stdout)

if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        deploy()
