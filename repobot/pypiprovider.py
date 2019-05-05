import cherrypy
import hashlib
import json
import os
import re
from email import message_from_string
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import Column, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.types import String, Integer, Text
from tempfile import TemporaryDirectory
from wheel import wheelfile
from repobot.tables import Base, db


APPROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../"))


def parse_wheel(path):
    fsize = os.path.getsize(path)

    # open up wheel file (it's actually a zip)
    p = wheelfile.WheelFile(path)

    # look for the files we care about in the '<wheelname>.dist-info' directory
    metadata_file = None
    metadata_wheel = None
    for zipfile in p.filelist:
        parts = os.path.split(zipfile.filename)
        if len(parts) == 2 and parts[0].endswith(".dist-info"):
            if parts[1] == "METADATA":
                metadata_file = zipfile
            elif parts[1] == "WHEEL":
                metadata_wheel = zipfile

    assert(metadata_file), "METADATA file not found"
    assert(metadata_wheel), "WHEEL file not found"

    metadata_data = message_from_string(p.read(metadata_file.filename).decode("UTF-8"))
    wheel_data = message_from_string(p.read(metadata_wheel.filename).decode("UTF-8"))

    # get version and whatnot from the pkginfo. there will be multiple Tags with the same python and api, but
    # there can be varying platforms.
    python_versions = set()
    python_apis = set()
    python_platforms = set()

    for tag in wheel_data.get_all("Tag"):
        python_version, python_api, python_platform = tag.split("-")  # ['py3', 'none', 'any']
        python_versions.update([python_version])
        python_apis.update([python_api])
        python_platforms.update([python_platform])

    assert(len(python_apis) == 1), "wheel metadata python api list has other than 1 unique entry"

    # generate final platforming string
    python_version = '.'.join(sorted(list(python_versions), key=natural_keys))
    python_api = python_apis.pop()
    python_platform = '.'.join(sorted(list(python_platforms), key=natural_keys))

    buildtag = wheel_data["Build"]
    name_parts = [metadata_data["Name"], metadata_data["Version"], python_version, python_api, python_platform]
    if buildtag:
        name_parts.insert(2, buildtag)

    assert(None not in name_parts), "Required metadata field missing"

    # construct filename, verify it matches what was submitted
    fname_parts = name_parts[:]
    fname_parts[0] = fname_parts[0].replace("-", "_")  # replaces dashes in dist name with underscore
    wheelname = "-".join(fname_parts) + ".whl"

    return {"fields": {"dist": name_parts[0],
                       "version": name_parts[1],
                       "build": buildtag,
                       "python": python_version,
                       "api": python_api,
                       "platform": python_platform},
            "wheel": wheel_data.items(),
            "metadata": metadata_data.items(),
            "description": metadata_data.get_payload(),
            "wheelname": wheelname,
            "size": fsize}


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


# https://stackoverflow.com/a/5967539
def sort_atoi(text):
    return int(text) if text.isdigit() else text


def natural_keys(text):
    """
    Sort keeping keys in "natural" order such that version names embedded in strings are ordered correctly such as:
    - macosx_10_6_intel
    - macosx_10_9_intel
    - macosx_10_9_x86_64
    - macosx_10_10_intel
    - macosx_10_10_x86_64
    """
    return [sort_atoi(c) for c in re.split(r'(\d+)', text)]


class PipRepo(Base):
    __tablename__ = 'piprepo'
    id = Column(Integer, primary_key=True)
    name = Column(String(length=32), unique=True, nullable=False)


class PipPackage(Base):
    __tablename__ = 'pippkg'
    id = Column(Integer, primary_key=True)

    repo_id = Column(Integer, ForeignKey("piprepo.id"), nullable=False)
    repo = relationship("PipRepo")

    # see https://github.com/pypa/wheel/blob/master/wheel/wheelfile.py
    # {distribution}-{version}(-{build tag})?-{python tag}-{abi tag}-{platform tag}.whl
    dist = Column(String(length=128), nullable=False)       # 'requests'
    dist_norm = Column(String(length=128), nullable=False)  # 'requests'
    version = Column(String(length=64), nullable=False)     # '2.14.2'
    build = Column(String(length=64), nullable=True)        # '1234'
    python = Column(String(length=64), nullable=False)      # 'cp37'
    api = Column(String(length=64), nullable=False)         # 'cp37m'
    platform = Column(String(length=256), nullable=False)   # 'manylinux1_x86_64'

    fname = Column(String(length=256), nullable=False)

    size = Column(Integer, nullable=False)
    sha256 = Column(String(length=64))

    fields = Column(Text())

    __table_args__ = (UniqueConstraint('fname', 'repo_id', name='pip_unique_repopkg'), )

    @property
    def blobpath(self):
        return os.path.join("repos", self.repo.name, "packages", self.name[0], self.fname)


