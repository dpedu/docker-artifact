#!/usr/bin/env python3

import requests
import traceback


def main():
    import argparse
    parser = argparse.ArgumentParser(description="package storage command line interface")
    parser.add_argument('-s', '--server', required=True, help="artifact server")

    subparser_action = parser.add_subparsers(dest='action', help='action')

    subparser_upload = subparser_action.add_parser('upload', help='upload package to repository')
    subparser_upload.add_argument('-y', '--provider', required=True, help="packaging provider")
    subparser_upload.add_argument('-f', '--file', required=True, help="file to upload")
    subparser_upload.add_argument('-r', '--repo', required=True, help="repo name")
    subparser_upload.add_argument('-p', '--package', required=True, help="package name")
    subparser_upload.add_argument('-i', '--package-version', required=True, help="package version")
    subparser_upload.add_argument('-a', '--args', nargs="+", help="extra args")

    args = parser.parse_args()

    params = {"provider": args.provider,
              "reponame": args.repo,
              "name": args.package,
              "version": args.package_version}

    if args.args:
        for entry in args.args:
            key, value = entry.split('=', 1)
            if key in params:
                parser.error(f"duplicate parameter '{key}'")
            params[key] = value

    endpoint = f'{args.server}/addpkg'
    resp = requests.post(endpoint, params=params, files={'f': open(args.file, 'rb')})

    try:
        resp.raise_for_status()
    except Exception:
        traceback.print_exc()

    print(resp.text)
