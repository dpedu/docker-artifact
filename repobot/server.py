import cherrypy
import logging
from repobot.repos import RepoDb
from repobot.provider import DuplicateException
from repobot.replication import RepoReplicator


class AppWeb(object):
    def __init__(self, db):
        self.db = db

    @cherrypy.expose
    def addpkg(self, provider, reponame, name, version, f, **params):
        try:
            self.db.add_package(provider, reponame, name, version, f.filename, f.file, params)
        except DuplicateException:
            raise cherrypy.HTTPError(409, 'Package already exists')

    @cherrypy.expose
    def repo(self, provider, repo, *args):
        return self.db.browse_repo(provider, repo, args)

    @cherrypy.expose
    def index(self):
        yield "<pre>"
        with self.db.db.transaction() as c:
            for provider, repos in c.root.repos.items():
                for reponame, repo in repos.items():
                    print(repo)
                    for pkgname, versions in repo.packages.items():
                        for version, pkg in versions.items():
                            for fname in pkg.data["files"]:
                                yield "{}/{}/{}/{}/{}\n".format(provider, reponame, pkgname, version, fname)


class FlatDispatch(cherrypy.dispatch.Dispatcher):
    def __init__(self, method):
        """
        Route all sub urls of this one to the single passed method
        """
        super().__init__(self)
        self.method = method

    def find_handler(self, path):
        # Hack, it does not respect settings of parent nodes
        cherrypy.serving.request.config = cherrypy.config
        return self.method, [i for i in filter(lambda o: len(o) > 0, path.split("/")[2:])]


def main():
    import argparse
    import signal

    parser = argparse.ArgumentParser(description="Repobot daemon")
    parser.add_argument('-p', '--port', default=8080, type=int, help="tcp port to listen on")
    parser.add_argument('-s', '--database', default="./repos.db", help="path to persistent database")
    parser.add_argument('-d', '--data-root', default="./data/", help="data storage dir")
    parser.add_argument('-n', '--neighbors', nargs="+", default=[], help="Replication neighbor uris")
    parser.add_argument('--debug', action="store_true", help="enable development options")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.debug else logging.WARNING,
                        format="%(asctime)-15s %(levelname)-8s %(filename)s:%(lineno)d %(message)s")

    db = RepoDb(args.database, args.data_root)
    repl = RepoReplicator(db, args.data_root, args.neighbors)

    repl.start()

    web = AppWeb(db)

    def validate_password(realm, username, password):
        s = library.session()
        if s.query(User).filter(User.name == username, User.password == pwhash(password)).first():
            return True
        return False

    cherrypy.tree.mount(web, '/', {'/': {'tools.trailing_slash.on': False,
                                         # 'error_page.403': web.error,
                                         # 'error_page.404': web.error
                                         },
                                   '/repo': {'request.dispatch': FlatDispatch(web.repo)},
                                   #'/static': {"tools.staticdir.on": True,
                                   #            "tools.staticdir.dir": os.path.join(APPROOT, "styles/dist")
                                   #            if not args.debug else os.path.abspath("styles/dist")},
                                   '/login': {'tools.auth_basic.on': True,
                                              'tools.auth_basic.realm': 'webapp',
                                              'tools.auth_basic.checkpassword': validate_password}})

    cherrypy.config.update({
        'tools.sessions.on': True,
        'tools.sessions.locking': 'explicit',
        'tools.sessions.timeout': 525600,
        'request.show_tracebacks': True,
        'server.socket_port': args.port,
        'server.thread_pool': 25,
        'server.socket_host': '0.0.0.0',
        'server.show_tracebacks': True,
        'log.screen': False,
        'engine.autoreload.on': args.debug
    })

    def signal_handler(signum, stack):
        logging.critical('Got sig {}, exiting...'.format(signum))
        cherrypy.engine.exit()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        cherrypy.engine.start()
        cherrypy.engine.block()
    finally:
        logging.info("API has shut down")
        cherrypy.engine.exit()


if __name__ == '__main__':
    main()