def get_repo(_db, repo_name, create_ok=True):
    """
    Fetch a repo from the database by name
    """
    repo = _db.query(PipRepo).filter(PipRepo.name == repo_name).first()
    if not repo and create_ok:
        repo = PipRepo(name=repo_name)
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


class PypiProvider(object):
    def __init__(self, dbcon, s3client, bucket="aptprovider"):
        self.db = dbcon
        self.s3 = s3client
        self.bucket = bucket
        """base path within the s3 bucket"""
        self.basepath = "data/provider/pip"

        cherrypy.tree.mount(PipWeb(self), "/repo/pypi", {'/': {'tools.trailing_slash.on': False,
                                                               'tools.db.on': True}})

        # ensure bucket exists
        #TODO bucket creation should happen in server.py
        if bucket not in [b['Name'] for b in self.s3.list_buckets()['Buckets']]:
            print("Creating bucket")
            self.s3.create_bucket(Bucket=bucket)

    def web_addpkg(self, reponame, name, version, fobj):
        repo = get_repo(db(), reponame)

        # write wheel to temp storage
        with TemporaryDirectory() as tdir:
            tmppkgpath = os.path.join(tdir, fobj.filename)  #TODO verify filename doesnt have any nonsense like ../../passwd
            with open(tmppkgpath, "wb") as fdest:
                shasum = copysha256(fobj.file, fdest)

            metadata = parse_wheel(tmppkgpath)
            assert(version == metadata["fields"]["version"]), "wheel metadata version doesn't match supplied version"
            assert(fobj.filename == metadata["wheelname"]), f"file name is invalid, wanted '{metadata['wheelname']}'"

            # s3 path - repos/<reponame>/wheels/f/foo.wheel
            dpath = os.path.join(self.basepath, "repos", repo.name, "wheels",
                                 metadata["wheelname"][0], metadata["wheelname"])

            files = self.s3.list_objects(Bucket=self.bucket, Prefix=dpath).get("Contents")
            if files:
                print(f"will overwrite: {files}")

            # add to db
            pkg = PipPackage(repo=repo,
                             dist=metadata["fields"]["dist"],
                             dist_norm=normalize(metadata["fields"]["dist"]),  # index me ?
                             version=metadata["fields"]["version"],
                             build=metadata["fields"]["build"],
                             python=metadata["fields"]["python"],
                             api=metadata["fields"]["api"],
                             platform=metadata["fields"]["platform"],
                             fname=metadata["wheelname"],
                             size=metadata["size"],
                             sha256=shasum,
                             fields=json.dumps(metadata))
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

            yield json.dumps(metadata, indent=4)


@cherrypy.popargs("reponame", "distname", "filename")
class PipWeb(object):
    def __init__(self, base):
        self.base = base

        template_dir = "templates" if os.path.exists("templates") else os.path.join(APPROOT, "templates")
        self.tpl = Environment(loader=FileSystemLoader(template_dir),
                               autoescape=select_autoescape(['html', 'xml']))
        self.tpl.filters.update(normalize=normalize)

    @cherrypy.expose
    def index(self, reponame=None, distname=None, filename=None):
        if filename:
            return self.handle_download(reponame, distname, filename)
        else:
            return self.handle_navigation(reponame, distname, filename)

    def handle_navigation(self, reponame=None, distname=None, filename=None):
        if reponame:
            repo = get_repo(db(), reponame, create_ok=False)
            if distname:
                yield self.tpl.get_template("pypi/dist.html") \
                    .render(repo=repo,
                            pkgs=db().query(PipPackage).filter(PipPackage.repo == repo,
                                                               PipPackage.dist_norm == distname).
                            order_by(PipPackage.version).all(),
                            distname=normalize(distname))
                return

            yield self.tpl.get_template("pypi/repo.html") \
                .render(repo=repo,
                        dists=db().query(PipPackage).filter(PipPackage.repo == repo).order_by(PipPackage.dist).all())
            return

        yield self.tpl.get_template("pypi/root.html") \
            .render(repos=db().query(PipRepo).order_by(PipRepo.name).all())

    def handle_download(self, reponame, distname, filename):
        repo = get_repo(db(), reponame, create_ok=False)
        pkg = db().query(PipPackage).filter(PipPackage.repo == repo, PipPackage.fname == filename).first()
        if not pkg:
            raise cherrypy.HTTPError(404)
        dpath = os.path.join(self.base.basepath, "repos", repo.name, "wheels", pkg.fname[0], pkg.fname)

        response = self.base.s3.get_object(Bucket=self.base.bucket, Key=dpath)

        cherrypy.response.headers["Content-Type"] = "binary/octet-stream"
        cherrypy.response.headers["Content-Length"] = response["ContentLength"]

        def stream():
            while True:
                data = response["Body"].read(65535)
                if not data:
                    return
                yield data

        return stream()

    handle_download._cp_config = {'response.stream': True}
