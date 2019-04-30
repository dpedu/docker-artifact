import cherrypy
from pydpkg import Dpkg
from repobot.tables import Base, db, get_engine
import sqlalchemy
from sqlalchemy import Column, ForeignKey
from sqlalchemy.types import String, Integer, Text, BOOLEAN
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import relationship
from sqlalchemy import UniqueConstraint
from tempfile import TemporaryDirectory
from threading import Thread
import hashlib
import os
from time import sleep
import gnupg
import traceback
import json


class AptRepo(Base):
    __tablename__ = 'aptrepo'
    id = Column(Integer, primary_key=True)
    name = Column(String(length=32), unique=True, nullable=False)
    gpgkey = Column(Text(), nullable=True)
    gpgkeyprint = Column(Text(), nullable=True)
    gpgpubkey = Column(Text(), nullable=True)


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

    __table_args__ = (UniqueConstraint('name', 'version', 'repo_id', 'dist_id', name='apt_unique_repodist'), )

    @property
    def blobpath(self):
        return "{}/{}/packages/{}/{}_{}.deb".format(self.repo.name, self.dist.name,
                                                    self.name[0], self.name, self.sha256[0:8])


def get_repo(_db, repo_name, create_ok=True):
    repo = _db.query(AptRepo).filter(AptRepo.name == repo_name).first()
    if not repo and create_ok:
        repo = AptRepo(name=repo_name)
        _db.add(repo)
        _db.commit()
    return repo


def get_dist(_db, repo, dist_name, create_ok=True):
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
    Copy a file and calculate hashes while doing so
    """
    hashes = {}
    for algo in algos.keys():
        hashes[algo] = getattr(hashlib, algo)()

    for h in hashes.values():
        h.update(data)

    return {k: v.hexdigest() for k, v in hashes.items()}


class AptProvider(object):
    def __init__(self, dbcon, s3client, bucket="aptprovider"):
        self.db = dbcon
        self.s3 = s3client
        self.bucket = bucket
        self.basepath = "data/provider/apt"
        """
        bucket path (after basedir)
        repos/{reponame}/packages/f/foo.deb
        """
        cherrypy.tree.mount(AptWeb(self), "/repo/apt", {'/': {'tools.trailing_slash.on': False,
                                                              'tools.db.on': True}})

        # ensure bucket exists
        if bucket not in [b['Name'] for b in self.s3.list_buckets()['Buckets']]:
            print("Creating bucket")
            self.s3.create_bucket(Bucket=bucket)

        self.updater = Thread(target=self.sign_packages, daemon=True)
        self.updater.start()

    def sign_packages(self):
        while True:
            sleep(2)
            try:
                self._sign_packages()
            except:
                traceback.print_exc()
            # sleep(10)
            break

    def _sign_packages(self):
        print("signing packages")
        session = sqlalchemy.orm.scoped_session(sqlalchemy.orm.sessionmaker(autoflush=True, autocommit=False))
        session.configure(bind=get_engine())

        dirtydists = session.query(AptDist).filter(AptDist.dirty == True).all()

        for dist in dirtydists:
            print("Signing dist {}/{}".format(dist.repo.name, dist.name))

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

                str_packages += "Filename: packages/{}/{}\n".format(package.fname[0], package.fname)
                str_packages += "Size: {}\n".format(package.size)

                str_packages += "\n"

            dist.packages_cache = str_packages.encode("utf-8")

            release_hashes = hashmany(dist.packages_cache)
            print(release_hashes)

            str_release = """Origin: . {dist}
Label: . {dist}
Suite: {dist}
Codename: {dist}
Date: Fri, 2 Nov 2018 04:58:59 UTC
Architectures: amd64
Components: main
Description: Generated by yolo
""".format(dist=dist.name)
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

        session.commit()

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

            p = Dpkg(tmppkgpath)
            pkgname = "{}_{}_{}.deb".format(p.message['Package'], p.message['Version'], p.message['Architecture'])
            yield "package name: {}\n".format(pkgname)
            yield "package size: {}\n".format(os.path.getsize(tmppkgpath))
            yield "package message:\n-----------------\n{}\n-----------------\n".format(p.message)
            yield "package hashes: {}".format(fhashes)

            # repos/<reponame>/packages/f/foo.deb
            dpath = os.path.join(self.basepath, "repos", repo.name, "packages", pkgname[0], pkgname)

            with open(tmppkgpath, "rb") as f:
                response = self.s3.put_object(Body=f, Bucket=self.bucket, Key=dpath)
                if response["ResponseMetadata"]["HTTPStatusCode"] != 200:
                    print(response)
                    raise Exception("failed to store package")

            fields = {key: p.message[key] for key in p.message.keys()}

            pkg = AptPackage(repo=repo, dist=dist,
                             name=p.message['Package'],
                             version=p.message['Version'],
                             arch=p.message['Architecture'],
                             fname=pkgname,
                             size=os.path.getsize(tmppkgpath),
                             **fhashes,
                             fields=json.dumps(fields))

            dist.dirty = True
            db().add(pkg)
            db().commit()

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
    def index(self, reponame=None):
        if reponame:
            #TODO
            yield "about apt repo '{}'".format(reponame)
        else:
            #TODO
            yield "about all apt repos"

    @cherrypy.expose
    def pubkey(self, reponame=None):
        yield get_repo(db(), reponame, create_ok=False).gpgpubkey


@cherrypy.expose
class AptDists(object):
    _cp_config = {'request.dispatch': cherrypy.dispatch.MethodDispatcher()}

    def __init__(self, base):
        self.base = base

    def __call__(self, *segments, reponame=None):
        if len(segments) == 4 and segments[3] == "Packages":
            distname, componentname, indexname, pkgs = segments

            repo = get_repo(db(), reponame, create_ok=False)
            dist = get_dist(db(), repo, distname, create_ok=False)

            if not repo or not dist:
                raise cherrypy.HTTPError(404)

            yield dist.packages_cache

            return

        elif len(segments) == 2:
            distname, target = segments

            repo = get_repo(db(), reponame, create_ok=False)
            dist = get_dist(db(), repo, distname, create_ok=False)

            if target == "Release":
                # yield "Release for repo={} dist={}".format(reponame, distname)
                yield dist.release_cache
                return

            elif target == "Release.gpg":
                yield dist.sig_cache
                return

        raise cherrypy.HTTPError(404)


@cherrypy.expose
class AptFiles(object):
    _cp_config = {'request.dispatch': cherrypy.dispatch.MethodDispatcher()}

    def __init__(self, base):
        self.base = base

    def __call__(self, *segments, reponame=None):

        firstletter, pkgname = segments
        repo = get_repo(db(), reponame, create_ok=False)
        package = db().query(AptPackage).filter(AptPackage.repo == repo, AptPackage.fname == pkgname).first()

        if not package:
            raise cherrypy.HTTPError(404)

        dpath = os.path.join(self.base.basepath, "repos", repo.name, "packages", package.fname[0], package.fname)

        response = self.base.s3.get_object(Bucket=self.base.bucket, Key=dpath)

        cherrypy.response.headers["Content-Type"] = "application/x-debian-package"
        cherrypy.response.headers["Content-Length"] = response["ContentLength"]

        def stream():
            while True:
                data = response["Body"].read()
                if not data:
                    return
                yield data

        return stream()

    __call__._cp_config = {'response.stream': True}
