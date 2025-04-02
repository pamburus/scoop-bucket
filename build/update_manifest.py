#!/usr/bin/env python3

import json
import subprocess
import urllib.request
import hashlib
import argparse

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

    # Read the current version from the manifest
    with open(manifest_path, "r") as manifest_file:
        manifest_data = json.load(manifest_file)
    current_version = manifest_data['version']

    print(f"Current version for '{package}': {current_version}")

    # Fetch the latest release info from the upstream repository
    with urllib.request.urlopen(f"https://api.github.com/repos/{repo}/releases/latest") as response:
        release_data = json.loads(response.read().decode())
    latest_tag = release_data['tag_name']
    # Remove the leading "v" if present
    import re
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
    assets_json = json.loads(assets)

    # Loop over each architecture in the asset mapping
    for arch, asset_filename in assets_json.items():
        asset_url = f"https://github.com/{repo}/releases/download/v{latest_version}/{asset_filename}"

        print(f"Calculating hash for asset {asset_url} (architecture: {arch})")

        with urllib.request.urlopen(asset_url) as response:
            new_hash = hashlib.sha256(response.read()).hexdigest()
        new_hash = f"SHA256:{new_hash}"

        print(f"New hash for {arch}: {new_hash}")

        # Update the URL in the manifest for this architecture
        manifest_data['architecture'][arch]['url'] = asset_url

        # Update the hash in the manifest for this architecture
        manifest_data['architecture'][arch]['hash'] = new_hash

    # Update the version field in the manifest
    manifest_data['version'] = latest_version

    with open(manifest_path, "w") as manifest_file:
        json.dump(manifest_data, manifest_file, indent=2)

    if args.skip_commit:
        print(f"Skipping commit for '{package}'.")
        return

    # Configure Git and commit changes
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", manifest_path], check=True)
    subprocess.run(["git", "commit", "-m", f"auto-update {package} to version {latest_version}"], check=True)

    print(f"Updated '{package}' to version {latest_version}.")

if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        print(f"Error: {e}")
        exit(1)
