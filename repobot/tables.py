import sqlalchemy
import cherrypy
from cherrypy.process import plugins
from sqlalchemy.ext.declarative import declarative_base


Base = declarative_base()


def db():
    return cherrypy.request.db


def get_engine(echo=False):
    return sqlalchemy.create_engine('mysql+pymysql://root:root@localhost/repobot', echo=echo, encoding="utf8")


class SAEnginePlugin(plugins.SimplePlugin):
    def __init__(self, bus, dbcon):
        plugins.SimplePlugin.__init__(self, bus)
        self.sa_engine = dbcon
        self.bus.subscribe("bind", self.bind)

    def start(self):
        Base.metadata.create_all(self.sa_engine)

    def bind(self, session):
        session.configure(bind=self.sa_engine)


class SATool(cherrypy.Tool):
    def __init__(self):
        """
        The SA tool is responsible for associating a SA session
        to the SA engine and attaching it to the current request.
        Since we are running in a multithreaded application,
        we use the scoped_session that will create a session
        on a per thread basis so that you don't worry about
        concurrency on the session object itself.

        This tools binds a session to the engine each time
        a requests starts and commits/rollbacks whenever
        the request terminates.
        """
        cherrypy.Tool.__init__(self, 'before_request_body',
                               self.bind_session,
                               priority=100)

        self.session = sqlalchemy.orm.scoped_session(
            sqlalchemy.orm.sessionmaker(autoflush=True, autocommit=False))

    def _setup(self):
        cherrypy.Tool._setup(self)
        cherrypy.request.hooks.attach('on_end_resource', self.commit_transaction, priority=80)

    def bind_session(self):
        cherrypy.engine.publish('bind', self.session)
        cherrypy.request.db = self.session

    def commit_transaction(self):
        cherrypy.request.db = None
        try:
            self.session.commit()  #TODO commit is issued even on endpoints with no queries
        except:
            self.session.rollback()
            raise
        finally:
            self.session.remove()
