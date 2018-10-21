docker-artifact
===============

Software repository server

Artifact provides an HTTP API for repository management. Currently, Python and Apt repositories are supported.


Quickstart
----------

* Pull or build the image
* `docker run -it --rm -v /some/host/dir:/data -p 80:8080 artifact`

Persistent data will be placed in `/data`. The webserver will listen on port 8080 by default.


Examples
--------

Upload python package:

`curl -vv -F 'f=@pyircbot-4.0.0.post3-py3.5.egg' 'http://host/addpkg?provider=pypi&reponame=main&name=pyircbot&version=4.0.0'`


Install python packages:

`pip3 install -f http://host/repo/pypi/main/ --trusted-host host repobot`


Upload apt package:

`curl -vv -F 'f=@extpython-python3.7_3.7.0_amd64.deb' 'http://host/addpkg?provider=apt&reponame=main&name=extpython-python3.7&version=3.7.0&dist=bionic'`


Install apt packages:

```
wget -qO- http://host/repo/apt/main/repo.key | apt-key add - && \
echo "deb http://host/repo/apt/main bionic main" | tee -a /etc/apt/sources.list && \
apt-get update && \
apt-get install -y extpython-python3.6
```


Notes
-----

* Repos are created automatically when a package is added to them.
* Repo URLs are structured as: `/repo/<provider>/<name>`. Deeper URLs are handled directly by the provider.
* The apt provider will generate a gpg key per repo upon repo creation


Todo
----

* Auth
* Delete packages
* Human-readable package listing
* Support using existing GPG keys
