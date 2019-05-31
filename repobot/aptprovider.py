import cherrypy
import gnupg
import hashlib
import json
import os
import queue
import sqlalchemy
import traceback
from datetime import datetime
from pydpkg import Dpkg
from sqlalchemy import Column, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship
from sqlalchemy.types import String, Integer, Text, BOOLEAN
from tempfile import TemporaryDirectory
from threading import Thread
from repobot.tables import Base, db


class AptRepo(Base):
    __tablename__ = 'aptrepo'
    id = Column(Integer, primary_key=True)
    name = Column(String(length=32), unique=True, nullable=False)
    gpgkey = Column(Text(), nullable=True)
    gpgkeyprint = Column(Text(), nullable=True)
    gpgpubkey = Column(Text(), nullable=True)

    dists = relationship("AptDist")


class AptDist(Base):
    __tablename__ = 'aptdist'
    id = Column(Integer, primary_key=True)
    repo_id = Column(Integer, ForeignKey("aptrepo.id"), nullable=False)
    repo = relationship("AptRepo")

    dirty = Column(BOOLEAN(), nullable=False, default=False)

    name = Column(String(length=32), nullable=False)

    packages_cache = Column(LONGTEXT(), nullable=True)
    release_cache = Column(Text(), nullable=True)
    sig_cache = Column(Text(), nullable=True)

    __table_args__ = (UniqueConstraint('repo_id', 'name', name='apt_unique_repodist'), )


class AptPackage(Base):
    __tablename__ = 'aptpkg'
    id = Column(Integer, primary_key=True)

    repo_id = Column(Integer, ForeignKey("aptrepo.id"), nullable=False)
    repo = relationship("AptRepo")

    dist_id = Column(Integer, ForeignKey("aptdist.id"), nullable=False)
    dist = relationship("AptDist")

    # index       (always 'binary-amd64' for now)

    name = Column(String(length=128), nullable=False)  # 'python3-pip'
    version = Column(String(length=128), nullable=False)  # '4.20.1'
    arch = Column(String(length=16), nullable=False)  # 'amd64'

    fname = Column(String(length=256), nullable=False)

    size = Column(Integer, nullable=False)

    md5 = Column(String(length=32))
    sha1 = Column(String(length=40))
    sha256 = Column(String(length=64))
    sha512 = Column(String(length=128))

    fields = Column(Text())

    __table_args__ = (UniqueConstraint('name', 'version', 'arch', 'repo_id', 'dist_id', name='apt_unique_repodist'), )

    @property
    def blobpath(self):
        return os.path.join("repos", self.repo.name, "packages", self.dist.name, self.name[0], self.fname)


def get_repo(_db, repo_name, create_ok=True):
    """
    Fetch a repo from the database by name
    """
    repo = _db.query(AptRepo).filter(AptRepo.name == repo_name).first()
    if not repo and create_ok:
        repo = AptRepo(name=repo_name)
        _db.add(repo)
        _db.commit()
    return repo


def get_dist(_db, repo, dist_name, create_ok=True):
    """
    Fetch a repo's dist from the database by name
    """
    dist = _db.query(AptDist).filter(AptDist.name == dist_name, AptDist.repo_id == repo.id).first()
    if not dist and create_ok:
        dist = AptDist(name=dist_name, repo_id=repo.id)
        _db.add(dist)
        _db.commit()
    return dist


algos = {"md5": "MD5Sum",
         "sha1": "SHA1",
         "sha256": "SHA256",
         "sha512": "SHA512"}


def copyhash(fin, fout):
    """
    Copy a file and calculate hashes while doing so
    """
    hashes = {}
    for algo in algos.keys():
        hashes[algo] = getattr(hashlib, algo)()

    while True:
        data = fin.read(4096)
        if not data:
            break
        for h in hashes.values():
            h.update(data)
        fout.write(data)

    return {k: v.hexdigest() for k, v in hashes.items()}


