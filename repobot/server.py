import boto3
import cherrypy
import logging
import os
import sqlalchemy
from botocore.client import Config as BotoConfig
from repobot.aptprovider import AptProvider
from repobot.pypiprovider import PypiProvider
from repobot.tables import SAEnginePlugin, SATool
from urllib.parse import urlparse


class AppWeb(object):
    def __init__(self, providers):
        self.providers = providers

    @cherrypy.expose
    def index(self):
        yield '<a href="/repo">repos</a>'

    @cherrypy.expose
    def repo(self):
        for provider in self.providers.keys():
            yield '<a href="/repo/{provider}">{provider}</a><br />'.format(provider=provider)

    @cherrypy.expose
    def addpkg(self, provider, reponame, name, version, f, **params):
        # TODO regex validate args
        yield from self.providers[provider].web_addpkg(reponame, name, version, f, **params)


def main():
    import argparse
    import signal

    parser = argparse.ArgumentParser(description="package storage database")
    parser.add_argument('-p', '--port', default=8080, type=int, help="http port to listen on")
    parser.add_argument('-d', '--database', help="mysql+pymysql:// connection string",
                        default=os.environ.get("DATABASE_URL"))
    parser.add_argument('-s', '--s3', help="http:// or https:// connection string",
                        default=os.environ.get("S3_URL"))
    parser.add_argument('--debug', action="store_true", help="enable development options")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING,
                        format="%(asctime)-15s %(levelname)-8s %(filename)s:%(lineno)d %(message)s")

    if not args.database:
        parser.error("--database or DATABASE_URL required")
    if not args.s3:
        parser.error("--s3 or S3_URL required")

    # set up database client
    dbcon = sqlalchemy.create_engine(args.database, echo=args.debug, encoding="utf8")
    SAEnginePlugin(cherrypy.engine, dbcon).subscribe()
    cherrypy.tools.db = SATool()

    # set up s3 client
    s3url = urlparse(args.s3)
    s3args = {"config": BotoConfig(signature_version='s3v4')}

    endpoint_url = f"{s3url.scheme}://{s3url.hostname}"
    if s3url.port:
        endpoint_url += f":{s3url.port}"
    s3args["endpoint_url"] = endpoint_url

    if s3url.username and s3url.password:
        s3args["aws_access_key_id"] = s3url.username
        s3args["aws_secret_access_key"] = s3url.password

    s3 = boto3.client('s3', **s3args)
    bucket = s3url.path[1:]

    # ensure bucket exists
    if bucket not in [b['Name'] for b in s3.list_buckets()['Buckets']]:
        print("Creating bucket")
        s3.create_bucket(Bucket=bucket)

    # set up providers
    providers = {"apt": AptProvider(dbcon, s3, bucket),
                 "pypi": PypiProvider(dbcon, s3, bucket)}

    # set up main web screen
    web = AppWeb(providers)

    cherrypy.tree.mount(web, '/', {'/': {'tools.trailing_slash.on': False,
                                         'tools.db.on': True}})

    cherrypy.config.update({
        'tools.sessions.on': False,
        'request.show_tracebacks': True,
        'server.socket_port': args.port,
        'server.thread_pool': 5,
        'server.socket_host': '0.0.0.0',
        'server.show_tracebacks': True,
        'log.screen': False,
        'engine.autoreload.on': args.debug,
        'server.max_request_body_size': 0,
        'server.socket_timeout': 3600,
        'response.timeout': 3600
    })

    def signal_handler(signum, stack):
        logging.warning('Got sig {}, exiting...'.format(signum))
        cherrypy.engine.exit()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        cherrypy.engine.start()
        cherrypy.engine.block()
    finally:
        cherrypy.engine.exit()


if __name__ == '__main__':
    main()
