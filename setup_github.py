"""
One-time script to create a GitHub repository and push the local project.

Usage:
    python setup_github.py --token YOUR_GITHUB_TOKEN --repo your-repo-name
    python setup_github.py --token YOUR_GITHUB_TOKEN --repo your-repo-name --private
    python setup_github.py --token YOUR_GITHUB_TOKEN --repo your-repo-name --org your-org-name
"""

import argparse
import subprocess
import sys
from pathlib import Path

def run(cmd, check=True):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"ERROR: {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()

def main():
    parser = argparse.ArgumentParser(description="Create GitHub repo and push local project")
    parser.add_argument("--token", required=True, help="GitHub personal access token")
    parser.add_argument("--repo", required=True, help="Repository name to create")
    parser.add_argument("--org", default=None, help="GitHub organization (optional; defaults to your user account)")
    parser.add_argument("--private", action="store_true", help="Create as a private repository")
    args = parser.parse_args()

    try:
        from github import Github, GithubException
    except ImportError:
        print("ERROR: PyGithub not installed. Run: pip install PyGithub")
        sys.exit(1)

    project_dir = Path(__file__).parent

    # Connect to GitHub
    g = Github(args.token)
    user = g.get_user()
    print(f"Authenticated as: {user.login}")

    # Create repository
    owner = g.get_organization(args.org) if args.org else user
    try:
        remote_repo = owner.create_repo(
            name=args.repo,
            description="Parametric Optimization Driver — Bayesian optimization co-pilot for CFD workflows",
            private=args.private,
            auto_init=False,
        )
        print(f"Created repository: {remote_repo.html_url}")
    except GithubException as e:
        if e.status == 422:
            print(f"Repository '{args.repo}' already exists. Proceeding with existing repo.")
            remote_repo = owner.get_repo(args.repo)
        else:
            print(f"GitHub error: {e}")
            sys.exit(1)

    remote_url = remote_repo.clone_url.replace(
        "https://", f"https://{args.token}@"
    )

    # Initialize local git repo if needed
    git_dir = project_dir / ".git"
    if not git_dir.exists():
        run("git init")
        run('git config user.email "cfd-opt-driver@local"')
        run('git config user.name "CFD Opt Driver"')
        print("Initialized local git repository.")
    else:
        print("Local git repository already exists.")

    # Set remote
    existing_remote = run("git remote", check=False)
    if "origin" in existing_remote:
        run(f"git remote set-url origin {remote_url}")
    else:
        run(f"git remote add origin {remote_url}")

    # Stage and commit
    run("git add .")
    status = run("git status --porcelain", check=False)
    if status:
        run('git commit -m "initial scaffold: requirements, github setup, progress doc, readme"')
        print("Created initial commit.")
    else:
        print("Nothing to commit — working tree is clean.")

    # Push
    run("git branch -M main")
    run("git push -u origin main")
    print(f"\nSuccess! Repository is live at: {remote_repo.html_url}")

if __name__ == "__main__":
    main()