def hashmany(data):
    """
    Hash the input data using several algos
    """
    hashes = {}
    for algo in algos.keys():
        hashes[algo] = getattr(hashlib, algo)()

    for h in hashes.values():
        h.update(data)

    return {k: v.hexdigest() for k, v in hashes.items()}


class AptProvider(object):
    def __init__(self, dbcon, s3client, bucket):
        self.db = dbcon
        self.s3 = s3client
        self.bucket = bucket
        """base path within the s3 bucket"""
        self.basepath = "data/provider/apt"
        """queue entries are tuples containing the database id of the dist to regenerate indexes and signatures for"""
        self.queue = queue.Queue()

        cherrypy.tree.mount(AptWeb(self), "/repo/apt", {'/': {'tools.trailing_slash.on': False,
                                                              'tools.db.on': True}})

        self.updater = Thread(target=self.sign_packages, daemon=True)
        self.updater.start()

    def sign_packages(self):
        Session = sqlalchemy.orm.sessionmaker(autoflush=True, autocommit=False)
        Session.configure(bind=self.db)
        while True:
            try:
                work = self.queue.get(block=True, timeout=5)
            except queue.Empty:
                continue

            session = Session()
            try:
                self._sign_packages(session, work)
            except:
                traceback.print_exc()
            finally:
                session.close()

    def _sign_packages(self, session, work):
        dist_id = work[0]
        dist = session.query(AptDist).filter(AptDist.id == dist_id).first()
        print("Generating metadata for repo:{} dist:{}".format(dist.repo.name, dist.name))

        str_packages = ""

        for package in session.query(AptPackage) \
                .filter(AptPackage.repo == dist.repo,
                        AptPackage.dist == dist) \
                .order_by(AptPackage.id).all():
            fields = json.loads(package.fields)
            for k, v in fields.items():
                str_packages += "{}: {}\n".format(k, v)
            for algo, algoname in algos.items():
                str_packages += "{}: {}\n".format(algoname, getattr(package, algo))

            str_packages += "Filename: packages/{}/{}/{}\n".format(dist.name, package.fname[0], package.fname)
            str_packages += "Size: {}\n".format(package.size)

            str_packages += "\n"

        dist.packages_cache = str_packages.encode("utf-8")

        release_hashes = hashmany(dist.packages_cache)

        str_release = """Origin: . {dist}
Label: . {dist}
Suite: {dist}
Codename: {dist}
Date: {time}
Architectures: amd64
Components: main
Description: Generated by Repobot
""".format(dist=dist.name, time=datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S UTC"))
        for algo, algoname in algos.items():
            str_release += "{}:\n {} {} {}/{}/{}\n".format(algoname,
                                                           release_hashes[algo],
                                                           len(dist.packages_cache),
                                                           "main",  #TODO component
                                                           "binary-amd64",  #TODO whatever this was
                                                           "Packages")

        dist.release_cache = str_release.encode("utf-8")

        keyemail = 'debian_signing@localhost'

        with TemporaryDirectory() as tdir:
            gpg = gnupg.GPG(gnupghome=tdir)

            def getkey():
                keys = [i for i in gpg.list_keys(secret=True) if any([keyemail in k for k in i["uids"]])]
                if keys:
                    return keys[0]

            fingerprint = None

            if not dist.repo.gpgkey:
                print("Generating key for", dist.repo.name)
                key = gpg.gen_key(gpg.gen_key_input(name_email=keyemail,
                                                    expire_date='2029-04-28',
                                                    key_type='RSA',
                                                    key_length=4096,
                                                    key_usage='encrypt,sign,auth',
                                                    passphrase="secret"))
                fingerprint = key.fingerprint
                dist.repo.gpgkey = gpg.export_keys(fingerprint, secret=True, passphrase="secret")
                dist.repo.gpgkeyprint = fingerprint
                dist.repo.gpgpubkey = gpg.export_keys(fingerprint)

            else:
                import_result = gpg.import_keys(dist.repo.gpgkey)
                fingerprint = import_result.results[0]['fingerprint']  # errors here suggests some gpg import issue
                assert(fingerprint == getkey()['fingerprint'])

            dist.sig_cache = gpg.sign(dist.release_cache, keyid=fingerprint, passphrase='secret',
                                      detach=True, clearsign=False).data
            dist.dirty = False
            session.commit()
        print("Metadata generation complete")

    def web_addpkg(self, reponame, name, version, fobj, dist):
        repo = get_repo(db(), reponame)
        dist = get_dist(db(), repo, dist)
        print("Dist:", dist)

        # - read f (write to temp storage if needed) and generate the hashes
        # - load with Dpkg to get name version and whatnot
        with TemporaryDirectory() as tdir:
            tmppkgpath = os.path.join(tdir, "temp.deb")
            with open(tmppkgpath, "wb") as fdest:
                fhashes = copyhash(fobj.file, fdest)
            fsize = os.path.getsize(tmppkgpath)

            p = Dpkg(tmppkgpath)
            pkgname = "{}_{}_{}.deb".format(p.message['Package'], p.message['Version'], p.message['Architecture'])

            #TODO keys can be duplicated in email.message.Message, does this cause any problems?
            fields = {key: p.message[key] for key in p.message.keys()}

            # repos/<reponame>/packages/f/foo.deb
            dpath = os.path.join(self.basepath, "repos", repo.name, "packages", dist.name, pkgname[0], pkgname)
            files = self.s3.list_objects(Bucket=self.bucket, Prefix=dpath).get("Contents")
            if files:
                print(f"will overwrite: {files}")

            pkg = AptPackage(repo=repo, dist=dist,
                             name=p.message['Package'],
                             version=p.message['Version'],
                             arch=p.message['Architecture'],
                             fname=pkgname,
                             size=fsize,
                             **fhashes,
                             fields=json.dumps(fields))
            db().add(pkg)
            db().commit()

            try:
                with open(tmppkgpath, "rb") as f:
                    response = self.s3.put_object(Body=f, Bucket=self.bucket, Key=dpath)
                    assert(response["ResponseMetadata"]["HTTPStatusCode"] == 200), f"Upload failed: {response}"
            except Exception:
                db().delete(pkg)
                db().commit()
                raise

        dist.dirty = True
        db().commit()
        self.regen_dist(dist.id)

        yield "package name: {}\n".format(pkgname)
        yield "package size: {}\n".format(fsize)
        yield "package message:\n-----------------\n{}\n-----------------\n".format(p.message)
        yield "package hashes: {}\n".format(fhashes)

    def regen_dist(self, dist_id):
        self.queue.put((dist_id, ))

        #TODO
        # - verify dpkg name & version match params
        # - copy to persistent storage
        # - add db record keyed under repo name and dist (and index but only 'binary-amd64' for now)
        # - mark dist dirty


@cherrypy.popargs("reponame")
class AptWeb(object):
    def __init__(self, base):
        self.base = base
        self.dists = AptDists(base)
        self.packages = AptFiles(base)

    @cherrypy.expose
    def index(self, reponame=None, regen=False):
        if reponame:
            repo = get_repo(db(), reponame, create_ok=False)

            yield "<a href='/repo/apt/{reponame}/pubkey'>pubkey</a> " \
                  "<a href='/repo/apt/{reponame}?regen=1'>regen</a><hr/>".format(reponame=repo.name)

            for dist in db().query(AptDist).filter(AptDist.repo == repo).order_by(AptDist.name).all():
                yield "<a href='/repo/apt/{reponame}/dists/{name}'>{name}</a>: <a href='/repo/apt/{reponame}/dists/{name}/main/indexname/Packages'>Packages</a> <a href='/repo/apt/{reponame}/dists/{name}/Release'>Release</a> <a href='/repo/apt/{reponame}/dists/{name}/Release.gpg'>Release.gpg</a> <a href='/repo/apt/{reponame}/dists/{name}/install'>install</a><br />".format(reponame=repo.name, name=dist.name)
                if regen:
                    self.base.regen_dist(dist.id)

            # yield "about apt repo '{}'".format(reponame)
        else:
            for repo in db().query(AptRepo).order_by(AptRepo.name).all():
                yield "<a href='/repo/apt/{name}'>{name}</a><br/>".format(name=repo.name)

    @cherrypy.expose
    def pubkey(self, reponame=None):
        cherrypy.response.headers['Content-Type'] = 'text/plain'
        return get_repo(db(), reponame, create_ok=False).gpgpubkey


@cherrypy.expose
class AptDists(object):
    _cp_config = {'request.dispatch': cherrypy.dispatch.MethodDispatcher()}

    def __init__(self, base):
        self.base = base

    def __call__(self, *segments, reponame=None):
        repo = get_repo(db(), reponame, create_ok=False)

        if len(segments) == 4 and segments[3] == "Packages":
            distname, componentname, indexname, pkgs = segments
            dist = get_dist(db(), repo, distname, create_ok=False)

            if not repo or not dist:
                raise cherrypy.HTTPError(404)

            cherrypy.response.headers['Content-Type'] = 'text/plain'
            return dist.packages_cache

        elif len(segments) == 2:
            distname, target = segments
            dist = get_dist(db(), repo, distname, create_ok=False)

            cherrypy.response.headers['Content-Type'] = 'text/plain'
            if target == "Release":
                return dist.release_cache
            elif target == "Release.gpg":
                return dist.sig_cache
            elif target == "install":

                return """#!/bin/sh -ex
wget -qO- {scheme}://{host}/repo/apt/{reponame}/pubkey | apt-key add -
echo 'deb {scheme}://{host}/repo/apt/{reponame}/ {dist} main' | tee /etc/apt/sources.list.d/{reponame}-{dist}.list
apt-get update
""".format(scheme=cherrypy.request.scheme, host=cherrypy.request.headers['Host'], reponame=repo.name, dist=dist.name)
            else:
                raise cherrypy.HTTPError(404)

        elif len(segments) == 1:
            distname = segments[0]
            dist = get_dist(db(), repo, distname, create_ok=False)
            body = ""
            for package in db().query(AptPackage).filter(AptPackage.repo == repo,
                                                         AptPackage.dist == dist).order_by(AptPackage.fname).all():
                body += "<a href='/repo/apt/{reponame}/packages/{dist.name}/{fname[0]}/{fname}'>{fname}</a><br />" \
                    .format(reponame=repo.name, dist=dist, fname=package.fname)
            return body

        raise cherrypy.HTTPError(404)


@cherrypy.expose
class AptFiles(object):
    _cp_config = {'request.dispatch': cherrypy.dispatch.MethodDispatcher()}

    def __init__(self, base):
        self.base = base

    def __call__(self, *segments, reponame=None):
        distname, firstletter, pkgname = segments
        repo = get_repo(db(), reponame, create_ok=False)
        dist = get_dist(db(), repo, distname, create_ok=False)
        package = db().query(AptPackage).filter(AptPackage.repo == repo,
                                                AptPackage.dist == dist,
                                                AptPackage.fname == pkgname).first()

        if not package:
            raise cherrypy.HTTPError(404)

        dpath = os.path.join(self.base.basepath, package.blobpath)

        if cherrypy.request.method == "DELETE":
            db().delete(package)
            self.base.s3.delete_object(Bucket=self.base.bucket, Key=dpath)
            db().commit()
            return

        elif cherrypy.request.method not in ("GET", "HEAD"):
            raise cherrypy.HTTPError(405)

        response = self.base.s3.get_object(Bucket=self.base.bucket, Key=dpath)

        def stream():
            while True:
                data = response["Body"].read(65535)
                if not data:
                    return
                yield data

        cherrypy.response.headers["Content-Type"] = "application/x-debian-package"
        cherrypy.response.headers["Content-Length"] = response["ContentLength"]

        return stream()

    __call__._cp_config = {'response.stream': True}
