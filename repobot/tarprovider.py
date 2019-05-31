import cherrypy
import hashlib
import json
import os
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import Column, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.types import String, Integer
from tempfile import TemporaryDirectory
from repobot.tables import Base, db


APPROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))


class TarRepo(Base):
    __tablename__ = 'tarrepo'
    id = Column(Integer, primary_key=True)
    name = Column(String(length=32), unique=True, nullable=False)


class TarPackage(Base):
    __tablename__ = 'tarpkg'
    id = Column(Integer, primary_key=True)

    repo_id = Column(Integer, ForeignKey("tarrepo.id"), nullable=False)
    repo = relationship("TarRepo")

    name = Column(String(length=128), nullable=False)       # 'cpython'
    version = Column(String(length=64), nullable=False)     # '3.7.3'

    fname = Column(String(length=256), nullable=False)      # cpython-3.7.3.tar.gz

    size = Column(Integer, nullable=False)
    sha256 = Column(String(length=64))

    __table_args__ = (UniqueConstraint('fname', 'repo_id', name='tar_unique_repopkg'), )

    @property
    def blobpath(self):
        """
        Get the s3 path within
        repos/<reponame>/tarballs/<f>/<foo>/<foo-1.2.3.tar.gz>
        """
        return os.path.join("repos", self.repo.name, "tarballs", self.fname[0].lower(), self.name, self.fname)


def get_repo(_db, repo_name, create_ok=True):  #TODO make this generic
    """
    Fetch a repo from the database by name
    """
    repo = _db.query(TarRepo).filter(TarRepo.name == repo_name).first()
    if not repo and create_ok:
        repo = TarRepo(name=repo_name)
        _db.add(repo)
        _db.commit()
    return repo


def copysha256(fin, fout):
    """
    Copy a file and calculate sha256 while doing so
    """
    h = hashlib.sha256()

    while True:
        data = fin.read(4096)
        if not data:
            break
        h.update(data)
        fout.write(data)

    return h.hexdigest()


class TarProvider(object):
    def __init__(self, dbcon, s3client, bucket):
        self.db = dbcon
        self.s3 = s3client
        self.bucket = bucket
        """base path within the s3 bucket"""
        self.basepath = "data/provider/tar"

        cherrypy.tree.mount(TarWeb(self), "/repo/tar", {'/': {'tools.trailing_slash.on': False,
                                                              'tools.db.on': True}})

    def web_addpkg(self, reponame, name, version, fobj):
        repo = get_repo(db(), reponame)

        # write wheel to temp storage
        with TemporaryDirectory() as tdir:
            tmppkgpath = os.path.join(tdir, fobj.filename)  #TODO verify filename doesnt have any nonsense like ../../passwd
            with open(tmppkgpath, "wb") as fdest:
                shasum = copysha256(fobj.file, fdest)

            #TODO assert that the uploaded file smells like a tarball
            #TODO assert the version string matches allowed chars
            #TODO assert the name string matches allowed chars
            #TODO support non-gzip
            fname = f"{name}-{version}.tar.gz"

            # add to db
            tar = TarPackage(repo=repo,
                             name=name,
                             version=version,
                             fname=fname,
                             size=os.path.getsize(tmppkgpath),
                             sha256=shasum)

            # s3 path - repos/<reponame>/tarballs/f/foo/foo-1234.tar.gz
            dpath = os.path.join(self.basepath, tar.blobpath)

            files = self.s3.list_objects(Bucket=self.bucket, Prefix=dpath).get("Contents")
            if files:
                print(f"will overwrite: {files}")

            db().add(tar)
            db().commit()

            try:
                with open(tmppkgpath, "rb") as f:
                    response = self.s3.put_object(Body=f, Bucket=self.bucket, Key=dpath)
                    assert(response["ResponseMetadata"]["HTTPStatusCode"] == 200), f"Upload failed: {response}"
            except Exception:
                db().delete(tar)
                db().commit()
                raise

            return json.dumps({"ok": True}, indent=4)  #TODO do something with this


@cherrypy.popargs("reponame", "pkgname", "filename")
class TarWeb(object):
    def __init__(self, base):
        self.base = base

        template_dir = "templates" if os.path.exists("templates") else os.path.join(APPROOT, "templates")
        self.tpl = Environment(loader=FileSystemLoader(template_dir),
                               autoescape=select_autoescape(['html', 'xml']))

    @cherrypy.expose
    def index(self, reponame=None, pkgname=None, filename=None):
        if filename:
            return self.handle_download(reponame, pkgname, filename)
        else:
            return self.handle_navigation(reponame, pkgname, filename)

    def handle_navigation(self, reponame=None, pkgname=None, filename=None):
        if reponame:
            repo = get_repo(db(), reponame, create_ok=False)
            if pkgname:
                return self.tpl.get_template("tar/package.html") \
                    .render(repo=repo,
                            pkgs=db().query(TarPackage).filter(TarPackage.repo == repo,
                                                               TarPackage.name == pkgname).
                            order_by(TarPackage.version).all())

            return self.tpl.get_template("tar/repo.html") \
                .render(repo=repo,
                        pkgs=self._get_dists(repo))

        return self.tpl.get_template("tar/root.html") \
            .render(repos=db().query(TarRepo).order_by(TarRepo.name).all())

    def _get_dists(self, repo):
        lastpkg = None
        for pkg in db().query(TarPackage).filter(TarPackage.repo == repo).order_by(TarPackage.fname).all():
            if lastpkg and pkg.name == lastpkg:
                continue
            yield pkg
            lastpkg = pkg.name

    def handle_download(self, reponame, distname, filename):
        repo = get_repo(db(), reponame, create_ok=False)
        pkg = db().query(TarPackage).filter(TarPackage.repo == repo, TarPackage.fname == filename).first()
        if not pkg:
            raise cherrypy.HTTPError(404)

        dpath = os.path.join(self.base.basepath, pkg.blobpath)
        print("dpath=", dpath)
        print("blobpath=", pkg.blobpath)
        print("basepath=", self.base.basepath)

        if str(cherrypy.request.method) == "DELETE":
            db().delete(pkg)
            files = self.base.s3.list_objects(Bucket=self.base.bucket, Prefix=dpath).get("Contents")
            if files:
                self.base.s3.delete_object(Bucket=self.base.bucket, Key=dpath)
            db().commit()
            return "OK"  #TODO delete the repo if we've emptied it(?)

        elif str(cherrypy.request.method) == "GET":
            response = self.base.s3.get_object(Bucket=self.base.bucket, Key=dpath)

            cherrypy.response.headers["Content-Type"] = "application/octet-stream"
            cherrypy.response.headers["Content-Length"] = response["ContentLength"]

            def stream():
                while True:
                    data = response["Body"].read(65535)
                    if not data:
                        return
                    yield data

            return stream()
        else:
            raise cherrypy.HTTPError(405)

    index._cp_config = {'response.stream': True}
