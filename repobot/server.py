import cherrypy
import logging
from repobot.tables import get_engine, SAEnginePlugin, SATool

from repobot.aptprovider import AptProvider
from repobot.pypiprovider import PypiProvider

import boto3
from botocore.client import Config as BotoConfig


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

    parser = argparse.ArgumentParser(description="irc web client server")
    parser.add_argument('-p', '--port', default=8080, type=int, help="tcp port to listen on")
    parser.add_argument('-s', '--database', help="mysql connection string")
    parser.add_argument('--debug', action="store_true", help="enable development options")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING,
                        format="%(asctime)-15s %(levelname)-8s %(filename)s:%(lineno)d %(message)s")

    dbcon = get_engine()

    SAEnginePlugin(cherrypy.engine, dbcon).subscribe()
    cherrypy.tools.db = SATool()

    s3 = boto3.client('s3', config=BotoConfig(signature_version='s3v4'), region_name='us-east-1',
                      endpoint_url='',
                      aws_access_key_id='',
                      aws_secret_access_key='')

    providers = {"apt": AptProvider(dbcon, s3),
                 "pypi": PypiProvider(dbcon, s3)}

    web = AppWeb(providers)

    def validate_password(realm, username, password):
        return True

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
