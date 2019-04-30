
class PypiProvider(object):
    def __init__(self, dbcon, s3client):
        self.db = dbcon
        self.s3 = s3client
