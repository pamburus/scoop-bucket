#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request


def fetch_url(url, timeout=10, token=None):
    """Fetch URL with optional authentication token."""
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    response = urllib.request.urlopen(request, timeout=timeout)
    if response.status != 200:
        response.close()
        raise RuntimeError(f"Failed to fetch URL {url}: HTTP {response.status}")
    return response


def retry_with_backoff(func, max_retries=3):
    """
    Generic retry wrapper that can retry any function with exponential backoff.
    Honors Retry-After header for rate limiting errors.
    
    Args:
        func: Callable to execute with retry logic
        max_retries: Maximum number of retry attempts
    
    Returns:
        The result of the successful function call
    
    Raises:
        The last exception if all retries fail
    """
    for attempt in range(max_retries):
        try:
            return func()
        except urllib.error.HTTPError as e:
            # HTTPError is a subclass of URLError and contains HTTP status code and headers
            if attempt < max_retries - 1:
                # Check for rate limiting (403/429) and honor Retry-After header
                if e.code in (403, 429):
                    retry_after = e.headers.get('Retry-After')
                    if retry_after:
                        wait_time = int(retry_after)
                        print(f"Rate limited (HTTP {e.code}). Retry-After: {wait_time}s. Waiting...")
                    else:
                        wait_time = 2 ** attempt
                        print(f"Rate limited (HTTP {e.code}). Using exponential backoff: {wait_time}s...")
                else:
                    # Other HTTP errors - use exponential backoff
                    wait_time = 2 ** attempt
                    print(f"HTTP error {e.code}: {e.reason}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise
        except urllib.error.URLError as e:
            # Network-level errors (connection errors, timeouts, etc.)
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                print(f"Network error: {e.reason}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise
    # Should not reach here, but just in case
    raise RuntimeError(f"Failed after {max_retries} attempts")


def calculate_hash(response):
    sha256_hash = hashlib.sha256()
    while True:
        chunk = response.read(8192)
        if not chunk:
            break
        sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Update package version and assets.")
    parser.add_argument("--package", required=True, help="Name of the package to update")
    parser.add_argument("--repo", required=True, help="Repository of the package to update")
    parser.add_argument("--assets", required=True, help="Asset mapping as a JSON string")
    parser.add_argument("--manifest", required=True, help="Path to the manifest file")
    parser.add_argument("--skip-commit", default=False, action='store_true', help="Skip committing changes to the repository")
    args = parser.parse_args()
    package = args.package
    repo = args.repo
    assets = args.assets
    manifest_path = args.manifest

    # Get GitHub token from environment variable
    github_token = os.environ.get('GITHUB_TOKEN')
    if github_token:
        print("Using authenticated GitHub API requests")
    else:
        print("Warning: GITHUB_TOKEN not set, using unauthenticated requests (lower rate limit)")

    # Read the current version from the manifest
    with open(manifest_path, "r") as manifest_file:
        manifest_data = json.load(manifest_file)
    current_version = manifest_data['version']

    print(f"Current version for '{package}': {current_version}")

    # Fetch the latest release info from the upstream repository
    with retry_with_backoff(lambda: fetch_url(f"https://api.github.com/repos/{repo}/releases/latest", token=github_token)) as response:
        release_data = json.load(response)

    # Parse the latest version from the release info
    latest_tag = release_data['tag_name']
    match = re.match(r"^v\d+\.\d+\.\d+$", latest_tag)
    if match:
        latest_version = latest_tag[1:]  # Remove the leading "v"
    else:
        raise RuntimeError(f"Latest tag '{latest_tag}' does not match the expected format.")

    print(f"Latest upstream version for '{package}': {latest_version}")

    if latest_version == current_version:
        print(f"No update needed for '{package}'.")
        return

    # Load the asset mapping from the command-line argument
    try:
        assets_json = json.loads(assets)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON for assets argument: {e}")

    # Collect the URLs and hashes for the assets and build architecture mapping
    arch_mapping = {}
    for arch, asset_filename in assets_json.items():
        asset_url = f"https://github.com/{repo}/releases/download/v{latest_version}/{asset_filename}"

        print(f"Calculating hash for asset {asset_url} (architecture: {arch})")

        with retry_with_backoff(lambda: fetch_url(asset_url)) as response:
            new_hash = calculate_hash(response)
        formatted_hash = f"SHA256:{new_hash}"

        print(f"New hash for {arch}: {formatted_hash}")

        arch_mapping[arch] = {
            'url': asset_url,
            'hash': formatted_hash
        }

    # Update the architecture field in the manifest
    manifest_data['architecture'] = arch_mapping

    # Update the version field in the manifest
    manifest_data['version'] = latest_version

    with open(manifest_path, "w") as manifest_file:
        json.dump(manifest_data, manifest_file, indent=2)

    if args.skip_commit:
        print(f"Skipping commit for '{package}'.")
        return

    # Configure Git and prepare to commit the changes
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", manifest_path], check=True)

    # Check if there are changes to commit
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if result.stdout == "":
        print(f"No changes to commit for '{package}'.")
        return

    # Commit the changes
    subprocess.run(["git", "commit", "-m", f"auto-update {package} to version {latest_version}"], check=True)

    print(f"Updated '{package}' to version {latest_version}.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"Error: {e}")
        exit(1)
